#!/usr/bin/env python3
"""Back up data that lives outside any git repo: encrypted, chunked, to one private GitHub repo per plane.

  data-backup.py plan                 -> machines/<m>/data-plan.json (what each plane includes and skips)
  data-backup.py push PLANE [...]      build, encrypt, push (use `all` for every plane)
  data-backup.py restore PLANE DIR    rebuild a plane's files under DIR (needs the age identity)

Planes are declared in plan/data-planes.json. For each plane:
  - files are gathered under its paths, minus excludes, pruned folders (node_modules, caches, .git) and
    nested git repos (those are backed up by backup.py);
  - large files selected by the legacy size/extension heuristic are unclassified, not proven
    regenerable. A run holds until they are included with keep_large or explicitly excluded;
  - SQLite databases are copied with `.backup` first, so a live database is captured consistently;
  - everything is tar | zstd | age (recipient in plan/age-recipient.txt) | split below 95 decimal MB;
  - backend github:siso-data/<repo>[#release] retains each run separately, pushed in batches
    under GitHub's 2 GB push limit; only a completed run receives a restore tag. Older snapshots stay.
The age identity lives at ~/.config/siso/age/estate-backup.key, with a copy on siso-vps at the same path.
Only aggregate summaries and transport metadata are plaintext; source names stay inside the archive.

A plane may also set:
  - "machine": the machine that runs it (default laptop); `run all` and `plan` only take this machine's planes;
  - "root": the folder its paths are relative to (default ~/SISO_Workspace; "/" for a server's /opt data);
  - "encrypt": false for a plain private repo (tar | zstd | split, no age; Shaan 26 Sep: "I don't like this
    encrypted data bullshit just push it to git, split it up, make it private"). Key files stay out of any
    plane either way (exclude them): keys live on their box (ADR 0008).
  - "prune": extra folder names to skip (e.g. a browser profile's caches).
  - "keep_large": paths whose large files must be included; size and extension alone never prove
    that a database, recording or archive can be regenerated. Review excludes before offloading.
  - "manual": true keeps a one-off plane (e.g. the Hetzner archive) out of `run all`; run it by name.
  - "repo": the plane repo's name when it is not siso-data-<name> (HALO's planes: halo-data-<plane>).
  - "pre_encrypted": the files are already age-encrypted where they were made (HALO's box locks its planes to HALO's
    own public key, plan/halo-age-recipient.txt); used with "encrypt": false, so there is no second layer and no
    gitleaks pass over ciphertext. Restoring needs HALO's identity, not the estate's.
"""
import argparse, fcntl, hashlib, io, json, os, shutil, socket, sqlite3, subprocess, sys, tarfile, tempfile, threading, time, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import importlib.util
_manifest_spec = importlib.util.spec_from_file_location("estate_data_manifest", os.path.join(os.path.dirname(__file__), "data_manifest.py"))
dm = importlib.util.module_from_spec(_manifest_spec)
_manifest_spec.loader.exec_module(dm)
_source_spec = importlib.util.spec_from_file_location("estate_safe_source", os.path.join(os.path.dirname(__file__), "safe_source.py"))
safe_source = importlib.util.module_from_spec(_source_spec)
_source_spec.loader.exec_module(safe_source)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
OWNER = "sisodias"
KEY = os.path.join(HOME, ".config", "siso", "age", "estate-backup.key")
WORK = os.path.join(WS, "_data", "backup-repos")
CHUNK = 94_000_000
PUSH_BATCH = 1_500_000_000
STREAM_PROFILE = "backup-20261016-15gb"
STREAM_CAP = 1_000_000_000
STREAM_FLOOR = 15_000_000_000
STREAM_METADATA = 32_000_000
STREAM_LOAD_MAX = 30
STREAM_MAX_LANES = 3
PRUNE = {"node_modules", ".venv", "venv", "__pycache__", ".next", ".cache", ".emb_cache", ".git",
         "site-packages", ".turbo", ".pytest_cache", ".mypy_cache", ".playwright-cli"}
REGEN_EXT = {".csv", ".duckdb", ".sqlite", ".db", ".zip", ".parquet", ".jsonl", ".gz", ".zst", ".mp4",
             ".mov", ".wav", ".bin", ".pt", ".safetensors", ".npy", ".bundle", ".tar"}
REGEN_MIN = 20 * 1024 * 1024


def sh(*cmd, cwd=None, timeout=3600, input=None):
    env = None
    if cmd and cmd[0] == "git":
        # Git hooks export these selectors. They override cwd and can redirect a
        # plane's commits/pushes into the caller's repository.
        env = os.environ.copy()
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                    "GIT_PREFIX", "GIT_IMPLICIT_WORK_TREE", "GIT_INTERNAL_SUPER_PREFIX",
                    "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                    "GIT_GRAFT_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE",
                    "GIT_SHALLOW_FILE"):
            env.pop(key, None)
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, input=input)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def planes(machine=None):
    pls = json.load(open(os.path.join(REPO, "plan", "data-planes.json")))["planes"]
    return pls if machine is None else [p for p in pls if p.get("machine", "laptop") == machine]


def root_of(plane):
    return plane.get("root", WS)


def gather(plane):
    inc, regen = [], []
    skipped_git = 0
    base = root_of(plane)
    prune = PRUNE | set(plane.get("prune", []))
    excl = [os.path.join(base, e) for e in plane.get("exclude", [])]
    keep_big = [os.path.join(base, k) for k in plane.get("keep_large", [])]
    for rel in plane["paths"]:
        root = os.path.join(base, rel)
        if os.path.islink(root) or any(root == e or root.startswith(e + "/") for e in excl):
            continue
        if os.path.isfile(root):
            inc.append(root)
            continue
        if os.path.exists(os.path.join(root, ".git")):
            skipped_git += 1
            continue
        for dp, dns, fns in os.walk(root):
            if dp != root and os.path.exists(os.path.join(dp, ".git")):
                dns[:] = []
                continue
            dns[:] = [d for d in dns if d not in prune and not os.path.islink(os.path.join(dp, d))
                      and not any(os.path.join(dp, d) == e or os.path.join(dp, d).startswith(e + "/") for e in excl)]
            for f in fns:
                p = os.path.join(dp, f)
                if os.path.islink(p) or any(p.startswith(e + "/") or p == e for e in excl):
                    continue
                if any(f.endswith(x) for x in plane.get("skip_suffixes", [])) or any(x in f for x in plane.get("skip_contains", [])):
                    continue
                try:
                    s = os.path.getsize(p)
                except OSError:
                    continue
                ext = os.path.splitext(f)[1].lower()
                big_ok = any(p.startswith(k + "/") or p == k for k in keep_big)
                if s >= REGEN_MIN and ext in REGEN_EXT and not big_ok:
                    regen.append({"path": os.path.relpath(p, base), "bytes": s, "mtime": int(os.path.getmtime(p))})
                else:
                    inc.append(p)
    if skipped_git:
        print(f"  {plane['name']}: skipped {skipped_git} configured Git source(s); coverage requires separate verification", file=sys.stderr)
    return inc, regen


def cmd_plan(machine):
    out = []
    for pl in planes(machine):
        inc, regen = gather(pl)
        size = sum(os.path.getsize(p) for p in inc if os.path.exists(p))
        out.append({"plane": pl["name"], "files": len(inc), "bytes": size,
                    "unclassified_large_files": len(regen), "unclassified_large_bytes": sum(r["bytes"] for r in regen),
                    "status": "held" if regen else "planned"})
        print(f"{pl['name']:<22} include {len(inc):>7} files {size/1e9:6.2f} GB | unclassified large {len(regen):>4} files {sum(r['bytes'] for r in regen)/1e9:6.2f} GB")
    with open(os.path.join(REPO, "machines", machine, "data-plan.json"), "w") as output:
        json.dump({"machine": machine, "planned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "planes": out}, output, indent=1)


def recipient():
    return open(os.path.join(REPO, "plan", "age-recipient.txt")).read().split()[0]


def rmtree_retry(path, tries=6):
    """shutil.rmtree that survives a git child still writing into the tree (ENOTEMPTY)."""
    for attempt in range(tries):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))


RATE_LIMIT_MAX_WAIT = 75 * 60


def rate_limited(text):
    text = (text or "").lower()
    return "rate limit" in text or "secondary rate" in text or "abuse detection" in text


def wait_for_rest_quota(max_wait=RATE_LIMIT_MAX_WAIT):
    """Sleep until the REST core quota resets. `gh api rate_limit` itself is not counted."""
    code, output, _ = sh("gh", "api", "rate_limit", timeout=60)
    reset = None
    if not code:
        try:
            reset = json.loads(output)["resources"]["core"]["reset"]
        except (ValueError, KeyError, TypeError):
            pass
    delay = min(max_wait, max(30, reset - time.time() + 5)) if reset else 120
    time.sleep(delay)


def gh_rest(*args, timeout=300, tries=4):
    """Run a `gh` REST command: bounded retries for transient failures, and a wait for
    the quota reset (not a crash) when GitHub answers with a rate limit."""
    waited = attempt = 0
    while True:
        code, output, err = sh("gh", *args, timeout=timeout)
        if not code:
            return code, output, err
        if rate_limited(err + output) and waited < 3:
            waited += 1
            wait_for_rest_quota()
            continue
        attempt += 1
        if attempt >= tries:
            return code, output, err
        time.sleep(15 * attempt)


def api_json(endpoint, paginate=False):
    args = ["api", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    code, output, _ = gh_rest(*args)
    if code:
        raise RuntimeError("GitHub inventory unavailable")
    value = json.loads(output)
    if paginate:
        if not isinstance(value, list) or any(not isinstance(page, list) for page in value):
            raise ValueError("incomplete inventory response")
        return [item for page in value for item in page]
    return value


def github_inventory():
    """Org-wide stored inventory, not logical dataset size; API sizes are reported KiB."""
    result = {"organization": "siso-data", "complete": False, "repositories": [],
              "failed": [], "missing": [], "git_objects_bytes": 0, "release_assets_bytes": 0,
              "git_size_basis": "GitHub repository size KiB; provider-reported, not a byte-exact pack measurement"}
    try:
        repos = api_json("orgs/siso-data/repos?type=all&per_page=100", paginate=True)
        seen = set()
        for repo in repos:
            name = repo["full_name"].lower()
            if not name.startswith("siso-data/") or name in seen or type(repo.get("size")) is not int or repo["size"] < 0:
                raise ValueError("invalid repository inventory")
            seen.add(name)
            try:
                releases = api_json(f"repos/{name}/releases?per_page=100", paginate=True)
                assets = []
                for release in releases:
                    # Embedded assets may be truncated; explicitly paginate every release's asset list.
                    assets.extend(api_json(f"repos/{name}/releases/{release['id']}/assets?per_page=100", paginate=True))
                if any(type(a.get("size")) is not int or a["size"] < 0 for a in assets):
                    raise ValueError("invalid release asset size")
                item = {"repo": name, "git_objects_bytes": repo["size"] * 1024,
                        "release_assets_bytes": sum(a["size"] for a in assets)}
                result["repositories"].append(item)
                result["git_objects_bytes"] += item["git_objects_bytes"]
                result["release_assets_bytes"] += item["release_assets_bytes"]
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
                result["failed"].append(name)
        declared = {"/".join(dm.github(p["backend"])[:2]).lower() for p in planes() if p.get("backend")}
        result["missing"] = sorted(declared - seen)
        membership = api_json("user/memberships/orgs/siso-data")
        # Admin membership is required to certify org-wide coverage, including private repos.
        visible_all = membership.get("state") == "active" and membership.get("role") == "admin"
        if not visible_all:
            result["failed"].append("organization-wide private repository coverage not proven")
        result["complete"] = not result["failed"] and not result["missing"]
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
        result["failed"].append("organization inventory unavailable or malformed")
    total = result["git_objects_bytes"] + result["release_assets_bytes"]
    result["observed_bytes"] = total
    result["observed_decimal_tb"] = total / 1_000_000_000_000
    result["total_stored_bytes"] = total if result["complete"] else None
    result["warning"] = "storage decision required: observed storage >= 1 decimal TB" if total >= 1_000_000_000_000 else None
    return result


def cmd_size():
    inventory = github_inventory()
    print(json.dumps(inventory, sort_keys=True))
    return 0 if inventory["complete"] else 1


def reservation_path():
    return WORK + "-capacity.json"


REST_CACHE_TTL = 600


def _rest_cache_path():
    return reservation_path() + ".rest-cache.json"


def _load_rest_cache():
    try:
        with open(_rest_cache_path()) as source:
            value = json.load(source)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_rest_cache(cache):
    tmp = _rest_cache_path() + f".{os.getpid()}.tmp"
    with open(tmp, "w") as output:
        json.dump(cache, output)
    os.replace(tmp, _rest_cache_path())


def cached_inventory():
    """Each admission used to list every org repo plus its releases (hundreds of REST calls),
    and lanes retry every 10 s, which spent the 5,000/hour core quota. Reuse a complete
    inventory for REST_CACHE_TTL; bytes released since are added in release_reservation, and
    bytes retained after an attempted write stay charged in the reservation state."""
    cache = _load_rest_cache()
    entry = cache.get("inventory")
    if entry and time.time() - entry.get("at", 0) < REST_CACHE_TTL and entry["value"].get("complete"):
        return entry["value"]
    value = github_inventory()
    if value["complete"]:
        cache["inventory"] = {"at": time.time(), "value": value}
        _save_rest_cache(cache)
    return value


def stored_bytes(inventory, owner, repo):
    return next((r["git_objects_bytes"] + r["release_assets_bytes"] for r in inventory["repositories"] if r["repo"] == f"{owner}/{repo}"), None)


def refresh_inventory():
    value = github_inventory()
    if value["complete"]:
        cache = _load_rest_cache()
        cache["inventory"] = {"at": time.time(), "value": value}
        _save_rest_cache(cache)
    return value


def boundary_proven(owner, repo):
    entry = _load_rest_cache().get("boundary", {}).get(f"{owner}/{repo}".lower())
    return bool(entry) and time.time() - entry < REST_CACHE_TTL


def remember_boundary(owner, repo):
    cache = _load_rest_cache()
    cache.setdefault("boundary", {})[f"{owner}/{repo}".lower()] = time.time()
    _save_rest_cache(cache)


def coordinator_admission(machine):
    try:
        with open(os.path.join(REPO, "plan", "github-data-storage.json")) as source:
            coordinator = json.load(source)["upload_coordinator"]
        return (coordinator["machine"] == machine and coordinator["hostname"] == socket.gethostname())
    except (OSError, KeyError, TypeError, ValueError):
        return False


def release_reservation(token, retain_remote=False):
    path = reservation_path()
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(path) as source:
            state = json.load(source)
        if retain_remote:
            # GitHub repository.size is eventually consistent. Keep the complete upper
            # bound charged after ANY attempted write, even a failed/lost response.
            # Reconciliation is explicit; conservative double counting is intentional.
            state[token]["state"] = "remote-write-attempted"
            state[token]["local_peak_bytes"] = 0
        else:
            record = state.pop(token, None)
            cache = _load_rest_cache()
            if record and "inventory" in cache:
                for item in cache["inventory"]["value"].get("repositories", []):
                    if item["repo"] == record["repo"]:
                        item["git_objects_bytes"] += record["bytes"]
                cache["inventory"]["value"]["observed_bytes"] += record["bytes"]
                _save_rest_cache(cache)
        with open(path, "w") as output:
            json.dump(state, output)


def _stream_reservations(state, profile):
    return [record for record in state.values()
            if record.get("stream_profile") == profile and record.get("local_peak_bytes", 0) > 0]


def admit_upload(pl, planned_bytes, local_peak_bytes=None, machine="laptop", stream_profile=None, max_streams=1):
    """Fail closed before writes: org visibility, reader teams, bounds and shared reservations."""
    owner, repo, _ = dm.github(pl["backend"], write=True)
    if not coordinator_admission(machine):
        return False, "single upload coordinator hostname/machine not configured or not this host"
    if pl.get("upload_hold") or pl.get("access_boundary") in (None, "mixed-unresolved"):
        return False, "access boundary unresolved"
    teams = pl.get("github_access_teams")
    if not isinstance(teams, list) or not teams or any(not isinstance(t, str) for t in teams):
        return False, "authorized storage reader teams are not declared"
    if not boundary_proven(owner, repo):
        info = api_json(f"repos/{owner}/{repo}")
        if info.get("private") is not True or info.get("permissions", {}).get("push") is not True:
            return False, "private writable storage repository not proven"
        org = api_json("orgs/siso-data")
        if org.get("default_repository_permission") != "none":
            return False, "organization default reader isolation not proven"
        actual = api_json(f"repos/{owner}/{repo}/teams?per_page=100", paginate=True)
        direct = api_json(f"repos/{owner}/{repo}/collaborators?affiliation=direct&per_page=100", paginate=True)
        if {t["slug"] for t in actual} != set(teams) or direct:
            return False, "storage reader boundary differs from declared teams"
        remember_boundary(owner, repo)
    path = reservation_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        inventory = cached_inventory()
        if not inventory["complete"]:
            return False, "organization storage inventory incomplete; upload denied"
        if os.path.exists(path):
            with open(path) as source:
                state = json.load(source)
        else:
            state = {}
        # Metadata/pack overhead gets an explicit reserve; no boundary relies on raw payload alone.
        planned = planned_bytes + max(1_000_000, planned_bytes // 20)
        outstanding = sum(r["bytes"] for r in state.values())
        local_peak_bytes = local_peak_bytes if local_peak_bytes is not None else planned * 4
        reserved_local = sum(r.get("local_peak_bytes", 0) for r in state.values())
        if stream_profile is not None:
            # Historical artifacts and other profiles consume actual free space;
            # only active allocations in this run profile share its 1GB cap.
            reserved_local = sum(r.get("local_peak_bytes", 0) for r in state.values() if r.get("stream_profile") == stream_profile)
            if stream_profile != STREAM_PROFILE or type(max_streams) is not int or max_streams < 1 or max_streams > STREAM_MAX_LANES:
                return False, "streaming staging reservation exceeds aggregate run cap"
            if len(_stream_reservations(state, stream_profile)) >= max_streams:
                return False, "concurrent stream lane limit reached"
            if reserved_local + local_peak_bytes > STREAM_CAP:
                return False, "concurrent aggregate staging reservation exceeds 1 GB cap"
            floor = STREAM_FLOOR
        else:
            floor = 60 * 1024 ** 3 if machine == "mini" else 100_000_000_000
        probe = WS if os.path.exists(WS) else os.path.dirname(path)
        if shutil.disk_usage(probe).free - reserved_local - local_peak_bytes < floor:
            return False, "local allocation would cross the preserved free-space floor"
        same_repo = sum(r["bytes"] for r in state.values() if r["repo"] == f"{owner}/{repo}")
        stored = stored_bytes(inventory, owner, repo)
        if stored is None:
            # A shard created after the cached inventory was taken is absent from it; without
            # a fresh look every new shard reads as full and lanes mint empty repos in a loop.
            inventory = refresh_inventory()
            stored = stored_bytes(inventory, owner, repo)
        if stored is None or stored + same_repo + planned >= dm.SHARD_LIMIT:
            return False, "repository shard capacity requires a new shard"
        if inventory["observed_bytes"] + outstanding + planned >= 5_000_000_000_000:
            return False, "projected organization storage reaches 5 decimal TB; storage decision required"
        token = uuid.uuid4().hex
        state[token] = {"repo": f"{owner}/{repo}", "bytes": planned, "local_peak_bytes": local_peak_bytes, "stream_profile": stream_profile, "at": time.time()}
        with open(path, "w") as output:
            json.dump(state, output)
        return True, token


@contextmanager
def streaming_lock(max_streams):
    """Serial runs take exclusive ownership; parallel lanes share the lock against them."""
    os.makedirs(os.path.dirname(reservation_path()), exist_ok=True)
    with open(reservation_path() + ".stream-lock", "a") as stream_lock:
        mode = fcntl.LOCK_SH if max_streams > 1 else fcntl.LOCK_EX
        try:
            fcntl.flock(stream_lock, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream_lock, fcntl.LOCK_UN)


def run_plane(pl, machine, stream_profile=None, max_streams=1):
    try:
        return _run_plane(pl, machine, stream_profile, max_streams)
    except RuntimeError as err:
        if str(err) == "streaming resource monitor failed: load ceiling":
            return {"plane": pl["name"], "status": "held", "held_reason": str(err)}
        return {"plane": pl["name"], "status": "error",
                "error": f"backup failed ({type(err).__name__}); diagnostic details withheld"}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as err:
        # Exception text may contain source names, local paths or the whole archive command.
        return {"plane": pl["name"], "status": "error",
                "error": f"backup failed ({type(err).__name__}); diagnostic details withheld"}


def _run_plane(pl, machine, stream_profile=None, max_streams=1):
    if pl.get("machine", "laptop") != machine:
        return {"plane": pl["name"], "status": "held", "held_reason": "plane belongs to a different machine"}
    if pl.get("upload_hold") or pl.get("access_boundary") == "mixed-unresolved":
        return {"plane": pl["name"], "status": "held", "held_reason": "upload_hold: access boundary unresolved"}
    owner, name, release = dm.github(pl.get("backend"), write=True)
    backend = pl["backend"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
    repo_dir = os.path.join(WORK, name + "-" + run_id)
    url = f"https://github.com/{owner}/{name}.git"
    upload_ref = f"refs/heads/uploads/{machine}/{run_id}"
    snapshot_ref = f"refs/tags/snapshots/{machine}/{run_id}"
    t0 = time.time()
    missing_sources = sum(not os.path.exists(os.path.join(root_of(pl), p)) for p in pl["paths"])
    git_sources = sum(os.path.exists(os.path.join(root_of(pl), p, ".git")) for p in pl["paths"])
    if missing_sources or git_sources:
        return {"plane": pl["name"], "status": "held", "held_reason": "configured source coverage is incomplete",
                "missing_sources": missing_sources, "git_sources": git_sources}
    inc, regen = gather(pl)
    # skip the upload when nothing in the plane changed since the last push (big planes are GBs)
    import hashlib
    fp = hashlib.sha256((backend + "\n" + ("" if pl.get("encrypt", True) else "plain\n")).encode()   # switching a plane to plain re-pushes it
                        + "\n".join(f"{p}\t{os.path.getsize(p)}\t{int(os.path.getmtime(p))}" for p in sorted(inc) if os.path.exists(p)).encode()).hexdigest()
    last = (json.load(open(os.path.join(REPO, "machines", machine, "data-backup.json"))).get("results", {}).get(pl["name"], {})
            if os.path.exists(os.path.join(REPO, "machines", machine, "data-backup.json")) else {})
    # A new omitted database does not change the included-file fingerprint. Hold before the
    # unchanged shortcut as well as before staging/uploading; report counts, never private names.
    if regen:
        return {"plane": pl["name"], "status": "held",
                "held_reason": "unclassified large files: review keep_large or explicit excludes before backup/offload",
                "files": last.get("files", 0),
                "included_files": len(inc), "unclassified_large_files": len(regen),
                "unclassified_large_bytes": sum(r["bytes"] for r in regen),
                "previous_ref": last.get("ref") or last.get("previous_ref"),
                "previous_commit": last.get("commit") or last.get("previous_commit")}
    # Never infer unchanged content from size/mtime. Every selected run gets a new
    # independently downloaded and extracted snapshot, including same-metadata edits.
    # An emptied source must not look like a successful current backup (agent-base-runs, 26 Sep).
    if not inc and (last.get("files") or last.get("included_files") or last.get("unclassified_large_files")):
        return dict(last, status="held", held_reason="no files on disk after earlier data was recorded; no new snapshot created")
    base = root_of(pl)
    if stream_profile is not None:
        return run_streamed(pl, machine, inc, run_id, fp, stream_profile, max_streams=max_streams)
    if not pl.get("encrypt", True) and not pl.get("pre_encrypted"):
        held = secrets_in(pl, inc)
        if held:
            return {"plane": pl["name"], "status": "held", "held_reason": f"gitleaks: {len(held)} finding(s) in a plain plane; "
                    "exclude those files or keep the plane encrypted", "findings": held[:10], "fingerprint": last.get("fingerprint"),
                    "files": last.get("files", 0)}
    # Reserve a conservative archive upper bound before SQLite copying or staging.
    source_bytes = sum(os.path.getsize(p) for p in inc)
    wal_bytes = sum(os.path.getsize(p + "-wal") for p in inc if pl.get("sqlite_backup") and os.path.exists(p + "-wal"))
    tar_overhead = sum(len(os.path.relpath(p, base).encode()) + 4096 for p in inc) + 2_000_000
    archive_bound = (source_bytes + wal_bytes + tar_overhead) * 11 // 10 + 1
    admitted, reason = admit_upload(pl, archive_bound, local_peak_bytes=archive_bound * 4 + (source_bytes + wal_bytes) * 2, machine=machine)
    if not admitted:
        return {"plane": pl["name"], "status": "held", "held_reason": reason}
    reservation = reason
    upload_started = False
    stage = os.path.join(WS, "_data", "estate-stage", pl["name"] + "-" + run_id)
    try:
        os.makedirs(stage)
        # consistent copies of SQLite databases
        files = []
        for p in inc:
            if p.endswith((".db", ".sqlite")) and pl.get("sqlite_backup"):
                dst = os.path.join(stage, "sqlite", os.path.relpath(p, base))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    src = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                    dstc = sqlite3.connect(dst)
                    src.backup(dstc)
                    dstc.close(); src.close()
                    files.append(("sqlite/" + os.path.relpath(p, base), dst))
                    continue
                except Exception:
                    pass
            files.append((os.path.relpath(p, base), p))
        manifest = {"plane": pl["name"], "machine": machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "files": [{"path": r, "bytes": os.path.getsize(a)} for r, a in files if os.path.exists(a)],
                    "regenerable_not_included": regen}
        mpath = os.path.join(stage, "MANIFEST.json")
        json.dump(manifest, open(mpath, "w"), indent=0)
        # tar reads a list of (archive name -> real path) via a staging tree of links would be slow;
        # instead tar with -C WS for normal files and -C stage for the sqlite copies and manifest
        # one list, all relative to WS; the stage prefix is stripped so MANIFEST.json and sqlite/ sit at the
        # archive root (bsdtar reads -T after every -C, so a second -C cannot be used)
        stage_rel = os.path.relpath(stage, base)
        names = [r for r, a in files if not r.startswith("sqlite/")]
        names.append(os.path.join(stage_rel, "MANIFEST.json"))
        if os.path.isdir(os.path.join(stage, "sqlite")):
            names.append(os.path.join(stage_rel, "sqlite"))
        lst = os.path.join(stage, "files.lst")
        open(lst, "wb").write(b"\0".join(r.encode() for r in names) + b"\0")
        chunks_dir = os.path.join(stage, "chunks")
        os.makedirs(chunks_dir)
        gnu = "GNU tar" in sh("tar", "--version")[1]
        strip = [f"--transform=s,^{stage_rel}/,,"] if gnu else ["-s", f",^{stage_rel}/,,"]
        tar_args = ["tar", "-cf", "-", "--null", "-C", base, *strip, "-T", lst]
        enc = pl.get("encrypt", True)
        prefix = "data.tar.zst.age." if enc else "data.tar.zst."
        pipe = (f"{' '.join(map(shq, tar_args))} 2>{shq(os.path.join(stage, 'tar.err'))} | zstd -q -T0 -6 | "
                + (f"age -r {shq(recipient())} | " if enc else "")
                + f"split -b {CHUNK} -a 3 - {shq(os.path.join(chunks_dir, prefix))}")
        r = subprocess.run(["bash", "-o", "pipefail", "-c", pipe], capture_output=True, text=True)
        if r.returncode != 0:
            return {"plane": pl["name"], "status": "error", "error": f"archive pipeline failed (exit {r.returncode}); diagnostic details withheld"}
        chunks = sorted(os.listdir(chunks_dir))
        total = sum(os.path.getsize(os.path.join(chunks_dir, c)) for c in chunks)
        transport = dm.build(chunks_dir, chunks, backend, enc)
        if not chunks or any(p["size"] >= dm.CHUNK_LIMIT for p in transport["parts"]):
            raise RuntimeError("archive chunk exceeds decimal blob bound")
        if total > archive_bound:
            raise RuntimeError("archive exceeds reserved upper bound; no upload attempted")
        os.makedirs(repo_dir)
        sh("git", "init", "-q", "-b", "main", cwd=repo_dir)
        # encrypted chunks are random bytes: no delta search, no zlib (both only burn CPU before the push)
        for k, v in (("core.bigFileThreshold", "1m"), ("core.compression", "0"), ("pack.window", "0"), ("pack.depth", "0")):
            sh("git", "config", k, v, cwd=repo_dir)
        sh("git", "remote", "add", "origin", url, cwd=repo_dir)
        label = ("HALO age-encrypted payload (unencrypted transport archive)" if pl.get("pre_encrypted")
                 else "Encrypted" if enc else "Plain (unencrypted, private repo)")
        summary = [f"# Snapshot {run_id}", "", label + f" backup, taken {manifest['at']}.",
                   f"{len(pl['paths'])} configured sources; {len(manifest['files'])} files, "
                   f"{sum(f['bytes'] for f in manifest['files'])/1e9:.2f} GB before compression; "
                   f"{len(chunks)} {'encrypted ' if enc else ''}chunks, {total/1e9:.2f} GB.",
                   f"{len(regen)} unclassified large files ({sum(x['bytes'] for x in regen)/1e9:.2f} GB).",
                   "File paths and sizes are recorded in MANIFEST.json inside the archive."]
        open(os.path.join(repo_dir, "SUMMARY.md"), "w").write("\n".join(summary) + "\n")
        open(os.path.join(repo_dir, "RESTORE.md"), "w").write(
            "# Restore\n\nPaths are relative to the original source root. Each run's `*.tar.zst.age` is encrypted to HALO's "
            "own age key (identity: ~/.config/siso/age/halo-backup.key on siso-vps; the estate credentials store under "
            "SISO_Agency/partners/halo/age).\n\n```bash\ncd <this repo>\ncat data.tar.zst.* | zstd -d | tar -xf - -C <dir>\n"
            "age -d -i ~/.config/siso/age/halo-backup.key <dir>/.../<plane>.tar.zst.age | zstd -d | tar -xf - -C <out>\n"
            "cd <out> && sha256sum -c FILES.sha256\n```\n" if pl.get("pre_encrypted") else
            "# Restore\n\nPaths are relative to the original source root.\n\n```bash\ncd <this repo>\ncat data.tar.zst.* | zstd -d | tar -xf - -C <dir>\n```\n\n"
            "Or `estate data restore <plane> <dir>`. SQLite databases come back under `sqlite/` as consistent copies.\n" if not enc else
            "# Restore\n\nNeeds the age identity (`~/.config/siso/age/estate-backup.key`; a copy is on siso-vps at the same path).\n\n"
            "```bash\ncd <this repo>\ncat data.tar.zst.age.* | age -d -i ~/.config/siso/age/estate-backup.key | zstd -d | tar -xf - -C ~/SISO_Workspace\n```\n\n"
            "Or `estate data restore <plane> <dir>`. SQLite databases come back under `sqlite/` as consistent copies.\n")
        with open(os.path.join(repo_dir, "RESTORE.md"), "a") as f:
            f.write(f"\nCompleted snapshot: `{snapshot_ref}`. Clone this tag explicitly; the default branch may be an older snapshot or a partial upload.\n")
        code, _, _ = sh("git", "add", "SUMMARY.md", "RESTORE.md", cwd=repo_dir)
        if code != 0:
            return {"plane": pl["name"], "status": "error", "error": f"add summary failed (exit {code})"}
        code, _, _ = sh("git", "commit", "-qm", f"{name}: summary", cwd=repo_dir)
        if code != 0:
            return {"plane": pl["name"], "status": "error", "error": f"commit summary failed (exit {code})"}
        batch, pushed = 0, 0
        uploaded_head = None
        def publish(ref, previous=None, head=None):
            nonlocal upload_started
            if head is None:
                code, head, _ = sh("git", "rev-parse", "HEAD", cwd=repo_dir)
                if code != 0:
                    raise RuntimeError(f"resolve snapshot failed (exit {code})")
            code, advertised, err = sh("git", "ls-remote", "origin", ref, cwd=repo_dir, timeout=120)
            if code != 0 or (advertised and advertised.split() not in ([head, ref], [previous, ref])):
                raise RuntimeError("snapshot destination unavailable or changed")
            upload_started = True
            code, _, err = sh("git", "push", "-q", "origin", f"{head}:{ref}", cwd=repo_dir, timeout=7200)
            # A lost successful-push response is safe only when the same frozen SHA is advertised.
            code, advertised, err = sh("git", "ls-remote", "origin", ref, cwd=repo_dir, timeout=120)
            if code != 0 or advertised.split() != [head, ref]:
                raise RuntimeError("snapshot remote SHA verification failed; local clone kept")
            return head
        if release:
            # A unique immutable tag per run preserves every prior release restore identifier.
            release = release + "-" + run_id
            backend = f"github:{owner}/{name}#{release}"
            transport["locations"] = [backend]
            uploaded_head = publish(upload_ref, uploaded_head)
            code, _, _ = sh("gh", "release", "create", release, "--repo", f"{owner}/{name}",
                            "--target", uploaded_head, "--title", "Data snapshot " + run_id, "--notes", "Archive transport; verify manifest before restore.")
            if code:
                raise RuntimeError("release creation failed")
            for c in chunks:
                if sh("gh", "release", "upload", release, os.path.join(chunks_dir, c), "--repo", f"{owner}/{name}", timeout=7200)[0]:
                    raise RuntimeError("release upload failed")
            info = api_json(f"repos/{owner}/{name}/releases/tags/{release}")
            asset_inventory = api_json(f"repos/{owner}/{name}/releases/{info['id']}/assets?per_page=100", paginate=True)
            assets = {a["name"]: a["id"] for a in asset_inventory}
            if set(assets) != set(chunks):
                raise RuntimeError("release asset inventory mismatch")
            transport["pins"][backend] = {"release_id": info["id"], "assets": assets}
        for c in ([] if release else chunks):
            chunk_path = os.path.join(repo_dir, c)
            shutil.move(os.path.join(chunks_dir, c), chunk_path)
            chunk_size = os.path.getsize(chunk_path)
            code, _, _ = sh("git", "add", c, cwd=repo_dir)
            if code != 0:
                return {"plane": pl["name"], "status": "error", "error": f"add chunk failed (exit {code})"}
            code, stored_size, err = sh("git", "cat-file", "-s", ":" + c, cwd=repo_dir)
            if code != 0 or stored_size != str(chunk_size):
                return {"plane": pl["name"], "status": "error", "error": "chunk not stored in Git: " + c}
            # The index and blob keep the chunk for commits/pushes. Release only this temporary
            # workfile; retaining it too doubles the plane's disk use. Source files stay intact.
            os.unlink(chunk_path)
            batch += chunk_size
            if batch >= PUSH_BATCH:
                code, _, _ = sh("git", "commit", "-qm", f"{name}: chunks up to {c}", cwd=repo_dir)
                if code != 0:
                    return {"plane": pl["name"], "status": "error", "error": f"commit chunks failed (exit {code})"}
                uploaded_head = publish(upload_ref, uploaded_head)
                batch = 0
        # Pin the payload commit (before adding its manifest, avoiding a self-referential SHA).
        if not release:
            code, _, _ = sh("git", "commit", "--allow-empty", "-qm", "Complete archive payload", cwd=repo_dir)
            if code:
                raise RuntimeError("payload commit failed")
            code, payload_head, _ = sh("git", "rev-parse", "HEAD", cwd=repo_dir)
            if code:
                raise RuntimeError("payload revision unavailable")
            transport["pins"][backend] = {"commit": payload_head}
        dm.validate(transport)
        with open(os.path.join(repo_dir, "DATA-MANIFEST.json"), "w") as f:
            json.dump(transport, f, sort_keys=True)
        if sh("git", "add", "DATA-MANIFEST.json", cwd=repo_dir)[0]:
            raise RuntimeError("manifest staging failed")
        completion = {"plane": pl["name"], "machine": machine, "fingerprint": fp,
                      "complete": True, "chunks": len(chunks), "encrypted_bytes": total}
        with open(os.path.join(repo_dir, "SNAPSHOT.json"), "w") as f:
            json.dump(completion, f)
        code, _, _ = sh("git", "add", "SNAPSHOT.json", cwd=repo_dir)
        if code != 0:
            return {"plane": pl["name"], "status": "error", "error": f"add completion failed (exit {code})"}
        code, _, _ = sh("git", "commit", "-qm", f"{name}: snapshot {manifest['at']}", cwd=repo_dir)
        if code != 0:
            return {"plane": pl["name"], "status": "error", "error": f"commit completion failed (exit {code})"}
        code, head, _ = sh("git", "rev-parse", "HEAD", cwd=repo_dir)
        if code != 0:
            raise RuntimeError(f"resolve completed snapshot failed (exit {code})")
        code, tree_names, err = sh("git", "ls-tree", "--name-only", head, cwd=repo_dir)
        if code != 0 or set(tree_names.splitlines()) != set(([] if release else chunks) + ["SUMMARY.md", "RESTORE.md", "SNAPSHOT.json", "DATA-MANIFEST.json"]):
            raise RuntimeError("completed snapshot chunk inventory differs from source archive")
        publish(upload_ref, uploaded_head, head=head)
        # Independent transport download: local Git objects are never verification input.
        with tempfile.TemporaryDirectory(prefix=".estate-verify-", dir=stage) as verify:
            used_location = dm.restore_manifest(transport, os.path.join(verify, "tree"), sh, machine, KEY)
        publish(snapshot_ref, head=head)
        # the whole local clone is a leftover once GitHub holds this exact commit (the next run re-inits it anyway);
        # its .git keeps every chunk, which on 24 Sep was 8.4 GB and helped fill the disk
        shutil.rmtree(repo_dir, ignore_errors=True)
        return {"plane": pl["name"], "status": "pushed", "fingerprint": fp, "repo": f"{owner}/{name}", "files": len(manifest["files"]),
                "ref": snapshot_ref, "commit": head, "remote_verified": True,
                "backend": pl["backend"], "manifest": transport, "restore_verified": True,
                "verified_location": used_location,
                "chunks": len(chunks), "encrypted_bytes": total, "regenerable_skipped": len(regen),
                "secs": round(time.time() - t0)}
    except (RuntimeError, subprocess.TimeoutExpired) as err:
        return {"plane": pl["name"], "status": "error", "run_id": run_id,
                "error": f"snapshot failed ({type(err).__name__}); local clone kept; diagnostic details withheld"}
    finally:
        if reservation is not None:
            release_reservation(reservation, retain_remote=upload_started)
        shutil.rmtree(stage, ignore_errors=True)



class StreamingBudget:
    """Measure this unique run's staging, guard growth and watch only owned processes."""
    def __init__(self, stage, aggregate_root=None):
        self.stage = Path(stage)
        # Concurrent lanes share one managed staging cap. Legacy serial runs remain
        # scoped to their unique subtree, preserving their previous accounting.
        self.roots = [Path(aggregate_root)] if aggregate_root else [self.stage]
        self.peak = 0
        self.error = None
        self.failure_reason = None
        self.processes = []
        self.stop = threading.Event()
        self.thread = None

    def usage(self):
        size = 0
        for root in self.roots:
            if not root.exists():
                continue
            for directory, dirs, names in os.walk(root):
                try:
                    directory_stat = os.lstat(directory)
                except FileNotFoundError:
                    # The foreground deletes each verified staging directory.
                    # A concurrent cleanup is not a capacity or I/O failure.
                    continue
                size += max(directory_stat.st_size, directory_stat.st_blocks * 512)
                for name in names:
                    try:
                        stat = os.lstat(os.path.join(directory, name))
                    except FileNotFoundError:
                        continue
                    size += max(stat.st_size, stat.st_blocks * 512)
        return size

    def check(self, additional=0):
        if self.error:
            raise RuntimeError("streaming resource guard stopped this run")
        size = self.usage()
        self.peak = max(self.peak, size)
        if size + additional > STREAM_CAP:
            raise RuntimeError("aggregate managed staging exceeds streaming run cap")
        if os.getloadavg()[0] >= STREAM_LOAD_MAX:
            raise RuntimeError("streaming load ceiling reached")
        if shutil.disk_usage(WS).free - additional < STREAM_FLOOR:
            raise RuntimeError("streaming run free-space floor reached")

    def register(self, process):
        self.processes.append(process)
        return process

    def watch(self):
        while not self.stop.wait(0.05):
            try:
                self.check()
            except (OSError, RuntimeError) as error:
                self.error = error
                if isinstance(error, RuntimeError):
                    reasons = {
                        "streaming load ceiling reached": "load ceiling",
                        "streaming run free-space floor reached": "free-space floor",
                        "aggregate managed staging exceeds streaming run cap": "aggregate staging cap",
                    }
                    self.failure_reason = reasons.get(str(error))
                for process in list(self.processes):
                    if process.poll() is None:
                        process.kill()
                return

    def start(self):
        self.check()
        self.thread = threading.Thread(target=self.watch, name="estate-staging-guard", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()

    def shell(self, *args, cwd=None, timeout=3600):
        self.check()
        process = self.register(subprocess.Popen(args, cwd=cwd, env=dm.git_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.check()
                try:
                    out, err = process.communicate(timeout=min(0.1, max(0.001, deadline - time.monotonic())))
                    self.check()
                    return process.returncode, out.strip(), err.strip()
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("streaming command timed out")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


class HashReader:
    def __init__(self, source, check):
        self.source, self.check = source, check
        self.sha, self.size = hashlib.sha256(), 0

    def read(self, size=-1):
        self.check()
        block = self.source.read(size)
        self.sha.update(block)
        self.size += len(block)
        return block


def archive_stream(pl, inc, stage, guard, sqlite_limit, source_root=None, source_root_identity=None,
                   source_identities=None):
    """Tar producer is pipe-backpressured; only one consistent SQLite copy may exist."""
    compressor = guard.register(subprocess.Popen(["zstd", "-q", "-T1", "-3", "-c"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
    if pl.get("encrypt", True):
        encryptor = guard.register(subprocess.Popen(["age", "-r", recipient()], stdin=compressor.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
        compressor.stdout.close()
        output = encryptor.stdout
    else:
        encryptor, output = None, compressor.stdout
    state = {"error": None, "files": 0, "source_inputs": {}, "current_source": None}
    def produce():
        entries, metadata_size = [], 0
        try:
            with tarfile.open(fileobj=compressor.stdin, mode="w|") as archive:
                for index, original in enumerate(inc):
                    state["current_source"] = original
                    guard.check()
                    original_relative = os.path.relpath(original, root_of(pl)).replace(os.sep, "/")
                    relative = original_relative
                    if relative == "MANIFEST.json" or relative.startswith("../") or os.path.isabs(relative):
                        raise ValueError("source archive path collides or escapes")
                    source = Path(original)
                    source_identity = {"file": (source_identities or {}).get(original_relative)}
                    if source_identity["file"] is None:
                        raise RuntimeError("source identity was not planned")
                    if pl.get("sqlite_backup") and source.suffix in (".db", ".sqlite"):
                        raise RuntimeError("secure SQLite snapshot path is unsupported")
                    try:
                        source_fd, opened_root_identity, opened_identity = safe_source.open_source(
                            source_root or root_of(pl), original_relative, source_identity["file"], source_root_identity)
                    except (OSError, ValueError, RuntimeError):
                        state["source_failure"] = True
                        raise
                    with os.fdopen(source_fd, "rb") as raw:
                        before = os.fstat(raw.fileno())
                        if safe_source.identity(before) != source_identity["file"] or opened_root_identity != source_root_identity:
                            state["source_failure"] = True
                            raise RuntimeError("source identity changed before read")
                        info = tarfile.TarInfo(relative)
                        info.size, info.mode, info.mtime = before.st_size, before.st_mode & 0o777, int(before.st_mtime)
                        reader = HashReader(raw, guard.check)
                        archive.addfile(info, reader)
                        after = os.fstat(raw.fileno())
                        if (reader.size != before.st_size or safe_source.identity(after) != source_identity["file"]):
                            state["source_failure"] = True
                            raise RuntimeError("source changed while streaming")
                        entries.append({"path": relative, "size": reader.size, "sha256": reader.sha.hexdigest()})
                        metadata_size += len(relative.encode()) + 180
                        if metadata_size > 16_000_000:
                            raise RuntimeError("internal stream manifest exceeds metadata quota")
                        state["source_inputs"][relative] = {"path": original_relative, "source_stat": source_identity}
                payload = json.dumps({"files": entries}, separators=(",", ":")).encode()
                if len(payload) > 16_000_000:
                    raise RuntimeError("internal stream manifest exceeds metadata quota")
                info = tarfile.TarInfo("MANIFEST.json")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
                state["files"] = len(entries)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            state["source_failure"] = (state.get("source_failure", False)
                                       or isinstance(error, (OSError, sqlite3.Error))
                                       or str(error).startswith(("source changed", "SQLite source changed")))
            state["error"] = RuntimeError("archive stream source failed")
        finally:
            try:
                compressor.stdin.close()
            except BrokenPipeError:
                pass
    thread = threading.Thread(target=produce, name="estate-tar-source", daemon=True)
    thread.start()
    return output, thread, state, [p for p in (compressor, encryptor) if p]


def _stream_source_boundary(pl, inc):
    """Require every explicit streaming source to be a real file under a declared root."""
    lexical_base = Path(root_of(pl)).absolute()
    base = lexical_base.resolve(strict=False)
    paths, excludes = pl.get("paths"), pl.get("exclude", [])
    if (not isinstance(paths, list) or not paths or not isinstance(excludes, list)
            or (pl.get("whole_home") and ("paths" not in pl or "exclude" not in pl))):
        return False

    def config_path(rel):
        if not isinstance(rel, str) or not rel or os.path.isabs(rel) or ".." in Path(rel).parts:
            raise ValueError("invalid configured source boundary")
        return (base / rel).resolve(strict=False)

    try:
        roots = [config_path(rel) for rel in paths]
        denied = [config_path(rel) for rel in excludes]
    except (OSError, RuntimeError, ValueError):
        return False

    halo_roots = [(Path(WS) / "SISO_Agency/partners/halo").resolve(strict=False),
                  Path("/srv/halo-backups").resolve(strict=False)]

    def within(path, root):
        try:
            return os.path.commonpath((str(path), str(root))) == str(root)
        except ValueError:
            return False

    for raw in inc:
        try:
            source = Path(raw).absolute()
            relative = source.relative_to(lexical_base)
            current = lexical_base
            if current.is_symlink():
                return False
            for component in relative.parts:
                current = current / component
                if current.is_symlink():
                    return False
            if not source.is_file():
                return False
            real = source.resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        if not any(within(real, root) for root in roots) or any(within(real, item) for item in denied):
            return False
        # HALO's client and operational datasets remain in HALO's separate custody,
        # even if a caller marks the bytes encrypted or the plane boundary as SISO.
        if any(within(real, protected) for protected in halo_roots):
            return False
    return True


def stream_git_part(path, remote, ref, guard):
    """One orphan commit and one blob per repository; never retain earlier objects."""
    directory = path.parent
    for args in [("git", "init", "-q", "-b", "part"),
                 ("git", "config", "core.compression", "0"), ("git", "config", "pack.window", "0"),
                 ("git", "config", "pack.depth", "0"), ("git", "remote", "add", "origin", remote),
                 ("git", "add", path.name), ("git", "commit", "-qm", "Immutable archive part")]:
        if guard.shell(*args, cwd=str(directory))[0]:
            raise RuntimeError("streamed part preparation failed")
    code, commit, _ = guard.shell("git", "rev-parse", "HEAD", cwd=str(directory))
    if code:
        raise RuntimeError("streamed part revision unavailable")
    code, refs, _ = guard.shell("git", "ls-remote", "origin", ref, cwd=str(directory))
    if code or refs:
        raise RuntimeError("streamed immutable part ref already exists or unavailable")
    # Intent is recorded before the first network write; lost responses remain charged.
    guard.remote_attempted = True
    if guard.shell("git", "push", "-q", "origin", commit + ":" + ref, cwd=str(directory), timeout=7200)[0]:
        raise RuntimeError("streamed part push failed")
    code, refs, _ = guard.shell("git", "ls-remote", "origin", ref, cwd=str(directory))
    if code or refs.split() != [commit, ref]:
        raise RuntimeError("streamed immutable part ref mismatch")
    return commit


def run_streamed(pl, machine, inc, run_id, fingerprint, profile, archive_factory=None, archive_upper_bytes=None,
                 on_verified_files=None, on_producer_state=None, max_streams=1):
    """Bounded transport extension: archive_factory returns encoded tar pipe + producer state.

    A custom producer must append the internal file SHA manifest and register all
    owned processes with guard. Its byte upper bound is reserved from inc sizes.
    Explicit inc lists permit encrypted registered paths without gather's pruning.
    """
    if profile != STREAM_PROFILE:
        raise ValueError("unknown streaming run profile")
    if type(max_streams) is not int or max_streams < 1 or max_streams > STREAM_MAX_LANES:
        raise ValueError("stream lane count must be from 1 through 3")
    if (pl.get("machine", "laptop") != machine or pl.get("upload_hold")
            or pl.get("access_boundary") == "mixed-unresolved" or str(pl.get("access_boundary", "")).lower().startswith("halo")):
        return {"plane": pl["name"], "status": "held", "held_reason": "streaming machine or access boundary hold"}
    inc = list(dict.fromkeys(inc))
    if not _stream_source_boundary(pl, inc):
        return {"plane": pl["name"], "status": "held", "held_reason": "streaming source escaped declared roots, entered an excluded or protected boundary, or used a linked path"}
    if pl.get("sqlite_backup") and any(Path(p).suffix in (".db", ".sqlite") for p in inc):
        return {"plane": pl["name"], "status": "held", "held_reason": "secure SQLite snapshot path is unsupported for streamed backup"}
    try:
        source_root = Path(root_of(pl)).resolve(strict=True)
        relatives = [os.path.relpath(p, root_of(pl)).replace(os.sep, "/") for p in inc]
        source_root_identity, source_identities = safe_source.snapshot(source_root, relatives)
    except (OSError, RuntimeError, ValueError):
        return {"plane": pl["name"], "status": "held", "held_reason": "streamed source identity could not be captured safely"}
    if not pl.get("encrypt", True) and not pl.get("pre_encrypted") and secrets_in(pl, inc):
        return {"plane": pl["name"], "status": "held", "held_reason": "plaintext streaming source failed secret scan"}
    if not inc:
        return {"plane": pl["name"], "status": "held", "held_reason": "empty streaming source has no coverage"}
    owner, repo, release = dm.github(pl["backend"], write=True)
    source_bytes = sum(identity["size"] for identity in source_identities.values())
    upper = (source_bytes + sum(len(p.encode()) + 4096 for p in inc) + 2_000_000) * 11 // 10 + 1
    if archive_upper_bytes is not None:
        if archive_factory is None or type(archive_upper_bytes) is not int or archive_upper_bytes <= 0:
            raise ValueError("custom stream requires a positive explicit archive upper bound")
        upper = archive_upper_bytes
    sqlite_size = 0
    chunk_size = min(CHUNK, (STREAM_CAP // max_streams - STREAM_METADATA - sqlite_size) // 5)
    peak = chunk_size * 5 + sqlite_size + STREAM_METADATA
    if chunk_size <= 0 or chunk_size >= dm.CHUNK_LIMIT or peak > STREAM_CAP:
        return {"plane": pl["name"], "status": "held", "held_reason": "one consistent SQLite snapshot plus transport exceeds staging cap"}
    with streaming_lock(max_streams) as lock_acquired:
        if not lock_acquired:
            return {"plane": pl["name"], "status": "held", "held_reason": "another streaming run owns the aggregate staging budget"}
        admitted, reason = admit_upload(pl, upper, local_peak_bytes=peak, machine=machine,
                                        stream_profile=profile, max_streams=max_streams)
        if not admitted:
            return {"plane": pl["name"], "status": "held", "held_reason": reason}
        stage = Path(WS) / "_data/estate-stream-stage" / run_id
        aggregate_root = stage.parent if max_streams > 1 else None
        guard, producer_thread, validator = StreamingBudget(stage, aggregate_root), None, None
        guard.remote_attempted = False
        try:
            guard.start()
            stage.mkdir(parents=True)
            if archive_factory:
                output, producer_thread, state, encoders = archive_factory(pl, inc, stage, guard, sqlite_size)
            else:
                output, producer_thread, state, encoders = archive_stream(
                    pl, inc, stage, guard, sqlite_size, source_root, source_root_identity, source_identities)
            if on_producer_state is not None:
                on_producer_state(state)
            validator = dm.StreamValidator(pl.get("encrypt", True), KEY, guard.check, guard.register)
            prefix = "data.tar.zst.age." if pl.get("encrypt", True) else "data.tar.zst."
            backend = pl["backend"]
            pin = {"commits": {}, "refs": {}}
            if release:
                release = release + "-" + run_id
                backend = f"github:{owner}/{repo}#{release}"
                bootstrap = stage / "release-start"
                bootstrap.mkdir()
                start_path = bootstrap / "UPLOAD.json"
                start_path.write_text('{"complete": false}')
                target = stream_git_part(start_path, f"https://github.com/{owner}/{repo}.git", f"refs/heads/uploads/{machine}/{run_id}", guard)
                rmtree_retry(bootstrap)
                guard.remote_attempted = True
                if guard.shell("gh", "release", "create", release, "--repo", f"{owner}/{repo}", "--target", target, "--title", "Immutable streamed snapshot", "--notes", "Incomplete until a verified snapshot manifest is published")[0]:
                    raise RuntimeError("streamed release creation failed")
                info = api_json(f"repos/{owner}/{repo}/releases/tags/{release}")
                pin = {"release_id": info["id"], "assets": {}}
            parts, whole, remote_whole, total = [], hashlib.sha256(), hashlib.sha256(), 0
            while True:
                guard.check(additional=chunk_size * 4 + STREAM_METADATA)
                index = len(parts)
                if index >= 26 ** 3:
                    raise RuntimeError("stream part inventory exceeds stable-name range")
                suffix = "".join(chr(97 + (index // divisor) % 26) for divisor in (676, 26, 1))
                part_dir = stage / "part"
                part_dir.mkdir()
                path = part_dir / (prefix + suffix)
                length, sha = 0, hashlib.sha256()
                with path.open("wb") as destination:
                    while length < chunk_size:
                        guard.check(additional=min(1024 * 1024, chunk_size - length))
                        block = output.read1(min(1024 * 1024, chunk_size - length))
                        if not block:
                            break
                        destination.write(block)
                        whole.update(block)
                        sha.update(block)
                        length += len(block)
                if not length:
                    rmtree_retry(part_dir)
                    break
                total += length
                if total > upper:
                    raise RuntimeError("stream archive exceeds remote reservation")
                part = {"name": path.name, "size": length, "sha256": sha.hexdigest()}
                if release:
                    guard.remote_attempted = True
                    if guard.shell("gh", "release", "upload", release, str(path), "--repo", f"{owner}/{repo}", timeout=7200)[0]:
                        raise RuntimeError("streamed release part upload failed")
                    assets = api_json(f"repos/{owner}/{repo}/releases/{pin['release_id']}/assets?per_page=100", paginate=True)
                    matches = [a for a in assets if a["name"] == path.name and a["size"] == length]
                    if len(matches) != 1:
                        raise RuntimeError("streamed release asset identifier unavailable")
                    pin["assets"][path.name] = matches[0]["id"]
                else:
                    ref = f"refs/heads/parts/{machine}/{run_id}/{suffix}"
                    commit = stream_git_part(path, f"https://github.com/{owner}/{repo}.git", ref, guard)
                    pin["commits"][path.name], pin["refs"][path.name] = commit, ref
                # Discard upload workfile AND its independent Git store before download.
                rmtree_retry(part_dir)
                download = stage / "download"
                download.mkdir()
                guard.check(additional=length * 3 + STREAM_METADATA)
                dm.verify_streamed_part(backend, part, pin, download, guard.shell, validator, remote_whole,
                                       machine, guard.check, guard.register)
                guard.check()
                rmtree_retry(download)
                parts.append(part)
            output.close()
            producer_thread.join(timeout=60)
            if producer_thread.is_alive() or state["error"] or any(process.wait(timeout=60) for process in encoders):
                raise RuntimeError("archive stream producer failed")
            restored = validator.finish()
            expected_files = state["files"] if archive_factory else len(inc)
            if remote_whole.hexdigest() != whole.hexdigest() or restored["files"] != expected_files or not expected_files:
                raise RuntimeError("remote streamed restore integrity differs")
            if on_verified_files:
                source_inputs = state.get("source_inputs")
                verified_files = restored.get("files_by_path")
                if (not isinstance(source_inputs, dict) or not isinstance(verified_files, dict)
                        or set(source_inputs) != set(verified_files)):
                    raise RuntimeError("verified source identity inventory differs")
                private_receipts = []
                for archive_path, verified in verified_files.items():
                    source = source_inputs[archive_path]
                    private_receipts.append({"path": source["path"], "size": verified["size"],
                                             "sha256": verified["sha256"], "source_stat": source["source_stat"]})
                on_verified_files(private_receipts)
            public_restored = {"files": restored["files"], "bytes": restored["bytes"]}
            transport = {"version": 1, "path": prefix[:-1], "sha256": whole.hexdigest(), "size": total,
                         "parts": parts, "locations": [backend], "pins": {backend: pin}, "encrypted": pl.get("encrypt", True),
                         "verification": "streamed-file-sha256"}
            dm.validate(transport)
            # Only the small completion manifest is published after full remote file verification.
            complete_dir = stage / "completion"
            complete_dir.mkdir()
            completion = {"plane": pl["name"], "machine": machine, "complete": True, "chunks": len(parts), "encrypted_bytes": total}
            (complete_dir / "DATA-MANIFEST.json").write_text(json.dumps(transport, sort_keys=True))
            (complete_dir / "SNAPSHOT.json").write_text(json.dumps(completion))
            snapshot_ref = f"refs/tags/snapshots/{machine}/{run_id}"
            for args in [("git", "init", "-q", "-b", "snapshot"), ("git", "add", "DATA-MANIFEST.json", "SNAPSHOT.json"),
                         ("git", "commit", "-qm", "Verified streamed snapshot"), ("git", "remote", "add", "origin", f"https://github.com/{owner}/{repo}.git")]:
                if guard.shell(*args, cwd=str(complete_dir))[0]:
                    raise RuntimeError("stream completion manifest failed")
            code, head, _ = guard.shell("git", "rev-parse", "HEAD", cwd=str(complete_dir))
            if code:
                raise RuntimeError("stream completion revision unavailable")
            code, refs, _ = guard.shell("git", "ls-remote", "origin", snapshot_ref, cwd=str(complete_dir))
            if code or refs:
                raise RuntimeError("stream completed snapshot ref already exists")
            guard.remote_attempted = True
            if guard.shell("git", "push", "-q", "origin", head + ":" + snapshot_ref, cwd=str(complete_dir), timeout=7200)[0]:
                raise RuntimeError("stream completion publication failed")
            code, refs, _ = guard.shell("git", "ls-remote", "origin", snapshot_ref, cwd=str(complete_dir))
            if code or refs.split() != [head, snapshot_ref]:
                raise RuntimeError("stream completion remote revision mismatch")
            guard.check()
            return {"plane": pl["name"], "status": "pushed", "repo": f"{owner}/{repo}", "backend": pl["backend"],
                    "manifest": transport, "commit": head, "ref": snapshot_ref, "fingerprint": fingerprint,
                    "files": restored["files"], "chunks": len(parts), "encrypted_bytes": total,
                    "remote_verified": True, "restore_verified": True, "stream_profile": profile,
                    "staging_peak_bytes": guard.peak, "stream_restore": public_restored}
        except Exception as error:
            if guard.error is not None:
                reason = guard.failure_reason or "resource guard"
                raise RuntimeError("streaming resource monitor failed: " + reason) from None
            if isinstance(error, RuntimeError) and str(error) == "streaming load ceiling reached":
                raise RuntimeError("streaming resource monitor failed: load ceiling") from None
            raise
        finally:
            guard.close()
            if validator:
                validator.close()
            if producer_thread:
                producer_thread.join(timeout=5)
            if stage.exists():
                rmtree_retry(stage)
            release_reservation(reason, retain_remote=guard.remote_attempted)

def secrets_in(pl, inc):
    """A plain plane is readable by anyone who can read the repo: gitleaks every path first (ADR 0008), on its own
    exit code. Returns [{file, rule}] for findings inside the files the plane would push (values never printed)."""
    base, keep, out = root_of(pl), set(inc), []
    for rel in pl["paths"]:
        path = os.path.join(base, rel)
        if not os.path.exists(path):
            continue
        fd, rep = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            r = subprocess.run(["gitleaks", "dir", path, "--redact", "--no-banner", "--max-target-megabytes", "0",
                                "-r", rep, "--log-level", "error"], capture_output=True, text=True, timeout=3600)
            if r.returncode not in (0, 1):
                return [{"file": rel, "rule": "gitleaks scan failed; diagnostic details withheld"}]
            with open(rep) as report:
                findings = json.load(report)
            if (not isinstance(findings, list) or (r.returncode == 1 and not findings)
                    or (r.returncode == 0 and findings)
                    or any(not isinstance(f, dict) or not isinstance(f.get("File"), str)
                           or not isinstance(f.get("RuleID"), str) for f in findings)):
                return [{"file": rel, "rule": "gitleaks report invalid"}]
            for f in findings:
                fp = f.get("File", "")
                fp = fp if os.path.isabs(fp) else os.path.join(path, fp) if os.path.isdir(path) else path
                if fp in keep:
                    out.append({"file": os.path.relpath(fp, base), "rule": f.get("RuleID", "")})
        except (OSError, ValueError, subprocess.SubprocessError):
            return [{"file": rel, "rule": "gitleaks scan or report unavailable; diagnostic details withheld"}]
        finally:
            if os.path.exists(rep):
                os.unlink(rep)
    return out


def shq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def cmd_restore(plane, dest, machine="laptop", ref=None):
    try:
        return _cmd_restore(plane, dest, machine, ref)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as err:
        sys.exit(f"restore failed ({type(err).__name__}); diagnostic details withheld")


def _cmd_restore(plane, dest, machine="laptop", ref=None):
    pl = next((p for p in planes() if p["name"] == plane), {})
    name = pl.get("repo", f"siso-data-{plane}")
    owner = pl.get("owner", OWNER)
    receipt_path = os.path.join(REPO, "machines", machine, "data-backup.json")
    last = json.load(open(receipt_path)).get("results", {}).get(plane, {}) if os.path.exists(receipt_path) else {}
    candidates = [last] + list(reversed(last.get("recovery_references", [])))
    attempted = False
    for candidate in candidates:
        if ref and ref != candidate.get("ref"):
            continue
        manifest = candidate.get("manifest")
        legacy = all(candidate.get(k) for k in ("ref", "repo", "commit"))
        if not manifest and not legacy:
            continue
        attempted = True
        try:
            if manifest:
                dm.restore_manifest(manifest, dest, sh, machine, KEY)
            else:
                _restore_git_snapshot(plane, dest, machine, candidate["ref"], candidate["repo"], candidate["commit"])
            return 0
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
            if ref:
                raise
            # Every restore is transactional; a failed candidate leaves destination absent.
            continue
    if attempted:
        raise RuntimeError("all preserved restore snapshots unavailable or corrupt")
    if pl.get("backend") and not ref:
        owner, name, _ = dm.github(pl["backend"])
    return _restore_git_snapshot(plane, dest, machine, ref, f"{owner}/{name}")


def _restore_git_snapshot(plane, dest, machine, ref, repository, expected_commit=None):
    owner, name = dm.github("github:" + repository)[:2]
    url = f"https://github.com/{owner}/{name}.git"
    prefix = f"refs/tags/snapshots/{machine}/"
    if ref and ref != "refs/heads/main" and not ref.startswith(prefix):
        raise RuntimeError("restore requires a completed snapshot tag for the selected machine or explicit refs/heads/main")
    code, advertised, _ = sh("git", "ls-remote", "--refs", url, ref or prefix + "*", timeout=120)
    if code != 0:
        raise RuntimeError(f"cannot discover restore snapshot (exit {code})")
    refs = {line.split()[1]: line.split()[0] for line in advertised.splitlines() if len(line.split()) == 2}
    if ref:
        if ref not in refs:
            raise RuntimeError("requested restore snapshot is absent")
    elif refs:
        ref = sorted(refs)[-1]
    else:
        raise RuntimeError("no completed snapshot tag; explicit legacy refs/heads/main required")
    expected = refs[ref]
    if expected_commit is not None and expected != expected_commit:
        raise RuntimeError("preserved snapshot revision differs from remote reference")
    tmp = tempfile.mkdtemp(prefix=".siso-ephemeral-estate-restore-")
    try:
        branch = ref.removeprefix("refs/tags/").removeprefix("refs/heads/")
        code, _, _ = sh("git", "clone", "-q", "--depth", "1", "--single-branch", "--branch", branch, url, tmp, timeout=7200)
        if code != 0:
            raise RuntimeError(f"restore clone failed (exit {code})")
        code, head, err = sh("git", "rev-parse", "HEAD", cwd=tmp)
        if code != 0 or head != expected:
            raise RuntimeError("restore snapshot changed between discovery and fetch")
        manifest_path = os.path.join(tmp, "DATA-MANIFEST.json")
        if os.path.exists(manifest_path):
            transport = json.load(open(manifest_path))
            dm.validate(transport)
            marker = json.load(open(os.path.join(tmp, "SNAPSHOT.json")))
            if (marker.get("complete") is not True or marker.get("plane") != plane
                    or marker.get("machine") != machine or marker.get("chunks") != len(transport["parts"])
                    or marker.get("encrypted_bytes") != transport["size"]):
                raise ValueError("manifest snapshot marker differs")
            dm.restore_manifest(transport, dest, sh, machine, KEY)
            return 0
        chunks = sorted(f for f in os.listdir(tmp) if f.startswith("data.tar.zst."))
        if ref.startswith(prefix):
            try:
                with open(os.path.join(tmp, "SNAPSHOT.json")) as f:
                    marker = json.load(f)
            except (OSError, ValueError):
                raise RuntimeError("completed snapshot marker is missing or invalid")
            if (marker.get("complete") is not True or marker.get("plane") != plane
                    or marker.get("machine") != machine or marker.get("chunks") != len(chunks)
                    or marker.get("encrypted_bytes") != sum(os.path.getsize(os.path.join(tmp, c)) for c in chunks)):
                raise RuntimeError("completed snapshot inventory is invalid")
        if not chunks:
            raise RuntimeError("restore snapshot has no archive chunks")
        # Legacy frozen references stay usable, with transactional safe extraction.
        archive = os.path.join(tmp, "legacy-archive")
        with open(archive, "wb") as output:
            for chunk in chunks:
                with open(os.path.join(tmp, chunk), "rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
        dm.extract_archive(archive, dest, chunks[0].startswith("data.tar.zst.age."), KEY)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run", "push", "restore", "size"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--ref", help="restore a completed snapshot tag, or explicit refs/heads/main for legacy data")
    ap.add_argument("--stream-profile", choices=[STREAM_PROFILE], help="explicit run-scoped one-part streaming admission; legacy path keeps its original floor")
    a = ap.parse_args()
    if a.cmd == "restore" and a.stream_profile:
        ap.error("--stream-profile is a backup-only bounded transport profile; restore uses the legacy restore path")
    if a.cmd == "size":
        return cmd_size()
    if a.cmd == "plan":
        return cmd_plan(a.machine)
    if a.cmd == "restore":
        return cmd_restore(a.args[0], a.args[1], a.machine, a.ref)
    names = [p["name"] for p in planes(a.machine) if not p.get("manual")] if a.args == ["all"] else a.args
    res_path = os.path.join(REPO, "machines", a.machine, "data-backup.json")
    os.makedirs(os.path.dirname(res_path), exist_ok=True)
    known = {p["name"] for p in planes()}
    if not names or set(names) - known:
        print(json.dumps({"status": "error", "error": "select registered plane names or all"}))
        return 1
    failed = False
    for pl in planes():
        if pl["name"] in names:
            r = run_plane(pl, a.machine, a.stream_profile) if a.stream_profile else run_plane(pl, a.machine)
            r["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            with open(res_path + ".retention-lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                prev = json.load(open(res_path)) if os.path.exists(res_path) else {"results": {}}
                old = prev["results"].get(pl["name"], {})
                recovery = list(old.get("recovery_references", []))
                if old.get("status") in ("pushed", "unchanged"):
                    reference = {k: old[k] for k in ("repo", "ref", "commit", "manifest", "backend", "restore_verified", "files", "fingerprint", "at") if k in old}
                    if reference and reference not in recovery:
                        recovery.append(reference)
                if recovery:
                    r["recovery_references"] = recovery
                prev["results"][pl["name"]] = r
                pending = res_path + ".pending-" + uuid.uuid4().hex
                try:
                    with open(pending, "w") as f:
                        json.dump(prev, f, indent=1)
                    os.replace(pending, res_path)
                finally:
                    if os.path.exists(pending):
                        os.unlink(pending)
            print(json.dumps(r))
            failed |= r["status"] not in ("pushed", "unchanged")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
