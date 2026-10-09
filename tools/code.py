#!/usr/bin/env python3
"""The building code (goal-2050 P2): score every live repo on the map against plan/building-code.json.

  code.py [--machine laptop]        score every repo -> machines/<m>/code.json (+ one line in code-history.jsonl)
  code.py <words>                   one repo's certificate: which rules it passes and what each failure is
  code.py --check                   exit 1 when a repo that was up to code has fallen below it since the last record

Reads the disk and git for each repo, the hidden-folder findings in machines/<m>/dots.json (run `estate dots` first for
fresh ones) and the last nightly backup in machines/<m>/backup.json. Read-only: it never changes a repo.
"""
import argparse, collections, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.join(os.path.expanduser("~"), "SISO_Workspace")
sys.path.insert(0, HERE)
from houses import KEYS  # noqa: E402  the one key-file pattern

CODE = json.load(open(os.path.join(REPO, "plan", "building-code.json")))
OWNERS = json.load(open(os.path.join(REPO, "plan", "owners.json")))
# commits the estate itself made while tidying (sweeps, doors, housing) are not a building's own life
ESTATE_COMMITS = ["--invert-grep", "--grep=^estate", "--grep=Stop tracking tool runtime state", "--grep=front door", "--grep=siso-estate",
                  "--grep=^doors", "--grep=^AGENTS.md", "--grep=^memory: commit", "--grep=^house:", "--grep=^Rescue:", "--grep=^wip snapshot"]


def owner_seat(rel):
    """The owning seat: the longest owners.json prefix that holds this path."""
    best = None
    for o in OWNERS["owners"]:
        p = o["path"]
        if (p == "." and best is None) or rel == p or rel.startswith(p + "/"):
            if best is None or best["path"] == "." or (p != "." and len(p) > len(best["path"])):
                best = o
    return (best or {}).get("seat", "none"), bool((best or {}).get("state_land"))


def lifecycle(top):
    """active: own work in 14 days; warm: in 90; dormant: none in 90 (the estate's own tidying commits do not count)."""
    ts = git(top, "log", "-1", "--format=%ct", *ESTATE_COMMITS)
    days = (time.time() - int(ts)) / 86400 if ts else 99999
    return ("active" if days < 14 else "warm" if days < 90 else "dormant"), round(days, 1)
RULES = [r["id"] for r in CODE["rules"]]
EXAMPLE = re.compile(r"\.(example|sample|template|dist|defaults?)$|(^|/)example[^/]*$", re.I)
BACKUP_OK = {"pushed", "bundled", "clean", "nothing-to-push"}
NOT_BUILDINGS = re.compile(r"^(_reference|_archive|_data)/|/\.lake/packages/|/node_modules/")
SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:(api[_-]?key|secret|token|passw(or)?d|auth|private[_-]?key)[\w-]*)[\"']?\s*[:=]\s*"
                    r"[\"']?(?!\$|<|process\.env|your|xxx|changeme|example|replace|todo|none|null|true|false)([A-Za-z0-9_\-./+=]{16,})")


def looks_secret(path):
    """A file or folder that actually holds a key value (read locally, never printed), not just a key-sounding name."""
    if os.path.isdir(path):
        return any(looks_secret(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs)
    try:
        with open(path, errors="replace") as f:
            return bool(SECRET.search(f.read(65536)))
    except OSError:
        return False


def owner_kind(origin):
    m = re.search(r"github\.com[:/]([^/]+)/", origin or "")
    who = m.group(1).lower() if m else ""
    if not origin or who in [o.lower() for o in CODE["own_owners"]]:
        return "own", who
    if who in [o.lower() for o in CODE["client_owners"]]:
        return "client", who
    return "foreign", who or (origin.split("/")[2] if "//" in origin else origin[:40])


def git(top, *a):
    r = subprocess.run(["git", "-C", top, *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def live_repos():
    paths = ["."] + sorted(json.load(open(os.path.join(WS, ".estate", "map.json")))["repos"])
    return [p for p in paths if os.path.exists(os.path.join(WS, p, ".git")) and not NOT_BUILDINGS.search(p + "/")]


def not_buildings():
    """Map entries that are not buildings: others' code on the study shelf, and package-manager clones."""
    paths = sorted(json.load(open(os.path.join(WS, ".estate", "map.json")))["repos"])
    return [p for p in paths if os.path.exists(os.path.join(WS, p, ".git")) and NOT_BUILDINGS.search(p + "/")]


def owner_of(path, repos):
    """The deepest scored repo that holds this workspace-relative path."""
    best = None
    for r in repos:
        if r == "." or path == r or path.startswith(r + "/"):
            if best is None or best == "." or (r != "." and len(r) > len(best)):
                best = r
    return best


def findings_by_repo(machine, repos):
    try:
        d = json.load(open(os.path.join(REPO, "machines", machine, "dots.json")))
    except (OSError, ValueError):
        return {}, None
    rule_of = {"retired conventions": "no-retired", "tool state tracked in git": "no-tool-state",
               "runtime files tracked": "no-tool-state", "key-looking files in hidden folders": "no-keys",
               "key stores outside .credentials": "no-keys", "worktrees inside a repo": "worktrees"}
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for kind, items in d.get("findings", {}).items():
        rule = rule_of.get(kind)
        for it in items if rule else []:
            if rule in ("no-retired", "no-tool-state") and it.get("tracked_files", 1) == 0:
                continue                           # an untracked folder the ignore already hides is not in the building
            r = owner_of(it["path"], repos)
            if r:
                out[r][rule].append(os.path.relpath(it["path"], r) if r != "." else it["path"])
    return out, d.get("at")


def backups(machine):
    try:
        b = json.load(open(os.path.join(REPO, "machines", machine, "backup.json")))
    except (OSError, ValueError):
        return {}, None
    return {os.path.relpath(r["path"], WS): r for r in b.get("results", []) if str(r.get("path", "")).startswith(WS)}, b.get("ran_at")


def norm(n):
    return n.lower().replace("_", "-").strip("-")


def check(rel, dots, backup):
    top = os.path.normpath(os.path.join(WS, rel))
    res = {}

    def put(rule, ok, note=""):
        res[rule] = {"ok": ok, **({"note": note} if note else {})}

    put("door", os.path.isfile(os.path.join(top, "AGENTS.md")), "" if os.path.isfile(os.path.join(top, "AGENTS.md")) else "no AGENTS.md")
    cl = os.path.join(top, "CLAUDE.md")
    if os.path.islink(cl) and os.path.basename(os.readlink(cl)) == "AGENTS.md":
        put("shim", True)
    elif not os.path.isfile(cl):
        put("shim", False, "no CLAUDE.md")
    else:
        body = open(cl, errors="replace").read()
        put("shim", re.sub(r"\s+", "", body) == "@AGENTS.md", "" if re.sub(r"\s+", "", body) == "@AGENTS.md" else f"CLAUDE.md holds {len(body.splitlines())} lines")
    put("agents", os.path.isdir(os.path.join(top, ".agents")), "" if os.path.isdir(os.path.join(top, ".agents")) else "no .agents/")
    for rule in ("no-retired", "no-tool-state"):
        hits = dots.get(rule, [])
        put(rule, not hits, ", ".join(sorted(hits)[:4]) + (f" (+{len(hits) - 4})" if len(hits) > 4 else "") if hits else "")
    tracked = (git(top, "ls-files", "-z") or "").split("\0")
    keys = [f for f in tracked if f and KEYS.search(f) and not EXAMPLE.search(f) and looks_secret(os.path.join(top, f))]
    hits = sorted(set([p for p in dots.get("no-keys", []) if looks_secret(os.path.join(top, p))] + [f + " (tracked)" for f in keys]))
    put("no-keys", not hits, ", ".join(hits[:3]) + (f" (+{len(hits) - 3})" if len(hits) > 3 else "") if hits else "")
    origin = git(top, "remote", "get-url", "origin") or ""
    put("on-github", "github.com" in origin, "" if "github.com" in origin else ("no origin" if not origin else "origin is not GitHub"))
    upstream = git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    branch = git(top, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        put("pushed", False, "detached HEAD")
    elif not upstream:
        put("pushed", False, f"{branch} has no upstream")
    else:
        ahead = int(git(top, "rev-list", "--count", "@{u}..HEAD") or 0)
        put("pushed", ahead == 0, f"{ahead} commits not on {upstream}" if ahead else "")
    b = backup.get(rel)
    if b is None:
        res["backed-up"] = {"ok": None, "note": "not in the last backup run"}
    elif b.get("status") == "skip":
        res["backed-up"] = {"ok": None, "note": "skipped by design: " + str(b.get("reason") or b.get("action") or "")[:60]}
    else:
        put("backed-up", b.get("status") in BACKUP_OK, "" if b.get("status") in BACKUP_OK else f"last run: {b.get('status')}")
    bad = []
    for block in (git(top, "worktree", "list", "--porcelain") or "").split("\n\n")[1:]:
        wt = next((l[9:] for l in block.splitlines() if l.startswith("worktree ")), None)
        if not wt:
            continue
        if not os.path.exists(wt):
            bad.append(f"{os.path.basename(wt)} (stale record)")
        elif not wt.startswith(os.path.join(WS, "_data", "worktrees") + "/"):
            bad.append(wt.replace(os.path.expanduser("~"), "~"))
    bad += [p + " (inside)" for p in dots.get("worktrees", [])]
    put("worktrees", not bad, ", ".join(bad[:3]) + (f" (+{len(bad) - 3})" if len(bad) > 3 else "") if bad else "")
    name = re.sub(r"\.git$", "", origin.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]) if origin else ""
    if rel in CODE["named_exceptions"]:
        res["named"] = {"ok": None, "note": CODE["named_exceptions"][rel]}
    elif not name:
        res["named"] = {"ok": None, "note": "no origin to compare"}
    else:
        base = os.path.basename(top)
        parent = os.path.basename(os.path.dirname(top))
        # the repo's name, allowing the siso- namespace prefix, a parent-prefixed name (oracle/core = oracle-core) and a
        # client's code/ folder (clients/<brand>/code = <brand>)
        same = norm(name) in (norm(base), "siso-" + norm(base), norm(parent) + "-" + norm(base)) or (base == "code" and norm(name) == norm(parent))
        put("named", same, "" if same else f"folder {base}, repo {name}")
    kind, who = owner_kind(origin)
    res["ours"] = {"ok": kind != "foreign", "note": {"own": "", "client": CODE["client_owners"].get(who, "")}.get(kind, f"{who}'s code")}
    if kind != "own":
        for k in RULES:
            if k not in (CODE["client_rules"] if kind == "client" else ["ours"]):
                res[k] = {"ok": None, "note": "a client's repo: our house rules do not apply" if kind == "client" else "not ours"}
    passed = sum(1 for v in res.values() if v["ok"])
    applicable = sum(1 for v in res.values() if v["ok"] is not None)
    seat, state_land = owner_seat(rel)
    life, idle = lifecycle(top)
    return {"path": rel, "district": rel.split("/")[0] if rel != "." else "(the map)", "origin": origin, "owner": kind,
            "seat": seat, "state_land": state_land, "lifecycle": life, "idle_days": idle,
            "score": round(passed / applicable, 3) if applicable else 1.0, "up_to_code": passed == applicable,
            "failed": [k for k in RULES if res[k]["ok"] is False], "rules": res}


def score(machine):
    repos = live_repos()
    dots, dots_at = findings_by_repo(machine, repos)
    backup, backup_at = backups(machine)
    rows = [check(r, dots.get(r, {}), backup) for r in repos]
    by_rule = {k: sum(1 for x in rows if k in x["failed"]) for k in RULES}
    by_district = collections.defaultdict(lambda: {"repos": 0, "up_to_code": 0})
    for x in rows:
        by_district[x["district"]]["repos"] += 1
        by_district[x["district"]]["up_to_code"] += x["up_to_code"]
    return {"machine": machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "code": "plan/building-code.json",
            "inputs": {"dots": dots_at, "backup": backup_at}, "repos": len(rows),
            "by_owner": dict(collections.Counter(x["owner"] for x in rows)), "not_buildings": len(not_buildings()),
            "by_lifecycle": dict(collections.Counter(x["lifecycle"] for x in rows if x["owner"] != "foreign")),
            "by_seat": {s: {"buildings": len(v), "up_to_code": sum(x["up_to_code"] for x in v),
                            "mean_score": round(sum(x["score"] for x in v) / len(v), 3), "state_land": sum(x["state_land"] for x in v)}
                        for s, v in sorted({k: [x for x in rows if x["seat"] == k and x["owner"] != "foreign"]
                                            for k in {x["seat"] for x in rows if x["owner"] != "foreign"}}.items())},
            "up_to_code": sum(x["up_to_code"] for x in rows), "mean_score": round(sum(x["score"] for x in rows) / max(1, len(rows)), 3),
            "failing_by_rule": by_rule, "by_district": dict(sorted(by_district.items())), "results": rows}


def certificate(x):
    print(f"{x['path']}  {int(x['score'] * 100)}%  {'up to code' if x['up_to_code'] else 'below code'}")
    for r in CODE["rules"]:
        v = x["rules"][r["id"]]
        mark = "pass" if v["ok"] else ("n/a " if v["ok"] is None else "FAIL")
        print(f"  {mark}  {r['name']}" + (f": {v['note']}" if v.get("note") else "") + (f"\n        fix: {r['fix']}" if v["ok"] is False else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("words", nargs="*")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    out = os.path.join(REPO, "machines", a.machine, "code.json")
    if a.words:
        want = [w.lower() for w in a.words]
        hits = [r for r in live_repos() if all(w in r.lower() for w in want)]
        if not hits:
            sys.exit(f"no live repo matches {' '.join(a.words)}")
        repos = live_repos()
        dots, _ = findings_by_repo(a.machine, repos)
        backup, _ = backups(a.machine)
        for r in hits[:5]:
            certificate(check(r, dots.get(r, {}), backup))
        return
    before = {}
    if os.path.exists(out):
        try:
            before = {x["path"]: x["up_to_code"] for x in json.load(open(out))["results"]}
        except (OSError, ValueError, KeyError):
            pass
    d = score(a.machine)
    fell = [x for x in d["results"] if before.get(x["path"]) and not x["up_to_code"]]
    with open(out, "w") as f:
        json.dump(d, f, indent=1)
    with open(os.path.join(REPO, "machines", a.machine, "code-history.jsonl"), "a") as f:
        f.write(json.dumps({"at": d["at"], "repos": d["repos"], "up_to_code": d["up_to_code"], "mean_score": d["mean_score"],
                            "failing_by_rule": d["failing_by_rule"], "fell": [x["path"] for x in fell]}) + "\n")
    print(f"building code: {d['up_to_code']} of {d['repos']} live repos up to code; mean score {int(d['mean_score'] * 100)}%"
          f"  ({', '.join(f'{v} {k}' for k, v in d['by_owner'].items())}; {d['not_buildings']} study-shelf or package clones not scored)")
    for r in CODE["rules"]:
        print(f"  {d['failing_by_rule'][r['id']]:4d} fail  {r['name']}")
    for x in fell:
        print(f"  fell below code: {x['path']} ({', '.join(x['failed'])})")
    if a.check and fell:
        sys.exit(1)


if __name__ == "__main__":
    main()
