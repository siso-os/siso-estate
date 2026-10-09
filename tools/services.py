#!/usr/bin/env python3
"""Every SISO launchd job from one manifest (goal A11): plan/services.json names each job, its owner and what it does;
plan/launchd/<label>.plist is its source. ~/Library/LaunchAgents holds generated copies.

  services.py check                   drift: a job on disk not in the manifest, an installed copy that differs from its
                                      source, a job not loaded, a program that is gone (exit 1 on any)
  services.py adopt LABEL --owner SEAT --what "..." [--run]   take an installed job into the manifest (source = its copy)
  services.py install [LABEL...] [--run]   write the source over the installed copy and reload the job where they differ

A job changes by editing its source here and running install; an owner edits it the same way (or asks ESTATE).
Retire a job with `estate move ~/Library/LaunchAgents/<label>.plist _archive/<date>-launchd-retired/ --no-link` after
`launchctl bootout`, then drop it from the manifest.
"""
import argparse, json, os, plistlib, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MANIFEST = os.path.join(REPO, "plan", "services.json")
SOURCES = os.path.join(REPO, "plan", "launchd")
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
PREFIX = "com.siso."
UID = os.getuid()


def load_manifest():
    with open(MANIFEST) as f:
        return json.load(f)


def save_manifest(m):
    m["services"] = sorted(m["services"], key=lambda s: s["label"])
    with open(MANIFEST + ".tmp", "w") as f:
        json.dump(m, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(MANIFEST + ".tmp", MANIFEST)


def loaded():
    """label -> (pid or None, last exit) for every job launchd knows in this session."""
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout.splitlines()[1:]
    res = {}
    for line in out:
        parts = line.split("\t")
        if len(parts) == 3:
            res[parts[2]] = (None if parts[0] == "-" else parts[0], parts[1])
    return res


def program_of(plist):
    """The file a job runs: the script after an interpreter or `-c`, else the program itself."""
    args = plist.get("ProgramArguments") or [plist.get("Program", "")]
    for i, a in enumerate(args[1:], 1):
        if args[i - 1] in ("-c", "-lc"):                   # bash -c "…": the command's first word, if it is a path
            w = a.split()[0] if a.split() else ""
            if w.startswith(("/", "~/")):
                return os.path.expanduser(w)
            continue
        if a.startswith(("/", "~/")) and not a.startswith("/usr/bin/"):
            return os.path.expanduser(a)
    return os.path.expanduser(args[0]) if args else ""


def installed():
    return sorted(f[:-6] for f in os.listdir(AGENTS) if f.startswith(PREFIX) and f.endswith(".plist"))


def same(a, b):
    try:
        with open(a, "rb") as x, open(b, "rb") as y:
            return x.read() == y.read()
    except OSError:
        return False


def check():
    m = load_manifest()
    known = {s["label"]: s for s in m["services"]}
    live = loaded()
    problems = []
    for label in installed():
        if label not in known:
            problems.append((label, "on disk, not in the manifest (services.py adopt it, or retire it)"))
    for label, s in sorted(known.items()):
        src = os.path.join(SOURCES, label + ".plist")
        dst = os.path.join(AGENTS, label + ".plist")
        if not os.path.exists(src):
            problems.append((label, "in the manifest, no source in plan/launchd"))
            continue
        if not os.path.exists(dst):
            problems.append((label, "not installed (services.py install)"))
            continue
        if not same(src, dst):
            problems.append((label, "installed copy differs from its source (install it, or adopt the change)"))
        with open(src, "rb") as f:
            p = plistlib.load(f)
        prog = program_of(p)
        if prog and not os.path.exists(prog):
            problems.append((label, f"its program is gone: {prog}"))
        if label not in live and s.get("state", "on") == "on":
            problems.append((label, "not loaded"))
    for label, why in problems:
        print(f"DRIFT {label}: {why}")
    print(f"services: {len(known)} in the manifest, {len(installed())} installed, {len(problems)} drift")
    return 1 if problems else 0


def adopt(label, owner, what, run):
    dst = os.path.join(AGENTS, label + ".plist")
    if not os.path.exists(dst):
        raise SystemExit(f"{label}: not installed")
    m = load_manifest()
    if any(s["label"] == label for s in m["services"]):
        raise SystemExit(f"{label}: already in the manifest")
    if not run:
        print(f"plan: adopt {label} (owner {owner}): {what}")
        return 0
    os.makedirs(SOURCES, exist_ok=True)
    shutil.copy2(dst, os.path.join(SOURCES, label + ".plist"))
    m["services"].append({"label": label, "owner": owner, "what": what, "state": "on"})
    save_manifest(m)
    print(f"adopted {label}")
    return 0


def install(labels, run):
    m = load_manifest()
    known = {s["label"] for s in m["services"]}
    for label in labels or sorted(known):
        if label not in known:
            print(f"skip {label}: not in the manifest")
            continue
        src = os.path.join(SOURCES, label + ".plist")
        dst = os.path.join(AGENTS, label + ".plist")
        if same(src, dst):
            continue
        with open(src, "rb") as f:
            plistlib.load(f)                                # refuse a source that is not a valid plist
        if not run:
            print(f"plan: install {label}")
            continue
        subprocess.run(["launchctl", "bootout", f"gui/{UID}/{label}"], capture_output=True)
        shutil.copy2(src, dst + ".tmp")
        os.replace(dst + ".tmp", dst)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{UID}", dst], capture_output=True, text=True)
        print(f"installed {label}" + ("" if r.returncode == 0 else f" (bootstrap: {r.stderr.strip()[:160]})"))
    return 0


EPHEMERAL = 49152                                     # IANA dynamic range start; city.py skips these too
DEV = ("node", "python", "Python", "bun", "deno", "vite", "next", "ruby", "uvicorn", "gunicorn")


def ports():
    """Listening TCP ports (lsof) against plan/ports.json: collisions (two processes on one port) and dev servers
    nobody registered (P5, the utility grid). Writes machines/<m>/ports.json; exit 1 on a collision."""
    reg = json.load(open(os.path.join(REPO, "plan", "ports.json")))
    known = {**reg.get("machine", {}), **reg.get("ports", {})}
    ranges = [(tuple(map(int, k.split("-"))), v) for k, v in reg.get("ranges", {}).items() if not k.startswith("_")]
    try:
        running = json.load(open(os.path.join(REPO, "machines", os.environ.get("SISO_MACHINE") or ("laptop" if sys.platform == "darwin" else os.uname().nodename), "running.json")))
    except (OSError, ValueError):
        running = {}
    out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
    by = {}
    for line in out.splitlines()[1:]:
        f = line.split()
        if len(f) < 9:
            continue
        port = f[-2].rsplit(":", 1)[-1]
        by.setdefault(port, {})[f[1]] = {"cmd": f[0], "addr": f[-2].rsplit(":", 1)[0]}
    rows, collisions, unreg = [], [], []
    for port in sorted(by, key=int):
        procs = by[port]
        cmds = sorted({v["cmd"] for v in procs.values()})
        reg_as = known.get(port, {}).get("service") if isinstance(known.get(port), dict) else known.get(port)
        if not reg_as:
            reg_as = next((v["service"] + " (range)" for (lo, hi), v in ranges if lo <= int(port) <= hi), None)
        if not reg_as:                              # started by `estate run`: named by its project
            reg_as = next((f"estate run: {r['project']}" for r in running.values() if r["port"] == int(port)), None)
        if not reg_as and int(port) >= EPHEMERAL:
            reg_as = "ephemeral (OS-assigned)"      # CDP, socks tunnels: handed out by the OS, never registered
        row = {"port": int(port), "pids": sorted(procs, key=int), "cmds": cmds,
               "addr": sorted({v["addr"] for v in procs.values()}), "registered": reg_as}
        if len(procs) > 1 and len(cmds) > 1:
            collisions.append(row)
        if not reg_as and any(c.startswith(DEV) for c in cmds):
            unreg.append(row)
        rows.append(row)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "listening": rows,
           "collisions": [r["port"] for r in collisions], "unregistered_dev_servers": [r["port"] for r in unreg]}
    with open(os.path.join(REPO, "machines", os.environ.get("SISO_MACHINE") or ("laptop" if sys.platform == "darwin" else os.uname().nodename), "ports.json"), "w") as fh:
        json.dump(rec, fh, indent=1)
    for r in rows:
        tag = "COLLISION" if r in collisions else ("UNREGISTERED" if r in unreg else ("ok" if r["registered"] else "-"))
        print(f"{r['port']:>6}  {tag:<12} {','.join(r['cmds'])[:24]:<24} pid {','.join(r['pids'])[:20]:<20} {r['registered'] or ''}")
    print(f"{len(rows)} ports listening, {len(collisions)} collisions, {len(unreg)} dev servers not in plan/ports.json")
    return 1 if collisions else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "adopt", "install", "ports"])
    ap.add_argument("labels", nargs="*")
    ap.add_argument("--owner")
    ap.add_argument("--what")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "check":
        return check()
    if a.cmd == "ports":
        return ports()
    if a.cmd == "adopt":
        if len(a.labels) != 1 or not a.owner or not a.what:
            raise SystemExit("adopt LABEL --owner SEAT --what '...'")
        return adopt(a.labels[0], a.owner, a.what, a.run)
    return install(a.labels, a.run)


if __name__ == "__main__":
    sys.exit(main())
