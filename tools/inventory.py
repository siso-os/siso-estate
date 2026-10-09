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
PRUNE = {"node_modules", ".venv", "venv", "__pycache__", ".next", ".turbo", ".cache", "dist",
         "build", ".Trash", "Library", ".npm", ".cargo", ".rustup", "Pods", ".gradle", "target",
         ".pnpm-store", ".bun", ".yarn", "site-packages", ".mypy_cache", ".pytest_cache"}
# home-level folders that are not where repos live (apps, caches, harness state are zoned separately)
HOME_SKIP = {"Library", "Applications", "Movies", "Music", "Pictures", "Public", ".Trash",
             "SISO_Workspace"}


def git(path, *args, timeout=60):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=timeout)
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


def describe(path):
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
    remotes = {}
    for line in (git(path, "remote", "-v") or "").splitlines():
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
    rec["branch"] = git(path, "symbolic-ref", "--short", "-q", "HEAD") or "(detached)"
    rec["head"] = git(path, "rev-parse", "HEAD")
    rec["last_commit"] = git(path, "log", "-1", "--format=%cI")
    if kind == "repo":
        # commits on local branches that no remote-tracking ref has (the ones only this disk holds)
        n = git(path, "rev-list", "--count", "--branches", "--not", "--remotes", timeout=120)
        rec["unpushed_commits"] = int(n) if n and n.isdigit() else None
        br = git(path, "for-each-ref", "--format=%(refname:short)\t%(upstream:short)", "refs/heads")
        rec["branches"] = len(br.splitlines()) if br else 0
    else:
        n = git(path, "rev-list", "--count", "HEAD", "--not", "--remotes", timeout=120)
        rec["unpushed_commits"] = int(n) if n and n.isdigit() else None
    st = git(path, "status", "--porcelain=v1", "-uno", timeout=180)
    rec["dirty"] = len(st.splitlines()) if st else 0 if st is not None else None
    un = git(path, "ls-files", "-o", "--exclude-standard", "--directory", "--no-empty-directory", timeout=180)
    rec["untracked"] = len(un.splitlines()) if un else 0 if un is not None else None
    if kind == "repo":
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
    a = ap.parse_args()
    t0 = time.time()
    roots = find_roots(a.roots)
    with ThreadPoolExecutor(a.jobs) as ex:
        recs = list(ex.map(describe, roots))
    out_dir = os.path.join(REPO, "machines", a.machine)
    os.makedirs(out_dir, exist_ok=True)
    doc = {"machine": a.machine, "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "roots": a.roots, "count": len(recs), "repos": recs}
    with open(os.path.join(out_dir, "repos.json"), "w") as f:
        json.dump(doc, f, indent=1)
    print(f"{len(recs)} git roots in {time.time()-t0:.0f}s -> {out_dir}/repos.json", file=sys.stderr)


if __name__ == "__main__":
    main()
