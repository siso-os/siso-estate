#!/usr/bin/env python3
"""The laptop's free-space floor, read every 5 minutes by launchd, no tokens: a status line anyone can read, and the
folders that grew since the last look.

  disk-floor.py           read free space and swap, size the watched folders when due, write the status (prints it)
  disk-floor.py --show    print the last status without measuring

Writes ~/.local/state/estate/disk-floor.json (always: free, floor, ok, swap, top growers) and, while free is under the
floor, one line per run to ~/.local/state/estate/alerts.log, the console card `estate-disk-floor`, and `disk_alert` on
ESTATE's owner card (~/.local/state/a0/owners/ESTATE.json, read by Agent Zero). Folders are
sized (du, nice 19) at most every 30 min, or every 10 min while under the floor; growers are the biggest increases
between the last two sizings. Agent Zero, 15 Oct: the full disk crashed SISO Code 5 times (ENOSPC) and killed every
agent turn; "under 25 GB free it writes a line Agent Zero and the Agent Base header can read, and it names the top
growers since the last check". Clearing stays with ESTATE (skill estate-space-sweep); this only watches.

It also lists dev servers (vite, next, webpack, astro, storybook, node/bun dev) that have had no client on their
port for over an hour, biggest memory footprint first (resident + swapped), in the status (`idle_dev_servers`), the line and `dev_server_alert` on the
owner card. Agent Zero, 15 Oct: 10 abandoned dev servers held 7 GB of RAM and pushed 11 GB into swap, which shares the
disk. A client is an established connection to the server's listening port (an open tab's HMR socket counts). Idle is
counted from the first run that saw no client. Listing only, never a kill; laptop-health's keep.txt is honoured, and
the live show's Chrome (halo-press) is never a candidate.
"""
import glob, json, os, re, shutil, subprocess, sys, time

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".local", "state", "estate")
STATUS = os.path.join(STATE, "disk-floor.json")
SIZES = os.path.join(STATE, "disk-floor-sizes.json")
ALERTS = os.path.join(STATE, "alerts.log")
OWNER_CARD = os.path.join(HOME, ".local", "state", "a0", "owners", "ESTATE.json")  # Agent Zero reads owner cards
FS = "/System/Volumes/Data"
FLOOR_GIB = 25
EVERY_OK, EVERY_LOW = 1800, 600
DEV = os.path.join(STATE, "disk-floor-dev.json")  # pid -> when it was last seen with a client (or first seen without)
DEV_IDLE = 3600
DEV_RE = re.compile(r"\bvite\b|next dev|next-server|next start|webpack|astro dev|storybook|nuxt|remix dev|"
                    r"\bbun\b.*(--hot|--watch|\bdev\b)|tsx watch|nodemon|\bnode\b.*\bdev\b|serve\.mjs|http\.server")
NEVER = re.compile(r"halo-press")  # the live show's Chrome and helpers: never listed (A0, 15 Oct)
KEEP = os.path.join(HOME, "SISO_Workspace", "SISO_Agents", "laptop-health", "keep.txt")
# each pattern's matches are sized one by one; growers are named at that depth
WATCH = ["~/SISO_Workspace/_data/worktrees/*/*", "~/SISO_Workspace/_data/*", "~/SISO_Workspace/*/*",
         "~/SISO_Workspace/Great_Library_of_SISO/banks/*", "/private/tmp/*", "~/.cache/*", "~/Library/Caches/*",
         "~/.local/state/*", "~/.local/share/*", "~/.codex/*", "~/.claude/projects", "~/.config/claude-*/projects",
         "~/Library/Application Support/*", "~/Library/Containers/*", "~/Downloads", "~/Desktop", "~/Documents",
         "~/Library/pnpm", "~/.npm", "~/.cargo", "~/.rustup"]
SKIP = ("~/SISO_Workspace/_data/worktrees", "~/SISO_Workspace/_data")  # parents of deeper patterns


def gib(b):
    return round(b / 2**30, 1)


def swap_gib():
    out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True).stdout.split()
    try:
        return float(out[out.index("used") + 2].rstrip("M")) / 1024
    except (ValueError, IndexError):
        return None


def paths():
    skip = {os.path.expanduser(s) for s in SKIP}
    seen = []
    for pat in WATCH:
        for p in sorted(glob.glob(os.path.expanduser(pat))):
            if p in skip or os.path.islink(p) or p in seen:
                continue
            if any(p.startswith(s + "/") for s in seen):
                continue
            seen.append(p)
    # a deeper pattern wins: drop a parent when its children are watched
    return [p for p in seen if not any(q.startswith(p + "/") for q in seen)]


def size_all():
    sizes = {}
    for p in paths():
        r = subprocess.run(["nice", "-n", "19", "du", "-sk", "-x", p], capture_output=True, text=True, timeout=600)
        try:
            sizes[p] = int(r.stdout.split()[0]) * 1024
        except (IndexError, ValueError):
            pass
    return sizes


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def keep_patterns():
    try:
        lines = open(KEEP).read().splitlines()
    except OSError:
        return []
    return [re.compile(l.split(" #")[0].strip()) for l in lines if l.strip() and not l.lstrip().startswith("#")]


def age_s(etime):
    d, _, rest = etime.rpartition("-")
    parts = [int(x) for x in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return (int(d) if d else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def lsof_tcp(state):
    """pid -> list of 'n' fields for TCP sockets in the given state."""
    out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:" + state, "-Fpn"], capture_output=True, text=True,
                         timeout=60).stdout
    got, pid = {}, None
    for l in out.splitlines():
        if l.startswith("p"):
            pid = int(l[1:])
        elif l.startswith("n") and pid:
            got.setdefault(pid, []).append(l[1:])
    return got


def footprint_bytes(pid, fallback):
    """macOS phys_footprint (resident + compressed + swapped): RSS reads tiny for a server pushed into swap."""
    try:
        out = subprocess.run(["footprint", "-p", str(pid)], capture_output=True, text=True, timeout=30).stdout
        m = re.search(r"phys_footprint:\s*([\d.]+)\s*([KMG])B", out)
        return int(float(m.group(1)) * {"K": 2**10, "M": 2**20, "G": 2**30}[m.group(2)]) if m else fallback
    except (OSError, subprocess.SubprocessError):
        return fallback


def idle_dev_servers(now):
    """Dev servers listening with no client for DEV_IDLE seconds, biggest RAM first."""
    rows = {}
    for l in subprocess.run(["ps", "-axo", "pid=,rss=,etime=,command="], capture_output=True, text=True).stdout.splitlines():
        f = l.split(None, 3)
        if len(f) == 4:
            rows[int(f[0])] = {"rss": int(f[1]) * 1024, "age": age_s(f[2]), "cmd": f[3]}
    keep = keep_patterns()
    listen = {p: {n.rsplit(":", 1)[1] for n in ns} for p, ns in lsof_tcp("LISTEN").items()}
    conns = lsof_tcp("ESTABLISHED")
    seen = load(DEV, {})
    out, nxt = [], {}
    for p, ports in listen.items():
        r = rows.get(p)
        if not r or not DEV_RE.search(r["cmd"]) or NEVER.search(r["cmd"]) or any(k.search(r["cmd"]) for k in keep):
            continue
        has_client = any("->" in n and n.split("->")[0].rsplit(":", 1)[1] in ports for n in conns.get(p, []))
        key = "%d:%s" % (p, r["cmd"][:200])  # pid plus command, so a reused pid starts fresh
        since = now if has_client else seen.get(key, max(now - r["age"], now))
        nxt[key] = since
        if now - since < DEV_IDLE:
            continue
        cwd = subprocess.run(["lsof", "-a", "-p", str(p), "-d", "cwd", "-Fn"], capture_output=True, text=True,
                             timeout=30).stdout.partition("\nn")[2].strip()
        if NEVER.search(cwd) or any(k.search(cwd) for k in keep):
            continue
        out.append({"pid": p, "ram_gib": round(footprint_bytes(p, r["rss"]) / 2**30, 2), "ports": sorted(ports), "idle_min": (now - since) // 60,
                    "cwd": cwd.replace(HOME, "~"), "cmd": r["cmd"][:160]})
    with open(DEV + ".tmp", "w") as f:
        json.dump(nxt, f)
    os.replace(DEV + ".tmp", DEV)
    return sorted(out, key=lambda d: -d["ram_gib"])


def card(st):
    post = os.path.join(HOME, ".claude", "console", "bin", "console-post")
    if not os.path.exists(post):
        return
    grow = "; ".join("%s +%.1f GB" % (g["path"].replace(HOME, "~"), g["grew_gib"]) for g in st["growers"][:3])
    body = "Laptop %.1f GB free (floor %d), swap %.1f GB. Grew: %s. ESTATE clears (skill estate-space-sweep)." % (
        st["free_gib"], FLOOR_GIB, st["swap_gib"] or 0, grow or "not sized yet")
    subprocess.run([post, "-a", "ESTATE", "-k", "status", "-i", "estate-disk-floor", "-t", "Laptop disk under floor", body],
                   capture_output=True, timeout=30)


def owner_card(st):
    """ESTATE's owner card carries `disk_alert` while the laptop is under its floor and `dev_server_alert` while idle
    dev servers hold RAM; each goes when its cause does."""
    card_ = load(OWNER_CARD, None)
    if not isinstance(card_, dict):
        return
    want = {"disk_alert": None if st["ok"] else st["line"], "dev_server_alert": st["dev_line"] or None}
    if all(card_.get(k) == v for k, v in want.items()):
        return
    for k, v in want.items():
        if v is None:
            card_.pop(k, None)
        else:
            card_[k] = v
    with open(OWNER_CARD + ".tmp", "w") as f:
        json.dump(card_, f, indent=2)
    os.replace(OWNER_CARD + ".tmp", OWNER_CARD)


def main():
    if "--show" in sys.argv:
        print(json.dumps(load(STATUS, {}), indent=1))
        return 0
    os.makedirs(STATE, exist_ok=True)
    now = int(time.time())
    free = shutil.disk_usage(FS).free
    low = free < FLOOR_GIB * 2**30
    sz = load(SIZES, {"runs": []})
    runs = sz["runs"]
    if not runs or now - runs[-1]["t"] >= (EVERY_LOW if low else EVERY_OK):
        runs.append({"t": now, "sizes": size_all()})
        sz["runs"] = runs[-2:]
        with open(SIZES + ".tmp", "w") as f:
            json.dump(sz, f)
        os.replace(SIZES + ".tmp", SIZES)
    growers, since = [], None
    if len(sz["runs"]) == 2:
        a, b = sz["runs"]
        since = time.strftime("%H:%M", time.localtime(a["t"]))
        d = [(b["sizes"][p] - a["sizes"].get(p, 0), p) for p in b["sizes"]]
        growers = [{"path": p, "grew_gib": gib(x), "now_gib": gib(b["sizes"][p]), "new": p not in a["sizes"]}
                   for x, p in sorted(d, reverse=True)[:5] if x >= 200 * 2**20]
    st = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "machine": "laptop", "free_gib": gib(free), "floor_gib": FLOOR_GIB,
          "ok": not low, "swap_gib": round(swap_gib() or 0, 1), "growers_since": since, "growers": growers,
          "line": ("OK laptop %.1f GB free" % gib(free)) if not low else
                  ("LOW laptop %.1f GB free (floor %d); grew since %s: %s" % (gib(free), FLOOR_GIB, since or "-", ", ".join(
                      "%s +%.1f" % (g["path"].replace(HOME, "~"), g["grew_gib"]) for g in growers[:3]) or "not sized yet"))}
    dev = idle_dev_servers(now)
    st["idle_dev_servers"] = dev
    st["idle_dev_ram_gib"] = round(sum(d["ram_gib"] for d in dev), 1)
    st["dev_line"] = ("%d dev servers with no client for 1 h+ hold %.1f GB RAM: %s" % (len(dev), st["idle_dev_ram_gib"], ", ".join(
        "pid %d :%s %s %.1f GB" % (d["pid"], ",".join(d["ports"]), os.path.basename(d["cwd"]) or "?", d["ram_gib"])
        for d in dev[:3]))) if dev else ""
    if dev:
        st["line"] += "; " + st["dev_line"]
    with open(STATUS + ".tmp", "w") as f:
        json.dump(st, f, indent=1)
    os.replace(STATUS + ".tmp", STATUS)
    owner_card(st)
    if low:
        with open(ALERTS, "a") as f:
            f.write("%s ESTATE %s\n" % (st["at"], st["line"]))
        card(st)
    print(st["line"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
