#!/usr/bin/env python3
"""Broken symlinks in the workspace (A3: "no broken symlink"). Walks without following links, skips build and
package trees and the vault, and writes machines/<m>/links.json. A broken link inside a path its repo gitignores (a
build or vendor artifact: a tool bundle, a worktree's vendored copy) is counted separately as `ignored`, never
repaired; `owned` are Oracle's and HALO's (their owners fix them); `vendor` are committed inside a vendored package
tree (the repo's owner fixes them, estate never edits a vendor tree); `live` is what A3 counts. `--list` prints each broken link;
`--repair` repoints absolute links broken by a recorded move (moves.jsonl, RETIRED.txt), never Oracle's or HALO's."""
import os, sys, json, time, socket
WS = os.path.expanduser("~/SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRUNE = {"node_modules", ".venv", "venv", "__pycache__", ".next", ".nuxt", ".turbo", ".cache", "target", ".git",
         ".pnpm-store", ".gradle", "Pods", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", ".tox",
         ".parcel-cache", ".svelte-kit", ".expo", ".vercel", ".wrangler", "bower_components", ".yarn", ".npm",
         "DerivedData", ".build", ".dart_tool", ".pub-cache", ".lake"}
SKIP_TOP = {"_archive"}  # the vault is read-only history; its links are recorded, not repaired


def machine():
    return os.environ.get("SISO_MACHINE") or ("laptop" if sys.platform == "darwin" else socket.gethostname())


def scan():
    broken, stack = [], [WS]
    while stack:
        d = stack.pop()
        try:
            it = list(os.scandir(d))
        except OSError:
            continue
        for e in it:
            try:
                if e.is_symlink():
                    if not os.path.exists(e.path):
                        broken.append({"path": os.path.relpath(e.path, WS), "target": os.readlink(e.path)})
                elif e.is_dir(follow_symlinks=False) and e.name not in PRUNE and not (d == WS and e.name in SKIP_TOP):
                    stack.append(e.path)
            except OSError:
                continue
    return sorted(broken, key=lambda x: x["path"])


OWNED_ELSEWHERE = ("SISO_Agency/apps/oracle", "SISO_Agency/partners/halo/", "_data/worktrees/halocrm/", "_data/worktrees/oracle",
                   ".agents/skills/oracle-")  # Oracle's and HALO's: report, never touch


def repo_root(p):
    d = os.path.dirname(p)
    while d.startswith(WS) and d != WS:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return WS if os.path.exists(os.path.join(WS, ".git")) else None


def classify(broken):
    """Mark each link ignored (its repo gitignores it and does not track it), owned (Oracle/HALO) or live."""
    import subprocess
    by = {}
    for x in broken:
        by.setdefault(repo_root(os.path.join(WS, x["path"])), []).append(x)
    for root, xs in by.items():
        ign = set()
        if root:
            rel = [os.path.relpath(os.path.join(WS, x["path"]), root) for x in xs]
            r = subprocess.run(["git", "-C", root, "check-ignore", "--stdin", "-z"], input="\0".join(rel) + "\0",
                               capture_output=True, text=True)
            ign = {os.path.join(root, p) for p in r.stdout.split("\0") if p}
        for x in xs:
            full = os.path.join(WS, x["path"])
            parts = x["path"].split("/")
            if full in ign or parts[-1] in PRUNE:      # gitignored, or a node_modules/.venv link (dir-only ignore rules miss links)
                x["kind"] = "ignored"
            elif x["path"].startswith(OWNED_ELSEWHERE):
                x["kind"] = "owned"
            elif "vendor" in parts[:-1]:               # committed inside a vendored package tree: the repo's owner fixes it upstream
                x["kind"] = "vendor"
            else:
                x["kind"] = "live"
    return broken


def moves_map():
    """old absolute prefix -> new absolute prefix, from recorded moves and the retired-link table"""
    m = {}
    try:
        for line in open(os.path.join(REPO, "machines", machine(), "moves.jsonl")):
            r = json.loads(line)
            if r.get("src") and r.get("dst"):
                m[r["src"].rstrip("/")] = r["dst"].rstrip("/")
    except OSError:
        pass
    try:
        for line in open(os.path.join(REPO, "plan", "link-retire", "RETIRED.txt")):
            if " -> " in line:
                a, b = (x.strip() for x in line.split(" -> ", 1))
                m[os.path.expanduser(a).rstrip("/")] = os.path.join(WS, b).rstrip("/")
    except OSError:
        pass
    return m


def repair(broken):
    """Repoint an absolute link whose target moved in a recorded move to where it lives now; one line per repair."""
    m, fixed = moves_map(), []
    for x in broken:
        t, p = x["target"], x["path"]
        if not t.startswith("/") or p.startswith(OWNED_ELSEWHERE) or x.get("kind") in ("ignored", "vendor"):
            continue
        src = max((s for s in m if t == s or t.startswith(s + "/")), key=len, default=None)
        if not src:
            continue
        new = m[src] + t[len(src):]
        if not os.path.exists(new):
            continue
        full = os.path.join(WS, p)
        os.remove(full)
        os.symlink(new, full)
        fixed.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": "symlink", "path": p, "old": t, "new": new})
    if fixed:
        with open(os.path.join(REPO, "machines", machine(), "repoints.jsonl"), "a") as f:
            for r in fixed:
                f.write(json.dumps(r) + "\n")
    return fixed


if __name__ == "__main__":
    t0 = time.time()
    b = classify(scan())
    if "--repair" in sys.argv:
        fixed = repair(b)
        print(f"repointed {len(fixed)} links broken by a recorded move")
        b = classify(scan()) if fixed else b
    n = {k: sum(1 for x in b if x["kind"] == k) for k in ("live", "owned", "vendor", "ignored")}
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0, 1), "counts": n, "broken": b}
    with open(os.path.join(REPO, "machines", machine(), "links.json"), "w") as f:
        json.dump(out, f, indent=1)
    if "--list" in sys.argv:
        for x in b:
            if x["kind"] != "ignored" or "--all" in sys.argv:
                print(f"{x['kind']:<7} {x['path']} -> {x['target']}")
    print(f"{len(b)} broken symlinks: {n['live']} live, {n['owned']} Oracle/HALO (their owners), "
          f"{n['vendor']} committed in vendor trees, "
          f"{n['ignored']} in gitignored build/vendor paths ({out['seconds']} s)")
