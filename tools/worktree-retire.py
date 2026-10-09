#!/usr/bin/env python3
"""Retire old worktrees that are clean, pushed and idle (role estate may-list), keeping any ignored evidence.

  worktree-retire.py [--days 3]          the plan: each worktree, retire or why not
  worktree-retire.py --only REPO/LANE ... [--run]
                                         an owner named these: no age rule; HEAD must be in main (or on a remote branch),
                                         no tracked change; untracked files are archived with the ignored ones
  worktree-retire.py [--days 3] --run    retire: ignored files that are not rebuildable (screenshots, runs, notes) are
                                         moved to _archive/<date>-worktree-ignored/<repo>/<lane>/ first, then
                                         `git worktree remove` (never --force); a line per worktree in machines/<m>/removed.jsonl

A worktree goes only when: no commit for DAYS days; no tracked change and no untracked file; HEAD on a remote branch;
no process has its cwd or a file open inside; not HALO, Oracle, Fahmy, Life or WhatsApp work, not pair-ab, ship-runner or
release-1. Shaan, 8 Oct: "how many of them are old work trees ... make some massive progress".
"""
import argparse, datetime as dt, json, os, re, shutil, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.expanduser("~/SISO_Workspace")
WT = os.path.join(WS, "_data", "worktrees")
NEVER = re.compile(r"halo|oracle|bykonz|fahmy|life|whatsapp|cam-|kellman", re.I)
NEVER_PRIVATE = re.compile(r"bykonz|fahmy|life|whatsapp", re.I)  # --halo: HALO lanes may go when clean, pushed, idle (Shaan, 8 Oct 22:12)
NEVER_NAMED = re.compile(r"halo|oracle|bykonz|fahmy|cam-|kellman", re.I)  # named lanes: a feature called life/whatsapp may go
KEEP_NAMES = {"pair-ab", "ship-runner", "release-1", "queue-base", "browser-20261007"}
REBUILD = re.compile(r"(^|/)(node_modules|dist|build|target|\.next|\.turbo|\.vite|\.cache|coverage|\.wrangler|\.astro|"
                     r"__pycache__|\.pytest_cache|\.venv|venv|out|\.oracle-builds|\.parcel-cache|\.svelte-kit|storybook-static)(/|$)|"
                     r"\.tsbuildinfo$|\.DS_Store$|\.pyc$")
HOST = socket.gethostname().lower()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("laptop" if HOST.startswith("shaans-macbook") else "mini" if "mini" in HOST else HOST.split(".")[0])
RECEIPTS = os.environ.get("ESTATE_REMOVED") or os.path.join(REPO, "machines", MACHINE, "removed.jsonl")  # the mini's guard runs from a clean checkout


def git(p, *a):
    return subprocess.run(["git", "-C", p, *a], capture_output=True, text=True)


def held():
    out = subprocess.run(["lsof", "-u", str(os.getuid()), "-Fn", "-w"], capture_output=True, text=True).stdout
    return [l[1:] for l in out.splitlines() if l.startswith("n" + WT)]


def landed(p):
    """HEAD is in main (local or origin) or on some remote branch: nothing to land."""
    for base in ("main", "origin/main"):
        if git(p, "merge-base", "--is-ancestor", "HEAD", base).returncode == 0:
            return True
    cherry = git(p, "cherry", "origin/main", "HEAD")  # landed as a cherry-pick or rebase: every commit patch-equivalent
    if cherry.returncode == 0 and not any(l.startswith("+") for l in cherry.stdout.splitlines()):
        return True
    return bool(git(p, "branch", "-r", "--contains", "HEAD").stdout.strip())


def touched(p):
    """When anyone last committed, checked out or made this worktree: its gitdir's HEAD, HEAD log, and the .git file."""
    g = os.path.join(p, ".git")
    ts = [os.lstat(g).st_mtime]
    if os.path.isfile(g):
        gd = open(g).read().strip().split(": ", 1)[-1]
        ts += [os.lstat(os.path.join(gd, f)).st_mtime for f in ("HEAD", "logs/HEAD") if os.path.exists(os.path.join(gd, f))]
    return max(ts)


def verdict(p, repo, lane, days, busy, named=False, halo=False, idle_hours=None):
    if (NEVER_PRIVATE if halo else NEVER_NAMED if named else NEVER).search(repo + "/" + lane) or lane in KEEP_NAMES:
        return "protected (HALO / private / standing)"
    if idle_hours is not None and not named:  # idle by the worktree's own activity, not by how old its commits are
        if time.time() - touched(p) < idle_hours * 3600:
            return f"committed, checked out or made here in the last {idle_hours:g} h"
    elif not named:
        last = git(p, "log", "-1", "--format=%ct").stdout.strip()
        if not last or time.time() - int(last) < days * 86400:
            return f"commit in the last {days} days"
        if time.time() - os.lstat(os.path.join(p, ".git")).st_mtime < days * 86400:  # made today from an old commit
            return f"worktree made in the last {days} days"
    if git(p, "status", "--porcelain", "--untracked-files=" + ("no" if named else "normal")).stdout.strip():
        return "uncommitted or untracked work"
    if not landed(p):
        return "HEAD not in main nor on any remote branch"
    if any(b == p or b.startswith(p + "/") for b in busy):
        return "a process is inside"
    return None


def ignored_keep(p):
    """Untracked and ignored files that do not rebuild themselves: they go to the archive before the worktree goes."""
    out = git(p, "status", "--porcelain", "--ignored", "-z").stdout.split("\0")
    paths = sorted(e[3:] for e in out if e[:3] in ("!! ", "?? ") and not REBUILD.search(e[3:].rstrip("/")))
    return [q for i, q in enumerate(paths) if not any(d.endswith("/") and q.startswith(d) for d in paths[:i])]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--idle-hours", type=float, help="instead of --days: nobody committed, checked out or made it here for H hours")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--halo", action="store_true", help="HALO/Oracle lanes too (still clean, pushed, idle, not today's)")
    ap.add_argument("--only", nargs="+", metavar="REPO/LANE", help="just these worktrees, named by their owner")
    a = ap.parse_args()
    busy = held()
    stamp = time.strftime("%Y-%m-%d")
    free0, n, why_not = shutil.disk_usage(WS).free, 0, {}
    pairs = [tuple(o.split("/", 1)) for o in a.only] if a.only else \
        [(r, l) for r in sorted(os.listdir(WT)) for l in sorted(os.listdir(os.path.join(WT, r)))]
    for repo, lane in pairs:
            p = os.path.join(WT, repo, lane)
            if not os.path.exists(os.path.join(p, ".git")):
                continue
            v = verdict(p, repo, lane, a.days, busy, named=bool(a.only), halo=a.halo, idle_hours=a.idle_hours)
            if v:
                why_not[v] = why_not.get(v, 0) + 1
                if a.only:
                    print(f"keep   {repo}/{lane}: {v}")
                continue
            keep = ignored_keep(p)
            print(f"retire {repo}/{lane}" + (f"  (keeping {len(keep)} ignored: {', '.join(keep[:3])})" if keep else ""))
            if not a.run:
                continue
            for rel in keep:
                src, dst = os.path.join(p, rel.rstrip("/")), os.path.join(WS, "_archive", f"{stamp}-worktree-ignored", repo, lane, rel.rstrip("/"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
            for e in git(p, "status", "--porcelain", "-z").stdout.split("\0"):  # untracked caches block a plain remove
                if e.startswith("?? ") and REBUILD.search(e[3:].rstrip("/")):
                    q = os.path.join(p, e[3:].rstrip("/"))
                    shutil.rmtree(q) if os.path.isdir(q) and not os.path.islink(q) else os.remove(q)
            head = git(p, "rev-parse", "HEAD").stdout.strip()
            branch = git(p, "branch", "--show-current").stdout.strip()
            main_repo = os.path.dirname(git(p, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
            r = git(main_repo, "worktree", "remove", p)
            if r.returncode:
                print(f"  FAILED: {r.stderr.strip()[:200]}")
                continue
            n += 1
            with open(RECEIPTS, "a") as f:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": "git worktree remove",
                                    "path": os.path.relpath(p, WS), "branch": branch, "head": head,
                                    "why": ("named by its owner; " if a.only else "") + "nothing to land (HEAD in main or on a remote branch), no tracked change, idle"
                                    if a.only else f"clean, HEAD on a remote branch, idle, no commit for {a.days}+ days",
                                    "kept_ignored": len(keep), "by": "ESTATE tools/worktree-retire.py"}) + "\n")
    print("kept:", ", ".join(f"{k} {v}" for k, v in sorted(why_not.items(), key=lambda x: -x[1])))
    if a.run:
        print(f"retired {n}; free {(shutil.disk_usage(WS).free - free0) / 2**30:+.2f} GiB")


if __name__ == "__main__":
    sys.exit(main())
