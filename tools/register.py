#!/usr/bin/env python3
"""The register (ADR 0018, docs/MODEL.md): one generated record of the whole estate, so every list, map, page and render
is a view of it and nothing is hand-kept (Shaan, 25 Sep: "it relies on an agent to go through and change it, rather
than somehow code changing it").

  register.py            write machines/register.json and print a summary with the doctor's findings
  register.py --check    write nothing; exit 1 when an invariant the register can test fails (I1, I9)
  register.py --json     print the register instead of the summary

Every plot on the umbrella map (~/SISO_Workspace/.estate/map.json) becomes a building, placed on its island, district and
compound by the rules in plan/model.json. It is given a postcode (its GitHub name), a provenance
(ours / adopted / client / foreign), a keeper seat (plan/owners.json, longest prefix, else the island's governor), where it is
built and what runs from it (the machine records), its lifecycle (the building code; dark when built on no site), its
size and 14-day activity (for the render), and its code score. Nothing is changed anywhere else.
"""
import collections, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import legend  # partners and their clients (plan/legend.json)

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "machines", "register.json")


# the estate's own commits (doors, untracking, repoints, fixes) are not work; the same filter as tools/code.py lifecycle
WORK_ONLY = ["--invert-grep", "--grep=^estate", "--grep=Stop tracking tool runtime state", "--grep=front door", "--grep=siso-estate",
             "--grep=^doors", "--grep=^AGENTS.md", "--grep=^memory: commit", "--grep=^house:", "--grep=^Rescue:", "--grep=^wip snapshot"]


def load(rel, default=None):
    p = rel if rel.startswith("/") else os.path.join(REPO, rel)
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


MODEL = load("plan/model.json")
CODE_RULES = load("plan/building-code.json", {})
OWN = {o.lower() for o in CODE_RULES.get("own_owners", ["sisodias"])} | {"lordsisodia"}  # Shaan's earlier GitHub account
CLIENT = {o.lower() for o in CODE_RULES.get("client_owners", {})}
ISLANDS = {i["folder"]: i for i in MODEL["islands"]}
ISLETS = {i["folder"]: i for i in MODEL["islets"]}
QUIET = {".DS_Store", "AGENTS.md", "CLAUDE.md", "README.md", "manifest.md", ".gitkeep", ".gitignore"}


def git(path, *a):
    r = subprocess.run(["git", "-C", path, *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def gh_name(url):
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return (m.group(1), m.group(2)) if m else None


def postcode_of(url, path):
    n = gh_name(url)
    if n:
        return (n[1] if n[0].lower() in OWN else f"{n[0]}/{n[1]}"), None
    if url:
        host = re.sub(r"^\w+://|^git@", "", url).split("/")[0].split(":")[0]
        return f"{host}:{path}", "remote not on GitHub"
    return f"local:{path}", "no remote"


def place(path):
    """(island id, district, compound id, compound folder) for a workspace-relative path."""
    if path in (".", ".agents") or path.startswith(".agents/"):
        return "territory", None, "territory/the-map", "."
    parts = path.split("/")
    top = parts[0]
    isl = ISLANDS.get(top) or ISLETS.get(top)
    iid = isl["id"] if isl else "unmapped"
    districts = set((ISLANDS.get(top) or {}).get("districts", []))
    district = parts[1] if len(parts) > 1 and parts[1] in districts else None
    depth = 3 if district else 2
    if district == "partners":  # SISO_Agency/partners/<agency> is the partner's hall; its systems and clients are compounds
        depth = 5 if len(parts) >= 5 and parts[3] == "clients" else 4
    if len(parts) < depth:  # the island's (or district's) own building: its hall
        folder = "/".join(parts)
        return iid, district, f"{iid}/{'/'.join(parts[1:]) or '_hall'}" + ("/_hall" if district and len(parts) == 2 else ""), folder
    folder = "/".join(parts[:depth])
    return iid, district, "/".join([iid] + parts[1:depth]), folder


STUDY = re.compile(r"/(research|runs|lab|references?|external-themes|\.research|source-inventories)/|github-themes/")
VENDOR = re.compile(r"/vendor/|/\.[^/]*-runtime$|-runtime$")


def foreign_role(path, iid, b):
    """Foreign code has four roles (docs/MODEL.md principle 10): shared study (the foreign quarter), study bound to one
    work, vendored into a building, or in use (run or changed, so it should be adopted). Dormant or dark clones are stale."""
    if b["runs"]:
        return "in use"
    if iid == "foreign":
        return "study (shared)"
    if STUDY.search(path + "/"):
        return "study (work-bound)"
    if VENDOR.search(path):
        return "vendored"
    if b["lifecycle"] in ("dormant", "dark", "archived"):
        return "stale"
    return "in use"


def door_line(path):
    """The building's own one line when GitHub has no description: its AGENTS.md "In one line", else README's first paragraph."""
    if re.search(r"(^|/)personal(/|$)|life|whatsapp|fahmy", path, re.I):  # private: never copied into git
        return None
    root = os.path.join(WS, path)
    for f in ("AGENTS.md", "README.md"):
        try:
            t = open(os.path.join(root, f), errors="ignore").read(20000)
        except OSError:
            continue
        m = re.search(r"\*\*In one line:\*\*\s*(.+)", t)
        if m:
            return re.sub(r"\s*District:.*$", "", m.group(1)).strip()
        for para in re.split(r"\n\s*\n", t):
            line = " ".join(l.strip() for l in para.splitlines()).strip()
            if line and not re.match(r"^(#|<|!\[|\[!|```|\||-{3}|>|@)", line) and len(line) > 30:
                return re.sub(r"[*_`]|\[([^]]*)\]\([^)]*\)", lambda m: m.group(1) or "", line)
    return None


def seat_of(path, owners):
    best = None
    for o in owners:
        p = o["path"]
        if path == p or p == "." or path.startswith(p + "/"):
            if best is None or len(p) > len(best["path"]) or best["path"] == ".":
                best = o
    return best


def main():
    t0 = time.time()
    mp = load(os.path.join(WS, ".estate", "map.json"))
    plots = dict(mp["repos"])
    plots.setdefault(".", {"url": "https://github.com/sisodias/siso-city.git", "on": ["laptop"]})
    code = {r["path"]: r for r in (load("machines/laptop/code.json") or {}).get("results", [])}
    inv = {x["path"][len(WS) + 1:] if x["path"] != WS else ".": x
           for x in (load("machines/laptop/repos.json") or {}).get("repos", []) if x["path"] == WS or x["path"].startswith(WS + "/")}
    gh = {x["nameWithOwner"].lower(): x for x in (load("machines/github/sisodias.json") or [])}
    vps_places = list(load("machines/vps-siso/placements.json", {}).get("placements", {}).items())
    vps_runs = collections.defaultdict(list)
    for at, mpath in vps_places:
        vps_runs[mpath].append(at)
    owners = load("plan/owners.json", {}).get("owners", [])
    seats_named = load("plan/owners.json", {}).get("seats", {})
    machines = load("plan/machines.json", {}).get("machines", {})

    buildings, issues = [], collections.defaultdict(list)
    by_postcode = collections.defaultdict(list)
    for path in sorted(plots):
        plot = plots[path]
        url = plot.get("url") or ""
        iid, district, cid, cfolder = place(path)
        pc, pc_issue = postcode_of(url, path)
        if pc_issue:
            issues["I1 " + pc_issue].append(path)
        by_postcode[pc].append(path)
        n = gh_name(url)
        who = n[0].lower() if n else ""
        cr = code.get(path, {})
        iv = inv.get(path, {})
        remotes = iv.get("remotes") or {}
        ghrow = gh.get(f"{n[0]}/{n[1]}".lower()) if n else None
        if who in CLIENT:
            prov, upstream = "client", url
        elif who in OWN or not who:
            up = remotes.get("upstream")
            if up and not up.startswith("/"):
                prov, upstream = "adopted", up
            elif ghrow and ghrow.get("isFork"):
                prov, upstream = "adopted", "GitHub fork (parent not recorded)"
            else:
                prov, upstream = "ours", None
        else:
            prov, upstream = "foreign", url
        built = os.path.exists(os.path.join(WS, path, ".git")) if path != "." else True
        on = set(plot.get("on") or [])
        if built:
            on.add("laptop")
        elif "laptop" in on:
            on.discard("laptop")
        if path in vps_runs:
            on.add("siso-vps")
        so = seat_of(path, owners)
        seat = cr.get("seat") or (so["seat"] if so else (ISLANDS.get(path.split("/")[0], {}).get("governor")))
        state_land = cr.get("state_land", so is None or so["path"] in (".",) or so["path"] == path.split("/")[0])
        if ghrow and ghrow.get("isArchived"):
            life = "archived"
        elif cr.get("lifecycle"):
            life = cr["lifecycle"]
        else:
            life = "dark" if not on else "unscored"
        act = None
        if built and path != ".":
            c = git(os.path.join(WS, path), "rev-list", "--count", "--since=14.days", *WORK_ONLY, "HEAD")  # work, not the estate's tidying
            act = int(c) if c.isdigit() else None
        po = legend.partner_of(path)
        b = {"postcode": pc, "path": path, "island": iid, "district": district, "compound": cid,
             "partner": po[0] if po else None, "client_of": (po[0] if po and po[1] else None), "client": po[1] if po else None,
             "law": (legend.law(po[0]) if po else None) or (ISLANDS.get(path.split("/")[0]) or {}).get("law"),
             "provenance": prov, "upstream": upstream, "seat": seat, "state_land": bool(state_land),
             "lifecycle": life, "built_on": sorted(on), "runs": [{"site": "siso-vps", "at": a} for a in vps_runs.get(path, [])],
             "size_kb": iv.get("git_kb") or (ghrow or {}).get("diskUsage"), "commits_14d": act,
             "code_score": cr.get("score"), "up_to_code": cr.get("up_to_code"), "failed": cr.get("failed", []),
             "description": (((ghrow or {}).get("description") or "") or (door_line(path) if built and path != "." else "") or "")[:200] or None}
        if b["compound"] and cfolder == path:
            b["gatehouse"] = True
        buildings.append(b)
        if prov == "foreign":
            b["foreign_role"] = foreign_role(path, iid, b)
            if b["foreign_role"] == "in use":
                issues["I5 foreign code in use or running (adopt it: fork, upstream remote, keeper)"].append(path)
            elif b["foreign_role"] == "study (work-bound)" and iid not in ("vault",):
                issues["foreign study clones inside a work (keep untracked; the register marks them study)"].append(path)
            elif b["foreign_role"] == "vendored":
                issues["foreign code vendored into a building (prefer a package, or adopt it)"].append(path)
            elif b["foreign_role"] == "stale" and iid not in ("vault", "foreign"):
                issues["pebbles: stale foreign clones on an island (to the vault)"].append(path)
        if iid == "unmapped":
            issues["I2 plot outside every island"].append(path)
        if po and not path.startswith(legend.PARTNERS_DIR + "/"):
            issues["legend: a partner's building outside SISO_Agency/partners (the partner moves, docs/LEGEND.md §8)"].append(path)
        for f in cr.get("failed", []):
            if f in ("no-tool-state", "worktrees", "no-keys"):
                issues[f"I6 {f}"].append(path)
    def declared_submodule(path):
        """A plot its parent repo declares in .gitmodules is the same repo checked out on purpose (A3 allows it)."""
        parts = path.split("/")
        for i in range(len(parts) - 1, 0, -1):
            gm = os.path.join(WS, *parts[:i], ".gitmodules")
            if os.path.isfile(gm):
                rel = "/".join(parts[i:])
                with open(gm, errors="replace") as fh:
                    if re.search(r"^\s*path\s*=\s*" + re.escape(rel) + r"\s*$", fh.read(), re.M):
                        return True
        return False

    for pc, paths in by_postcode.items():
        if len(paths) > 1:
            live = [x for x in paths if not x.startswith("_archive/") and not declared_submodule(x)]
            if len(live) + sum(1 for x in paths if x.startswith("_archive/")) < 2:
                continue
            key = ("I1 one repo on two live plots (second homes)" if len(live) > 1 else
                   "I1 a repo archived on GitHub still has a live plot beside its vault plot")
            issues[key].append(f"{pc}: {', '.join(paths)}")

    # compounds
    comp = collections.OrderedDict()
    for b in buildings:
        c = comp.setdefault(b["compound"], {"id": b["compound"], "island": b["island"], "district": b["district"],
                                            "folder": None, "gatehouse": None, "buildings": [], "keeper": None, "lifecycle": None})
        c["buildings"].append(b["postcode"])
        if b.get("gatehouse"):
            c["gatehouse"] = b["postcode"]
    rank = {"active": 0, "warm": 1, "unscored": 2, "dormant": 3, "dark": 4, "archived": 5}
    for c in comp.values():
        mem = [b for b in buildings if b["compound"] == c["id"]]
        c["folder"] = place(mem[0]["path"])[3]
        c["lifecycle"] = min((b["lifecycle"] for b in mem), key=lambda s: rank.get(s, 9))
        gate = [b for b in mem if b.get("gatehouse")]
        c["keeper"] = (gate[0]["seat"] if gate else collections.Counter(b["seat"] for b in mem).most_common(1)[0][0])
        c["provenance"] = dict(collections.Counter(b["provenance"] for b in mem))
        live = [b for b in mem if b["provenance"] in ("ours", "adopted", "client")]
        if len(live) >= 2 and not c["gatehouse"] and c["island"] not in ("territory", "foreign", "utilities", "vault", "customs"):
            issues["I2 compound with several buildings and no gatehouse"].append(f"{c['id']} ({len(live)})")
        full = os.path.join(WS, c["folder"])
        if not c["gatehouse"] and os.path.isdir(full) and c["folder"] not in (".",):
            bpaths = {b["path"] for b in mem}
            loose = [e for e in os.listdir(full) if e not in QUIET and not e.startswith(".")
                     and os.path.join(c["folder"], e) not in bpaths
                     and not any(p.startswith(os.path.join(c["folder"], e) + "/") for p in bpaths)]
            if loose and len(bpaths) >= 1 and c["island"] not in ("territory",):
                c["loose"] = len(loose)
                if len(loose) >= 5:
                    issues["pebbles: compound folders holding loose files outside any building"].append(f"{c['folder']} ({len(loose)})")

    # git folders in the workspace that are not plots (outside the vault, rooms and runtime)
    for p in sorted(inv):
        if p in plots or p == "." or re.match(r"^(_archive|_data)/", p) or "/node_modules/" in p or "/.lake/" in p:
            continue
        if re.search(r"/cache/(sdists|wheels|archive|git)-v\d+(/|$)", p):   # a uv/pip cache's own checkout: the tool's
            continue
        if "/.worktrees/" in p:
            issues["I6 rooms inside a building (.worktrees, ADR 0006)"].append(p)
            continue
        if "/.t/" in p or re.search(r"[\w-]\.(?=[A-Za-z0-9]{6}(/|$))(?=[a-z0-9]*[A-Z])[A-Za-z0-9]{6}(/|$)", p):
            issues["pebbles: temporary git folders left inside a building (test or tool leftovers)"].append(p)
            continue
        issues["I9 git folders off the map (second copies, nested clones or unplotted work)"].append(p)
    for at, mpath in vps_places:
        if mpath not in plots:
            issues["I9 a site runs something not on the map"].append(f"siso-vps {at} -> {mpath}")
    a0 = os.path.join(WS, "SISO_Agents/agent-zero/siso-firstmate/siso/plan/projects.json")
    if os.path.exists(a0):
        issues["I7 hand-kept list of estate things"].append("SISO_Agents/agent-zero/siso-firstmate/siso/plan/projects.json (A0's project list)")
    nightly = open(os.path.join(REPO, "bin", "estate-nightly")).read()
    for gen in ("projects.py", "register.py", "docs_check.py"):
        if os.path.exists(os.path.join(REPO, "tools", gen)) and gen not in nightly:
            issues["I7 a generator nothing runs"].append(f"tools/{gen}")

    islands = []
    for f, i in list(ISLANDS.items()) + list(ISLETS.items()):
        mem = [b for b in buildings if b["island"] == i["id"]]
        islands.append({"id": i["id"], "folder": f, "name": i.get("name", i["id"]), "law": i.get("law"), "governor": i.get("governor"),
                        "buildings": len(mem), "compounds": len({b["compound"] for b in mem}),
                        "built_on_laptop": sum(1 for b in mem if "laptop" in b["built_on"]), "dark": sum(1 for b in mem if b["lifecycle"] == "dark"),
                        "lifecycle": dict(collections.Counter(b["lifecycle"] for b in mem))})
    sites = []
    for sid, m in machines.items():
        name = {"vps-siso": "siso-vps"}.get(sid, sid)
        sites.append({"id": name, "host": m.get("host"), "status": m.get("status"),
                      "builds": sum(1 for b in buildings if name in b["built_on"]),
                      "runs": sum(len(b["runs"]) for b in buildings if any(r["site"] == name for r in b["runs"]))})
    seats = [{"name": s, "what": w, "holds": sum(1 for b in buildings if b["seat"] == s)} for s, w in seats_named.items()]
    for s in sorted({b["seat"] for b in buildings} - set(seats_named)):
        seats.append({"name": s, "what": None, "holds": sum(1 for b in buildings if b["seat"] == s)})

    reg = {"_what": "The estate register, generated by tools/register.py from the map, the machine records, GitHub and plan/owners.json "
                    "(ADR 0018). Every list of estate things is a view of this file; edit the sources, never this file.",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "model": MODEL["version"],
           "counts": {"buildings": len(buildings), "compounds": len(comp), "built_on_laptop": sum(1 for b in buildings if "laptop" in b["built_on"]),
                      "dark": sum(1 for b in buildings if b["lifecycle"] == "dark"),
                      "provenance": dict(collections.Counter(b["provenance"] for b in buildings)),
                      "lifecycle": dict(collections.Counter(b["lifecycle"] for b in buildings))},
           "islands": islands, "compounds": list(comp.values()), "buildings": buildings, "sites": sites, "seats": seats,
           "issues": {k: {"count": len(v), "items": v[:60]} for k, v in sorted(issues.items())}}
    if "--json" in sys.argv:
        print(json.dumps(reg, indent=1, ensure_ascii=False))
        return 0
    if "--check" not in sys.argv:
        json.dump(reg, open(OUT, "w"), indent=1, ensure_ascii=False)
        open(OUT, "a").write("\n")
    c = reg["counts"]
    print(f"register: {c['buildings']} buildings in {c['compounds']} compounds; {c['built_on_laptop']} built on the laptop, {c['dark']} dark; "
          f"provenance {c['provenance']}  ({time.time() - t0:.1f}s)")
    for i in islands:
        if i["buildings"]:
            print(f"  {i['id']:10} {i['buildings']:4} buildings {i['compounds']:3} compounds  {i['lifecycle']}")
    print("doctor:")
    for k, v in reg["issues"].items():
        print(f"  {v['count']:4}  {k}")
    if "--check" in sys.argv:
        hard = [k for k in reg["issues"] if k.startswith(("I1 no remote", "I9 a site runs"))]
        return 1 if hard else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
