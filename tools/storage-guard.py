#!/usr/bin/env python3
"""Keep the laptop's free space from leaking: apply the storage rules in plan/fix-policies.json (rule "storage").

  storage-guard.py          the plan: what each rule would clear now, and what it skips and why
  storage-guard.py --run    do it, then one receipt line per item and one per run in machines/<m>/storage-guard.jsonl

Every rule clears only things that rebuild themselves or were left behind by a process that is gone: never a
worktree, never chat history, never anything a running process has open (one `lsof` of the user's open files and
working folders, taken at the start). Hourly from launchd (com.siso.estate-storage-guard). Shaan, 8 Oct: "so we
don't every three days have to keep cleaning 50 gig". The map of what fills the laptop: docs/STORAGE-MAP.md.
"""
import argparse, glob, json, os, shutil, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
POL = json.load(open(os.path.join(REPO, "plan", "fix-policies.json")))
RULES = [p for p in POL["policies"] if p.get("rule") == "storage" and not p.get("kind")]
TOOLS = {"worktree-shed": ["worktree-shed.py"], "worktree-retire": ["worktree-retire.py", "--halo"]}
SHEDS = [p for p in POL["policies"] if p.get("rule") == "storage" and p.get("kind") in TOOLS]
HOST = socket.gethostname().lower()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("laptop" if HOST.startswith("shaans-macbook") else "mini" if "mini" in HOST else HOST.split(".")[0])
# the mini runs the guard from a clean worktree refreshed each hour: its receipts live outside the repo (ESTATE_RECEIPTS)
RECEIPTS = os.environ.get("ESTATE_RECEIPTS") or os.path.join(REPO, "machines", MACHINE, "storage-guard.jsonl")


def sh(*a, timeout=120):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def free_kib():
    out = sh("df", "-k", "/System/Volumes/Data" if sys.platform == "darwin" else "/")
    return int(out.splitlines()[1].split()[3])


def open_paths():
    """Every file and working folder the user's processes hold open. None when lsof cannot answer: then touch nothing."""
    out = sh("lsof", "-u", str(os.getuid()), "-Fn", "-w", timeout=180)
    if not out:
        return None
    return {line[1:] for line in out.splitlines() if line.startswith("n/")}


def in_use(path, held):
    real = os.path.realpath(path)
    for p in (path, real):
        pre = p.rstrip("/") + "/"
        if any(h == p or h.startswith(pre) for h in held):
            return True
    return False


def running(pattern):
    return bool((sh("pgrep", "-f", pattern) or "").strip())


def size_kib(path):
    out = sh("du", "-sk", path, timeout=600)
    return int(out.split()[0]) if out else 0


def expand(pattern):
    pattern = pattern.replace("$TMPDIR", os.environ.get("TMPDIR", "/tmp").rstrip("/")).replace("~", HOME, 1)
    if "$DARWIN_USER_DIR" in pattern:
        tmp = (sh("getconf", "DARWIN_USER_TEMP_DIR") or "").strip().rstrip("/")
        pattern = pattern.replace("$DARWIN_USER_DIR", os.path.dirname(tmp)) if tmp else ""
    return sorted(glob.glob(pattern)) if pattern else []


def candidates(rule, held, now):
    """(path, why-skipped-or-None) for one rule."""
    if rule.get("unless_running") and running(rule["unless_running"]):
        return [(p, "its app is running") for p in sum((expand(g) for g in rule["paths"]), [])]
    found = []
    for g in rule["paths"]:
        paths = expand(g)
        keep = set(sorted(paths, key=lambda p: os.lstat(p).st_mtime)[-rule["keep_newest"]:]) if rule.get("keep_newest") else set()
        for p in paths:
            age_h = (now - os.lstat(p).st_mtime) / 3600
            if p in keep:
                found.append((p, "one of the newest %d kept" % rule["keep_newest"]))
            elif age_h < rule.get("min_age_hours", 24):
                found.append((p, "younger than %sh" % rule.get("min_age_hours", 24)))
            elif in_use(p, held):
                found.append((p, "a running process has it open"))
            else:
                found.append((p, None))
    if rule.get("min_total_mib"):
        total = sum(size_kib(p) for p, why in found if why is None)
        if total < rule["min_total_mib"] * 1024:
            found = [(p, why or "under %d MiB, not worth it" % rule["min_total_mib"]) for p, why in found]
    return found


def remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        def writable(fn, p, _):  # Go's module cache is read-only on purpose; open the folder, then retry
            os.chmod(os.path.dirname(p), 0o755)
            os.chmod(p, 0o755) if os.path.isdir(p) else None
            fn(p)
        shutil.rmtree(path, onerror=writable)
    else:
        os.remove(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", action="store_true", help="clear what the rules allow and write receipts")
    ap.add_argument("--quiet", action="store_true", help="print only the totals")
    a = ap.parse_args()
    held = open_paths()
    if held is None:
        print("storage-guard: cannot see open files (lsof): touching nothing")
        return 1
    now, start = time.time(), free_kib()
    receipts, cleared, kib_total = [], 0, 0
    for rule in RULES:
        for path, why in candidates(rule, held, now):
            if why:
                if not a.quiet and why not in ("younger than %sh" % rule.get("min_age_hours", 24),):
                    print(f"  skip  {rule['id']:<24} {path}  ({why})")
                continue
            kib = size_kib(path)
            if not a.run:
                print(f"  would {rule['id']:<24} {kib / 1048576:6.2f} GiB  {path}")
                kib_total += kib
                continue
            try:
                remove(path)
            except OSError as e:
                print(f"  FAIL  {rule['id']:<24} {path}: {e}")
                continue
            cleared += 1
            kib_total += kib
            receipts.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rule": rule["id"], "path": path, "du_kib": kib})
    for rule in SHEDS:  # idle worktrees: their own tools, their own receipt lines
        tool, *flags = TOOLS[rule["kind"]]
        out = sh(sys.executable, os.path.join(HERE, tool), *flags, "--idle-hours", str(rule.get("idle_hours", 6)),
                 *(["--run"] if a.run else []), timeout=1800) or ""
        tail = out.strip().splitlines()[-2:] if a.quiet else out.strip().splitlines()
        print("\n".join(f"  {rule['id']}: {l}" for l in tail))
    end = free_kib()
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run": bool(a.run), "items": cleared,
            "du_kib": kib_total, "free_kib_before": start, "free_kib_after": end}
    if a.run:
        with open(RECEIPTS, "a") as f:
            for r in receipts + [line]:
                f.write(json.dumps(r) + "\n")
    verb = "cleared" if a.run else "would clear"
    print(f"storage-guard: {verb} {cleared if a.run else ''} {kib_total / 1048576:.2f} GiB by du; "
          f"free {start / 1048576:.1f} -> {end / 1048576:.1f} GiB (du overcounts APFS clones; the df change is the truth)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
