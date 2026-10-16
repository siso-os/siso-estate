#!/usr/bin/env python3
"""Keep the laptop to its working set: retire cold repos to GitHub while free space is under the target.

  repo-offload.py                      the plan: which cold repos would go, biggest first, and why others stay
  repo-offload.py --run                retire them (tools/houses.py retire --run) until free space reaches the target

Shaan, 16 Oct: "everything should be pushed to GitHub ... chunks that we just don't need right now ... we can off-board
them if everything's back-up-able from GitHub ... if we always have like 100 gig free". A repo is cold when nobody
committed in it and no file in it (outside .git and build folders) changed for --idle-hours. houses.py retire does the
rest with its own refusals: every branch, stash and edit pushed and verified first, secret-flagged work held, nothing
removed while a process uses it or a worktree lives elsewhere, and the repo keeps its place on the map
(`estate restore --only <path> --run` brings it back). HALO (ADR 0007), private data and parents of nested repos stay.
Laptop only; the list comes from machines/laptop/repos.json (the census).
"""
import argparse, datetime as D, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.expanduser("~/SISO_Workspace")
SKIP = re.compile(r"(?i)halo|oracle|/personal/(private|data|legal)(/|$)|/_archive/|/\.credentials/|/\.lake/|/SISO_Agents/siso-estate(/|$)")
STATE = os.path.expanduser("~/.local/state/estate/repo-offload.json")  # path -> when houses last held it
BUILD = {".git", "node_modules", ".next", "dist", "build", "target", ".turbo", ".venv", "venv", ".cache", ".svelte-kit"}


def free_gib():
    s = os.statvfs(os.path.expanduser("~"))
    return s.f_bavail * s.f_frsize / 2**30


def touched_since(path, cutoff):
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in BUILD]
        for f in files:
            try:
                if os.lstat(os.path.join(root, f)).st_mtime > cutoff:
                    return True
            except OSError:
                pass
    return False


def du_kib(path):
    out = subprocess.run(["du", "-sk", "-x", path], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


def load_held():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def cold_repos(idle_hours):
    repos = json.load(open(os.path.join(REPO, "machines", "laptop", "repos.json")))["repos"]
    live = [r["path"] for r in repos if os.path.exists(os.path.join(r["path"], ".git"))]
    now = D.datetime.now(D.timezone.utc)
    cutoff = time.time() - idle_hours * 3600
    for r in repos:
        p = r["path"]
        if not r.get("github") or r.get("kind") != "repo" or not p.startswith(WS + "/") or SKIP.search(p + "/") or p not in live:
            continue
        if not r.get("last_commit") or (now - D.datetime.fromisoformat(r["last_commit"])).total_seconds() < idle_hours * 3600:
            continue
        if any(q.startswith(p + "/") for q in live):
            continue  # a parent goes only after its nested repos have
        if time.time() - HELD.get(p, 0) < 7 * 86400:
            continue  # held last time (secret flag, missing remote): retried weekly, not hourly
        if touched_since(p, cutoff):
            continue
        yield p


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--idle-hours", type=float, default=168)
    ap.add_argument("--target-gib", type=float, default=100)
    ap.add_argument("--max", type=int, default=5, help="repos per run (the hourly guard gives a run 30 min)")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    host = os.environ.get("ESTATE_MACHINE") or os.uname().nodename.lower()
    if not (host == "laptop" or host.startswith("shaans-macbook")):
        print("repo-offload: laptop only")
        return 0
    global HELD
    HELD = load_held()
    free = free_gib()
    if free >= a.target_gib:
        print(f"repo-offload: {free:.1f} GiB free, target {a.target_gib:.0f}: nothing to do")
        return 0
    cands = sorted(((du_kib(p), p) for p in cold_repos(a.idle_hours)), reverse=True)[: a.max]
    print(f"repo-offload: {free:.1f} GiB free, target {a.target_gib:.0f}; {len(cands)} cold repos")
    why = "repo-offload: idle %dh, laptop under %d GiB free (Shaan 16 Oct: GitHub holds it, the laptop keeps the working set)" % (a.idle_hours, a.target_gib)
    for kib, p in cands:
        if not a.run:
            print(f"  would retire {kib / 1048576:6.2f} GiB  {os.path.relpath(p, WS)}")
            continue
        if free_gib() >= a.target_gib:
            break
        out = subprocess.run([sys.executable, os.path.join(HERE, "houses.py"), "retire", p, "--run", "--why", why],
                             capture_output=True, text=True, timeout=3600)
        last = [l for l in (out.stdout + out.stderr).splitlines() if l.strip()][-1:] or ["(no output)"]
        print(f"  {kib / 1048576:6.2f} GiB  {os.path.relpath(p, WS)}: {last[0].strip()[:160]}")
        if os.path.exists(os.path.join(p, ".git")):
            HELD[p] = time.time()
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            json.dump(HELD, open(STATE, "w"), indent=1)
    print(f"repo-offload: free now {free_gib():.1f} GiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
