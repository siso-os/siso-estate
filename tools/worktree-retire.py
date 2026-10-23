#!/usr/bin/env python3
"""Retire old worktrees that are clean, pushed and idle (role estate may-list), keeping any ignored evidence.

  worktree-retire.py [--days 3]          the plan: each worktree, retire or why not
  worktree-retire.py --only REPO/LANE ... [--run]
                                         an owner named these: no age rule; HEAD must be in main (or on a remote branch),
                                         no tracked change; untracked files are archived with the ignored ones
  worktree-retire.py --pin-local ...     also clean, idle worktrees whose commits are only local: HEAD is pinned at
                                         refs/estate-kept/<repo>/<lane> in the shared repo first, so nothing becomes
                                         unreachable and nothing is pushed (Shaan, 14 Oct: "clean up 20-30 gigs on each")
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
sys.path.insert(0, os.path.join(REPO, "tools"))
from estate_state import record_path

WS = os.path.expanduser("~/SISO_Workspace")
WT = os.path.join(WS, "_data", "worktrees")
NEVER = re.compile(r"halo|oracle|bykonz|fahmy|life|whatsapp|cam-|kellman", re.I)
NEVER_PRIVATE = re.compile(r"bykonz|fahmy|life|whatsapp", re.I)  # --halo: HALO lanes may go when clean, pushed, idle (Shaan, 8 Oct 22:12)
NEVER_NAMED = re.compile(r"halo|oracle|bykonz|fahmy|cam-|kellman", re.I)  # named lanes: a feature called life/whatsapp may go
KEEP_NAMES = {"main", "live", "mini-runtime-20261018", "pair-ab", "ship-runner", "release-1", "queue-base", "browser-20261007"}
REBUILD = re.compile(r"(^|/)(node_modules|dist|build|target|\.next|\.turbo|\.vite|\.cache|coverage|\.wrangler|\.astro|"
                     r"__pycache__|\.pytest_cache|\.venv|venv|out|\.oracle-builds|\.parcel-cache|\.svelte-kit|storybook-static)(/|$)|"
                     r"\.tsbuildinfo$|\.DS_Store$|\.pyc$")
HOST = socket.gethostname().lower()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("laptop" if HOST.startswith("shaans-macbook") else "mini" if "mini" in HOST else HOST.split(".")[0])
RECEIPTS = os.environ.get("ESTATE_REMOVED") or record_path(REPO, "machines", MACHINE, "removed.jsonl")  # the mini's guard runs from a clean checkout


def git(p, *a):
    return subprocess.run(["git", "-C", p, *a], capture_output=True, text=True)


def held():
    result = subprocess.run(["lsof", "-u", str(os.getuid()), "-Fn", "-w"], capture_output=True, text=True)
    if result.returncode or not result.stdout:
        raise RuntimeError("cannot see open files; retirement held")
    out = result.stdout
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
        with open(g) as f:
            gd = os.path.normpath(os.path.join(p, f.read().strip().split(": ", 1)[-1]))
        ts += [os.lstat(os.path.join(gd, f)).st_mtime for f in ("HEAD", "logs/HEAD") if os.path.exists(os.path.join(gd, f))]
    return max(ts)


def verdict(p, repo, lane, days, busy, named=False, halo=False, idle_hours=None, pin=False, preserve=False):
    if repo.lower() == "halocrm" or (NEVER_PRIVATE if halo else NEVER_NAMED if named else NEVER).search(repo + "/" + lane) or lane in KEEP_NAMES:
        return "protected (HALO / private / standing)"
    if not os.path.isfile(os.path.join(p, ".git")) and not (preserve and os.path.isdir(os.path.join(p, '.git'))):
        return "primary checkout (not a linked worktree)"
    if idle_hours is not None and not named:  # idle by the worktree's own activity, not by how old its commits are
        if time.time() - touched(p) < idle_hours * 3600:
            return f"committed, checked out or made here in the last {idle_hours:g} h"
    elif not named:
        last = git(p, "log", "-1", "--format=%ct").stdout.strip()
        if not last or time.time() - int(last) < days * 86400:
            return f"commit in the last {days} days"
        if time.time() - os.lstat(os.path.join(p, ".git")).st_mtime < days * 86400:  # made today from an old commit
            return f"worktree made in the last {days} days"
    st = git(p, "status", "--porcelain", "--untracked-files=" + ("no" if named else "normal"))
    if st.returncode:  # a broken worktree (its parent's metadata gone) must never read as clean
        return "git cannot read it (orphaned worktree)"
    if st.stdout.strip():
        if not preserve:
            return "uncommitted or untracked work"
        # HEAD age alone says nothing about an unfinished edit.
        cutoff = time.time() - (idle_hours if idle_hours is not None else days * 24) * 3600
        for root, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d != ".git" and not REBUILD.search(d)]
            for f in files:
                if os.lstat(os.path.join(root, f)).st_mtime > cutoff and not named:
                    return "authored files touched recently"
    if not preserve and not landed(p):
        return "HEAD not in main nor on any remote branch"
    if any(b == p or b.startswith(p + "/") for b in busy):
        return "a process is inside"
    return None


def ignored_keep(p):
    """Untracked and ignored files that do not rebuild themselves: they go to the archive before the worktree goes."""
    out = git(p, "status", "--porcelain", "--ignored", "-z").stdout.split("\0")
    paths = sorted(e[3:] for e in out if e[:3] in ("!! ", "?? ") and not REBUILD.search(e[3:].rstrip("/")))
    return [q for i, q in enumerate(paths) if not any(d.endswith("/") and q.startswith(d) for d in paths[:i])]


def landed_builder_lane(path):
    """An explicit builder may retire only its clean linked lane, freshly on its trunk."""
    path = os.path.abspath(path)
    relative = os.path.relpath(path, WT)
    parts = relative.split(os.sep)
    if len(parts) != 2 or parts[0] == ".." or os.path.realpath(path) != path:
        raise ValueError("outside the worktree lane boundary")
    repo, lane = parts
    trunk = "halo/v2" if repo.lower() == "halo" else "main"
    if lane in KEEP_NAMES or NEVER_PRIVATE.search(repo + "/" + lane):
        raise ValueError("standing or private lane")
    if not os.path.isfile(os.path.join(path, ".git")):
        raise ValueError("not a linked worktree")
    status = git(path, "status", "--porcelain", "--untracked-files=normal")
    if status.returncode or status.stdout.strip():
        raise ValueError("unfinished authored work")
    # A cached tracking ref is not proof that this builder landed.
    try:
        advertised = subprocess.run(["git", "-C", path, "ls-remote", "origin", "refs/heads/" + trunk],
                                    capture_output=True, text=True, timeout=30)
        fields = advertised.stdout.split()
        if advertised.returncode or len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
            raise ValueError("cannot freshly verify origin/" + trunk + "")
        main_sha = fields[0]
        fetched = subprocess.run(["git", "-C", path, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "origin", main_sha],
                                 capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("cannot freshly verify origin/" + trunk + "")
    if fetched.returncode or git(path, "merge-base", "--is-ancestor", "HEAD", main_sha).returncode:
        raise ValueError("HEAD is not freshly verified in origin/" + trunk + "")
    return repo, lane


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--landed-builder", metavar="PATH", help="retire only this completed builder lane after fresh main verification")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--idle-hours", type=float, help="instead of --days: nobody committed, checked out or made it here for H hours")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--preserve-wip", action="store_true", help="save dirty/unpushed work to verified remote WIP before retirement")
    ap.add_argument("--halo", action="store_true", help="HALO/Oracle lanes too (still clean, pushed, idle, not today's)")
    ap.add_argument("--pin-local", action="store_true", help="clean, idle worktrees with local-only commits too: pin HEAD at refs/estate-kept/ first")
    ap.add_argument("--only", nargs="+", metavar="REPO/LANE", help="just these worktrees, named by their owner")
    a = ap.parse_args()
    if a.landed_builder:
        if a.only or a.preserve_wip or a.pin_local:
            ap.error("--landed-builder cannot preserve or select other work")
        try:
            repo, lane = landed_builder_lane(a.landed_builder)
        except ValueError as exc:
            print("builder-retire: kept: " + str(exc))
            return 0
        a.only, a.halo = [repo + "/" + lane], True
    if a.pin_local:
        ap.error("--pin-local no longer permits retirement: use --preserve-wip for a verified remote copy")
    busy = held()
    stamp = time.strftime("%Y-%m-%d")
    free0, n, why_not = shutil.disk_usage(WS).free, 0, {}
    pairs = [tuple(o.split("/", 1)) for o in a.only] if a.only else \
        [(r, l) for r in sorted(os.listdir(WT)) if os.path.isdir(os.path.join(WT, r))
         for l in sorted(os.listdir(os.path.join(WT, r)))]
    for repo, lane in pairs:
            p = os.path.join(WT, repo, lane)
            if not os.path.exists(os.path.join(p, ".git")):
                continue
            v = verdict(p, repo, lane, a.days, busy, named=bool(a.only), halo=a.halo, idle_hours=a.idle_hours, pin=a.pin_local, preserve=a.preserve_wip)
            if v:
                why_not[v] = why_not.get(v, 0) + 1
                if a.only:
                    print(f"keep   {repo}/{lane}: {v}")
                continue
            if not a.run:
                print(f"retire {repo}/{lane}: verify remote WIP and preserve excluded data first")
                continue
            import importlib.util
            spec = importlib.util.spec_from_file_location("worktree_wip", os.path.join(HERE, "worktree-wip.py"))
            wip = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(wip)
            result = wip.process(p, True, True, busy, wip.seat_cwds())
            wip.receipt(**result)
            if result.get("result") == "removed":
                n += 1
                print(f"retired {repo}/{lane}: remote WIP verified")
            else:
                reason = result.get("result", "preservation failed")
                why_not[reason] = why_not.get(reason, 0) + 1
    print("kept:", ", ".join(f"{k} {v}" for k, v in sorted(why_not.items(), key=lambda x: -x[1])))
    if a.run:
        print(f"retired {n}; free {(shutil.disk_usage(WS).free - free0) / 2**30:+.2f} GiB")


if __name__ == "__main__":
    sys.exit(main())
