#!/usr/bin/env python3
"""Start a project on its registered port, know who started it, stop it cleanly (goal-2050 P5, the utility grid).

  estate run <project words> [--port N] [-- CMD...]   start it: the project is found like `estate path`; its port is
                                  the one plan/ports.json gives it, or the next free one in the estate-run block
                                  (6100-6199), written there so it keeps it; the command is its plan/runs.json entry,
                                  else its package.json dev/start script (vite/next/astro get --port), else CMD
  estate stop <project words>     stop the whole process group, then drop the record
  estate running                  what estate run started on this machine, dead ones pruned

The record is machines/<m>/running.json: project, pid, port, url, command, log, which agent started it and when.
Logs go to ~/SISO_Workspace/_data/logs/run/<name>.log. `services.py ports` names these listeners by project.
"""
import json, os, signal, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.expanduser("~/SISO_Workspace")
PORTS = os.path.join(REPO, "plan", "ports.json")
RUNS = os.path.join(REPO, "plan", "runs.json")
BLOCK = (6100, 6199)
LOGS = os.path.join(WS, "_data", "logs", "run")


def machine():
    return os.environ.get("SISO_MACHINE") or ("laptop" if sys.platform == "darwin" else os.uname().nodename)


RUNNING = os.path.join(REPO, "machines", machine(), "running.json")


def load(p, default):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def save(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")
    if os.path.exists(p):
        import shutil
        shutil.copymode(p, tmp)
    os.replace(tmp, p)


def rel(path):
    return os.path.relpath(path, WS) if path.startswith(WS + "/") else path


def resolve(words):
    if len(words) == 1 and os.path.isdir(os.path.expanduser(words[0])):
        return os.path.realpath(os.path.expanduser(words[0]))
    r = subprocess.run([os.path.join(REPO, "bin", "estate"), "path", *words], capture_output=True, text=True)
    p = r.stdout.strip()
    if r.returncode or not os.path.isdir(p):
        raise SystemExit(f"no project found for '{' '.join(words)}' (try `estate where {' '.join(words)}`)")
    return p


def listening(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def port_for(path, name, want):
    reg = load(PORTS, {})
    ports = reg.setdefault("ports", {})
    key = rel(path)
    mine = [int(p) for p, v in ports.items() if isinstance(v, dict) and v.get("project") == key]
    if want:
        other = ports.get(str(want))
        if other and other.get("project") != key:
            raise SystemExit(f"port {want} is registered to {other.get('service')}; pick another or drop --port")
        port = want
    elif mine:
        return mine[0]
    else:
        port = next((p for p in range(BLOCK[0], BLOCK[1] + 1) if str(p) not in ports and not listening(p)), None)
        if port is None:
            raise SystemExit(f"the estate-run block {BLOCK[0]}-{BLOCK[1]} is full")
    ports[str(port)] = {"service": name, "project": key, "owner": "its building (estate run)", "runs": "estate run " + name}
    reg["ports"] = dict(sorted(ports.items(), key=lambda kv: int(kv[0])))
    save(PORTS, reg)
    return port


def command_for(path, port):
    runs = load(RUNS, {}).get(rel(path))
    if runs:
        return [c.replace("{port}", str(port)) for c in runs["cmd"]], runs.get("url", "http://127.0.0.1:{port}/").replace("{port}", str(port))
    pj = os.path.join(path, "package.json")
    if os.path.exists(pj):
        scripts = load(pj, {}).get("scripts", {})
        script = "dev" if "dev" in scripts else "start" if "start" in scripts else None
        if script:
            pm = "pnpm" if os.path.exists(os.path.join(path, "pnpm-lock.yaml")) else \
                 "bun" if os.path.exists(os.path.join(path, "bun.lockb")) or os.path.exists(os.path.join(path, "bun.lock")) else "npm"
            body = scripts[script]
            extra = ["--port", str(port), "--strictPort"] if "vite" in body else \
                    ["-p", str(port)] if "next " in body + " " else ["--port", str(port)] if "astro" in body else []
            cmd = [pm, "run", script] + (["--"] + extra if extra and pm == "npm" else extra)
            return cmd, f"http://127.0.0.1:{port}/"
    if os.path.exists(os.path.join(path, "manage.py")):
        return [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}"], f"http://127.0.0.1:{port}/"
    return None, None


def who():
    for k in ("ESTATE_AGENT", "HERDR_AGENT_NAME", "HERDR_AGENT"):
        if os.environ.get(k):
            return os.environ[k]
    pane = os.environ.get("HERDR_PANE_ID")
    if pane:                                           # the herdr agent's name, else its pane
        try:
            out = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=5).stdout
            for a in json.loads(out)["result"]["agents"]:
                if a.get("pane_id") == pane:
                    return a.get("name") or a.get("terminal_title_stripped") or "herdr pane " + pane
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass
        return "herdr pane " + pane
    if os.environ.get("CLAUDECODE"):
        return "claude " + os.path.basename(os.environ.get("PWD", ""))
    if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_HOME"):
        return "codex"
    return os.environ.get("USER", "?")


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def live():
    rec = load(RUNNING, {})
    dead = [k for k, v in rec.items() if not alive(v["pid"])]
    for k in dead:
        rec.pop(k)
    if dead:
        save(RUNNING, rec)
    return rec


def start(args):
    want, cmd = None, None
    if "--" in args:
        i = args.index("--")
        args, cmd = args[:i], args[i + 1:]
    if "--port" in args:
        i = args.index("--port")
        want = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    if not args:
        raise SystemExit(__doc__)
    path = resolve(args)
    name = os.path.basename(path)
    rec = live()
    if rel(path) in rec:
        r = rec[rel(path)]
        print(f"already running: {name} pid {r['pid']} on {r['url']} (started by {r['agent']} at {r['started']})")
        return 0
    port = port_for(path, name, want)
    if listening(port):
        raise SystemExit(f"port {port} (registered to {name}) is already taken by something estate run did not start: "
                         f"`lsof -nP -iTCP:{port}`")
    url = f"http://127.0.0.1:{port}/"
    if cmd:
        cmd = [c.replace("{port}", str(port)) for c in cmd]
    else:
        cmd, url = command_for(path, port)
    if not cmd:
        raise SystemExit(f"no way to start {name}: no package.json dev/start script, no manage.py; add it to "
                         f"plan/runs.json as \"{rel(path)}\": {{\"cmd\": [..., \"{{port}}\"]}} or pass `-- CMD`")
    os.makedirs(LOGS, exist_ok=True)
    log = os.path.join(LOGS, f"{name}.log")
    env = dict(os.environ, PORT=str(port), HOST="127.0.0.1")
    with open(log, "ab") as fh:
        fh.write(f"\n== estate run {name} on {port} by {who()} at {time.strftime('%Y-%m-%d %H:%M:%S')}: {' '.join(cmd)}\n".encode())
        p = subprocess.Popen(cmd, cwd=path, env=env, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
    rec[rel(path)] = {"project": name, "path": rel(path), "pid": p.pid, "port": port, "url": url, "cmd": cmd,
                      "log": log, "agent": who(), "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    save(RUNNING, rec)
    for _ in range(120):                               # up to 60 s for it to listen
        if listening(port):
            print(f"{name} is up on {url} (pid {p.pid}, log {log.replace(os.path.expanduser('~'), '~')})")
            return 0
        if p.poll() is not None:
            live()
            tail = open(log, errors="replace").read()[-600:]
            raise SystemExit(f"{name} exited with {p.returncode} before listening on {port}; log tail:\n{tail}")
        time.sleep(0.5)
    print(f"{name} started (pid {p.pid}) but is not listening on {port} after 60 s; it may use another port. "
          f"Log: {log}")
    return 1


def stop(args):
    rec = live()
    if not args:
        raise SystemExit("estate stop <project words>")
    key = next((k for k in rec if k == " ".join(args) or rec[k]["project"] == " ".join(args)), None)
    if not key:
        try:
            key = rel(resolve(args))
        except SystemExit:
            key = None
    if key not in rec:
        print(f"estate run is not running '{' '.join(args)}' here (estate running)")
        return 1
    r = rec[key]
    try:
        os.killpg(r["pid"], signal.SIGTERM)
    except OSError:
        pass
    for _ in range(20):
        if not alive(r["pid"]) and not listening(r["port"]):
            break
        time.sleep(0.5)
    else:
        try:
            os.killpg(r["pid"], signal.SIGKILL)
        except OSError:
            pass
    rec.pop(key)
    save(RUNNING, rec)
    print(f"stopped {r['project']} (pid {r['pid']}, port {r['port']}{', still listening!' if listening(r['port']) else ' free'})")
    return 0


def running(_):
    rec = live()
    if not rec:
        print("nothing started by estate run is running here")
    for r in rec.values():
        print(f"{r['project']:<32} {r['url']:<26} pid {r['pid']:<7} by {r['agent']} since {r['started']}")
    return 0


if __name__ == "__main__":
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("-h", [])
    if cmd in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    sys.exit({"start": start, "stop": stop, "running": running}[cmd](rest))
