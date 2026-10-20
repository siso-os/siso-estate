#!/usr/bin/env python3
"""The storage ledger's logic: where each place's master copy lives (home), whether it is in use (need), and which of
five buckets its bytes fall in. Pure functions, imported by storage-map.py (render) and pull.py (the way back).

Shaan, 16 Oct: "stop just trying to hit criteria of X amount free and more about clean it once, make sure it's clean
forever and we understand when everything's there and push what needs to be pushed to GitHub and understand on the
estate how it gets pulled back."

plan/storage-map.json places may carry two optional fields beside `rule` (never replacing it):
  home  github:<owner/repo> | plane:<data-plane> | vault:<mini path> | rebuild:<command> | here | system
  need  working | cold | auto   (auto: working when anything in the top 2 levels changed in 7 days, or the repo committed)
Defaults when home is unset: a clean, pushed git repo with a GitHub remote -> github; rule regenerable -> rebuild;
rule github -> github (pending); rule vault -> vault. Anything else has no home yet and counts as ONLY HERE.
"""
import os, re

WORKING, SAFE, REBUILD, HERE, UNMAPPED = "WORKING", "SAFE COPY", "REBUILDABLE", "ONLY HERE", "UNMAPPED"
BUCKETS = [WORKING, SAFE, REBUILD, HERE, UNMAPPED]
WHAT = {WORKING: "in use now (changed in 7 days, or declared working)",
        SAFE: "cold, with a master copy elsewhere: can go, comes back with `estate pull`",
        REBUILD: "regenerates or reinstalls itself",
        HERE: "the only copy, or unpushed/dirty work, or no home declared yet: never delete",
        UNMAPPED: "under no place in plan/storage-map.json"}
DAYS = 7


def newest_mtime(path, budget=20000):
    """Newest mtime among PATH and its top 2 levels (lstat, no symlink following, bounded). None if missing."""
    try:
        best = os.lstat(path).st_mtime
    except OSError:
        return None
    n = 0
    try:
        top = list(os.scandir(path))
    except OSError:
        return best
    for e in top:
        n += 1
        try:
            best = max(best, e.stat(follow_symlinks=False).st_mtime)
            if e.is_dir(follow_symlinks=False) and n < budget:
                for c in os.scandir(e.path):
                    n += 1
                    best = max(best, c.stat(follow_symlinks=False).st_mtime)
                    if n >= budget:
                        break
        except OSError:
            pass
        if n >= budget:
            break
    return best


BACKED = ("pushed", "bundled", "clean", "nothing-to-push")


def repo_index(repos_json, backup_json=None):
    """{absolute path: repo row} from machines/<m>/repos.json, each row carrying its last nightly backup result
    (machines/<m>/backup.json: backup.py pushes every branch, stash and edit to ops/estate/<date>/* refs)."""
    idx = {r["path"]: dict(r) for r in (repos_json or {}).get("repos", []) if r.get("path")}
    for b in (backup_json or {}).get("results", []):
        if b.get("path") in idx:
            held = (b.get("held_copy") or {}).get("status") == "pushed"
            idx[b["path"]]["backup"] = {"ok": b.get("status") in BACKED or held, "status": b.get("status"),
                                        "target": (b.get("held_copy") or {}).get("repo") if held else b.get("target"),
                                        "at": (backup_json or {}).get("ran_at", "")[:16]}
    return idx


def _gh(repo):
    for g in repo.get("github") or []:
        if g.get("owner") and g.get("repo"):
            return f"{g['owner']}/{g['repo']}"
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", " ".join((repo.get("remotes") or {}).values()))
    return m.group(1) if m else None


def repo_state(repo):
    """('clean', 'owner/repo') | ('dirty', why) | ('noremote', why) for a repos.json row."""
    gh = _gh(repo)
    if not gh:
        return "noremote", "git repo with no GitHub remote"
    bits = [f"{repo.get(k)} {w}" for k, w in (("unpushed_commits", "unpushed"), ("dirty", "dirty"), ("untracked", "untracked")) if repo.get(k)]
    if not bits:
        return "clean", gh
    b = repo.get("backup") or {}
    if b.get("ok") and b.get("target"):
        return "backed", "%s (%s; nightly backup %s pushed the work in progress)" % (b["target"], ", ".join(bits), b["at"])
    return "dirty", ", ".join(bits)


def home_of(place, repo=None):
    """(home, pending, why). home None = nothing declared or derivable. pending = the copy is promised, not yet proven."""
    h = place.get("home")
    rule = place.get("rule")
    if repo and not (h or "").startswith(("rebuild:", "system")):
        st, info = repo_state(repo)
        if st == "backed":
            return h or "github:" + info.split(" ", 1)[0], False, info
        if st != "clean":
            return h if h and h != "here" else "here", True, info
        if not h:
            return f"github:{info}", False, "clean and pushed"
    if h:
        return h, False, "declared"
    if rule == "regenerable":
        return "rebuild:regenerable", False, "rule regenerable"
    if rule == "github":
        return "github:pending", True, "rule github, not proven pushed"
    if rule == "vault":
        return "vault:cold-storage/laptop", False, "rule vault"
    return None, False, "no home declared"


def need_of(place, mtime, now, repo=None):
    n = place.get("need") or "auto"
    if n in ("working", "cold"):
        return n
    recent = now - DAYS * 86400
    if mtime and mtime >= recent:
        return "working"
    if repo and repo.get("last_commit"):
        import datetime
        try:
            if datetime.datetime.fromisoformat(repo["last_commit"]).timestamp() >= recent:
                return "working"
        except ValueError:
            pass
    return "cold"


def bucket_of(place, home, pending, need, why=""):
    """Which of the five buckets. Declared working wins; then rebuildable; then anything not provably safe; then use."""
    kind = (home or "").split(":", 1)[0]
    if place.get("need") == "working":
        return WORKING
    if kind in ("rebuild", "system") or (home is None and place.get("rule") == "regenerable"):
        return REBUILD
    if home is None or kind == "here" or pending:
        return HERE
    if need == "working":
        return WORKING
    return SAFE


def comes_back(home, path):
    if not home:
        return "nothing: no home declared (it exists only here)"
    kind, _, arg = home.partition(":")
    if kind == "github":
        return f"estate pull {path} --run  (git clone github.com/{arg})" if arg != "pending" else "nothing yet: push it first"
    if kind == "plane":
        return f"estate data restore {arg} <dir>  (age-encrypted plane, sisodias/siso-data-{arg})"
    if kind == "vault":
        return f"estate pull {path} --run  (archive on the mini vault: {arg})"
    if kind == "rebuild":
        return arg if arg != "regenerable" else "regenerates itself when its tool runs"
    if kind == "system":
        return "reinstall macOS / the app"
    return "nothing: it exists only here"


def attribute(tree, places, rule_for):
    """Bytes per place (KiB) and the unmapped remainder: every tree node's own residual goes to the place that owns it
    (longest path match) or to unmapped. The residuals add up to the root exactly."""
    per, un = {}, []

    def walk(n):
        pl = rule_for(n["path"], places)
        rest = n.get("rest", 0)
        if pl:
            per[pl["path"]] = per.get(pl["path"], 0) + rest
        elif rest > 0:
            un.append((n["path"], rest))
        for c in n.get("children", []):
            walk(c)
    walk(tree)
    return per, un


def with_repos(tree, places, repos, data="/System/Volumes/Data"):
    """Every git repo measured in the tree is its own place (rule repo), so a folder of many repos (apps, banks) is judged
    repo by repo from repos.json: clean and pushed -> SAFE COPY when cold, unpushed or dirty -> ONLY HERE."""
    have = {os.path.expanduser(p["path"]).rstrip("/") for p in places}
    out = list(places)

    def walk(n):
        q = n["path"][len(data):] if n["path"].startswith(data + "/") else n["path"]
        if q in repos and q not in have:
            out.append({"path": q, "rule": "repo", "owner": "the repo's owner", "auto": True})
            have.add(q)
        for c in n.get("children", []):
            walk(c)
    walk(tree)
    return out


def ledger(tree, places, rule_for, repos, worktrees, volumes, now, mtime=newest_mtime, expand=os.path.expanduser):
    """Rows [{path, kib, bucket, home, need, why, comes_back}] plus unmapped [(path, kib)] and the bucket sums (KiB)."""
    places = with_repos(tree, places, repos)
    per, un = attribute(tree, places, rule_for)
    rows, wt_rows = [], []
    for pl in places:
        kib = per.get(pl["path"], 0)
        if kib <= 0:
            continue
        full = expand(pl["path"]).rstrip("/")
        repo = repos.get(full)
        home, pending, why = home_of(pl, repo)
        need = need_of(pl, mtime(full), now, repo)
        b = bucket_of(pl, home, pending, need, why)
        if "worktrees" in full and full.endswith("_data/worktrees"):
            for w in worktrees:
                take = min(w.get("kib") or 0, kib)
                if take <= 0:
                    continue
                kib -= take
                wn = "working" if (now - _ts(w.get("last_commit"))) < DAYS * 86400 or w.get("busy") else "cold"
                safe = w.get("pushed") and not w.get("dirty")
                wb = WORKING if wn == "working" else SAFE if safe else HERE
                wt_rows.append({"path": "~/SISO_Workspace/_data/worktrees/%s/%s" % (w["repo"], w["lane"]), "kib": take, "bucket": wb,
                                "home": "github:branch " + (w.get("branch") or "(detached)") if w.get("pushed") else "here", "need": wn,
                                "why": "dirty" if w.get("dirty") else "unpushed" if not w.get("pushed") else "clean and pushed",
                                "comes_back": "git worktree add (branch %s is on GitHub)" % (w.get("branch") or "detached HEAD") if safe else "nothing: unpushed or dirty work",
                                "only_here": not safe})
        rows.append({"path": pl["path"], "kib": kib, "bucket": b, "home": home or "-", "need": need, "why": why,
                     "comes_back": comes_back(home, pl["path"]), "rule": pl.get("rule"),
                     "only_here": home is None or home == "here" or pending})
    rows += wt_rows
    sums = {b: 0 for b in BUCKETS}
    for r in rows:
        sums[r["bucket"]] += r["kib"]
    sums[UNMAPPED] = sum(k for _, k in un)
    sysb = sum(v["bytes"] for v in volumes if v.get("role") in ("System", "Preboot", "Recovery", "VM")) // 1024
    sums[REBUILD] += sysb
    if sysb:
        rows.append({"path": "(macOS System, Preboot, Recovery, swap volumes)", "kib": sysb, "bucket": REBUILD, "home": "system",
                     "need": "auto", "why": "volumes outside Data", "comes_back": comes_back("system", ""), "rule": None})
    here_working = sum(r['kib'] for r in rows if r['bucket'] == WORKING and r.get('only_here'))
    return rows, sorted(un, key=lambda x: -x[1]), sums, here_working


def _ts(day):
    import datetime
    try:
        return datetime.datetime.fromisoformat(day).timestamp() if day and "T" in day else datetime.datetime.strptime(day, "%Y-%m-%d").timestamp()
    except (ValueError, TypeError):
        return 0
