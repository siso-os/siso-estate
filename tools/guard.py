#!/usr/bin/env python3
"""The births guard (goal-2050 pillar P1: "buildings arrive with their utilities"). Every git repo on this machine whose
first commit is inside the window, with no `estate new` birth record for its folder, is a hand-made project.

  guard.py [--days 30] [--machine laptop]   -> machines/<m>/births-guard.json, and one count line on stdout

Repos come from the inventory (machines/<m>/repos.json, refreshed by the nightly before this runs); worktrees,
submodules, harness homes (~/.*), _reference, _archive and _data are not projects and are skipped. A birth record is a
line in machines/<m>/births-by-road.jsonl with `made_by` (or the older `by`) = "estate new". Report-only: it changes
nothing but its own record.
"""
import argparse, json, os, subprocess, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
SKIP_TOP = ("_reference", "_archive", "_data")


def first_commit(path):
    """The earliest root commit's date (ISO), or None for an empty repo."""
    r = subprocess.run(["git", "-C", path, "log", "--max-parents=0", "--format=%cI", "HEAD"], capture_output=True, text=True)
    dates = sorted(r.stdout.split()) if r.returncode == 0 else []
    return dates[0] if dates else None


def ephemeral(path):
    """A repo under a hidden folder inside a repo (test fixtures in .t/, .worktrees/): scratch, not a project."""
    rel = path[len(WS) + 1:] if path.startswith(WS + "/") else path
    return any(seg.startswith(".") for seg in rel.split("/")[:-1])


def project(path):
    if path.startswith(WS + "/"):
        return path[len(WS) + 1:].split("/")[0] not in SKIP_TOP
    if path.startswith(HOME + "/"):
        return not path[len(HOME) + 1:].startswith(".")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    mdir = os.path.join(REPO, "machines", a.machine)
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - a.days * 86400))
    born, road_opened = set(), None
    try:
        for line in open(os.path.join(mdir, "births-by-road.jsonl")):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if "estate new" in (r.get("made_by"), r.get("by")):
                born.add(os.path.join(WS, r["folder"]))
                road_opened = min(road_opened or r["at"], r["at"])
    except OSError:
        pass
    repos = json.load(open(os.path.join(mdir, "repos.json")))["repos"]
    by_road, hand, checked, scratch = [], [], 0, 0
    for x in repos:
        p = x["path"]
        if x.get("kind") != "repo" or not project(p) or not os.path.isdir(p):
            continue
        checked += 1
        fc = first_commit(p)
        if not fc or fc[:19] < since:          # ISO dates with offsets; the window is days wide, so offsets do not matter
            continue
        if ephemeral(p):
            scratch += 1
            continue
        rel = p.replace(HOME, "~")
        (by_road if p in born else hand).append({"path": rel, "first_commit": fc})
    out = {"machine": a.machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "window_days": a.days, "since": since,
           "repos_checked": checked, "new_in_window": len(by_road) + len(hand),
           "made_by_estate_new": len(by_road), "hand_made_count": len(hand), "ephemeral_skipped": scratch,
           "road_opened": road_opened,          # the first estate new birth: hand-made after it is a miss, before it is history
           "hand_made_since_road": [r["path"] for r in hand if road_opened and r["first_commit"][:19] >= road_opened[:19]],
           "by_estate_new": sorted(by_road, key=lambda r: r["first_commit"]),
           "hand_made": [r["path"] for r in sorted(hand, key=lambda r: r["first_commit"])],
           "hand_made_detail": sorted(hand, key=lambda r: r["first_commit"])}
    with open(os.path.join(mdir, "births-guard.json"), "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(f"births guard ({a.days} days): {out['new_in_window']} new repos, {len(by_road)} made by estate new, "
          f"{len(hand)} hand-made, {len(out['hand_made_since_road'])} of them since the road opened {(road_opened or '-')[:10]}; "
          f"{scratch} scratch fixtures skipped (machines/{a.machine}/births-guard.json)")


if __name__ == "__main__":
    main()
