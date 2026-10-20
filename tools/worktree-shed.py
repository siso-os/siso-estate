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
import argparse, glob, json, os, re, shutil, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
from estate_state import record_path

WT = os.path.expanduser("~/SISO_Workspace/_data/worktrees")
NAMES = {"node_modules", "target", "dist", ".next", ".turbo", ".vite", ".svelte-kit", ".parcel-cache", "coverage",
         ".pytest_cache", "__pycache__", "storybook-static", ".astro", "out", "build", ".venv"}
SKIP_IN = {".git", "node_modules", "target"}
HOST = socket.gethostname().lower()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("laptop" if HOST.startswith("shaans-macbook") else "mini" if "mini" in HOST else HOST.split(".")[0])
RECEIPTS = os.environ.get("ESTATE_REMOVED") or record_path(REPO, "machines", MACHINE, "removed.jsonl")


def held():
    result = subprocess.run(["lsof", "-u", str(os.getuid()), "-Fn", "-w"], capture_output=True, text=True)
    if result.returncode or not result.stdout:
        raise RuntimeError("cannot see open files; shedding held")
    out = result.stdout
    return [l[1:] for l in out.splitlines() if l.startswith("n/")]


def gitdir(p):
    g = os.path.join(p, ".git")
    if os.path.isfile(g):
        with open(g) as f:
            line = f.read().strip()
        return os.path.normpath(os.path.join(p, line.split(": ", 1)[1])) if line.startswith("gitdir: ") else None
    return g if os.path.isdir(g) else None


def last_touch(gd):
    ts = [os.lstat(os.path.join(gd, f)).st_mtime for f in ("logs/HEAD", "HEAD") if os.path.exists(os.path.join(gd, f))]
    return max(ts) if ts else 0


def candidates(p, depth=5):
    """Rebuildable folders under p (not descending into them), at most `depth` levels down."""
    out, stack = [], [(p, 0)]
    while stack:
        d, n = stack.pop()
        if d != p and os.path.lexists(os.path.join(d, '.git')):
            continue  # a nested repository owns its own ignore/tracked-file rules
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if not e.is_dir(follow_symlinks=False):
                continue
            if os.path.lexists(os.path.join(e.path, '.git')):
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
    if r.returncode not in (0, 1):
        return []
    # An ignored directory may still contain force-added authored files.
    safe = []
    for x in r.stdout.splitlines():
        if not x:
            continue
        tracked = subprocess.run(["git", "-C", p, "ls-files", "--", x], capture_output=True, text=True)
        if tracked.returncode or tracked.stdout:
            continue
        if any('.git' in dirs or '.git' in files for _, dirs, files in os.walk(x)):
            continue
        safe.append(x)
    return safe


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


def registered_clones(census, workspace):
    roots = tuple(os.path.join(workspace, district) + '/' for district in
                  ('SISO_Agency', 'SISO_Agents', 'Great_Library_of_SISO'))
    return [r['path'] for r in census.get('repos', []) if r.get('kind') == 'repo' and r.get('github')
            and r.get('path', '').startswith(roots) and '/_archive/' not in r['path']]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--idle-hours", type=float, default=24)
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    busy, now, f0 = held(), time.time(), free_kib()
    plan, skipped = [], {"in use": 0, "touched recently": 0}
    lanes = []
    for repo in sorted(os.listdir(WT)):
        rd = os.path.join(WT, repo)
        if os.path.isdir(rd):
            lanes.extend(os.path.join(rd, lane) for lane in sorted(os.listdir(rd)))
    # CI's warm clones are another build-output tap, not all in /worktrees.
    # Only ignored generated folders go; source, refs and recovery evidence stay.
    ci = os.path.join(os.path.dirname(WT), "siso-ci-work")
    lanes.extend(glob.glob(os.path.join(ci, "workers", "*", "clones", "*", "*")))
    # Standalone source checkouts retain dependencies after their modules move
    # into the monorepo. Apply the same ignored-only proof to registered clones.
    try:
        census = json.load(open(record_path(REPO, 'machines', MACHINE, 'repos.json')))
        workspace = os.path.dirname(os.path.dirname(WT))
        lanes.extend(registered_clones(census, workspace))
    except (OSError, ValueError, KeyError):
        pass
    lanes = sorted(set(lanes))
    for p in lanes:
        label = os.path.relpath(p, WT)
        if re.search(r"halocrm|bykonz|fahmy|life|whatsapp", label, re.I) or os.path.basename(p) in {"main", "live", "mini-runtime-20261018"}:
            continue
        remote = subprocess.run(["git", "-C", p, "remote", "get-url", "origin"], capture_output=True, text=True)
        if remote.returncode or re.search(r"camronkellman|halocrm", remote.stdout, re.I):
            continue
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
            plan.append((kib(c), c, p))
    plan.sort(reverse=True)
    total = sum(k for k, _, _ in plan)
    for k, c, owner in plan[:25]:
        print(f"{'delete' if a.run else 'would'} {k / 1048576:6.2f} GiB  {os.path.relpath(c, WT)}")
    print(f"{len(plan)} folders, {total / 1048576:.1f} GiB by du; worktrees skipped: {skipped}")
    if not a.run:
        return 0
    gone = 0
    for k, c, owner in plan:
        try:
            # A public build path disappears atomically before the final lsof.
            # Writers that start afterwards create a new output, which stays.
            if not owner or not ignored(owner, [c]) or any(b == owner or b.startswith(owner + "/") for b in held()):
                continue
            quarantine = c + ".estate-shedding-" + str(os.getpid())
            if os.path.lexists(quarantine):
                continue
            os.rename(c, quarantine)
            try:
                if any(b == quarantine or b.startswith(quarantine + "/") for b in held()):
                    if not os.path.lexists(c):
                        os.rename(quarantine, c)
                    continue
                shutil.rmtree(quarantine)
                gone += k
            except Exception:
                if os.path.isdir(quarantine) and not os.path.lexists(c):
                    os.rename(quarantine, c)
                raise
        except OSError as e:
            print(f"  FAIL {c}: {e}")
    f1 = free_kib()
    os.makedirs(os.path.dirname(RECEIPTS), exist_ok=True)
    with open(RECEIPTS, "a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": "delete rebuildable build output in idle worktrees",
                            "folders": len(plan), "du_kib": gone, "free_gain_kib": f1 - f0, "idle_hours": a.idle_hours,
                            "by": "ESTATE tools/worktree-shed.py"}) + "\n")
    print(f"freed {(f1 - f0) / 1048576:+.1f} GiB by df")
    return 0


if __name__ == "__main__":
    sys.exit(main())
