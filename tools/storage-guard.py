#!/usr/bin/env python3
"""Keep the laptop's free space from leaking: apply the storage rules in plan/fix-policies.json (rule "storage").

  storage-guard.py          the plan: what each rule would clear now, and what it skips and why
  storage-guard.py --run    do it, then one receipt line per item and one per run in machines/<m>/storage-guard.jsonl

Every rule clears only things that rebuild themselves or were left behind by a process that is gone: never a
worktree, never chat history, never anything a running process has open (one `lsof` of the user's open files and
working folders, taken at the start). Hourly from launchd (com.siso.estate-storage-guard). Shaan, 8 Oct: "so we
don't every three days have to keep cleaning 50 gig". The map of what fills the laptop: docs/STORAGE-MAP.md.
"""
import argparse, fcntl, fnmatch, glob, hashlib, importlib.util, json, os, re, shutil, socket, subprocess, sys, time
import signal

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
from estate_state import record_path

HOME = os.path.expanduser("~")
POL = json.load(open(os.path.join(REPO, "plan", "fix-policies.json")))
RULES = [p for p in POL["policies"] if p.get("rule") == "storage" and not p.get("kind")]
TOOLS = {"cold-clones": ["to-vault.py", "--cold-clones"], "pnpm-generations": ["to-vault.py", "--pnpm-generations"], "retired-services": ["to-vault.py", "--retired-services"], "installer-fixtures": ["installer-fixtures.py"], "ci-retire": ["ci-storage-retire.py"], "worktree-shed": ["worktree-shed.py"], "worktree-retire": ["worktree-retire.py", "--halo", "--preserve-wip"], "worktree-orphans": ["worktree-orphans.py"], "repo-offload": ["repo-offload.py"], "archive-offload": ["archive-sweep.py"]}
SHEDS = [p for p in POL["policies"] if p.get("rule") == "storage" and p.get("kind") in TOOLS]
CAPS = [p for p in POL["policies"] if p.get("rule") == "storage" and p.get("kind") == "log-cap"]
HOST = socket.gethostname().lower()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("laptop" if HOST.startswith("shaans-macbook") else "mini" if "mini" in HOST else HOST.split(".")[0])
# the mini runs the guard from a clean worktree refreshed each hour: its receipts live outside the repo (ESTATE_RECEIPTS)
RECEIPTS = os.environ.get("ESTATE_RECEIPTS") or record_path(REPO, "machines", MACHINE, "storage-guard.jsonl")
# Work pauses only when the disk itself is in danger: 5 GiB never-write reserve (houses.py MIN_FREE; a full disk
# crashed herdr on 24 Sep) plus 10 GiB for one build's growth. The 60/100 GiB figures are cleaning targets, never
# a wait (Shaan, 21 Oct: "we can be freeing space on the mini and doing work").
HOLD_GIB = 15


def tool_environment():
    """launchd has a minimal PATH; preservation tools also need Homebrew/user CLIs."""
    paths = ['/opt/homebrew/bin', os.path.join(HOME, '.local/bin'), *os.environ.get('PATH', '').split(':'),
             '/usr/bin', '/bin', '/usr/sbin', '/sbin']
    return dict(os.environ, ESTATE_MACHINE=MACHINE, PATH=':'.join(dict.fromkeys(p for p in paths if p)))


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


def _elapsed(etime):
    parts = etime.split("-")
    days = int(parts[0]) if len(parts) == 2 else 0
    clock = parts[-1].split(":")
    if len(clock) == 3:
        h, m, s = map(int, clock)
    else:
        h, m, s = 0, *map(int, clock)
    return days * 86400 + h * 3600 + m * 60 + s


def automation_has_client(pid):
    listeners = sh("lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn", timeout=10)
    if listeners is None:
        return True  # unknown ownership is held
    for port in re.findall(r"(?m)^n.*:(\d+)$", listeners):
        connected = sh("lsof", "-nP", "-a", "-p", str(pid), "-iTCP:" + port, "-sTCP:ESTABLISHED", "-t", timeout=10)
        if connected is None or connected.strip():
            return True
    return False


def orphan_automation_chrome(run=False, now=None, state=None):
    """Observe an ownerless automation browser idle for an hour before TERM."""
    now = time.time() if now is None else now
    state_path = record_path(REPO, "machines", MACHINE, "automation-orphans.json")
    supplied = state is not None
    if state is None:
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
    observed, rows = {}, []
    out = sh("ps", "-axo", "pid=,ppid=,pcpu=,etime=,command=", timeout=30) or ""
    for line in out.splitlines():
        fields = line.strip().split(None, 4)
        if len(fields) != 5:
            continue
        try:
            pid, ppid, cpu = int(fields[0]), int(fields[1]), float(fields[2])
            age = _elapsed(fields[3])
        except ValueError:
            continue
        cmd = fields[4]
        marker = "puppeteer_dev_chrome_profile" in cmd or "--remote-debugging" in cmd
        if ppid != 1 or not marker or "--type=" in cmd or age < 3600:
            continue
        executable = (sh("ps", "-p", str(pid), "-o", "comm=", timeout=10) or "").strip()
        if os.path.basename(executable).lower() not in ("chrome", "google chrome", "google chrome for testing", "chromium", "headless_shell"):
            continue
        started = (sh("ps", "-p", str(pid), "-o", "lstart=", timeout=10) or "").strip()
        if not started:
            continue
        identity = hashlib.sha256((started + "|" + cmd).encode()).hexdigest()
        previous = state.get(str(pid), {})
        since = previous.get("since", now) if previous.get("identity") == identity else now
        active = automation_has_client(pid)
        if active:
            since = now
        observed[str(pid)] = {"identity": identity, "since": since}
        row = {"pid": pid, "age_hours": round(age / 3600, 1), "cpu": cpu, "automation": True,
               "action": "active control client" if active else "observing idle"}
        if not active and now - since >= 3600:
            if run:
                check = sh("ps", "-p", str(pid), "-o", "ppid=,command=", timeout=10) or ""
                check_fields = check.strip().split(None, 1)
                if len(check_fields) != 2 or check_fields[0] != "1" or check_fields[1] != cmd:
                    continue
                if (sh("ps", "-p", str(pid), "-o", "lstart=", timeout=10) or "").strip() != started or automation_has_client(pid):
                    observed[str(pid)]["since"] = now
                    continue
                try:
                    os.kill(pid, signal.SIGTERM)
                    row["action"] = "TERM"
                except ProcessLookupError:
                    row["action"] = "gone"
                except OSError:
                    row["action"] = "TERM failed"
            else:
                row["action"] = "would TERM"
        rows.append(row)
    if run:
        state.clear()
        state.update(observed)
        if not supplied:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path + ".tmp", "w") as f:
                json.dump(state, f)
            os.replace(state_path + ".tmp", state_path)
    return rows


def size_kib(path):
    out = sh("du", "-sk", path, timeout=600)
    return int(out.split()[0]) if out else 0


def expand(pattern):
    pattern = pattern.replace("$TMPDIR", os.environ.get("TMPDIR", "/tmp").rstrip("/")).replace("~", HOME, 1)
    if "$DARWIN_USER_DIR" in pattern:
        tmp = (sh("getconf", "DARWIN_USER_TEMP_DIR") or "").strip().rstrip("/")
        pattern = pattern.replace("$DARWIN_USER_DIR", os.path.dirname(tmp)) if tmp else ""
    return sorted(glob.glob(pattern)) if pattern else []


def disposable_temp(path):
    """Age never proves a scratch source tree or database is disposable."""
    sensitive = re.compile(r"(^|/)(\.git|\.env[^/]*|credentials[^/]*|.*\.(?:sqlite[^/]*|db(?:-[^/]*)?|pem|key|jsonl))$", re.I)
    if sensitive.search(path):
        return False
    if not os.path.isdir(path) or os.path.islink(path):
        return True
    seen = 0
    for root, dirs, files in os.walk(path):
        if any(sensitive.search(n) for n in dirs + files):
            return False
        dirs[:] = [d for d in dirs if d not in ("node_modules", "target")]
        seen += len(files) + len(dirs)
        if seen > 10000:
            return False  # unknown large trees require their owner's cleanup
    return True


def candidates(rule, held, now):
    """(path, why-skipped-or-None) for one rule."""
    if rule.get("unless_running") and running(rule["unless_running"]):
        return [(p, "its app is running") for p in sum((expand(g) for g in rule["paths"]), [])]
    found = []
    for g in rule["paths"]:
        stamps = {}
        for p in expand(g):
            try:
                stamps[p] = os.lstat(p).st_mtime
            except OSError:
                continue  # another job cleaned its own temp after globbing
        keep = set(sorted(stamps, key=stamps.get)[-rule["keep_newest"]:]) if rule.get("keep_newest") else set()
        for p, modified in stamps.items():
            if not os.path.lexists(p):
                continue
            age_h = (now - modified) / 3600
            if any(fnmatch.fnmatch(os.path.basename(p), x) for x in rule.get("except", [])):
                continue  # sockets, live session folders: never candidates
            if rule["id"] == "storage-old-tmp" and not os.path.basename(p).startswith(("npm-cache-temp", "playwright-", "puppeteer_", "siso-code-", "uihub-")):
                found.append((p, "unknown scratch: owner must preserve it"))
            elif rule["id"] in ("storage-old-tmp", "storage-dead-npm-temp") and not disposable_temp(p):
                found.append((p, "authored work or private data: held"))
            elif p in keep:
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
        elif active and ours:
            # Space came back (or the floor was lowered): stop holding work at once, not when the hold expires.
            result = subprocess.run([heavy, "--release"], capture_output=True, text=True, timeout=15)
            state["released_exit"] = result.returncode
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
                    help="cleaning target: shed rebuildable folders harder below this; never pauses work")
    ap.add_argument("--hold-gib", type=float, default=HOLD_GIB,
                    help="pause new heavy admissions only below this (default %d GiB)" % HOLD_GIB)
    ap.add_argument("--floor-only", action="store_true", help="only record the floor and update admission; no cleanup")
    ap.add_argument("--cleanup-interval", type=int, default=0, help="minimum seconds between successful cleanups")
    a = ap.parse_args()
    if a.cleanup_interval < 0:
        ap.error("--cleanup-interval must be nonnegative")
    if a.floor_gib < 0 or a.hold_gib < 0:
        ap.error("--floor-gib and --hold-gib must be nonnegative")
    if a.hold_gib:
        check_floor(a.hold_gib, a.run)
    if a.floor_only:
        return 0
    cleanup_stamp = os.path.join(HOME, ".local", "state", "estate", "storage-guard-last-cleanup")
    cleanup_lock = None
    if a.run:
        state_dir = os.path.join(HOME, ".local", "state", "estate")
        os.makedirs(state_dir, exist_ok=True)
        cleanup_lock = open(os.path.join(state_dir, "storage-guard.lock"), "a")
        try:
            fcntl.flock(cleanup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            cleanup_lock.close()
            print("storage-guard: cleanup already running; next scheduled check will retry")
            return 0
    now = time.time()
    cleanup_due = True
    if a.run and a.cleanup_interval:
        try:
            with open(cleanup_stamp) as f:
                last = float(f.read())
        except (OSError, ValueError):
            last = 0
        cleanup_due = now - last >= a.cleanup_interval
    receipts, cleared, kib_total = [], 0, 0
    for row in orphan_automation_chrome(a.run, now):
        print("  %s orphan automation Chrome pid %d (%.1fh, %.1f%% CPU)" %
              (row["action"], row["pid"], row["age_hours"], row["cpu"]))
        if a.run and row["action"] == "TERM":
            receipts.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rule": "storage-orphan-automation-chrome",
                             "pid": row["pid"], "action": "TERM"})
    if not cleanup_due:
        # Sample browser ownership every five minutes, including between hourly
        # cleanups: an active debugging client resets the observed-idle clock.
        if receipts:
            os.makedirs(os.path.dirname(RECEIPTS), exist_ok=True)
            with open(RECEIPTS, "a") as f:
                for row in receipts:
                    f.write(json.dumps(row) + "\n")
        print("storage-guard: hourly cleanup not due; automation idle check complete")
        return 0
    held = open_paths()
    if held is None:
        print("storage-guard: cannot see open files (lsof): touching nothing")
        return 1
    start = free_kib()
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
                try:
                    size = os.path.getsize(path) if os.path.isfile(path) and not os.path.islink(path) else 0
                except OSError:
                    continue
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
    tool_failures = 0
    for rule in SHEDS:  # idle worktrees: their own tools, their own receipt lines
        tool, *flags = TOOLS[rule["kind"]]
        idle_hours = rule.get("idle_hours", 6)
        target = a.floor_gib or (60 if MACHINE == "mini" else 100)
        if rule["kind"] == "worktree-shed" and free_kib() < target * 1048576:
            # Disk-floor recovery may shed freshly built dependencies once their
            # processes have closed. Authored work and worktree retirement keep
            # their own stricter preservation/age rules.
            idle_hours = 0
        tool_free_before = free_kib()
        try:
            result = subprocess.run([sys.executable, os.path.join(HERE, tool), *flags, "--idle-hours", str(idle_hours), *(["--run"] if a.run else [])], capture_output=True, text=True, timeout=1800, env=tool_environment())
            out = result.stdout or ""
            if result.stderr:
                print("  %s stderr: %s" % (rule["id"], result.stderr.strip()[-500:]))
        except (OSError, subprocess.TimeoutExpired) as exc:
            result, out = None, ""
            print("  %s dispatch failed: %s" % (rule["id"], exc))
        tail = out.strip().splitlines()[-2:] if a.quiet else out.strip().splitlines()
        print("\n".join(f"  {rule['id']}: {l}" for l in tail))
        if result is None or result.returncode:
            tool_failures += 1
        if a.run:
            tool_free_after = free_kib()
            receipts.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rule": rule["id"], "tool": tool,
                             "exit": result.returncode if result else None, "free_gain_gib": max(0, tool_free_after - tool_free_before) / 1048576})
    if a.run:
        mapped = subprocess.run([sys.executable, os.path.join(HERE, "storage-map.py"), "--if-due"], capture_output=True, text=True, timeout=1800)
        print(mapped.stdout.strip().splitlines()[-1] if mapped.stdout.strip() else "storage-map: failed (see exit)")
        if mapped.returncode:
            tool_failures += 1
    end = free_kib()
    if a.run:
        trend_spec = importlib.util.spec_from_file_location("storage_trend", os.path.join(HERE, "storage-trend.py"))
        trend_mod = importlib.util.module_from_spec(trend_spec)
        trend_spec.loader.exec_module(trend_mod)
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run": bool(a.run), "items": cleared,
            "du_kib": kib_total, "tool_failures": tool_failures, "free_kib_before": start, "free_kib_after": end}
    if a.run and MACHINE == "laptop":
        low = floor_check()
        if low:
            line["floor_alert"] = low
    if a.run:
        os.makedirs(os.path.dirname(RECEIPTS), exist_ok=True)
        with open(RECEIPTS, "a") as f:
            for r in receipts + [line]:
                f.write(json.dumps(r) + "\n")
        trend_receipt = record_path(REPO, "machines", MACHINE, "storage-trend.jsonl")
        rows = trend_mod.load_lines(trend_receipt)
        day = time.strftime("%Y-%m-%d")
        if not any(r.get("day") == day for r in rows):
            cleared_rules = [r["rule"] for r in receipts if r.get("rule") and (r.get("du_kib", 0) > 0 or r.get("free_gain_gib", 0) > 0)]
            trend_mod.record_daily(trend_receipt, MACHINE, end * 1024,
                                   trend_mod.measure_categories(),
                                   trend_mod.recent_cleared(RECEIPTS, time.time()),
                                   cleared_rules, now=time.time())
    if a.run and a.cleanup_interval:
        os.makedirs(os.path.dirname(cleanup_stamp), exist_ok=True)
        with open(cleanup_stamp, "w") as f:
            f.write(str(time.time()))
    verb = "cleared" if a.run else "would clear"
    tool_gain = sum(r.get("free_gain_gib", 0) for r in receipts if r.get("exit") == 0)
    print(f"storage-guard: tools reclaimed {tool_gain:.2f} GiB; {verb} {cleared if a.run else ''} {kib_total / 1048576:.2f} GiB by du; "
          f"free {start / 1048576:.1f} -> {end / 1048576:.1f} GiB (du overcounts APFS clones; the df change is the truth)")
    return 1 if tool_failures else 0


if __name__ == "__main__":
    sys.exit(main())
