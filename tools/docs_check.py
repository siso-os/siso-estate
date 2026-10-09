#!/usr/bin/env python3
"""Doors that keep themselves (MODEL §9, invariant I11): check every path a door cites, and say which broke.

  docs_check.py [--machine laptop]   check every own/client building's AGENTS.md -> machines/<m>/docs-check.json
  docs_check.py --json               print that record instead of the summary
  docs_check.py --check              change nothing; exit 1 when a citation is broken (for CI, commit and nightly)

A door (AGENTS.md) is the one screen an agent reads where it stands, so its citations are claims about code and are
checked, never trusted (§9: a document block is generated, checked or a journal). Every backticked token that looks
like a path is resolved against where the door stands; one that no longer resolves is a pebble when a recorded move
explains it (machines/<site>/moves.jsonl, plan/link-retire/RETIRED.txt) and gets a suggestion, and a rock for the
building's keeper otherwise. A command line is not a path, so a token with whitespace is not a citation.

Read-only: it writes the one record and never edits a door, a mapping or any other file.
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.join(os.path.expanduser("~"), "SISO_Workspace")
HOME = os.path.expanduser("~")
MAIN = os.path.join(WS, "SISO_Agents", "siso-estate")  # the main checkout: the register lives there, not in a room
GITHUB = os.path.join(MAIN, "machines", "github", "sisodias.json")
ALWAYS_OWNERS = {"sisodias", "camronkellman", "lordsisodia"}  # the account was renamed; old URLs still redirect

BACKTICK = re.compile(r"`([^`\n]+)`")
STRIP = ".,;:)"
NOT_PATH = set('*<>{} $|"\'=;')          # markers, shell syntax: a citation, not a path
GIT_REF = ("origin/", "upstream/", "refs/")
SKIP_START = ("http://", "https://", "git@", "-")


def owners_from_github(path=GITHUB):
    """The GitHub account names, from the repo list (owner/repo tokens are postcodes, not paths)."""
    names = set(ALWAYS_OWNERS)
    try:
        with open(path) as f:
            gh = json.load(f)
        for r in gh:
            full = r.get("nameWithOwner") or f"{r.get('owner', {}).get('login', '')}/{r.get('name', '')}"
            if "/" in full:
                names.add(full.split("/")[0].lower())
    except (OSError, ValueError):
        pass
    return names


FS_ROOTS = {"Users", "opt", "etc", "tmp", "var", "usr", "root", "home", "private", "Applications", "Library", "Volumes", "bin", "srv", "mnt"}
ELSEWHERE = {"opt", "srv", "root", "home", "tmp", "mnt"}        # a VPS path or a runtime file: not this disk's claim
PLACEHOLDER = ("YYYY", "MM-DD", "NNNN")                                  # a template for a name, not a name


def looks_like_path(tok, owners):
    """A citation is a path: it has a slash, no markers or shell syntax, no whitespace, and is not a ref or command."""
    if "/" not in tok or any(c.isspace() for c in tok):
        return False
    if tok.startswith(SKIP_START) or set(tok) & NOT_PATH:
        return False
    if tok.startswith("/") and tok.count("/") == 1:   # a slash command (/compact), not a path
        return False
    if tok.startswith(GIT_REF):                        # origin/main
        return False
    if tok.startswith("/") and tok.split("/")[1] not in FS_ROOTS:   # an API route (/tables/create), not a file
        return False
    if tok.startswith("/") and tok.split("/")[1] in ELSEWHERE:     # /opt/... lives on a VPS, /tmp/... at runtime
        return False
    if tok.startswith("@") or any(p in tok for p in PLACEHOLDER):  # @scope/package, _archive/YYYY-MM-DD-subject/
        return False
    parts = tok.split("/")
    return not (len(parts) == 2 and parts[0].lower() in owners)  # owner/repo is a postcode


def citations(text, owners):
    """Every path a door cites, once each, in the order it cites them."""
    out = {}
    for raw in BACKTICK.findall(text):
        tok = raw.strip().rstrip(STRIP)
        if looks_like_path(tok, owners):
            out.setdefault(tok, None)
    return list(out)


def candidates(tok, door_dir, ws=WS, home=HOME, tops=None):
    """Every absolute place a token could mean: where the door stands, up its parents to the workspace, the workspace
    itself, and each island (§12: a door cites paths relative to where it stands)."""
    if tok.startswith("~/"):
        return [os.path.join(home, tok[2:])]
    if tok.startswith("/"):
        return [tok]
    out = [os.path.join(door_dir, tok)]
    parent = os.path.dirname(os.path.abspath(door_dir))
    while parent == ws or parent.startswith(ws + os.sep):
        out.append(os.path.join(parent, tok))
        if parent == ws:
            break
        parent = os.path.dirname(parent)
    out.append(os.path.join(ws, tok))
    if tops is None:
        try:
            tops = sorted(os.listdir(ws))
        except OSError:
            tops = []
    out += [os.path.join(ws, t, tok) for t in tops]
    return out


def mappings(moves_path, retired_path, ws=WS, home=HOME):
    """Recorded moves: (old absolute path, new absolute path), longest old first so the nearest move wins."""
    out = []
    try:  # moves.jsonl: one move per line, both ends absolute
        with open(moves_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("src") and d.get("dst"):
                    out.append((d["src"].rstrip("/"), d["dst"].rstrip("/")))
    except OSError:
        pass
    try:  # RETIRED.txt: `old -> new`, old may start with ~/, new is relative to the workspace
        with open(retired_path) as f:
            for line in f:
                if "->" not in line:
                    continue
                old, new = (p.strip() for p in line.split("->", 1))
                if not old:
                    continue
                if old.startswith("~/"):
                    old = os.path.join(home, old[2:])
                elif not old.startswith("/"):
                    old = os.path.join(ws, old)
                new = new if new.startswith("/") else os.path.join(ws, new)
                out.append((old.rstrip("/"), new.rstrip("/")))
    except OSError:
        pass
    return sorted(out, key=lambda m: -len(m[0]))


def resolves(tok, door_dir, ws=WS, home=HOME, tops=None):
    """Does this citation still resolve from where the door stands?"""
    return any(os.path.exists(c) for c in candidates(tok, door_dir, ws, home, tops))


def explain(cands, maps, exists=os.path.exists):
    """The suggestion for a broken citation whose old ground a recorded move names, else None. Moves are followed to the
    end of their chain (a folder moved twice), and a suggestion counts only when it exists on disk: a repair is made by
    code, so it must be certain (docs/MODEL.md section 10)."""
    for c in cands:
        cur, hops = c, 0
        while hops < 10:
            step = next((new + cur[len(old):] for old, new in maps if cur == old or cur.startswith(old + "/")), None)
            if not step or step == cur:
                break
            cur, hops = step, hops + 1
        if hops and exists(cur):
            return cur
    return None


def ignored(tok, door_dir):
    """A relative citation the door's own repo ignores is runtime state (inbox/, dist/, state/.afk): it is made by
    running the thing, so its absence is not a broken claim."""
    if tok.startswith(("~/", "/", "../")):
        return False
    try:
        r = subprocess.run(["git", "-C", door_dir, "check-ignore", "-q", "--no-index", tok.rstrip("/")],
                           capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


RUNTIME_HOME = ("~/.cache/", "~/Library/Caches/")                   # a cache file is made by running, not a claim


def door_verdict(text, door_dir, owners=frozenset(), maps=(), ws=WS, home=HOME, tops=None, adopted=False, names=frozenset()):
    """One door's citations: how many resolve, which a move explains (with the suggested path), which are missing.
    In an adopted fork the relative paths are upstream's own prose about its tree, so only ~ and absolute paths (the
    estate's claims about where things are) are checked there."""
    v = {"cited": 0, "resolved": 0, "moved": [], "missing": []}
    for tok in citations(text, owners):
        if adopted and not tok.startswith(("~/", "/")):
            continue
        if tok.rstrip("/") in names or tok.startswith(RUNTIME_HOME):   # a compound address (engine/x) is a name
            continue
        v["cited"] += 1
        if resolves(tok, door_dir, ws, home, tops):
            v["resolved"] += 1
            continue
        # an absolute or ~ citation names one place; a relative one is only trusted inside its own door's folder
        cands = candidates(tok, door_dir, ws, home, tops)
        if not tok.startswith(("~/", "/")):
            cands = [c for c in cands if c.startswith(os.path.normpath(door_dir) + "/")]
        suggest = explain(cands, maps)
        if suggest:
            v["moved"].append({"token": tok, "suggest": suggest})
        elif ignored(tok, door_dir):
            v["runtime"] = v.get("runtime", 0) + 1
        else:
            v["missing"].append(tok)
    return v


def scan(machine="laptop", main=MAIN, ws=WS, home=HOME):
    """Check every own/client building's door. Reads the register's code record; touches nothing but the returned dict."""
    with open(os.path.join(main, "machines", machine, "code.json")) as f:
        code = json.load(f)
    owners = owners_from_github(os.path.join(main, "machines", "github", "sisodias.json"))
    maps = mappings(os.path.join(main, "machines", machine, "moves.jsonl"),
                    os.path.join(main, "plan", "link-retire", "RETIRED.txt"), ws, home)
    try:
        tops = sorted(os.listdir(ws))
    except OSError:
        tops = []
    try:  # the register's provenance: an adopted building is a fork of someone else's project
        with open(os.path.join(main, "machines", "register.json")) as f:
            reg = json.load(f)
        adopted = {b["path"] for b in reg["buildings"] if b.get("provenance") == "adopted"}
        names = frozenset(c["id"] for c in reg.get("compounds", []))
    except (OSError, ValueError, KeyError):
        adopted, names = set(), frozenset()
    doors, by_door, totals = 0, [], {"cited": 0, "resolved": 0, "moved": 0, "missing": 0}
    for b in sorted(code["results"], key=lambda x: x["path"]):
        if b.get("owner") not in ("own", "client"):
            continue
        root = os.path.join(ws, b["path"])
        door = os.path.join(root, "AGENTS.md")
        if not os.path.exists(door):
            continue
        doors += 1
        try:
            with open(door, errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        v = door_verdict(text, root, owners, maps, ws, home, tops, adopted=b["path"] in adopted, names=names)
        totals["cited"] += v["cited"]
        totals["resolved"] += v["resolved"]
        totals["moved"] += len(v["moved"])
        totals["missing"] += len(v["missing"])
        if v["moved"] or v["missing"]:
            by_door.append({"path": b["path"], "cited": v["cited"], "moved": v["moved"], "missing": v["missing"]})
    return doors, totals, by_door


def record(machine="laptop", main=MAIN, ws=WS, home=HOME):
    """The record: totals over every door checked, plus the doors with a problem (a clean door is not listed)."""
    doors, totals, by_door = scan(machine, main, ws, home)
    return {"machine": machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "doors": doors,
            "cited": totals["cited"], "resolved": totals["resolved"], "moved": totals["moved"],
            "missing": totals["missing"], "by_door": sorted(by_door, key=lambda x: x["path"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 when a citation is broken")
    ap.add_argument("--json", action="store_true", help="print the record instead of the summary")
    a = ap.parse_args()
    d = record(a.machine)
    if not a.check:  # --check reports; only a real run leaves the record
        out = os.path.join(REPO, "machines", a.machine, "docs-check.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            json.dump(d, f, indent=1)
    if a.json:
        print(json.dumps(d, indent=1))
    else:
        broken = d["moved"] + d["missing"]
        print(f"docs check ({d['machine']}): {d['doors']} doors, {d['cited']} paths cited, {d['resolved']} resolve, "
              f"{broken} broken ({d['moved']} explained by a recorded move, {d['missing']} missing) "
              f"in {len(d['by_door'])} doors")
        worst = sorted(d["by_door"], key=lambda x: (-(len(x["moved"]) + len(x["missing"])), x["path"]))[:5]
        for x in worst:
            print(f"  {x['path']}  {len(x['moved']) + len(x['missing'])} broken ({len(x['moved'])} moved, "
                  f"{len(x['missing'])} missing)")
            for m in x["moved"][:3]:
                print(f"    moved   {m['token']} -> {m['suggest']}")
            for t in x["missing"][:3]:
                print(f"    missing {t}")
            rest = len(x["moved"]) + len(x["missing"]) - min(3, len(x["moved"])) - min(3, len(x["missing"]))
            if rest > 0:
                print(f"    … {rest} more in the record")
    if a.check and d["moved"] + d["missing"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
