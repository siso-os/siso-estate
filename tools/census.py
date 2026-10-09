#!/usr/bin/env python3
"""Count the city: every repo on this machine and where it sits, which are on the one map (sisodias/siso-city) and
which are not, what work is not on GitHub yet, duplicate homes, and homeless files (files no repo or data plane holds).

  census.py [--machine laptop] [--quiet]   -> machines/<m>/census.json, and a summary on stdout

Reads the generated records (machines/<m>/repos.json from `estate inventory`, machines/<m>/backup-plan.json from
`estate backup plan`, machines/github/sisodias.json, ~/SISO_Workspace/.estate/map.json, plan/data-planes.json), then
walks ~/SISO_Workspace once, stopping at every repo, and runs one `git ls-files -o` per live repo with loose files.
Nothing is changed. "Homeless" = a file that is in no repo, not tracked by the map, not in an encrypted data plane and
not in _archive/_data/_reference/_inbox; or an untracked file inside a live repo that nobody has touched for 7 days.
"""
import json, os, re, subprocess, sys, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STALE = 7 * 86400
WIP_FILES, WIP_BYTES = 20000, 200 * 1024 * 1024  # tools/backup.py MAX_WIP_*: a bigger pile of loose files is not backed up


def _backup():
    """tools/backup.py, for the one rule of what a snapshot takes (browser state, HALO and data-plane folders and files
    over its per-file limit are left out), so the census counts a pile the way the nightly does."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("estate_backup", os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BACKUP = _backup()
EPHEMERAL = re.compile(r"/\.t/|/\.codex/(\.tmp|backups|vendor_imports)/|/\.claude/plugins/marketplaces/|/\.scratch-binding-")
DEPENDENCY = re.compile(r"/(node_modules|\.lake|vendor|site-packages|\.venv|bower_components)/")
HARNESS = re.compile(r"^~/\.(claude|codex|agents|opencode|cursor|gemini|omp|config|local|hermes|pi)(/|$)")
HOME_ALLOWED = {"Applications", "Desktop", "Documents", "Downloads", "Library", "Movies", "Music", "Pictures", "Public",
                "SISO_Workspace", "bin", "AGENTS.md", "SISO_Agency",  # ~/SISO_Agency is a typo-guard file
                "go",        # Go's own GOPATH (bin/wireproxy, pkg/)
                "plugins"}   # Codex's personal plugins (siso-laptop@personal: DevSpace connectors; the agent stack's)
WALK_SKIP = {".git", "node_modules", ".DS_Store", "__pycache__", ".next", ".turbo", "dist", ".cache"}
OWNERS = {"lordsisodia": "sisodias"}  # the account was renamed; old URLs redirect


def load(p, default=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def tilde(p):
    return p.replace(HOME, "~", 1)


def key(url_or_gh):
    if isinstance(url_or_gh, dict):
        o, n = url_or_gh.get("owner", ""), url_or_gh.get("repo", "")
    else:
        m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url_or_gh or "")
        if not m:
            return None
        o, n = m.groups()
    o = o.lower()
    return f"{OWNERS.get(o, o)}/{n.lower()}"


def place(p):
    t = tilde(p)
    if EPHEMERAL.search(t + "/"):
        return "ephemeral"
    if DEPENDENCY.search(p + "/"):
        return "dependency"
    if not p.startswith(WS + "/") and p != WS:
        return "harness-home" if HARNESS.match(t) else "home-other"
    rel = os.path.relpath(p, WS)
    top = rel.split("/")[0]
    return {"_archive": "archive", "_reference": "reference", "_data": "data", "_inbox": "inbox"}.get(top, "live")


def untracked_files(path):
    r = subprocess.run(["git", "-C", path, "ls-files", "-o", "--exclude-standard", "-z"], capture_output=True, timeout=300)
    if r.returncode:
        return None
    now, n, b, stale_n, stale_b, tops = time.time(), 0, 0, 0, 0, Counter()
    files = [f for f in r.stdout.decode(errors="replace").split("\0") if f]
    out_of_snapshot = set(BACKUP.browser_state(path, files)) | set(BACKUP.left_out_folders(path, files))
    wip_n = wip_b = big_n = big_b = 0  # what the nightly snapshot takes; files over its per-file limit in no plane
    for f in files:
        try:
            st = os.lstat(os.path.join(path, f))
        except OSError:
            continue
        n += 1
        b += st.st_size
        if not any(f == d or f.startswith(d + "/") for d in out_of_snapshot):
            if st.st_size > BACKUP.MAX_FILE:
                big_n, big_b = big_n + 1, big_b + st.st_size
            else:
                wip_n, wip_b = wip_n + 1, wip_b + st.st_size
        if now - st.st_mtime > STALE:
            stale_n += 1
            stale_b += st.st_size
            tops["/".join(f.split("/")[:2])] += st.st_size
    return {"files": n, "bytes": b, "stale_files": stale_n, "stale_bytes": stale_b,
            "stale_top": [[k, v] for k, v in tops.most_common(5)],
            "wip_files": wip_n, "wip_bytes": wip_b, "big_files": big_n, "big_bytes": big_b}


def walk_outside_repos(tracked, plane_paths):
    """Files under ~/SISO_Workspace that no repo holds, the map does not track and no data plane covers."""
    now, out = time.time(), defaultdict(lambda: [0, 0, 0])  # dir -> files, bytes, stale files
    skip_tops = {"_archive", "_data", "_reference", "_inbox", ".git"}
    for root, dirs, files in os.walk(WS):
        rel_root = os.path.relpath(root, WS)
        if rel_root != "." and os.path.exists(os.path.join(root, ".git")):
            dirs[:] = []
            continue
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in skip_tops]
        dirs[:] = [d for d in dirs if d not in WALK_SKIP and not os.path.islink(os.path.join(root, d))
                   and not any((os.path.join(rel_root, d) if rel_root != "." else d).startswith(pp) for pp in plane_paths)]
        for f in files:
            if f in WALK_SKIP:
                continue
            rel = os.path.join(rel_root, f) if rel_root != "." else f
            if rel in tracked or any(rel.startswith(pp) for pp in plane_paths):
                continue
            try:
                st = os.lstat(os.path.join(root, f))
            except OSError:
                continue
            d = out["/".join(rel.split("/")[:3]) if "/" in rel else "(root)"]
            d[0] += 1
            d[1] += st.st_size
            d[2] += now - st.st_mtime > STALE
    return out


def main():
    machine = sys.argv[sys.argv.index("--machine") + 1] if "--machine" in sys.argv else os.environ.get("ESTATE_MACHINE", "laptop")
    quiet = "--quiet" in sys.argv
    inv = load(os.path.join(REPO, "machines", machine, "repos.json"), {})
    repos = inv.get("repos", [])
    plan = {i["path"]: i for i in load(os.path.join(REPO, "machines", machine, "backup-plan.json"), {}).get("items", [])}
    umap = load(os.path.join(WS, ".estate", "map.json"), {})
    mapped = umap.get("repos", {})
    gh = load(os.path.join(REPO, "machines", "github", "sisodias.json"), [])
    planes = [p for pl in load(os.path.join(REPO, "plan", "data-planes.json"), {}).get("planes", []) for p in pl.get("paths", [])]
    tracked = set(subprocess.run(["git", "-C", WS, "ls-files"], capture_output=True, text=True).stdout.splitlines())

    # 1. repos by place
    checkouts = [r for r in repos if r["kind"] == "repo"]
    for r in checkouts:
        r["place"] = place(r["path"])
        gh_ = sorted(r.get("github") or [], key=lambda g: g.get("remote") != "origin")  # origin first
        r["key"] = next((key(g) for g in gh_), None) or key((r.get("remotes") or {}).get("origin"))
    by_place = Counter(r["place"] for r in checkouts)
    live = [r for r in checkouts if r["place"] == "live"]

    # 2. the map
    on = Counter(m for e in mapped.values() for m in e.get("on", []))
    only = Counter(",".join(sorted(e.get("on", []))) for e in mapped.values())
    live_off_map = []
    for r in live:
        rel = os.path.relpath(r["path"], WS)
        if rel not in mapped and rel != ".":
            owner = (r["key"] or "").split("/")[0]
            why = ("no GitHub remote" if not r["key"] else "third-party clone" if owner != "sisodias" else "not in the map yet")
            live_off_map.append({"path": rel, "remote": r["key"], "why": why})
    map_keys = {key(e.get("url")) for e in mapped.values()} - {None}

    # 3. GitHub repos with no place on the map
    disk_keys, second = defaultdict(set), {}
    for r in checkouts:
        if r["key"]:
            disk_keys[r["key"]].add(r["place"])
        for g in r.get("github") or []:
            if key(g) != r["key"] and r["place"] == "live":
                second.setdefault(key(g), tilde(r["path"]))
    gh_off = defaultdict(list)
    for g in gh:
        k = f"sisodias/{g['name'].lower()}"
        if k in map_keys:
            continue
        places = disk_keys.get(k, set())
        cat = ("the map itself (~/SISO_Workspace)" if g["name"] == "siso-city" else
               "data-plane (encrypted backup store)" if g["name"].startswith("siso-data-") else
               "second remote of a repo on the map" if k in second else
               "harness home (outside the city)" if "harness-home" in places else
               "archived on GitHub" if g.get("isArchived") else
               "on this disk in _archive/_data/_reference" if places & {"archive", "data", "reference"} else
               "on this disk outside the city (homeless repo)" if "home-other" in places else
               "fork of someone else's repo" if g.get("isFork") else
               "GitHub only, no place on the map")
        gh_off[cat].append(g["name"])

    # 4. duplicate homes: one GitHub repo checked out in two or more places
    homes = defaultdict(list)
    for r in checkouts:
        if r["key"] and r["place"] in ("live", "harness-home", "home-other", "data", "reference", "archive"):
            homes[r["key"]].append((r["place"], tilde(r["path"])))
    dups = {k: v for k, v in homes.items() if sum(1 for p, _ in v if p == "live") > 1}
    shadows = {k: v for k, v in homes.items() if k not in dups and len(v) > 1 and any(p == "live" for p, _ in v)}

    # 5. is it all on GitHub? (disk-only work per `estate backup plan`)
    work = Counter()
    for r in live + [r for r in checkouts if r["place"] == "harness-home"]:
        it = plan.get(r["path"], {})
        act = it.get("action", "unplanned")
        work["with disk-only work" if act in ("own-private", "overlay", "new-repo") else act] += 1
    unpushed = [(os.path.relpath(r["path"], WS), r.get("unpushed_commits") or 0) for r in live if (r.get("unpushed_commits") or 0) > 0]
    no_remote = [os.path.relpath(r["path"], WS) for r in live if not r.get("remotes")]
    backup = load(os.path.join(REPO, "machines", machine, "backup.json"), {})

    # 6. homeless files
    outside = walk_outside_repos(tracked, [p.rstrip("/") + "/" for p in planes])
    with ThreadPoolExecutor(8) as ex:
        loose = dict(zip([r["path"] for r in live if r.get("untracked")],
                         ex.map(lambda r: untracked_files(r["path"]), [r for r in live if r.get("untracked")])))
    loose = {tilde(p): v for p, v in loose.items() if v and v["files"]}
    over_cap = {p: v for p, v in loose.items() if v["wip_files"] > WIP_FILES or v["wip_bytes"] > WIP_BYTES}
    big_only = {p: v for p, v in loose.items() if v["big_files"]}
    now = time.time()
    home_extra = [e for e in os.listdir(HOME) if not e.startswith(".") and e not in HOME_ALLOWED]
    intake = {}
    for d in ("Downloads", "Desktop", "Documents"):
        if not os.path.isdir(os.path.join(HOME, d)):
            continue
        items = [e for e in os.listdir(os.path.join(HOME, d)) if not e.startswith(".") and e != "Icon\r"]
        old = [e for e in items if now - os.lstat(os.path.join(HOME, d, e)).st_mtime > STALE]
        intake[d] = {"items": len(items), "older_than_7_days": len(old)}
    inbox = [e for e in (os.listdir(os.path.join(WS, "_inbox")) if os.path.isdir(os.path.join(WS, "_inbox")) else [])
             if not e.startswith(".") and e != "README.md"]

    out = {
        "machine": machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "inputs": {"inventory": inv.get("observed_at"), "backup_plan": load(os.path.join(REPO, "machines", machine, "backup-plan.json"), {}).get("planned_at"),
                   "last_backup_run": backup.get("ran_at"), "map_built": umap.get("built_at"), "github_repos": len(gh)},
        "repos": {"checkouts": len(checkouts), "worktrees": sum(1 for r in repos if r["kind"] == "worktree"),
                  "submodules": sum(1 for r in repos if r["kind"] == "submodule"), "by_place": dict(by_place.most_common())},
        "map": {"entries": len(mapped), "gitlinks": sum(1 for l in open(os.path.join(WS, ".gitmodules")) if l.startswith("[submodule")),
                "checked_out_on": dict(on), "by_machines": dict(only), "live_here_not_on_map": live_off_map},
        "github": {"sisodias_repos": len(gh), "on_map": len(gh) - sum(len(v) for v in gh_off.values()),
                   "not_on_map": {k: sorted(v) for k, v in sorted(gh_off.items())}},
        "duplicates": {"live_duplicate_homes": {k: v for k, v in sorted(dups.items())},
                       "live_with_a_copy_elsewhere": {k: v for k, v in sorted(shadows.items())}},
        "sync": {"live_and_harness_checkouts": dict(work), "live_with_unpushed_commits": len(unpushed),
                 "unpushed_commits": sum(n for _, n in unpushed), "top_unpushed": sorted(unpushed, key=lambda x: -x[1])[:10],
                 "live_without_any_remote": no_remote},
        "homeless": {
            "outside_repos": {"files": sum(v[0] for v in outside.values()), "bytes": sum(v[1] for v in outside.values()),
                              "stale_files": sum(v[2] for v in outside.values()),
                              "top": sorted(([k, *v] for k, v in outside.items()), key=lambda x: -x[2])[:15]},
            "loose_in_repos": {"repos": len(loose), "files": sum(v["files"] for v in loose.values()),
                               "bytes": sum(v["bytes"] for v in loose.values()),
                               "stale_files": sum(v["stale_files"] for v in loose.values()),
                               "stale_bytes": sum(v["stale_bytes"] for v in loose.values()),
                               "top": sorted(([p, v] for p, v in loose.items()), key=lambda x: -x[1]["stale_bytes"])[:15],
                               "too_big_for_backup": {"repos": sorted(over_cap), "files": sum(v["wip_files"] for v in over_cap.values()),
                                                      "bytes": sum(v["wip_bytes"] for v in over_cap.values())},
                               "big_files_in_no_plane": {"files": sum(v["big_files"] for v in big_only.values()),
                                                         "bytes": sum(v["big_bytes"] for v in big_only.values()),
                                                         "top": sorted(([p, v["big_bytes"]] for p, v in big_only.items()), key=lambda x: -x[1])[:10]}},
            "repos_outside_the_city": sorted(tilde(r["path"]) for r in checkouts if r["place"] == "home-other"),
            "throwaway_clones": {"count": by_place.get("ephemeral", 0),
                                 "where": dict(Counter(re.split(r"/\.t/|/\.tmp/|/backups/|/vendor_imports/|/marketplaces/|/\.scratch-binding-",
                                                                tilde(r["path"]))[0] for r in checkouts if r["place"] == "ephemeral").most_common(5))},
            "home": {"unplanned_top_level": home_extra, **intake}, "inbox": len(inbox)},
    }
    dst = os.path.join(REPO, "machines", machine, "census.json")
    json.dump(out, open(dst, "w"), indent=1)
    open(dst, "a").write("\n")
    if quiet:
        return
    h, gb = out["homeless"], 1024 ** 3
    print(f"census {machine} {out['at']} -> {tilde(dst)}")
    print(f"  checkouts {len(checkouts)} ({', '.join(f'{k} {v}' for k, v in by_place.most_common())}); worktrees {out['repos']['worktrees']}")
    print(f"  map: {len(mapped)} repos ({', '.join(f'{k} {v}' for k, v in only.items())}); live here but not on the map: {len(live_off_map)}")
    print(f"  GitHub sisodias: {len(gh)}; on the map {out['github']['on_map']}; " + "; ".join(f"{k} {len(v)}" for k, v in sorted(gh_off.items())))
    print(f"  duplicate homes (live twice): {len(dups)}; live with a copy elsewhere: {len(shadows)}")
    print(f"  disk-only work: {dict(work)}; unpushed commits {out['sync']['unpushed_commits']} in {len(unpushed)} live repos; no remote {len(no_remote)}")
    print(f"  homeless outside repos: {h['outside_repos']['files']} files {h['outside_repos']['bytes'] / gb:.2f} GB "
          f"({h['outside_repos']['stale_files']} untouched 7d+)")
    print(f"  loose in live repos: {h['loose_in_repos']['files']} files {h['loose_in_repos']['bytes'] / gb:.2f} GB in {len(loose)} repos; "
          f"untouched 7d+: {h['loose_in_repos']['stale_files']} files {h['loose_in_repos']['stale_bytes'] / gb:.2f} GB")
    tb = h["loose_in_repos"]["too_big_for_backup"]
    print(f"  too big for the backup (on this disk only): {tb['files']} files {tb['bytes'] / gb:.2f} GB in {len(tb['repos'])} repos")
    bf = h["loose_in_repos"]["big_files_in_no_plane"]
    print(f"  files over the snapshot's {BACKUP.MAX_FILE // 1048576} MB limit in no data plane (on this disk only): {bf['files']} files {bf['bytes'] / gb:.2f} GB")
    print(f"  home: unplanned {home_extra}; " + "; ".join(f"{d} {v['items']} ({v['older_than_7_days']} 7d+)" for d, v in intake.items())
          + f"; _inbox {len(inbox)}")


if __name__ == "__main__":
    main()
