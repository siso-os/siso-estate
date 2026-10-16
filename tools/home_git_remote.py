#!/usr/bin/env python3
"""Read-only, bounded evidence for local Git refs on declared remotes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

from home_git import clean_env, is_under, read_scope, sanitize_remote

MAX_WORKERS = 2
COMMAND_TIMEOUT = 12
RUN_DEADLINE = 300
REF_BATCH = 100

SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_probes (
  common_dir TEXT NOT NULL, remote_name TEXT NOT NULL, endpoint_fingerprint TEXT NOT NULL,
  owner_class TEXT NOT NULL, host TEXT, status TEXT NOT NULL, error_category TEXT,
  refs_checked INTEGER NOT NULL, observed_at TEXT NOT NULL,
  PRIMARY KEY(common_dir,remote_name,endpoint_fingerprint)
);
CREATE TABLE IF NOT EXISTS ref_evidence (
  common_dir TEXT NOT NULL, remote_name TEXT NOT NULL, endpoint_fingerprint TEXT NOT NULL,
  refname TEXT NOT NULL, ref_type TEXT NOT NULL, local_sha TEXT NOT NULL, query_ref TEXT,
  status TEXT NOT NULL, remote_sha TEXT,
  PRIMARY KEY(common_dir,remote_name,endpoint_fingerprint,refname,local_sha)
);
CREATE INDEX IF NOT EXISTS evidence_by_status ON ref_evidence(status);
"""


def read_prefixes(path):
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    prefixes = value.get("protected_prefixes", []) if isinstance(value, dict) else []
    if not isinstance(prefixes, list) or any(not isinstance(item, str) for item in prefixes):
        raise ValueError("protected_prefixes must be a list of paths")
    return prefixes


def path_is_protected(path, prefixes):
    return any(is_under(path, prefix) or is_under(prefix, path) for prefix in prefixes)


def endpoint_details(url, identity):
    safe_url, parsed_identity = sanitize_remote(url)
    if not safe_url:
        return None, "unknown_non_github", None, ""
    identity = identity or parsed_identity
    try:
        host = (urlsplit(safe_url).hostname or "").lower() or None
    except ValueError:
        host = None
    if identity:
        owner = identity.split("/", 1)[0].lower()
        owner_class = "own_sisodias" if owner == "sisodias" else "external_github"
    else:
        owner_class = "unknown_non_github"
    fingerprint = hashlib.sha256(safe_url.encode("utf-8", "replace")).hexdigest()[:16]
    return safe_url, owner_class, host, fingerprint


def load_inventory(databases, prefixes):
    roots = {}
    for database in databases:
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            for row in conn.execute(
                "SELECT root,git_dir,common_dir,current_branch,protected FROM repositories"
            ):
                root, git_dir, common_dir, branch, protected = row
                common = os.path.abspath(common_dir or git_dir or root)
                entry = roots.setdefault(root, {"common_dir": common, "branch": branch,
                                                "protected": False, "refs": [], "remotes": []})
                entry["protected"] |= bool(protected) or any(
                    path_is_protected(path, prefixes) for path in (root, git_dir or "", common) if path
                )
                if branch:
                    entry["branch"] = branch
            for root, refname, ref_type, sha in conn.execute(
                "SELECT root,refname,ref_type,sha FROM refs"
            ):
                if root in roots:
                    roots[root]["refs"].append((refname, ref_type, sha))
            for row in conn.execute(
                "SELECT root,name,fetch_url,github_identity,protected FROM remotes"
            ):
                root, name, url, identity, protected = row
                if root in roots:
                    roots[root]["remotes"].append((name, url, identity, bool(protected)))

    # Worktrees share common refs and remote config; probe each common-dir/endpoint once.
    jobs = {}
    skipped = {}
    for root, repo in roots.items():
        common = repo["common_dir"]
        for name, url, identity, remote_protected in repo["remotes"]:
            safe_url, owner_class, host, fingerprint = endpoint_details(url or "", identity)
            key = (common, name, fingerprint or "")
            existing = jobs.get(key) or skipped.get(key)
            refs = dict(existing.get("refs", {})) if existing else {}
            for refname, ref_type, sha in repo["refs"]:
                if ref_type == "head":
                    query_ref = f"refs/heads/{repo['branch']}" if repo.get("branch") else None
                elif refname.startswith("stash@{"):
                    query_ref = None
                else:
                    query_ref = refname
                refs[(refname, ref_type, sha, query_ref)] = None
            if repo["protected"] or remote_protected:
                skipped[key] = {"common_dir": common, "remote_name": name,
                                "fingerprint": fingerprint or "", "owner_class": owner_class,
                                "host": host, "status": "protected_skipped", "error": "protected",
                                "refs": refs}
                jobs.pop(key, None)
                continue
            if key in skipped:
                continue
            job = jobs.setdefault(key, {"common_dir": common, "remote_name": name,
                                        "fingerprint": fingerprint, "owner_class": owner_class,
                                        "host": host, "url": safe_url, "refs": {}})
            job["refs"].update(refs)
            if not url:
                job["url"] = None
    return list(jobs.values()), list(skipped.values())


def probe_job(job, deadline, runner=subprocess.run):
    refs = job["refs"]
    query_names = sorted({entry[3] for entry in refs if entry[3]})
    if not job.get("url"):
        return {**job, "status": "unavailable", "error": "no_fetch_url", "remote_refs": None}
    remote_refs = {}
    batches = [query_names[i:i + REF_BATCH] for i in range(0, len(query_names), REF_BATCH)] or [[]]
    env = clean_env()
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8"
    env["GIT_TERMINAL_PROMPT"] = "0"
    for batch in batches:
        if time.monotonic() >= deadline:
            return {**job, "status": "deadline_skipped", "error": "run_deadline", "remote_refs": None}
        args = ["git", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=always",
                "--no-optional-locks", "ls-remote", "--refs", job["url"], *batch]
        try:
            result = runner(args, env=env, capture_output=True, timeout=COMMAND_TIMEOUT)
        except subprocess.TimeoutExpired:
            return {**job, "status": "unavailable", "error": "timeout", "remote_refs": None}
        except OSError:
            return {**job, "status": "unavailable", "error": "probe_start_failed", "remote_refs": None}
        if result.returncode:
            return {**job, "status": "unavailable", "error": f"exit_{result.returncode}", "remote_refs": None}
        output = result.stdout.decode("utf-8", "replace") if isinstance(result.stdout, bytes) else result.stdout
        for line in output.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[1] in batch:
                remote_refs[parts[1]] = parts[0]
    return {**job, "status": "available", "error": None, "remote_refs": remote_refs}


def run_probes(jobs, skipped, output, max_wall_seconds=RUN_DEADLINE, runner=subprocess.run):
    max_wall_seconds = max(1, min(int(max_wall_seconds), 600))
    deadline = time.monotonic() + max_wall_seconds
    results = list(skipped)
    completed = set()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(probe_job, job, deadline, runner): job for job in jobs}
        try:
            for future in as_completed(futures, timeout=max_wall_seconds):
                completed.add(future)
                try:
                    results.append(future.result())
                except Exception:
                    job = futures[future]
                    results.append({**job, "status": "unavailable", "error": "probe_error", "remote_refs": None})
        except TimeoutError:
            for future in futures:
                future.cancel()
        # Running children have COMMAND_TIMEOUT, so this join remains bounded after deadline.
    for future, job in futures.items():
        if future in completed:
            continue
        if future.cancelled():
            results.append({**job, "status": "deadline_skipped", "error": "run_deadline", "remote_refs": None})
        else:
            try:
                results.append(future.result())
            except Exception:
                results.append({**job, "status": "unavailable", "error": "probe_error", "remote_refs": None})
    with sqlite3.connect(output) as conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM ref_evidence")
        conn.execute("DELETE FROM remote_probes")
        observed = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        for result in results:
            remote_refs = result.get("remote_refs")
            conn.execute("INSERT OR REPLACE INTO remote_probes VALUES (?,?,?,?,?,?,?,?,?)",
                         (result["common_dir"], result["remote_name"], result["fingerprint"],
                          result["owner_class"], result["host"], result["status"], result["error"],
                          len(result["refs"]), observed))
            for refname, ref_type, local_sha, query_ref in result["refs"]:
                if result["status"] == "protected_skipped":
                    status, remote_sha = "protected_skipped", None
                elif result["status"] == "deadline_skipped":
                    status, remote_sha = "probe_skipped", None
                elif result["status"] != "available":
                    status, remote_sha = "remote_unavailable", None
                elif not query_ref:
                    status, remote_sha = "not_advertisable_ref", None
                else:
                    remote_sha = (remote_refs or {}).get(query_ref)
                    status = "absent_at_named_ref" if not remote_sha else (
                        "match" if remote_sha == local_sha else "object_differs_at_named_ref")
                conn.execute("INSERT OR REPLACE INTO ref_evidence VALUES (?,?,?,?,?,?,?,?,?)",
                             (result["common_dir"], result["remote_name"], result["fingerprint"],
                              refname, ref_type, local_sha, query_ref, status, remote_sha))
        conn.commit()
    os.chmod(output, 0o600)
    with sqlite3.connect(output) as conn:
        probes = conn.execute("SELECT status,count(*) FROM remote_probes GROUP BY status").fetchall()
        refs = conn.execute("SELECT status,count(*) FROM ref_evidence GROUP BY status").fetchall()
    return dict(probes), dict(refs), len(results)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", action="append", required=True, help="private home_git SQLite; repeatable")
    parser.add_argument("--scope", required=True, help="scope.json with protected halo_prefixes")
    parser.add_argument("--protected-prefixes", required=True, help="private protected-prefix JSON")
    parser.add_argument("--output", required=True, help="private SQLite evidence output")
    parser.add_argument("--deadline-seconds", type=int, default=RUN_DEADLINE)
    args = parser.parse_args(argv)
    try:
        prefixes = read_scope(args.scope) + read_prefixes(args.protected_prefixes)
        jobs, skipped = load_inventory(args.inventory, prefixes)
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        fd = os.open(args.output, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(args.output, 0o600)
        p, r, total = run_probes(jobs, skipped, args.output, args.deadline_seconds)
    except (OSError, sqlite3.Error, ValueError, subprocess.SubprocessError) as error:
        print(f"remote evidence failed ({type(error).__name__})", file=sys.stderr)
        return 1
    print(f"remote_groups={total} probes={json.dumps(p,sort_keys=True)} refs={json.dumps(r,sort_keys=True)} output_mode=0600")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
