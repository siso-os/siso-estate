#!/usr/bin/env python3
"""Vault worktrees git can no longer read (their parent clone is gone), then free their space.

  worktree-orphans.py                      the plan: each orphaned worktree under _data/worktrees, its size, busy or not
  worktree-orphans.py --run [--only R/L] [--idle-hours H]   for each one no process holds: commit every file (tracked, untracked, and
                                           ignored files that do not rebuild) into one bare repo in the vault, verify
                                           the folder against its commit by content, and only then remove the folder;
                                           a receipt line per folder in machines/<m>/removed.jsonl (or $ESTATE_REMOVED)

An orphan is a folder whose .git file points at a gitdir that no longer exists, so `git status` fails and every other
tool sees it as unreadable. 14 Oct: the mini had 14 (24 GiB) after oracle/stream-b was removed on 8 Oct. Restore:
  git --git-dir=<vault>/orphans.git --work-tree=<empty dir> checkout refs/orphans/<repo>/<lane> -- .
Python 3.9 (the mini's /usr/bin/python3).
"""
import argparse, json, os, re, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.expanduser("~/SISO_Workspace")
WT = os.path.join(WS, "_data", "worktrees")
HOST = socket.gethostname().lower()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("laptop" if HOST.startswith("shaans-macbook") else "mini" if "mini" in HOST else HOST.split(".")[0])
RECEIPTS = os.environ.get("ESTATE_REMOVED") or os.path.join(REPO, "machines", MACHINE, "removed.jsonl")
REBUILD = re.compile(r"(^|/)(node_modules|dist|build|target|\.next|\.turbo|\.vite|\.cache|coverage|\.wrangler|\.astro|"
                     r"__pycache__|\.pytest_cache|\.venv|venv|out|\.oracle-builds|\.parcel-cache|\.svelte-kit|storybook-static)(/|$)|"
                     r"\.tsbuildinfo$|\.DS_Store$|\.pyc$")


def git(store, work, *a, index=None, inp=None):
    env = dict(os.environ, **({"GIT_INDEX_FILE": index} if index else {}))
    return subprocess.run(["git", "--git-dir=" + store, *(["--work-tree=" + work] if work else []), *a],
                          capture_output=True, text=True, env=env, input=inp)


def orphans():
    for repo in sorted(os.listdir(WT)) if os.path.isdir(WT) else []:
        for lane in sorted(os.listdir(os.path.join(WT, repo))) if os.path.isdir(os.path.join(WT, repo)) else []:
            p = os.path.join(WT, repo, lane)
            g = os.path.join(p, ".git")
            if os.path.isfile(g):
                gd = open(g).read().strip().split(": ", 1)[-1]
                if not os.path.exists(gd):
                    yield repo, lane, p, gd


def held():
    out = subprocess.run(["lsof", "-u", str(os.getuid()), "-Fn", "-w"], capture_output=True, text=True).stdout
    return [l[1:] for l in out.splitlines() if l.startswith("n" + WT)]


def du_kib(p):
    out = subprocess.run(["du", "-sk", p], capture_output=True, text=True).stdout
    return int(out.split()[0]) if out else 0


def others(store, work, index):
    """Untracked-by-the-index files outside rebuildable output (after an add -A this is the ignored set)."""
    out = git(store, work, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", index=index).stdout
    return [f for f in out.split("\0") if f and not REBUILD.search(f)]


def vault_one(store, repo, lane, p, gd):
    tmp = tempfile.mkdtemp(prefix="orph-")
    try:
        idx = os.path.join(tmp, "index")
        if git(store, p, "add", "-A", ".", index=idx).returncode:
            return None, "git add failed"
        keep = others(store, p, idx)
        if keep and git(store, p, "add", "-f", "--pathspec-from-file=-", "--pathspec-file-nul", index=idx,
                        inp="\0".join(keep)).returncode:
            return None, "git add of ignored keepers failed"
        tree = git(store, None, "write-tree", index=idx).stdout.strip()
        msg = "orphaned worktree %s/%s (gitdir %s gone); vaulted %s by ESTATE" % (repo, lane, gd, time.strftime("%Y-%m-%d"))
        c = git(store, None, "commit-tree", tree, inp=msg).stdout.strip()
        if not c or git(store, None, "update-ref", "refs/orphans/%s/%s" % (repo, lane), c).returncode:
            return None, "commit failed"
        # verify by content: an index read from the commit matches the folder; nothing else but rebuildable output
        v = os.path.join(tmp, "verify")
        git(store, None, "read-tree", c, index=v)
        git(store, p, "update-index", "-q", "--refresh", index=v)
        differ = [f for f in git(store, p, "diff-files", "--name-only", index=v).stdout.splitlines() if f]
        extra = [f for f in git(store, p, "ls-files", "--others", index=v).stdout.splitlines() if f and not REBUILD.search(f)]
        if differ or extra:
            return None, "verify failed: %d differ, %d extra (e.g. %s)" % (len(differ), len(extra), (differ + extra)[0])
        return c, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--only", nargs="+", metavar="REPO/LANE")
    ap.add_argument("--idle-hours", type=float, default=0, help="only folders nobody touched for H hours (the hourly law uses 24)")
    a = ap.parse_args()
    found = [o for o in orphans() if not a.only or "%s/%s" % (o[0], o[1]) in a.only]
    busy = held()
    if not found:
        print("no orphaned worktrees")
        return 0
    stamp = time.strftime("%Y-%m-%d")
    vault = os.path.join(WS, "_archive", "%s-orphaned-worktrees-%s" % (stamp, MACHINE))
    store = os.path.join(vault, "orphans.git")
    free0, done = shutil.disk_usage(WS).free, []
    for repo, lane, p, gd in found:
        kib = du_kib(p)
        if a.idle_hours and time.time() - max(os.lstat(p).st_mtime, os.lstat(os.path.join(p, ".git")).st_mtime) < a.idle_hours * 3600:
            print("keep   %s/%s  %.2f GiB: touched in the last %g h" % (repo, lane, kib / 1048576, a.idle_hours))
            continue
        if any(b == p or b.startswith(p + "/") for b in busy):
            print("keep   %s/%s  %.2f GiB: a process is inside" % (repo, lane, kib / 1048576))
            continue
        print("%s %s/%s  %.2f GiB  (gitdir gone: %s)" % ("vault " if a.run else "would ", repo, lane, kib / 1048576, gd))
        if not a.run:
            continue
        if not os.path.isdir(store):
            os.makedirs(vault, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", store], check=True)
        c, err = vault_one(store, repo, lane, p, gd)
        if err:
            print("  KEPT: " + err)
            continue
        shutil.rmtree(p)
        done.append((repo, lane, c, kib))
        with open(RECEIPTS, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": "orphaned worktree vaulted as a git commit",
                                "path": os.path.relpath(p, WS), "bytes": kib * 1024,
                                "why": "git cannot read it: its gitdir %s is gone" % gd,
                                "proof": "content-identical commit %s at refs/orphans/%s/%s in %s; only rebuildable output not kept"
                                         % (c, repo, lane, os.path.relpath(store, WS)),
                                "by": "ESTATE tools/worktree-orphans.py"}) + "\n")
    if done:
        new = not os.path.exists(os.path.join(vault, "MANIFEST.md"))
        with open(os.path.join(vault, "MANIFEST.md"), "a") as f:
            if new:
                f.write("# Orphaned worktrees (%s), vaulted by ESTATE\n\n**What:** worktrees whose parent clone was gone, so git "
                        "could not read them. Each folder's files (tracked, untracked, and ignored files that do not rebuild) are "
                        "one commit in `orphans.git` under `refs/orphans/<repo>/<lane>`; rebuildable output was left out.\n"
                        "**Proof:** each folder matched its commit by content before removal (read-tree, update-index --refresh, "
                        "diff-files: 0 differ, 0 extra).\n**Restore:** `git --git-dir=orphans.git --work-tree=<empty dir> "
                        "checkout refs/orphans/<repo>/<lane> -- .`\n\n" % MACHINE)
            for repo, lane, c, kib in done:
                f.write("- %s/%s  %s  %.2f GiB  (tools/worktree-orphans.py)\n" % (repo, lane, c, kib / 1048576))
        subprocess.run(["git", "--git-dir=" + store, "gc", "-q", "--prune=now"])
        print("vaulted %d into %s; free %+.2f GiB" % (len(done), os.path.relpath(vault, WS), (shutil.disk_usage(WS).free - free0) / 2**30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
