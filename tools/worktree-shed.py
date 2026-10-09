#!/usr/bin/env python3
"""Delete rebuildable build output inside idle worktrees: node_modules, Rust target/, dist, .next, caches.

  worktree-shed.py [--idle-hours 24]          the plan: each folder that would go, biggest first
  worktree-shed.py [--idle-hours 24] --run    delete them; one receipt line per run in machines/<m>/removed.jsonl

Shaan, 8 Oct 22:12: "I want shit that we don't need to be deleted if we don't fucking need it this is way too slow".
A folder goes only when: git ignores it (never tracked work); nothing in the worktree is held by a process (one lsof);
the worktree's HEAD and HEAD log are older than --idle-hours (nobody has committed or checked out there; the index is
not a signal: any `git status` rewrites it, and fleet monitors run that on every worktree).
The worktree itself, its commits and its own files stay; `pnpm install` / `cargo build` bring the folders back.
"""
import argparse, json, os, shutil, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WT = os.path.expanduser("~/SISO_Workspace/_data/worktrees")
NAMES = {"node_modules", "target", "dist", ".next", ".turbo", ".vite", ".svelte-kit", ".parcel-cache", "coverage",
         ".pytest_cache", "__pycache__", "storybook-static", ".wrangler", ".astro", "out", "build", ".venv"}
SKIP_IN = {".git", "node_modules", "target"}
HOST = socket.gethostname().lower()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("laptop" if HOST.startswith("shaans-macbook") else "mini" if "mini" in HOST else HOST.split(".")[0])
RECEIPTS = os.environ.get("ESTATE_REMOVED") or os.path.join(REPO, "machines", MACHINE, "removed.jsonl")


def held():
    out = subprocess.run(["lsof", "-u", str(os.getuid()), "-Fn", "-w"], capture_output=True, text=True).stdout
    return [l[1:] for l in out.splitlines() if l.startswith("n" + WT)]


def gitdir(p):
    g = os.path.join(p, ".git")
    if os.path.isfile(g):
        line = open(g).read().strip()
        return line.split(": ", 1)[1] if line.startswith("gitdir: ") else None
    return g if os.path.isdir(g) else None


def last_touch(gd):
    ts = [os.lstat(os.path.join(gd, f)).st_mtime for f in ("logs/HEAD", "HEAD") if os.path.exists(os.path.join(gd, f))]
    return max(ts) if ts else 0


def candidates(p, depth=5):
    """Rebuildable folders under p (not descending into them), at most `depth` levels down."""
    out, stack = [], [(p, 0)]
    while stack:
        d, n = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if not e.is_dir(follow_symlinks=False):
                continue
            if e.name in NAMES:
                out.append(e.path)
            elif e.name not in SKIP_IN and n < depth:
                stack.append((e.path, n + 1))
    return out


def ignored(p, paths):
    if not paths:
        return []
    r = subprocess.run(["git", "-C", p, "check-ignore", "--stdin"], input="\n".join(paths), capture_output=True, text=True)
    return [x for x in r.stdout.splitlines() if x]


def kib(path):
    out = subprocess.run(["du", "-skx", path], capture_output=True, text=True).stdout
    return int(out.split()[0]) if out else 0


def rm(path):
    def writable(fn, q, _):
        os.chmod(os.path.dirname(q), 0o755)
        os.chmod(q, 0o755) if os.path.isdir(q) else None
        fn(q)
    shutil.rmtree(path, onerror=writable)


def free_kib():
    return int(subprocess.run(["df", "-k", "/System/Volumes/Data"], capture_output=True, text=True).stdout.splitlines()[1].split()[3])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--idle-hours", type=float, default=24)
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    busy, now, f0 = held(), time.time(), free_kib()
    plan, skipped = [], {"in use": 0, "touched recently": 0}
    for repo in sorted(os.listdir(WT)):
        rd = os.path.join(WT, repo)
        if not os.path.isdir(rd):
            continue
        for lane in sorted(os.listdir(rd)):
            p = os.path.join(rd, lane)
            gd = gitdir(p)
            if not gd:
                continue
            if any(b == p or b.startswith(p + "/") for b in busy):
                skipped["in use"] += 1
                continue
            if now - last_touch(gd) < a.idle_hours * 3600:
                skipped["touched recently"] += 1
                continue
            for c in ignored(p, candidates(p)):
                plan.append((kib(c), c))
    plan.sort(reverse=True)
    total = sum(k for k, _ in plan)
    for k, c in plan[:25]:
        print(f"{'delete' if a.run else 'would'} {k / 1048576:6.2f} GiB  {os.path.relpath(c, WT)}")
    print(f"{len(plan)} folders, {total / 1048576:.1f} GiB by du; worktrees skipped: {skipped}")
    if not a.run:
        return 0
    gone = 0
    for k, c in plan:
        try:
            rm(c)
            gone += k
        except OSError as e:
            print(f"  FAIL {c}: {e}")
    f1 = free_kib()
    with open(RECEIPTS, "a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": "delete rebuildable build output in idle worktrees",
                            "folders": len(plan), "du_kib": gone, "free_gain_kib": f1 - f0, "idle_hours": a.idle_hours,
                            "by": "ESTATE tools/worktree-shed.py"}) + "\n")
    print(f"freed {(f1 - f0) / 1048576:+.1f} GiB by df")
    return 0


if __name__ == "__main__":
    sys.exit(main())
