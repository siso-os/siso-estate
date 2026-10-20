#!/usr/bin/env python3
"""Record a cheap, bounded 24-hour storage trend for the machine status cards."""
import argparse
import json
import os
import time
import fcntl
import subprocess, glob

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".local", "state", "estate")
TREND = os.path.join(STATE_DIR, "storage-trend.json")


def default_path(machine):
    return TREND if machine == "laptop" else os.path.join(STATE_DIR, "storage-trend-%s.json" % machine)


def load(path, default=None):
    try:
        with open(path) as f:
            value = json.load(f)
        return value
    except (OSError, ValueError):
        return default


def recent_cleared(receipts, now=None, window=24 * 3600):
    """Return GiB cleared by guard receipts in the requested window."""
    now = time.time() if now is None else now
    total = 0
    for row in load_lines(receipts):
        try:
            stamp = time.mktime(time.strptime(row["at"][:19], "%Y-%m-%dT%H:%M:%S"))
            if 0 <= now - stamp <= window and (row.get("run") is True or "free_gain_gib" in row):
                if "du_kib" in row:
                    total += int(row.get("du_kib", 0)) * 1024
                else:
                    total += float(row.get("free_gain_gib", 0)) * 2**30
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return total / 2**30


def measure_categories(home=HOME):
    """Bounded, labeled du probes; never emits private path names."""
    patterns = {"tmp": "/private/tmp", "caches": os.path.join(home, "Library", "Caches")}
    result = {}
    for label, path in patterns.items():
        if not os.path.exists(path):
            continue
        try:
            out = subprocess.run(["du", "-sk", "-x", path], capture_output=True, text=True, timeout=30).stdout
            result[label] = int(out.split()[0]) * 1024 if out else 0
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            continue
    wt = os.path.join(home, "SISO_Workspace", "_data", "worktrees")
    for repo in glob.glob(os.path.join(wt, "*")):
        if os.path.isdir(repo):
            try:
                out = subprocess.run(["du", "-sk", "-x", repo], capture_output=True, text=True, timeout=30).stdout
                result["private-worktrees" if any(x in os.path.basename(repo).lower() for x in ("life", "whatsapp", "fahmy", "bykonz")) else "worktree:" + os.path.basename(repo)[:60]] = int(out.split()[0]) * 1024 if out else 0
            except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
                pass
    # macOS code-sign clones live beside the per-user temp directory, under X.
    try:
        temp = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True, timeout=10).stdout.strip().rstrip("/")
        clones = os.path.join(os.path.dirname(temp), "X", "com.google.Chrome.code_sign_clone") if temp else ""
        if clones and os.path.isdir(clones):
            out = subprocess.run(["du", "-sk", "-x", clones], capture_output=True, text=True, timeout=30).stdout
            result["browser-clones"] = int(out.split()[0]) * 1024 if out else 0
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    ci = os.path.join(home, "SISO_Workspace", "_data", "siso-ci-work")
    if os.path.isdir(ci):
        try:
            out = subprocess.run(["du", "-sk", "-x", ci], capture_output=True, text=True, timeout=30).stdout
            result["ci-workers"] = int(out.split()[0]) * 1024 if out else 0
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            pass
    return result


def record_daily(path, machine, free_bytes, categories, cleared_gib=0, cleared_rules=(), now=None):
    """Append at most one runtime JSONL row per machine and local day under an exclusive lock."""
    now = time.time() if now is None else now
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        rows = load_lines(path)
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        existing = next((r for r in rows if r.get("machine") == machine and r.get("day") == day), None)
        if existing:
            return existing
        prior = [r for r in rows if r.get("machine") == machine]
        prior = next((r for r in reversed(prior) if now - r["at"] >= 20 * 3600), None)
        days = (now - prior["at"]) / 86400 if prior else 1
        current = {k: round(v / 2**30, 2) for k, v in categories.items()}
        growth = {k: round((v - prior.get("categories_gib", {}).get(k, v)) / days, 2)
                  for k, v in current.items()} if prior else {}
        growers = sorted(({"path": k, "grew_gib": v, "now_gib": current[k]}
                          for k, v in growth.items() if v > 0), key=lambda x: -x["grew_gib"])[:3]
        change = round((free_bytes / 2**30 - prior["free_gib"]) / days, 2) if prior else None
        line = "%s free %.1f GiB (24h %s GiB); top growers: %s; cleared %.1f GiB" % (
            machine, free_bytes / 2**30, "n/a" if change is None else "%+.1f" % change,
            ", ".join("%s +%.1f GiB" % (g["path"], g["grew_gib"]) for g in growers) or "none", cleared_gib)
        row = {"at": int(now), "day": day, "machine": machine, "free_gib": round(free_bytes / 2**30, 2),
               "change_24h_gib": change, "categories_gib": current, "growers": growers,
               "cleared_24h_gib": round(cleared_gib, 2), "cleared_rules": sorted(set(cleared_rules)), "line": line}
        fh.seek(0, 2)
        fh.write(json.dumps(row) + "\n")
        fcntl.flock(fh, fcntl.LOCK_UN)
        return row


def load_lines(path):
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []


def update(machine, free_bytes, growers=(), cleared_gib=0, now=None, path=TREND):
    """Store one observation and return the human-readable daily line and state."""
    now = time.time() if now is None else now
    old = load(path, {}) or {}
    samples = [s for s in old.get("samples", []) if now - s.get("at", now) <= 8 * 86400]
    sample = {"at": int(now), "free_gib": round(free_bytes / 2**30, 2)}
    # Disk-floor runs every few minutes, but the daily trend has one sample per
    # local day. Replacing today's observation keeps repeated runs idempotent.
    today = time.localtime(now)[:3]
    same_day = next((i for i, s in enumerate(samples) if time.localtime(s.get("at", 0))[:3] == today), None)
    if same_day is None:
        samples.append(sample)
    samples = samples[-500:]
    target = now - 86400
    prior = min((s for s in samples[:-1] if s.get("at", now) <= target),
                key=lambda s: abs(s["at"] - target), default=None)
    current = sample["free_gib"]
    change = round(current - prior["free_gib"], 2) if prior else None
    incoming = [dict(g) for g in growers if g.get("grew_gib", 0) > 0]
    top = sorted(incoming or old.get("growers", []),
                 key=lambda g: g.get("grew_gib", 0), reverse=True)[:3]
    state = {"at": int(now), "machine": machine, "free_gib": current,
             "change_24h_gib": change, "growers": top,
             "cleared_24h_gib": round(float(cleared_gib), 2), "samples": samples}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(state, f, indent=1)
    os.replace(path + ".tmp", path)
    delta = "n/a" if change is None else "%+.1f" % change
    grow = ", ".join("%s +%.1f GiB" % (g.get("path", "?"), g["grew_gib"]) for g in top) or "none"
    line = "%s free %.1f GiB (24h %s GiB); top growers: %s; cleared %.1f GiB" % (
        machine, current, delta, grow, float(cleared_gib))
    state["line"] = line
    with open(path + ".tmp", "w") as f:
        json.dump(state, f, indent=1)
    os.replace(path + ".tmp", path)
    return state


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--free-bytes", type=int, required=True)
    ap.add_argument("--growers-json", default="[]")
    ap.add_argument("--cleared-gib", type=float, default=0)
    ap.add_argument("--state", default=None)
    a = ap.parse_args(argv)
    state = update(a.machine, a.free_bytes, json.loads(a.growers_json), a.cleared_gib,
                   path=a.state or default_path(a.machine))
    print(state["line"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
