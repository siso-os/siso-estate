#!/usr/bin/env python3
"""The nervous system (goal-2050 P3): the estate learns about a change as it happens, not at the next hourly run.

  watch.py serve        run forever (launchd com.siso.estate-watch): macOS FSEvents on ~/SISO_Workspace; a repo that
                        appears, moves, disappears, commits, switches branch or pushes is re-described
                        (tools/inventory.py describe) into machines/<m>/repos.json, which `estate where` reads, and the
                        register (tools/register.py) is rebuilt. Each change is a line in machines/<m>/watch.jsonl with
                        its lag: seconds from the file event to the record being written.
  watch.py status       the last changes and their lags (median and worst of the last 50)

Events inside node_modules, build output and the vault (_archive) are ignored. The hourly inventory stays the full
recount; this keeps the record right between counts.
"""
import ctypes, ctypes.util, json, os, statistics, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
WS = os.path.expanduser("~/SISO_Workspace")
MACHINE = os.environ.get("SISO_MACHINE") or ("laptop" if sys.platform == "darwin" else os.uname().nodename)
REPOS = os.path.join(REPO, "machines", MACHINE, "repos.json")
LOG = os.path.join(REPO, "machines", MACHINE, "watch.jsonl")
NOISE = ("/node_modules/", "/.next/", "/dist/", "/build/", "/.venv/", "/__pycache__/", "/.turbo/", "/.cache/",
         "/_archive/", "/.git/objects/", "/.git/lfs/")
GIT_SIGNAL = ("/.git/HEAD", "/.git/packed-refs", "/.git/logs/HEAD", "/.git/config")   # never .git/index: git status writes it
SETTLE = 3.0          # seconds of quiet before acting on a burst (a clone or a checkout fires thousands of events)
MAXWAIT = 10.0        # a folder that never goes quiet (an agent writing all day) is acted on after this anyway

pending, lock, wake = {}, threading.Lock(), threading.Event()


def on_event(path):
    p = path.rstrip("/") + "/"
    if any(n in p for n in NOISE) or "/.t/" in p:
        return
    with lock:
        pending.setdefault(path.rstrip("/"), time.time())
    wake.set()


def fsevents(paths, latency=1.0):
    """Minimal FSEvents binding: calls on_event(path) for each changed file or folder (macOS)."""
    cs = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreServices"))
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFArrayCreate.restype = ctypes.c_void_p
    cf.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p]
    cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
    cf.CFRunLoopRun.restype = None
    kCFRunLoopDefaultMode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
    CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_char_p),
                          ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint64))

    def cb(stream, info, n, paths_, flags, ids):
        for i in range(n):
            on_event(paths_[i].decode("utf-8", "replace"))

    callback = CB(cb)
    cs.FSEventStreamCreate.restype = ctypes.c_void_p
    cs.FSEventStreamCreate.argtypes = [ctypes.c_void_p, CB, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64,
                                       ctypes.c_double, ctypes.c_uint32]
    cs.FSEventStreamScheduleWithRunLoop.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    cs.FSEventStreamStart.argtypes = [ctypes.c_void_p]
    arr = (ctypes.c_void_p * len(paths))(*[cf.CFStringCreateWithCString(None, p.encode(), 0x08000100) for p in paths])
    cfpaths = cf.CFArrayCreate(None, arr, len(paths), None)
    since_now = 0xFFFFFFFFFFFFFFFF
    stream = cs.FSEventStreamCreate(None, callback, None, cfpaths, since_now, latency, 0x2 | 0x10)  # NoDefer, FileEvents
    cs.FSEventStreamScheduleWithRunLoop(stream, cf.CFRunLoopGetCurrent(), kCFRunLoopDefaultMode)
    if not cs.FSEventStreamStart(stream):
        raise SystemExit("FSEventStreamStart failed")
    fsevents.keep = (callback, stream)                 # the callback must outlive the run loop
    cf.CFRunLoopRun()


def load_repos():
    try:
        return json.load(open(REPOS))
    except (OSError, ValueError):
        return {"machine": MACHINE, "repos": []}


def save_repos(doc):
    doc["count"] = len(doc["repos"])
    doc["watched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(REPOS + ".tmp", "w") as f:
        json.dump(doc, f, indent=1)
    if os.path.exists(REPOS):
        import shutil
        shutil.copymode(REPOS, REPOS + ".tmp")
    os.replace(REPOS + ".tmp", REPOS)


def repo_of(path, known):
    """The known repo that holds path (its .git or its tree), or None."""
    cur = path.split("/.git/")[0] if "/.git/" in path + "/" else path
    cur = cur[:-5] if cur.endswith("/.git") else cur
    while cur.startswith(WS):
        if cur in known:
            return cur
        cur = os.path.dirname(cur)
    return None


def changes(batch, known):
    """(path -> kind) for a settled batch of changed files and folders."""
    out = {}
    for p in batch:
        if "/.git/" in p + "/":                      # inside a .git: a commit, a push, a checkout, or a new repo
            root = p.split("/.git")[0]
            tail = p[len(root):]
            if root in known:
                if tail in GIT_SIGNAL or tail.startswith("/.git/refs/"):
                    out.setdefault(root, "git")
            elif root.startswith(WS + "/") and os.path.exists(os.path.join(root, ".git")):
                out[root] = "new"
            continue
        if p in known and not os.path.exists(os.path.join(p, ".git")):
            out[p] = "gone"
        for k in known:                              # a folder moved or removed with repos under it
            if k.startswith(p + "/") and not os.path.exists(k):
                out[k] = "gone"
        if os.path.isdir(p) and not os.path.islink(p):
            for d, depth in [(p, 0)]:                # a folder moved in carrying repos (two levels down)
                stack = [(d, depth)]
                while stack:
                    cur, dep = stack.pop()
                    if cur not in known and os.path.exists(os.path.join(cur, ".git")):
                        out[cur] = "new"
                        continue
                    if dep < 2:
                        try:
                            stack += [(e.path, dep + 1) for e in os.scandir(cur)
                                      if e.is_dir(follow_symlinks=False) and e.name not in ("node_modules", ".git")]
                        except OSError:
                            pass
    return out


def apply(found, batch):
    from inventory import describe
    doc = load_repos()
    by = {r["path"]: i for i, r in enumerate(doc["repos"])}
    lines = []
    for path, kind in found.items():
        if kind == "gone":
            if path in by:
                doc["repos"][by[path]] = None
        else:
            rec = describe(path)
            if path in by:
                doc["repos"][by[path]] = rec
            else:
                doc["repos"].append(rec)
        lines.append({"kind": kind, "path": path})
    doc["repos"] = sorted((r for r in doc["repos"] if r), key=lambda r: r["path"])
    save_repos(doc)
    subprocess.run([sys.executable, os.path.join(HERE, "register.py")], capture_output=True, timeout=120)
    now = time.time()
    with open(LOG, "a") as f:
        for l in lines:
            first = min((t for p, t in batch.items() if p == l["path"] or p.startswith(l["path"] + "/")), default=now)
            l.update(at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), lag_s=round(now - first, 1))
            f.write(json.dumps(l) + "\n")
    return lines


def worker():
    while True:
        wake.wait()
        time.sleep(SETTLE)
        with lock:
            if pending and time.time() - max(pending.values()) < SETTLE and time.time() - min(pending.values()) < MAXWAIT:
                continue                             # still busy: wait for quiet, but never past MAXWAIT
            batch, first = dict(pending), min(pending.values()) if pending else time.time()
            pending.clear()
            wake.clear()
        known = {r["path"] for r in load_repos()["repos"]}
        found = changes(batch, known)
        if found:
            try:
                for l in apply(found, batch):
                    print(f"{time.strftime('%H:%M:%S')} {l['kind']:<4} {l['path']}  ({l['lag_s']} s)", flush=True)
            except Exception as e:                   # one bad repo never stops the watch
                print(f"{time.strftime('%H:%M:%S')} error {e!r}", flush=True)


def status():
    try:
        rows = [json.loads(l) for l in open(LOG) if l.strip()][-50:]
    except OSError:
        rows = []
    if not rows:
        print("no changes recorded yet")
        return 0
    for r in rows[-10:]:
        print(f"{r['at']}  {r['kind']:<4} {r['path'].replace(os.path.expanduser('~'), '~')}  {r['lag_s']} s")
    lags = [r["lag_s"] for r in rows]
    print(f"last {len(lags)}: median {statistics.median(lags)} s, worst {max(lags)} s")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "-h"
    if cmd == "serve":
        threading.Thread(target=worker, daemon=True).start()
        print(f"watching {WS}", flush=True)
        fsevents([WS])
    elif cmd == "status":
        sys.exit(status())
    else:
        print(__doc__)
