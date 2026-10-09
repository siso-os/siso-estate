#!/usr/bin/env python3
"""The laptop's storage map: every gigabyte, where it is, who owns it, how fast it grows, and its rule.

  storage-map.py            one throttled du pass over the whole Data volume, then render docs/STORAGE-MAP.md and
                            write machines/<m>/storage-tree.json; each run's sizes go to machines/<m>/storage-history.jsonl
  storage-map.py --render   render again from the last tree (no du)

Shaan, 8 Oct: "map out absolutely every gigabyte on our laptop, see where it is, see where it's hidden dot folders,
see where it's work trees, and then note it down in the estate ... so it's always tracked". The disk adds up: APFS
volumes (macOS, Preboot, Recovery, swap, Data) from diskutil, then the Data volume as a tree down to 0.5 GiB with a
"smaller items" line under each folder, so nothing is left out. Owners and rules come from plan/storage-map.json
(longest matching path); a folder of 1 GiB or more that matches none is listed as having no owner yet. Every worktree
and every hidden folder in ~ gets its own line. GB/day compares with the newest run at least 20 hours older; "since
last" with the run before this one. du counts APFS clones in full, so folders can add to slightly more than the volume.
"""
import argparse, json, os, re, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
DATA = "/System/Volumes/Data"
MAP = os.path.join(REPO, "plan", "storage-map.json")
DOC = os.path.join(REPO, "docs", "STORAGE-MAP.md")
HOST = socket.gethostname().lower()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("laptop" if HOST.startswith("shaans-macbook") else "mini" if "mini" in HOST else HOST.split(".")[0])
if MACHINE != "laptop":  # the same map on every machine (Shaan, 8 Oct ~21:00: "laptop and Mac mini should both be his domain")
    DOC = os.path.join(REPO, "docs", f"STORAGE-MAP-{MACHINE}.md")
TREE = os.path.join(REPO, "machines", MACHINE, "storage-tree.json")
HIST = os.path.join(REPO, "machines", MACHINE, "storage-history.jsonl")
WT = os.path.join(HOME, "SISO_Workspace", "_data", "worktrees")
GIB = 1048576  # KiB
SHOW = GIB // 2  # a folder gets its own line from 0.5 GiB
KEEP = 100 * 1024  # sizes kept in history from 100 MiB
DEPTH = 9  # deep enough for Data/Users/<u>/SISO_Workspace/_data/worktrees/<repo>/<lane>/<dir>


def nice(path):
    p = path[len(DATA):] if path.startswith(DATA + "/") else path
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def volumes():
    out = subprocess.run(["diskutil", "apfs", "list"], capture_output=True, text=True).stdout
    vols, cur = [], {}
    for line in out.splitlines():
        if m := re.search(r"Capacity In Use By Volumes:\s+(\d+) B", line):
            vols.append({"name": "_container_used", "bytes": int(m.group(1))})
        if m := re.search(r"APFS Volume Disk \(Role\):\s+(\S+) \((.*)\)", line):
            cur = {"disk": m.group(1), "role": m.group(2)}
        if (m := re.search(r"Name:\s+(.*) \(Case", line)) and cur:
            cur["name"] = m.group(1)
        if (m := re.search(r"Capacity Consumed:\s+(\d+) B", line)) and cur.get("disk", "").startswith("disk3"):
            vols.append(dict(cur, bytes=int(m.group(1))))
            cur = {}
    return vols


def walk():
    """{path: KiB} for every folder on the Data volume down to DEPTH, from ONE du at background priority."""
    sizes = {}
    p = subprocess.Popen(["taskpolicy", "-b", "du", "-kx", "-d", str(DEPTH), DATA], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    for line in p.stdout:
        kib, _, path = line.rstrip("\n").partition("\t")
        if int(kib) >= KEEP or path.count("/") <= 6:
            sizes[path] = int(kib)
    p.wait()
    return sizes


def worktrees():
    """Every worktree: size comes from the tree; git says dirty, pushed, last commit; lsof says who sits in it."""
    cwds = subprocess.run(["lsof", "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
    cwds = [l[1:] for l in cwds.splitlines() if l.startswith("n/")]
    rows = []
    for repo in sorted(os.listdir(WT)) if os.path.isdir(WT) else []:
        for lane in sorted(os.listdir(os.path.join(WT, repo))):
            p = os.path.join(WT, repo, lane)
            if not os.path.isdir(p) or not os.path.exists(os.path.join(p, ".git")):
                continue
            g = lambda *a: subprocess.run(["git", "-C", p, *a], capture_output=True, text=True).stdout.strip()
            rows.append({"path": nice(p), "repo": repo, "lane": lane, "branch": g("branch", "--show-current"),
                         "last_commit": g("log", "-1", "--format=%cs"),
                         "dirty": len(g("status", "--porcelain", "-uno").splitlines()),
                         "pushed": bool(g("branch", "-r", "--contains", "HEAD")),
                         "busy": any(c == p or c.startswith(p + "/") for c in cwds)})
    return rows


def real(path):
    return path[len(DATA):] if path.startswith(DATA + "/") else path


def rule_for(path, places):
    path, best = real(path), None
    for pl in places:
        q = os.path.expanduser(pl["path"]).rstrip("/")
        if (path == q or path.startswith(q + "/")) and (best is None or len(q) > len(best[0])):
            best = (q, pl)
    return best[1] if best else None


def build_tree(sizes, root):
    """Nested {path, kib, children[], rest} from the flat du output, children over SHOW, rest = everything smaller."""
    kids = {}
    for p in sizes:
        kids.setdefault(os.path.dirname(p), []).append(p)

    def node(p):
        ch = sorted((c for c in kids.get(p, []) if sizes[c] >= SHOW), key=lambda c: -sizes[c])
        n = {"path": p, "kib": sizes[p], "children": [node(c) for c in ch]}
        n["rest"] = sizes[p] - sum(c["kib"] for c in n["children"])
        return n
    return node(root)


def history():
    return [json.loads(l) for l in open(HIST) if l.strip()] if os.path.exists(HIST) else []


def growth(path, kib, now, hist, min_age):
    for h in reversed(hist):
        if now - h["t"] >= min_age and path in h.get("sizes", {}):
            return round((kib - h["sizes"][path]) / GIB / max((now - h["t"]) / 86400, 1e-9), 2) if min_age \
                else round((kib - h["sizes"][path]) / GIB, 2)
    return None


def render(t, m):
    places, now, hist = m["places"], t["t"], t.get("hist", [])
    L = [f"# {MACHINE.capitalize()} storage map — {t['at']}", "",
         "Generated by `tools/storage-map.py` (one throttled du over the whole disk; weekly and on demand). Owners and",
         f"rules: `plan/storage-map.json`. Data: `machines/{MACHINE}/storage-tree.json`. Do not edit this page by hand.", ""]
    v = {x.get("role", x["name"]): x["bytes"] / 2**30 for x in t["volumes"]}
    used = t["disk_gib"] - t["free_gib"]
    L += [f"**Disk {t['disk_gib']:.0f} GiB: {used:.1f} used, {t['free_gib']:.1f} free** (target 100 free).", "",
          "| Volume | GiB | What |", "|---|---:|---|"]
    what = {"System": "macOS itself (read-only)", "Preboot": "boot files", "Recovery": "recovery OS",
            "VM": "swap: grows when memory runs out (live processes), shrinks when they end", "Data": "everything below"}
    for x in t["volumes"]:
        if x["name"] != "_container_used":
            L.append(f"| {x.get('role')} | {x['bytes'] / 2**30:.1f} | {what.get(x.get('role'), '')} |")
    L += ["", "## Every gigabyte on the Data volume", "",
          "Each folder of 0.5 GiB or more, biggest first; *smaller items* is everything under it below 0.5 GiB. "
          "Δ = change since the last run; /day from a run at least 20 h older.", ""]

    def own(n):
        """The place written for exactly this folder, if any."""
        return next((pl for pl in places if os.path.expanduser(pl["path"]).rstrip("/") == real(n["path"])), None)

    def line(n, depth, ruled):
        pl, k = own(n), n["kib"]
        d1 = growth(n["path"], k, now, hist, 0)
        dd = growth(n["path"], k, now, hist, 20 * 3600)
        if pl:
            tag = f" — **{pl['rule']}** · {pl['owner']} · {pl['writer']}" + (f" — {pl['note']}" if pl.get("note") else "")
        elif not ruled and not n["children"] and k >= GIB:
            tag = " — ⚠ no owner yet"
        else:
            tag = ""
        g = "".join([f" (Δ{d1:+.1f})" if d1 and abs(d1) >= 0.1 else "", f" ({dd:+.2f}/day)" if dd and abs(dd) >= 0.05 else ""])
        L.append(f"{'  ' * depth}- {k / GIB:.1f} GiB `{nice(n['path'])}`{tag}{g}")
        if real(n["path"]) == WT:
            return  # every worktree has its own row in the worktree table below
        under = ruled or bool(pl)
        kids = n["children"] if not pl or depth < 6 else []
        for c in kids:
            if under and own(c) is None and c["kib"] < 2 * GIB:
                continue  # inside an owned place: only the 2 GiB+ parts get their own line
            line(c, depth + 1, under)
        shown = sum(c["kib"] for c in kids if not (under and own(c) is None and c["kib"] < 2 * GIB))
        rest = k - shown
        if shown and rest >= GIB // 2:
            flag = " — ⚠ no owner yet" if not under and rest >= GIB else ""
            L.append(f"{'  ' * (depth + 1)}- {rest / GIB:.1f} GiB in smaller folders{flag}")
    for c in t["tree"]["children"]:
        line(c, 0, False)
    L += ["", f"## Hidden folders in ~ ({len(t['dots'])}, all of them)", "", "| GiB | Folder | Owner · rule |", "|---:|---|---|"]
    for p, k in t["dots"]:
        pl = rule_for(p, places)
        L.append(f"| {k / GIB:.2f} | `{nice(p)}` | {(pl['owner'] + ' · ' + pl['rule']) if pl else ''} |")
    wts = t["worktrees"]
    tot = sum(w.get("kib") or 0 for w in wts) / GIB
    L += ["", f"## Worktrees ({len(wts)}, {tot:.1f} GiB)", "",
          "Retire when clean, pushed and nobody sits in it (role estate). busy = a process has its cwd inside.", "",
          "| GiB | Worktree | Branch | Last commit | Dirty | Pushed | Busy |", "|---:|---|---|---|---:|---|---|"]
    for w in sorted(wts, key=lambda w: -(w.get("kib") or 0)):
        L.append(f"| {(w.get('kib') or 0) / GIB:.2f} | `{w['repo']}/{w['lane']}` | {w['branch']} | {w['last_commit']} | "
                 f"{w['dirty']} | {'yes' if w['pushed'] else 'NO'} | {'yes' if w['busy'] else ''} |")
    L += ["", "## Growers and their guards", ""] + [f"{i}. {g}" for i, g in enumerate(m.get("growers", []), 1)]
    L += ["", "## Agents declare big writes", "", m.get("declare", ""), ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--render", action="store_true")
    a = ap.parse_args()
    m = json.load(open(MAP))
    if a.render:
        t = json.load(open(TREE))
    else:
        now, hist = time.time(), history()
        sizes = walk()
        st = os.statvfs(DATA)
        t = {"t": int(now), "at": time.strftime("%Y-%m-%d %H:%M %z"), "volumes": volumes(),
             "disk_gib": st.f_blocks * st.f_frsize / 2**30, "free_gib": st.f_bavail * st.f_frsize / 2**30,
             "tree": build_tree(sizes, DATA)}
        home = DATA + HOME
        t["dots"] = sorted(([p, k] for p, k in sizes.items() if os.path.dirname(p) == home
                            and os.path.basename(p).startswith(".")), key=lambda x: -x[1])
        t["worktrees"] = worktrees()
        for w in t["worktrees"]:
            w["kib"] = sizes.get(DATA + w["path"].replace("~", HOME, 1))
        kept = {p: k for p, k in sizes.items() if k >= KEEP}
        with open(HIST, "a") as f:
            f.write(json.dumps({"t": int(now), "at": t["at"], "free_gib": round(t["free_gib"], 2), "sizes": kept}) + "\n")
        t["hist"] = hist[-60:]
        json.dump({k: v for k, v in t.items() if k != "hist"}, open(TREE, "w"), indent=0)
    t.setdefault("hist", history()[:-1][-60:])
    open(DOC, "w").write(render(t, m))
    print(f"storage-map: {t['free_gib']:.1f} GiB free; tree, {len(t['dots'])} hidden folders, "
          f"{len(t['worktrees'])} worktrees -> {os.path.relpath(DOC, REPO)}")


if __name__ == "__main__":
    sys.exit(main())
