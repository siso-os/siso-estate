#!/usr/bin/env python3
"""Live inventory of every git repo on this machine (read-only).

Finds every git root under the machine's roots, then records what backup needs:
remotes, branch, commits no remote has, dirty and untracked counts, worktree or not.
Writes machines/<machine>/repos.json. Never changes a repo.

usage: inventory.py [--machine laptop] [--roots ~/SISO_Workspace ~ ...] [--jobs 12]
"""
import argparse, json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
from estate_state import record_path

PRUNE = {"node_modules", ".venv", "venv", "__pycache__", ".next", ".turbo", ".cache", "dist",
         "build", ".Trash", "Library", ".npm", ".cargo", ".rustup", "Pods", ".gradle", "target",
         ".pnpm-store", ".bun", ".yarn", "site-packages", ".mypy_cache", ".pytest_cache"}
# home-level folders that are not where repos live (apps, caches, harness state are zoned separately)
HOME_SKIP = {"Library", "Applications", "Movies", "Music", "Pictures", "Public", ".Trash",
             "SISO_Workspace"}


def git(path, *args, timeout=60):
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", path, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except subprocess.TimeoutExpired:
        return None


def find_roots(roots, maxdepth=14):
    home = os.path.expanduser("~")
    seen = []
    for root in roots:
        root = os.path.realpath(os.path.expanduser(root))
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            depth = dirpath[len(root):].count(os.sep)
            if ".git" in dirnames or ".git" in filenames:
                seen.append(dirpath)
            keep = []
            for d in dirnames:
                if d == ".git" or d in PRUNE:
                    continue
                if dirpath == home and d in HOME_SKIP:
                    continue
                if os.path.islink(os.path.join(dirpath, d)):
                    continue
                keep.append(d)
            dirnames[:] = keep if depth < maxdepth else []
    return sorted(set(seen))


GH = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$")


def describe(path, *, quick=False, previous=None, git_timeout=None, sizes=True):
    def read_git(*args, timeout=60):
        return git(path, *args, timeout=min(timeout, git_timeout) if git_timeout else timeout)
    dotgit = os.path.join(path, ".git")
    kind = "repo"
    gitdir = dotgit
    if os.path.isfile(dotgit):
        txt = open(dotgit, errors="replace").read().strip()
        gitdir = txt.split("gitdir:", 1)[-1].strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.normpath(os.path.join(path, gitdir))
        kind = "worktree" if "/worktrees/" in gitdir else "submodule"
    rec = {"path": path, "kind": kind}
    if not os.path.exists(gitdir):
        rec["broken"] = f"gitdir missing: {gitdir}"
        return rec
    if quick:
        # Event updates retain the hourly inventory's expensive counts and remotes.
        rec = dict(previous or {}, **rec)
        rec["branch"] = read_git("symbolic-ref", "--short", "-q", "HEAD") or "(detached)"
        rec["head"] = read_git("rev-parse", "HEAD")
        rec["watch_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return rec
    remotes = {}
    for line in (read_git("remote", "-v") or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "(fetch)":  # partial clones append "[blob:none]"
            remotes[parts[0]] = parts[1]
    rec["remotes"] = remotes
    gh = []
    for name, url in remotes.items():
        m = GH.search(url)
        if m:
            gh.append({"remote": name, "owner": m.group(1), "repo": m.group(2)})
    rec["github"] = gh
    rec["branch"] = read_git("symbolic-ref", "--short", "-q", "HEAD") or "(detached)"
    rec["head"] = read_git("rev-parse", "HEAD")
    rec["last_commit"] = read_git("log", "-1", "--format=%cI")
    if kind == "repo":
        # commits on local branches that no remote-tracking ref has (the ones only this disk holds)
        n = read_git("rev-list", "--count", "--branches", "--not", "--remotes", timeout=120)
        rec["unpushed_commits"] = int(n) if n and n.isdigit() else None
        br = read_git("for-each-ref", "--format=%(refname:short)\t%(upstream:short)", "refs/heads")
        rec["branches"] = len(br.splitlines()) if br else 0
    else:
        n = read_git("rev-list", "--count", "HEAD", "--not", "--remotes", timeout=120)
        rec["unpushed_commits"] = int(n) if n and n.isdigit() else None
    st = read_git("status", "--porcelain=v1", "-uno", timeout=180)
    rec["dirty"] = len(st.splitlines()) if st else 0 if st is not None else None
    un = read_git("ls-files", "-o", "--exclude-standard", "--directory", "--no-empty-directory", timeout=180)
    rec["untracked"] = len(un.splitlines()) if un else 0 if un is not None else None
    if kind == "repo" and sizes:
        try:
            out = subprocess.run(["du", "-sk", dotgit], capture_output=True, text=True, timeout=120).stdout
            rec["git_kb"] = int(out.split()[0])
        except Exception:
            rec["git_kb"] = None
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--roots", nargs="*", default=["~/SISO_Workspace", "~"])
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--git-timeout", type=float, default=None)
    ap.add_argument("--no-sizes", action="store_true")
    ap.add_argument("--known", action="store_true", help="refresh recently discovered Git roots without walking data payloads again")
    a = ap.parse_args()
    t0 = time.time()
    previous = record_path(REPO, "machines", a.machine, "repos.json")
    if a.known and os.path.isfile(previous) and time.time() - os.path.getmtime(previous) < 24 * 3600:
        with open(previous) as stream:
            roots = sorted({r["path"] for r in json.load(stream)["repos"]
                            if os.path.exists(os.path.join(r["path"], ".git"))})
    else:
        roots = find_roots(a.roots)
    with ThreadPoolExecutor(a.jobs) as ex:
        from functools import partial
        recs = list(ex.map(partial(describe, git_timeout=a.git_timeout, sizes=not a.no_sizes), roots))
    out_dir = record_path(REPO, "machines", a.machine)
    os.makedirs(out_dir, exist_ok=True)
    doc = {"machine": a.machine, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "roots": a.roots, "count": len(recs), "repos": recs}
    with open(os.path.join(out_dir, "repos.json"), "w") as f:
        json.dump(doc, f, indent=1)
    print(f"{len(recs)} git roots in {time.time()-t0:.0f}s -> {out_dir}/repos.json", file=sys.stderr)


if __name__ == "__main__":
    main()
