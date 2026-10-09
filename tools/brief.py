#!/usr/bin/env python3
"""The Estate Manager's boot packet: where the estate stands and what changed since the last manager session.

  brief.py [--machine laptop] [--mark]

Prints, from records only (fast; no scan): what changed since the marker (siso-estate and map commits, moves, removals,
houses, retirements), the last nightly run, the census now and how it moved, open queue jobs, what waits on other lanes,
and which agents are running where. --mark ends a session: it records the current heads and census as the new marker
(machines/<m>/manager-seen.json, committed with the records) so the next manager sees only what is new.
"""
import json, os, re, shutil, subprocess, sys, time

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(*a, cwd=None):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else ""


def load(p, d=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def jl(p):
    out = []
    if os.path.exists(p):
        for l in open(p):
            try:
                out.append(json.loads(l))
            except ValueError:
                pass
    return out


def numbers(c):
    if not c:
        return {}
    h, g = c.get("homeless", {}), c.get("github", {})
    return {"repos on the map": c.get("map", {}).get("entries"),
            "GitHub repos without a place": len(g.get("not_on_map", {}).get("GitHub only, no place on the map", [])),
            "duplicate homes": len(c.get("duplicates", {}).get("live_duplicate_homes", {})),
            "loose files in repos (GB)": round(h.get("loose_in_repos", {}).get("bytes", 0) / 1024 ** 3, 2),
            "too big for the backup (GB)": round(h.get("loose_in_repos", {}).get("too_big_for_backup", {}).get("bytes", 0) / 1024 ** 3, 2),
            "unpushed commits": c.get("sync", {}).get("unpushed_commits"),
            "Downloads items": h.get("home", {}).get("Downloads", {}).get("items")}


def main():
    m = sys.argv[sys.argv.index("--machine") + 1] if "--machine" in sys.argv else os.environ.get("ESTATE_MACHINE", "laptop")
    rec = os.path.join(REPO, "machines", m)
    marker_p = os.path.join(rec, "manager-seen.json")
    mk = load(marker_p, {})
    since = mk.get("at", "1970")
    heads = {"estate": sh("git", "-C", REPO, "rev-parse", "HEAD"), "map": sh("git", "-C", WS, "rev-parse", "HEAD")}
    census = load(os.path.join(rec, "census.json"), {})
    if "--mark" in sys.argv:
        json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "heads": heads, "census": numbers(census)}, open(marker_p, "w"), indent=1)
        open(marker_p, "a").write("\n")
        print(f"marked: the next brief starts from now ({heads['estate'][:8]} / map {heads['map'][:8]}). Commit machines/{m}/manager-seen.json.")
        return
    print(f"# Estate brief · {m} · {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"Last manager session: {since[:16] if mk else 'none recorded (first boot: read .agents/HANDOFF.md State sections)'}")
    free = shutil.disk_usage(HOME).free / 2**30  # a full disk crashed herdr and every agent on 24 Sep
    print(f"Disk free: {free:.1f} GB" + ("  !! under 10 GB: free space before any big write (du -sm ~/SISO_Workspace/_data/*)" if free < 10 else "") + "\n")

    print("## Changed since then")
    for name, path, base in (("siso-estate", REPO, mk.get("heads", {}).get("estate")), ("the map (siso-city)", WS, mk.get("heads", {}).get("map"))):
        rng = f"{base}..HEAD" if base else "-15"
        log = sh("git", "-C", path, "log", "--format=%h %ad %s", "--date=format:%m-%d %H:%M", rng)
        lines = [l for l in log.splitlines() if "nightly records" not in l and "estate snapshot" not in l]
        snaps = len(log.splitlines()) - len(lines)
        print(f"- {name}: {len(lines)} commits" + (f" (+{snaps} nightly/snapshot)" if snaps else ""))
        for l in lines[:12]:
            print(f"    {l[:120]}")
    for f, label in (("moves.jsonl", "moves"), ("removed.jsonl", "removals"), ("houses.jsonl", "houses/syncs"), ("retired.jsonl", "retirements")):
        rows = [r for r in jl(os.path.join(rec, f)) if r.get("at", "") > since]
        if rows:
            print(f"- {label}: {len(rows)} · last: {(rows[-1].get('path') or rows[-1].get('dst') or '').replace(HOME, '~')[:90]}")

    print("\n## Last nightly")
    log = open(os.path.join(rec, "nightly.log")).read() if os.path.exists(os.path.join(rec, "nightly.log")) else ""
    runs = log.split("=== ")
    last = "=== " + runs[-2] + "=== " + runs[-1] if len(runs) > 2 else log
    bad = [l for l in last.splitlines() if re.search(r"fail|error|held|not pushed|flagged", l, re.I)]
    starts = re.findall(r"=== (\S+ \S+) nightly start", log)
    print(f"- started {starts[-1] if starts else 'never'}; " + (f"{len(bad)} lines to read:" if bad else "clean"))
    for l in bad[:6]:
        print(f"    {l[:140]}")
    b = load(os.path.join(rec, "backup.json"), {})
    held = [r for r in b.get("results", []) if r.get("status") == "held"]
    safe = [r["path"].replace(HOME, "~") for r in held if (r.get("held_copy") or {}).get("status") in ("pushed", "unchanged", "nothing-to-keep")]
    unsafe = [r["path"].replace(HOME, "~") for r in held if r["path"].replace(HOME, "~") not in safe]
    if unsafe:
        print(f"- backup held, NOT backed up: {', '.join(unsafe)}")
    if safe:
        print(f"- backup held for a secret scan, kept age-encrypted (sisodias/siso-held-backups): {', '.join(safe)}")

    print("\n## The city now (census " + census.get("at", "?")[:16] + ")")
    now, was = numbers(census), mk.get("census", {})
    for k, v in now.items():
        d = "" if k not in was or was[k] is None or v is None else f" ({v - was[k]:+g})" if v != was[k] else " (=)"
        print(f"- {k}: {v}{d}")

    print("\n## Open jobs (.agents/EXECUTION-QUEUE.md)")
    q = open(os.path.join(REPO, ".agents", "EXECUTION-QUEUE.md")).read()
    starts = [(mm.start(), mm.group(1), mm.group(2)) for mm in re.finditer(r"^(\d+[a-z]?)\. \*\*(.+?)\*\*", q, re.M)]
    ends = [mm.start() for mm in re.finditer(r"^## ", q, re.M)]
    open_ = []
    for k, (pos, n, t) in enumerate(starts):
        end = min([p2 for p2, _, _ in starts[k + 1:]][:1] + [e for e in ends if e > pos][:1] + [len(q)])
        if "- [ ]" in q[pos:end]:
            open_.append((n, t))
    for n, t in open_:
        print(f"- {n}. {t}")
    if not open_:
        print("- none open")

    print("\n## Inbox: messes other agents reported (.agents/INBOX.md, the estate-keeper skill)")
    ib = os.path.join(REPO, ".agents", "INBOX.md")
    items = [l for l in (open(ib).read().splitlines() if os.path.exists(ib) else []) if l.startswith("- [ ]")]
    for l in items:
        print(l[:160])
    if not items:
        print("- empty")

    print("\n## Waiting on other lanes (plan/COORDINATION.md, newest)")
    entries = re.findall(r"^(?:## |- )(20\d\d-\d\d-\d\d.*)$", open(os.path.join(REPO, "plan", "COORDINATION.md")).read(), re.M)
    for h in entries[-4:]:
        print(f"- {h[:140]}")

    print("\n## Agents running now")
    raw = sh("herdr", "agent", "list")
    try:
        for a in json.loads(raw)["result"]["agents"]:
            print(f"- {a.get('name') or '(unnamed)'} · {a.get('agent')} · {a.get('agent_status')} · {a.get('cwd', '').replace(HOME, '~')}")
    except (ValueError, KeyError):
        print("- herdr not available on this machine")
    print("\nNext: the first open job above, after `estate census` if the census is older than a day. End with "
          "`estate brief --mark` and a State section in .agents/HANDOFF.md.")


if __name__ == "__main__":
    main()
