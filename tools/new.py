#!/usr/bin/env python3
"""estate new: the one road to a new building (ADR 0018, docs/MODEL.md principle 6 and invariant I10). A building born
here arrives up to code: in its island, district and compound; with its door, its card and the .agents core; with a
keeper; with a name no other plot uses. Agents stop making project folders by hand.

  new.py <island>/<compound>[/<building>]            show what would be made (nothing changes)
  new.py <island>/<district>/<compound>[/<building>] (islands with districts: agency apps|clients|partners|factory, ...)
  new.py agency/partners/<agency>/<compound>[/<building>]        a partner's own system (docs/LEGEND.md §3)
  new.py agency/partners/<agency>/clients/<brand>[/<building>]   one of a partner's clients
         --kind app|service|package|site|infra|data|research|docs|tool   (default: tool)
         --what "one line: what it is"  --keeper SEAT  --run  [--github]  [--halo-ok "the HALO lead's words"]

With one segment after the compound, the compound's first building is the compound itself (its own gate). --run makes
the folder, git init, the files (AGENTS.md with the generated door top from plan/door-template.md, CLAUDE.md =
@AGENTS.md, .agents/ with memory/MEMORY.md) and a first commit, creates the private repo sisodias/<name> and pushes
(skip with --no-github), places it in plan/github-placements.json, gives the plot its title in plan/owners.json and
leaves a receipt (`made_by: estate new`) in machines/<machine>/births-by-road.jsonl, which the guard (tools/guard.py)
reads to count hand-made projects. Without --run nothing changes (the dry run).
Refuses: a name already in the register (a second home), an existing folder, an unknown island, district or partner, a
partner's client filed as a direct client (the legend's first question), and HALO without --halo-ok (ADR 0007: the HALO
lead decides what is built there). A partner's building is made in the partner's home on this disk (plan/legend.json), so
a birth before the partner moves lands in the block that moves.
"""
import argparse, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import legend  # partners and their clients (plan/legend.json)

HOME = os.path.expanduser("~")
WS = os.environ.get("ESTATE_WS", os.path.join(HOME, "SISO_Workspace"))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KINDS = {"app": "a user-facing app: run it with its README's dev command on a port from `estate run` (to come)",
         "service": "a long-running service: where it runs and its port go in .agents/building.json",
         "package": "a library other buildings import: tests and releases live here",
         "site": "a static site published to a lighthouse with the publish skill",
         "infra": "machine and deploy configuration; keys never live here (the key store)",
         "data": "a data satellite (-source, -sessions, a corpus): private, append-only",
         "research": "research: docs/research and its question",
         "docs": "documents only", "tool": "a command-line tool"}


def load(p, d=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def dump_owners(o, p):
    """plan/owners.json keeps one owner per line, so a birth is a one-line diff."""
    head = json.dumps({k: v for k, v in o.items() if k != "owners"}, indent=1, ensure_ascii=False)[:-2]
    rows = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in o["owners"])
    open(p, "w").write(head + ',\n "owners": [\n' + rows + "\n ]\n}\n")


def plan(addr, kind, what, keeper, model, register):
    islands = {i["id"]: i for i in model["islands"]}
    parts = [p for p in addr.strip("/").split("/") if p]
    if len(parts) < 2 or parts[0] not in islands:
        raise SystemExit(f"estate new: start with an island ({', '.join(islands)}), then the compound: e.g. engine/estate-radio")
    isl = islands[parts[0]]
    rest = parts[1:]
    district = None
    if isl.get("districts"):
        if rest[0] in isl["districts"]:
            district, rest = rest[0], rest[1:]
        elif isl["id"] == "agency" and rest[0] != "hq":
            raise SystemExit(f"estate new: Agency Island needs a district first ({', '.join(isl['districts'])}), or hq")
    partner = client = None
    if district == "partners":
        if not rest or rest[0] not in legend.PARTNERS:
            raise SystemExit(f"estate new: partners/<agency> must be a partner in plan/legend.json ({', '.join(legend.PARTNERS)}); "
                             "a new partner is Shaan's call, recorded in the legend first")
        partner, rest = rest[0], rest[1:]
        if rest and rest[0] == "clients":
            if len(rest) < 2:
                raise SystemExit(f"estate new: give the brand: agency/partners/{partner}/clients/<brand>[/<building>]")
            client, rest = rest[1], rest[1:]
    elif district == "clients" and rest and legend.client_partner(rest[0]):
        pid = legend.client_partner(rest[0])
        raise SystemExit(f"estate new: {rest[0]} came through {pid} (plan/legend.json): "
                         f"agency/partners/{pid}/clients/{'/'.join(rest)}")
    if not rest or len(rest) > 2:
        raise SystemExit("estate new: give <compound> or <compound>/<building> after the island (and district)")
    compound, building = rest[0], (rest[1] if len(rest) == 2 else rest[0])
    for n in (compound, building):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", n):
            raise SystemExit(f"estate new: '{n}' is not a repo name (lower case, digits, hyphens)")
    tail = [compound] + ([building] if len(rest) == 2 else [])
    if client:   # the client's home today if it has one, else under the partner's home
        homes = (legend.PARTNERS[partner].get("client_homes") or {}).get(client, [])
        base = next((h for h in homes if os.path.isdir(os.path.join(WS, h))), f"{legend.home(partner, WS)}/clients/{client}")
        folder = "/".join([base] + tail[1:])
    elif partner:
        folder = "/".join([legend.home(partner, WS)] + tail)
    else:
        folder = "/".join([isl["folder"]] + ([district] if district else []) + tail)
    taken = {b["postcode"].lower(): b["path"] for b in (register or {}).get("buildings", [])}
    problems = []
    if building.lower() in taken:
        problems.append(f"the name '{building}' is already a building at {taken[building.lower()]} (a second home)")
    if os.path.exists(os.path.join(WS, folder)):
        problems.append(f"{folder} already exists")
    comp_id = "/".join([isl["id"]] + ([district] if district else []) + ([partner] if partner else [])
                       + (["clients"] if client else []) + [compound])
    siblings = [b for b in (register or {}).get("buildings", []) if b["compound"] == comp_id]
    advice = []
    if len(rest) == 2 and siblings and not any(b.get("gatehouse") for b in siblings):
        advice.append(f"compound {comp_id} will have {len(siblings) + 1} buildings and no gatehouse: give it one (the model's rule)")
    if not keeper:  # the title follows plan/owners.json (longest prefix), else the island's governor
        owners = (load(os.path.join(REPO, "plan", "owners.json"), {}) or {}).get("owners", [])
        best = max((o for o in owners if o["path"] not in (".",) and (folder == o["path"] or folder.startswith(o["path"] + "/"))),
                   key=lambda o: len(o["path"]), default=None)
        keeper = best["seat"] if best else isl.get("governor")
    card = {"postcode": building, "kind": kind, "island": isl["id"], "district": district, "compound": comp_id,
            "provenance": "ours", "keeper": keeper, "born": time.strftime("%Y-%m-%d"), "by": "estate new"}
    if partner:
        card.update({"partner": partner, "client": client})
    serves = (f"one of {partner}'s clients" if client else f"{partner}'s own systems" if partner else
              SERVES.get(district or isl["id"], isl.get("role", "")))
    if kind == "data":
        advice.append("the legend's third question: data goes in a data plane, never a repo (plan/data-planes.json); "
                      "make a repo only for a data satellite's code")
    return {"folder": folder, "island": isl, "card": card, "what": what, "problems": problems, "advice": advice,
            "partner": partner, "serves": serves}


SERVES = {"apps": "every partner: a module built once", "clients": "a client that came without a partner",
          "hq": "how SISO runs itself", "factory": "every partner: the way SISO builds", "engine": "the agents that do the work",
          "library": "knowledge, including code kept to learn from", "home": "Shaan's life", "halo": "HALO's own systems"}


def door(p):
    c, isl = p["card"], p["island"]
    law = {"halo": "HALO's law: Cam's code stays in his repo; the HALO lead decides what is built here.",
           "home": "Private: nothing leaves without Shaan."}.get(p.get("partner") or isl["id"], "SISO's law (docs/MODEL.md §12).")
    return (f"# {c['postcode']}\n\n**In one line:** {p['what'] or 'TODO: what this is, in one line'}. Island: {isl['name']} · "
            f"compound `{c['compound']}` · postcode `{c['postcode']}` · keeper {c['keeper']}.\n\n"
            f"Kind: {c['kind']} ({KINDS[c['kind']]}).\n\n{law}\n\n"
            "Work in a room (`_data/worktrees/<repo>/<lane>`), write back to `.agents/HANDOFF.md`, and find anything else with "
            "`estate where <words>` or `estate packet <building>`.\n")


def door_top(p, top, github):
    """The generated door top (tools/door_top.py, plan/door-template.md) for a building not yet on the register."""
    import door_top as dt
    x = {"path": p["folder"], "district": p["folder"].split("/")[0], "seat": p["card"]["keeper"],
         "origin": f"https://github.com/sisodias/{p['card']['postcode']}.git" if github else ""}
    return dt.block(x) if os.path.isdir(top) else dt.BEGIN + "\n" + "\n".join(
        r.format(path=x["path"], district=x["district"], github=dt.github(x), owner=dt.owner(x), run="(from the repo once made)",
                 write="`.agents/HANDOFF.md` (state); `.agents/memory/MEMORY.md` (durable facts)", name=p["card"]["postcode"])
        for r in dt.template_rows()) + "\n" + dt.END + "\n"


def place(p, why):
    """The building's place on the map by its GitHub repo (plan/github-placements.json); a checkout overrides it."""
    gp = os.path.join(REPO, "plan", "github-placements.json")
    doc = json.load(open(gp))
    doc["placements"][p["folder"]] = {"repo": f"sisodias/{p['card']['postcode']}", "why": why}
    doc["placements"] = dict(sorted(doc["placements"].items()))
    with open(gp, "w") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("address")
    ap.add_argument("--kind", default="tool", choices=sorted(KINDS))
    ap.add_argument("--what", default="")
    ap.add_argument("--keeper")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--github", action="store_true", help="kept for old callers: GitHub is now the default")
    ap.add_argument("--no-github", action="store_true", help="make it on disk only (no sisodias repo, no placement)")
    ap.add_argument("--halo-ok")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    model = load(os.environ.get("ESTATE_MODEL", os.path.join(REPO, "plan", "model.json")))
    register = load(os.environ.get("ESTATE_REGISTER", os.path.join(REPO, "machines", "register.json")), {})
    p = plan(a.address, a.kind, a.what, a.keeper, model, register)
    if "halo" in (p["island"]["id"], p["partner"]) and not a.halo_ok:
        p["problems"].append("HALO: pass --halo-ok \"<the HALO lead's words>\" (ADR 0007)")
    print(f"estate new {a.address}\n  serves:  {p['serves']} (the legend's first question)\n"
          f"  folder:  ~/SISO_Workspace/{p['folder']}\n  card:    {json.dumps(p['card'])}")
    for x in p["advice"]:
        print(f"  advice:  {x}")
    for x in p["problems"]:
        print(f"  refused: {x}")
    if p["problems"]:
        return 1
    gh = not a.no_github
    top = os.path.join(WS, p["folder"])
    print("  wires:   git init · AGENTS.md (door + generated top) · CLAUDE.md=@AGENTS.md · .agents/{building.json,HANDOFF.md,memory/MEMORY.md}"
          + (f" · github private sisodias/{p['card']['postcode']} · plan/github-placements.json" if gh else " · (no GitHub: --no-github)")
          + f" · plan/owners.json · machines/{a.machine}/births-by-road.jsonl made_by=estate new")
    if not a.run:
        print("  door top:\n    " + door_top(p, top, gh).rstrip("\n").replace("\n", "\n    "))
        print("  (nothing made; add --run)")
        return 0
    os.makedirs(os.path.join(top, ".agents", "memory"))
    files = {"AGENTS.md": door(p), "CLAUDE.md": "@AGENTS.md\n",
             ".agents/building.json": json.dumps(p["card"], indent=1) + "\n",
             ".agents/HANDOFF.md": f"# Handoff\n\n## State ({p['card']['born']}, born by estate new)\n- Nothing built yet.\n",
             ".agents/memory/MEMORY.md": "# Memory\n\nOne line per memory: `- [Title](file.md) — hook`.\n",
             ".agents/owners.log": f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} · {p['card']['keeper']} · born by estate new · AGENTS.md\n",
             ".gitignore": ".DS_Store\nnode_modules/\n.agents/scratch/\n"}
    for rel, text in files.items():
        open(os.path.join(top, rel), "w").write(text)
    import door_top as dt
    rendered = dt.render(files["AGENTS.md"], door_top(p, top, gh))
    if rendered:
        open(os.path.join(top, "AGENTS.md"), "w").write(rendered)
    run = lambda *c: subprocess.run(c, cwd=top, capture_output=True, text=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", f"{p['card']['postcode']}: born by estate new ({p['card']['kind']}, {p['card']['compound']})")
    made_gh = False
    if gh:
        r = run("gh", "repo", "create", f"sisodias/{p['card']['postcode']}", "--private", "--source", ".", "--push")
        made_gh = r.returncode == 0
        print("  github: " + ("sisodias/" + p["card"]["postcode"] if made_gh else "not created: " + (r.stderr or r.stdout).strip()[-200:]))
        if made_gh and "ESTATE_WS" not in os.environ:
            place(p, f"born by estate new {time.strftime('%Y-%m-%d')}: {p['what'] or p['card']['kind']}")
    ow = os.path.join(REPO, "plan", "owners.json")
    o = load(ow)
    if o is not None and "ESTATE_WS" not in os.environ:
        o["owners"].append({"path": p["folder"], "seat": p["card"]["keeper"], "why": f"born by estate new {time.strftime('%Y-%m-%d')}"})
        dump_owners(o, ow)
    rec = os.path.join(REPO, "machines", a.machine, "births-by-road.jsonl") if "ESTATE_WS" not in os.environ else os.path.join(WS, "births-by-road.jsonl")
    with open(rec, "a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "folder": p["folder"], **p["card"], "github": made_gh, "made_by": "estate new"}) + "\n")
    print(f"  made: {p['folder']} (door, card, .agents core, first commit); run `estate register` to see it on the map")
    return 0


if __name__ == "__main__":
    sys.exit(main())
