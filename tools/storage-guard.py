#!/usr/bin/env python3
"""Keep the laptop's free space from leaking: apply the storage rules in plan/fix-policies.json (rule "storage").

  storage-guard.py          the plan: what each rule would clear now, and what it skips and why
  storage-guard.py --run    do it, then one receipt line per item and one per run in machines/<m>/storage-guard.jsonl

Every rule clears only things that rebuild themselves or were left behind by a process that is gone: never a
worktree, never chat history, never anything a running process has open (one `lsof` of the user's open files and
working folders, taken at the start). Hourly from launchd (com.siso.estate-storage-guard). Shaan, 8 Oct: "so we
don't every three days have to keep cleaning 50 gig". The map of what fills the laptop: docs/STORAGE-MAP.md.
"""
import argparse, fnmatch, glob, json, os, shutil, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
POL = json.load(open(os.path.join(REPO, "plan", "fix-policies.json")))
RULES = [p for p in POL["policies"] if p.get("rule") == "storage" and not p.get("kind")]
TOOLS = {"worktree-shed": ["worktree-shed.py"], "worktree-retire": ["worktree-retire.py", "--halo"], "worktree-orphans": ["worktree-orphans.py"], "repo-offload": ["repo-offload.py"], "archive-offload": ["archive-sweep.py"]}
SHEDS = [p for p in POL["policies"] if p.get("rule") == "storage" and p.get("kind") in TOOLS]
CAPS = [p for p in POL["policies"] if p.get("rule") == "storage" and p.get("kind") == "log-cap"]
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
            if any(fnmatch.fnmatch(os.path.basename(p), x) for x in rule.get("except", [])):
                continue  # sockets, live session folders: never candidates
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


def floor_check():
    """All four disks (laptop, mini, halo-vps, vps-siso): floor and days-until-full, one console card (tools/disk-watch.py)."""
    out = sh(sys.executable, os.path.join(HERE, "disk-watch.py"), "--record", timeout=240) or ""
    return [l.split("<- ", 1)[0].split()[0] for l in out.splitlines() if "<- " in l]


def check_floor(floor_gib, run):
    """Bound new heavy admissions; running work continues and holds expire through heavy."""
    free = free_kib() * 1024
    state = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "machine": MACHINE,
             "floor_bytes": int(floor_gib * 2**30), "free_bytes": free,
             "low": free < floor_gib * 2**30, "run": run}
    if run:
        heavy = os.path.join(HOME, ".local", "bin", "heavy")
        hold = os.path.join(HOME, ".cache", "siso-heavy", "hold")
        try:
            with open(hold) as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""
        try:
            active = float(existing.split("\t", 1)[0]) > time.time()
        except ValueError:
            active = bool(existing)  # preserve an unrecognised owner hold
        # An active unrelated hold belongs to its caller.
        ours = "ESTATE-MINI:" in existing
        if state["low"] and (not active or ours):
            result = subprocess.run([heavy, "--hold", "10", "ESTATE-MINI: system disk below %.0f GiB floor" % floor_gib],
                                    capture_output=True, text=True, timeout=15)
            state["admission_exit"] = result.returncode
        elif state["low"]:
            state["admission"] = "another owner's hold preserved"
        directory = os.path.join(HOME, ".local", "state", "estate")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "mini-storage-floor.json")
        with open(path + ".tmp", "w") as f:
            json.dump(state, f, indent=2)
        os.replace(path + ".tmp", path)
    print("storage-floor: " + json.dumps(state, sort_keys=True))
    return state


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", action="store_true", help="clear what the rules allow and write receipts")
    ap.add_argument("--quiet", action="store_true", help="print only the totals")
    ap.add_argument("--floor-gib", type=float, default=60 if MACHINE == "mini" else 0,
                    help="pause new heavy admissions below this floor (mini default: 60 GiB)")
    ap.add_argument("--floor-only", action="store_true", help="only record the floor and update admission; no cleanup")
    ap.add_argument("--cleanup-interval", type=int, default=0, help="minimum seconds between successful cleanups")
    a = ap.parse_args()
    if a.cleanup_interval < 0:
        ap.error("--cleanup-interval must be nonnegative")
    if a.floor_gib < 0:
        ap.error("--floor-gib must be nonnegative")
    if a.floor_gib:
        check_floor(a.floor_gib, a.run)
    if a.floor_only:
        return 0
    cleanup_stamp = os.path.join(HOME, ".local", "state", "estate", "storage-guard-last-cleanup")
    if a.run and a.cleanup_interval:
        try:
            with open(cleanup_stamp) as f:
                last = float(f.read())
        except (OSError, ValueError):
            last = 0
        if time.time() - last < a.cleanup_interval:
            print("storage-guard: hourly cleanup not due")
            return 0
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
    for rule in CAPS:  # logs a live service keeps writing: keep the newest tail in place (the writer's handle stays valid)
        for g in rule["paths"]:
            for path in expand(g):
                size = os.path.getsize(path) if os.path.isfile(path) and not os.path.islink(path) else 0
                if size <= rule.get("max_mib", 50) * 1048576:
                    continue
                keep = rule.get("keep_mib", 5) * 1048576
                if not a.run:
                    print(f"  would {rule['id']:<24} {(size - keep) / 1073741824:6.2f} GiB  {path} (keep last {keep // 1048576} MiB)")
                    kib_total += (size - keep) // 1024
                    continue
                with open(path, "r+b") as fh:
                    fh.seek(size - keep)
                    tail = fh.read()
                    fh.seek(0)
                    fh.write(tail)
                    fh.truncate(len(tail))
                cleared += 1
                kib_total += (size - keep) // 1024
                receipts.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rule": rule["id"], "path": path,
                                 "du_kib": (size - keep) // 1024, "kept_tail_mib": keep // 1048576})
    for rule in SHEDS:  # idle worktrees: their own tools, their own receipt lines
        tool, *flags = TOOLS[rule["kind"]]
        out = sh(sys.executable, os.path.join(HERE, tool), *flags, "--idle-hours", str(rule.get("idle_hours", 6)),
                 *(["--run"] if a.run else []), timeout=1800) or ""
        tail = out.strip().splitlines()[-2:] if a.quiet else out.strip().splitlines()
        print("\n".join(f"  {rule['id']}: {l}" for l in tail))
    end = free_kib()
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run": bool(a.run), "items": cleared,
            "du_kib": kib_total, "free_kib_before": start, "free_kib_after": end}
    if a.run and MACHINE == "laptop":
        low = floor_check()
        if low:
            line["floor_alert"] = low
    if a.run:
        with open(RECEIPTS, "a") as f:
            for r in receipts + [line]:
                f.write(json.dumps(r) + "\n")
    if a.run and a.cleanup_interval:
        os.makedirs(os.path.dirname(cleanup_stamp), exist_ok=True)
        with open(cleanup_stamp, "w") as f:
            f.write(str(time.time()))
    verb = "cleared" if a.run else "would clear"
    print(f"storage-guard: {verb} {cleared if a.run else ''} {kib_total / 1048576:.2f} GiB by du; "
          f"free {start / 1048576:.1f} -> {end / 1048576:.1f} GiB (du overcounts APFS clones; the df change is the truth)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
