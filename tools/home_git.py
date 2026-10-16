#!/usr/bin/env python3
"""Read-only inventory of Git refs, worktree metadata, remotes, and WIP.

This tool never changes a source repository. It records metadata only; it does not
create remotes, push refs, or read tracked/untracked file contents.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
  root TEXT PRIMARY KEY, git_dir TEXT, common_dir TEXT, bare INTEGER,
  head_sha TEXT, current_branch TEXT, detached INTEGER, dirty INTEGER,
  changed_entries INTEGER, snapshot_stable INTEGER, protected INTEGER,
  visibility TEXT, error TEXT, observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS remotes (
  root TEXT NOT NULL, name TEXT NOT NULL, fetch_url TEXT, push_url TEXT,
  github_identity TEXT, visibility TEXT, protected INTEGER NOT NULL,
  error TEXT, PRIMARY KEY(root,name)
);
CREATE TABLE IF NOT EXISTS refs (
  root TEXT NOT NULL, refname TEXT NOT NULL, ref_type TEXT NOT NULL,
  sha TEXT NOT NULL, object_type TEXT, PRIMARY KEY(root,refname)
);
CREATE TABLE IF NOT EXISTS remote_ref_checks (
  root TEXT NOT NULL, remote_name TEXT NOT NULL, refname TEXT NOT NULL,
  local_sha TEXT NOT NULL, remote_sha TEXT, exact_match INTEGER,
  error TEXT, PRIMARY KEY(root,remote_name,refname)
);
CREATE INDEX IF NOT EXISTS refs_by_root_type ON refs(root,ref_type);
CREATE INDEX IF NOT EXISTS remote_checks_by_root ON remote_ref_checks(root,remote_name);
"""

PROTECTED_GITHUB_OWNERS = {"camronkellman", "halo-agency"}
REF_BATCH = 100


class ProbeError(Exception):
    pass


def clean_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run(args, cwd=None, timeout=30):
    return subprocess.run(args, cwd=cwd, env=clean_env(), capture_output=True, timeout=timeout)


def decode(data):
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data or "")


def git(root, *args, runner=run, timeout=30):
    result = runner(["git", "--no-optional-locks", "-C", str(root), *args], timeout=timeout)
    if result.returncode:
        raise ProbeError(f"git {args[0]} failed (exit {result.returncode})")
    return decode(result.stdout).strip()


def canonical_github(url):
    """Return (credential-free URL, owner/repo) for GitHub remotes only."""
    value = url.strip()
    scp = re.fullmatch(r"(?:[^@/:]+@)?github\.com:(.+)", value, re.IGNORECASE)
    if scp:
        path = scp.group(1)
        scheme = "ssh"
    else:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None, None
        if (parsed.hostname or "").lower() != "github.com":
            return None, None
        path, scheme = parsed.path.lstrip("/"), parsed.scheme.lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    pieces = path.split("/")
    if len(pieces) != 2 or any(not piece for piece in pieces):
        return None, None
    identity = f"{pieces[0]}/{pieces[1]}"
    if scheme in ("ssh", "git+ssh"):
        return f"git@github.com:{identity}.git", identity
    if scheme in ("http", "https"):
        return f"https://github.com/{identity}.git", identity
    return None, None


def sanitize_remote(url):
    canonical, identity = canonical_github(url)
    if canonical:
        return canonical, identity
    try:
        parsed = urlsplit(url)
        if parsed.scheme and parsed.hostname:
            host = parsed.hostname
            if parsed.port:
                host += f":{parsed.port}"
            return urlunsplit((parsed.scheme, host, parsed.path, "", "")), None
    except ValueError:
        pass
    # SCP-like non-GitHub endpoints have no userinfo syntax we can safely normalize.
    if "@" in url and ":" in url:
        return url.rsplit("@", 1)[-1], None
    return url.split("?", 1)[0].split("#", 1)[0], None


def remote_rows(root, runner=run):
    text = git(root, "remote", "-v", runner=runner)
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[-1] not in ("(fetch)", "(push)"):
            continue
        name, raw, direction = fields[0], fields[1], fields[2][1:-1]
        url, identity = sanitize_remote(raw)
        row = result.setdefault(name, {"fetch_url": None, "push_url": None, "github_identity": identity})
        row[direction + "_url"] = url
        if identity:
            row["github_identity"] = identity
    return result


def gh_visibility(identity, runner=run):
    result = runner(["gh", "repo", "view", identity, "--json", "visibility"], timeout=20)
    if result.returncode:
        return "unavailable", "GitHub visibility query unavailable"
    try:
        value = json.loads(decode(result.stdout))
        visibility = value.get("visibility")
        if visibility not in ("PUBLIC", "PRIVATE", "INTERNAL"):
            raise ValueError("visibility missing")
        return visibility.lower(), None
    except (ValueError, TypeError, AttributeError):
        return "unavailable", "GitHub visibility response invalid"


def read_refs(root, runner=run):
    refs = []
    lines = git(root, "for-each-ref", "--format=%(refname)%09%(objectname)%09%(objecttype)",
                 "refs/heads", "refs/tags", "refs/stash", runner=runner).splitlines()
    for line in lines:
        fields = line.split("\t")
        if len(fields) != 3:
            raise ProbeError("local ref inventory malformed")
        name, sha, object_type = fields
        ref_type = "branch" if name.startswith("refs/heads/") else "stash" if name == "refs/stash" else "tag"
        refs.append({"refname": name, "ref_type": ref_type, "sha": sha, "object_type": object_type})
    try:
        stash_lines = git(root, "stash", "list", "--format=%gd%x09%H", runner=runner).splitlines()
    except ProbeError:
        if git(root, "rev-parse", "--is-bare-repository", runner=runner) != "true":
            raise
        stash_lines = []
    for line in stash_lines:
        fields = line.split("\t", 1)
        if len(fields) == 2 and re.fullmatch(r"stash@\{\d+\}", fields[0]):
            refs.append({"refname": fields[0], "ref_type": "stash", "sha": fields[1], "object_type": "commit"})
    # Save detached HEAD and current branch HEAD explicitly, even when its ref exists.
    head = git(root, "rev-parse", "--verify", "HEAD", runner=runner)
    refs.append({"refname": "HEAD", "ref_type": "head", "sha": head, "object_type": "commit"})
    return refs, head


def status_summary(root, runner=run):
    result = runner(["git", "--no-optional-locks", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"], timeout=90)
    if result.returncode:
        raise ProbeError(f"git status failed (exit {result.returncode})")
    records = result.stdout.split(b"\0") if isinstance(result.stdout, bytes) else result.stdout.encode().split(b"\0")
    count, index = 0, 0
    while index < len(records):
        record = records[index]
        if not record:
            index += 1
            continue
        count += 1
        status = record[:2]
        index += 2 if b"R" in status or b"C" in status else 1
    return count > 0, count


def resolve_paths(root, runner=run):
    git_dir = git(root, "rev-parse", "--absolute-git-dir", runner=runner)
    bare = git(root, "rev-parse", "--is-bare-repository", runner=runner) == "true"
    try:
        common_dir = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir", runner=runner)
    except ProbeError:
        common_dir = git(root, "rev-parse", "--git-common-dir", runner=runner)
        if not os.path.isabs(common_dir):
            common_dir = os.path.normpath(os.path.join(root, common_dir))
    branch_result = runner(["git", "--no-optional-locks", "-C", str(root), "symbolic-ref", "--short", "-q", "HEAD"], timeout=30)
    branch = decode(branch_result.stdout).strip() if branch_result.returncode == 0 else None
    return git_dir, common_dir, bare, branch


def verify_refs(root, refs, remotes, protected, runner=run):
    checks = []
    ref_map = {ref["refname"]: ref["sha"] for ref in refs if ref["ref_type"] in ("branch", "tag") or ref["refname"] == "refs/stash"}
    ref_names = sorted(ref_map)
    for remote_name, remote in remotes.items():
        identity = remote.get("github_identity")
        url = remote.get("fetch_url")
        if not identity or not url or remote.get("protected") or protected:
            continue
        remote_refs = {}
        failure = None
        for start in range(0, len(ref_names), REF_BATCH):
            batch = ref_names[start:start + REF_BATCH]
            if not batch:
                continue
            try:
                result = runner(["git", "--no-optional-locks", "ls-remote", "--refs", url, *batch], timeout=45)
                if result.returncode:
                    raise ProbeError(f"ls-remote failed (exit {result.returncode})")
                for line in decode(result.stdout).splitlines():
                    fields = line.split("\t", 1)
                    if len(fields) == 2 and fields[1] in ref_map:
                        remote_refs[fields[1]] = fields[0]
            except (OSError, subprocess.SubprocessError, ProbeError) as error:
                failure = str(error) if isinstance(error, ProbeError) else "ls-remote unavailable"
                break
        for refname, sha in ref_map.items():
            remote_sha = remote_refs.get(refname)
            checks.append({"remote_name": remote_name, "refname": refname, "local_sha": sha,
                           "remote_sha": remote_sha, "exact_match": int(remote_sha == sha) if remote_sha else 0,
                           "error": failure})
    return checks


def inspect_root(root, runner=run, visibility_probe=gh_visibility, local_only=False, scope_prefixes=()):
    root = os.path.abspath(os.path.expanduser(str(root)))
    record = {"root": root, "git_dir": None, "common_dir": None, "bare": None,
              "head_sha": None, "current_branch": None, "detached": None,
              "dirty": None, "changed_entries": None, "snapshot_stable": 0,
              "protected": 0, "visibility": None, "error": None}
    try:
        git_dir, common_dir, bare, branch = resolve_paths(root, runner)
        if any(is_under(root, prefix) or is_under(common_dir, prefix) for prefix in scope_prefixes):
            record["protected"] = 1
        refs, head = read_refs(root, runner)
        dirty, changed = status_summary(root, runner) if not bare else (False, 0)
        remotes = remote_rows(root, runner)
        for remote in remotes.values():
            identity = remote.get("github_identity")
            owner_repo = tuple(part.lower() for part in identity.split("/")) if identity else None
            remote["protected"] = int(bool(owner_repo and owner_repo[0] in PROTECTED_GITHUB_OWNERS))
            if remote["protected"]:
                record["protected"] = 1
            if identity and not remote["protected"] and not record["protected"] and not local_only:
                remote["visibility"], remote["error"] = visibility_probe(identity, runner)
                if record["visibility"] is None:
                    record["visibility"] = remote["visibility"]
            elif identity:
                remote["visibility"] = None
        final_refs, final_head = read_refs(root, runner)
        final_dirty, final_changed = status_summary(root, runner) if not bare else (False, 0)
        stable = (refs == final_refs and head == final_head and branch == resolve_paths(root, runner)[3]
                  and (dirty, changed) == (final_dirty, final_changed))
        record.update(git_dir=git_dir, common_dir=common_dir, bare=int(bare), head_sha=head,
                      current_branch=branch, detached=int(branch is None), dirty=int(dirty),
                      changed_entries=changed, snapshot_stable=int(stable))
        if not stable:
            record["error"] = "source refs, HEAD, or worktree status changed during inspection"
        checks = [] if local_only else verify_refs(root, refs, remotes, record["protected"], runner)
        return record, remotes, refs, checks
    except (OSError, ValueError, ProbeError, subprocess.SubprocessError) as error:
        record["error"] = str(error) if isinstance(error, ProbeError) else f"inspection failed ({type(error).__name__})"
        return record, {}, [], []


def inventory_roots(inventory):
    uri = Path(inventory).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        return sorted({row[0] for row in conn.execute(
            "SELECT path FROM entries WHERE kind IN ('git_root','bare_git_root')")})


def is_under(path, prefix):
    path = os.path.normcase(os.path.abspath(path))
    prefix = os.path.normcase(os.path.abspath(prefix))
    return path == prefix or path.startswith(prefix.rstrip(os.sep) + os.sep)


def inspect(roots, output, runner=run, visibility_probe=gh_visibility, local_only=False, scope=None):
    output = os.path.abspath(os.path.expanduser(output))
    roots = list(dict.fromkeys(os.path.abspath(os.path.expanduser(str(root))) for root in roots))
    if not roots:
        raise ValueError("no Git roots supplied")
    if output in roots:
        raise ValueError("output path cannot be a Git root")
    scope_prefixes = tuple(os.path.abspath(os.path.expanduser(p)) for p in (scope or ()))
    os.makedirs(os.path.dirname(output), exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(output, 0o600)
    with sqlite3.connect(output) as conn:
        conn.executescript(SCHEMA)
        for table in ("repositories", "remotes", "refs", "remote_ref_checks"):
            conn.execute(f"DELETE FROM {table}")
        for root in roots:
            repo, remotes, refs, checks = inspect_root(root, runner, visibility_probe, local_only, scope_prefixes)
            conn.execute("INSERT OR REPLACE INTO repositories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (repo["root"], repo["git_dir"], repo["common_dir"], repo["bare"], repo["head_sha"],
                          repo["current_branch"], repo["detached"], repo["dirty"], repo["changed_entries"],
                          repo["snapshot_stable"], repo["protected"], repo["visibility"], repo["error"],
                          time.strftime("%Y-%m-%dT%H:%M:%S%z")))
            for name, remote in remotes.items():
                conn.execute("INSERT OR REPLACE INTO remotes VALUES (?,?,?,?,?,?,?,?)",
                             (root, name, remote.get("fetch_url"), remote.get("push_url"),
                              remote.get("github_identity"), remote.get("visibility"),
                              remote.get("protected", 0), remote.get("error")))
            conn.executemany("INSERT OR REPLACE INTO refs VALUES (?,?,?,?,?)",
                             [(root, ref["refname"], ref["ref_type"], ref["sha"], ref["object_type"]) for ref in refs])
            conn.executemany("INSERT OR REPLACE INTO remote_ref_checks VALUES (?,?,?,?,?,?,?)",
                             [(root, check["remote_name"], check["refname"], check["local_sha"],
                               check["remote_sha"], check["exact_match"], check["error"]) for check in checks])
            conn.commit()
    os.chmod(output, 0o600)
    with sqlite3.connect(output) as conn:
        return conn.execute("SELECT count(*),sum(error IS NOT NULL),sum(dirty=1),sum(protected=1) FROM repositories").fetchone()


def read_scope(path):
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    prefixes = value.get("halo_prefixes", []) if isinstance(value, dict) else []
    if not isinstance(prefixes, list) or any(not isinstance(item, str) for item in prefixes):
        raise ValueError("scope halo_prefixes must be a list of paths")
    return prefixes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inventory", help="home_inventory SQLite database")
    source.add_argument("--root", action="append", help="individual Git root; repeatable")
    parser.add_argument("--output", required=True, help="private SQLite metadata output")
    parser.add_argument("--local-only", action="store_true", help="skip GitHub visibility and remote-ref network probes")
    parser.add_argument("--scope", help="JSON scope file containing protected halo_prefixes")
    args = parser.parse_args(argv)
    try:
        roots = inventory_roots(args.inventory) if args.inventory else args.root
        count, errors, dirty, protected = inspect(roots, args.output, local_only=args.local_only,
                                                  scope=read_scope(args.scope) if args.scope else None)
    except (OSError, sqlite3.Error, ValueError, ProbeError) as error:
        print(f"Git inventory failed ({type(error).__name__})", file=sys.stderr)
        return 1
    print(f"repositories={count or 0} errors={errors or 0} dirty={dirty or 0} protected={protected or 0} output={os.path.abspath(args.output)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
