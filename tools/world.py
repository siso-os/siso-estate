#!/usr/bin/env python3
"""The estate as one world (docs/RENDER.md): a snapshot of the records laid out as islands, for plan/world/index.html.

  python3 tools/world.py        -> plan/world/world.json and plan/world/index.html (the template with the snapshot inlined)

Every place is decided here, not in the browser, so the layout is deterministic and stable: a building's slot comes
from its postcode's hash on its district's hex spiral, and adding buildings never moves the ones already placed.
Regions follow docs/LEGEND.md (the business), never the folder tree. Reads only the estate's records."""
import collections, hashlib, json, math, os, pathlib, re, subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT.parent.parent
HOME = pathlib.Path.home()


def load(p, d=None):
    try:
        return json.loads((ROOT / p).read_text()) if not str(p).startswith("/") else json.loads(pathlib.Path(p).read_text())
    except Exception:
        return d


def h(s):
    return int(hashlib.sha1(s.encode()).hexdigest()[:8], 16)


# ---------- regions: the legend drawn as land (docs/RENDER.md §5.2) ----------
# centre (x, z) in world units; the mainland is the business, the rest sit where their flows put them
REGIONS = {
    "mainland": {"name": "SISO Agency", "sub": "the business", "at": (0, 0), "tone": "#7fb069"},
    "port": {"name": "SISO Agents", "sub": "the agents' port", "at": (118, 18), "tone": "#9aa7b8"},
    "library": {"name": "Great Library", "sub": "knowledge, the lighthouse", "at": (-10, -112), "tone": "#d9c589"},
    "personal": {"name": "Personal", "sub": "Shaan's island, fogged", "at": (-128, 22), "tone": "#8fb3a3"},
    "embassy": {"name": "Embassy quarter", "sub": "other people's code", "at": (92, -92), "tone": "#b39ddb"},
    # the Gold Mine (plan/world/app/plans/2-terrain.md (a)): where raw material is dug and assayed (scraping, the repo
    # bank, analysis runs); the Library keeps what is refined. A rendering choice only: nothing moves on disk
    "mine": {"name": "Gold Mine", "sub": "research: scraping, the repo bank, analysis", "at": (-118, -100), "tone": "#c9a227"},
}
DISTRICT_ORDER = {  # angle slots around each region's centre; hq sits at the centre of the mainland
    "mainland": ["hq", "apps", "factory", "clients", "partner:halo", "partner:fahmy"],
    "port": ["agent-zero", "agent stack", "city hall", "compute", "tools", "houses"],
    "library": ["banks", "knowledge", "works", "foundry", "people", "catalog"],
    "personal": ["money", "goals", "research", "study", "apps", "vault"],
    "embassy": ["code-references", "study"],
    "mine": ["repo bank", "smelter", "analysis", "trading lab", "maths", "scrapers"],
}
MINE = [  # (path prefix, district), checked before the Library rule; a prefix ending in / takes everything under it
    ("Great_Library_of_SISO/banks/siso-repo-bank", "repo bank"), ("Great_Library_of_SISO/foundry", "smelter"),
    ("Great_Library_of_SISO/works/trader-platform-research", "trading lab"), ("SISO_Agency/apps/Polymarket-Research", "trading lab"),
    ("Great_Library_of_SISO/works/erdos", "maths"), ("Great_Library_of_SISO/works/", "analysis")]
GENERIC = {"monorepo", "design-system", "SISO_Agency", "SISO_Workspace", "agent-zero", "uihub", "setup"}


def place_of(path):
    """(region, district) for a workspace path, by the legend: what part of the business the work serves."""
    p = path.replace("SISO_Agency/clients/fahmy-2026-08", "SISO_Agency/partners/fahmy")
    for pre, d in MINE:
        if p.startswith(pre) if pre.endswith("/") else (p == pre or p.startswith(pre + "/")):
            return "mine", d
    parts = p.split("/")
    top = parts[0]
    if top == "SISO_Agency":
        if len(parts) > 2 and parts[1] == "partners":
            return "mainland", "partner:" + parts[2]
        if len(parts) > 2 and parts[1] == "clients" and parts[2] == "melanotresses":
            return "mainland", "partner:fahmy"
        return "mainland", {"hq": "hq", "apps": "apps", "factory": "factory", "clients": "clients"}.get(parts[1] if len(parts) > 1 else "", "hq")
    if top == "SISO_Agents":
        n = parts[1] if len(parts) > 1 else ""
        if n == "agent-zero":
            return "port", "agent-zero"
        if n == "siso-estate":
            return "port", "city hall"
        if n in ("siso-compute-pool", "siso-worker-node"):
            return "port", "compute"
        if n in ("siso-harness-lab", "siso-skills-hub", "siso-agent-brain", "siso-agent-stack", "siso-agent-hooks",
                 "siso-agent-runtime", "siso-agent-playbook", "siso-agent-integrations", "siso-session-intelligence",
                 "jev-agent-skills", "siso-project-os", "siso-project-team"):
            return "port", "agent stack"
        return "port", "tools"
    if top == "Great_Library_of_SISO":
        n = parts[1] if len(parts) > 1 else ""
        return "library", {"banks": "banks", "knowledge": "knowledge", "works": "works", "foundry": "foundry",
                           "people-graph": "people"}.get(n, "catalog")
    if top == "personal":
        n = parts[1] if len(parts) > 1 else ""
        return "personal", {"trading-for-dad": "money", "accounting": "money", "goals": "goals", "data": "goals",
                            "math-bounties": "research", "team-entrepreneurship": "study", "apps": "apps",
                            "legal": "vault", "private": "vault"}.get(n, "apps")
    if top == "_reference":
        return "embassy", "code-references"
    return None, None


COMPOUND_SIZE = {}


def kind_of(b):
    """The building's kind decides its shape (docs/RENDER.md §6.1)."""
    p, d = b["path"].lower(), (b.get("description") or "").lower()
    if b.get("runs"):
        return "service"
    if b.get("gatehouse") and COMPOUND_SIZE.get(b.get("compound"), 0) >= 2:
        return "gatehouse"
    if "/banks/" in p or "bank" in d.split(":")[0] or "registry" in d[:60]:
        return "package"
    if re.search(r"(site|landing|website|-lp$|/lp/|web$)", p) or "landing" in d:
        return "site"
    if re.search(r"(data|corpus|dossier|recovery|backup)", p) or "backup of" in d or "corpus" in d:
        return "data"
    if "/works/" in p or "research" in p or "research" in d[:80] or "erdos" in p:
        return "research"
    if re.search(r"(docs|knowledge|notes|playbook)", p):
        return "docs"
    if re.search(r"(tool|cli|hooks|skills|bin|agent)", p):
        return "tool"
    return "app"


def hex_spiral(n):
    """Axial hex coordinates in spiral order: centre, then ring 1, ring 2, ..."""
    out = [(0, 0)]
    dirs = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
    k = 1
    while len(out) < n:
        q, r = -k, k  # start of ring k
        for d in range(6):
            for _ in range(k):
                out.append((q, r))
                q, r = q + dirs[d][0], r + dirs[d][1]
        k += 1
    return out[:n]


def axial_xy(q, r, size):
    return size * math.sqrt(3) * (q + r / 2), size * 1.5 * r


def main():
    reg = load("machines/register.json", {})
    edges = load("plan/planner/edges.json", {"edges": []})
    city = load(str(WS / "_data/estate/city/city.json"), {})
    hist = [json.loads(l) for l in (ROOT / "machines/laptop/score-history.jsonl").read_text().splitlines() if l.strip()]
    act = city.get("activity", {})
    COMPOUND_SIZE.update(collections.Counter(b.get("compound") for b in reg.get("buildings", [])))
    lit_repos = {s.get("repo") for s in city.get("servers", []) if s.get("repo")}
    # git facts for the hover card: GitHub slug, last commit, branch, uncommitted files (machines/laptop/repos.json)
    rj = load("machines/laptop/repos.json", {"repos": []})
    gitf = {str(pathlib.Path(r["path"]).relative_to(WS)): r for r in (rj.get("repos", []) if isinstance(rj, dict) else rj)
            if str(r.get("path", "")).startswith(str(WS) + "/")}

    # ---------- buildings ----------
    districts = collections.defaultdict(list)
    vault, vacant_count = [], collections.Counter()
    for b in reg.get("buildings", []):
        path = b["path"]
        if path in (".",) or path.startswith(".agents"):
            continue
        life = b.get("lifecycle") or "unscored"
        if path.startswith("_archive") or life == "archived":
            vault.append({"id": b["postcode"], "path": path, "size": b.get("size_kb") or 0})
            continue
        region, dist = place_of(path)
        if not region:
            continue
        a = act.get(path, {})
        rec = {"id": b["postcode"] or path, "path": path.replace("SISO_Agency/clients/fahmy-2026-08", "SISO_Agency/partners/fahmy"),
               "name": path.rstrip("/").split("/")[-1], "kind": kind_of(b), "life": life,
               "size": b.get("size_kb") or 0, "c14": b.get("commits_14d") or 0, "d30": a.get("d30", 0),
               "score": b.get("code_score"), "ok": bool(b.get("up_to_code")), "prov": b.get("provenance"),
               "upstream": b.get("upstream"), "keeper": b.get("seat"), "public": None,
               "vps": bool(b.get("runs")) or "siso-vps" in (b.get("built_on") or []), "laptop": "laptop" in (b.get("built_on") or []),
               "lit": path in lit_repos, "unpushed": "pushed" in (b.get("failed") or []) or "backed-up" in (b.get("failed") or []),
               "desc": (b.get("description") or "")[:140]}
        g = gitf.get(path) or {}
        gh = next((x for x in g.get("github") or [] if x.get("remote") == "origin"), None) or ((g.get("github") or [None])[0])
        rec.update({"gh": f"{gh['owner']}/{gh['repo']}" if gh else None, "last": g.get("last_commit"),
                    "branch": g.get("branch"), "dirty": (g.get("dirty") or 0) + (g.get("untracked") or 0)})
        districts[(region, dist)].append(rec)

    # ---------- one id per building (WORLD gate 5, 26 Sep): a postcode listed twice at one path is one building; the
    # same postcode at a second path (a second home) keeps the plain id at its first path in path order, and the others
    # get a stable suffix from their path, so ids never collide and never move when a building is added
    seen, by_code = set(), collections.defaultdict(list)
    for key in list(districts):
        keep = []
        for r in districts[key]:
            if (r["id"], r["path"]) in seen:
                continue
            seen.add((r["id"], r["path"]))
            keep.append(r)
            by_code[r["id"]].append(r)
        districts[key] = keep
    for code, recs in by_code.items():
        for r in sorted(recs, key=lambda r: r["path"])[1:]:
            r["id"] = f"{code}@{hashlib.sha1(r['path'].encode()).hexdigest()[:6]}"
            r["second_home"] = True

    # ---------- layout: regions -> district clusters -> hex slots ----------
    TILE = 3.2
    world_buildings, district_out, tiles = [], [], []
    for region, order in DISTRICT_ORDER.items():
        cx, cz = REGIONS[region]["at"]
        present = [d for (r, d) in districts if r == region]
        names = [d for d in order if d in present] + sorted(d for d in present if d not in order)
        if region == "port":
            names = names + (["houses"] if "houses" not in names else [])
        for i, dname in enumerate(names):
            members = sorted(districts.get((region, dname), []), key=lambda x: h(x["id"]))
            slots_needed = max(len(members), 1)
            # hq (or the first district) at the region's centre; others on a ring, angle fixed by the order list
            if i == 0:
                dx, dz = cx, cz
            else:
                ring = 34 if region == "mainland" else 26
                ang = (order.index(dname) if dname in order else len(order) + h(dname) % 6) * (2 * math.pi / max(len(order), 6)) + 0.4
                dx, dz = cx + ring * math.cos(ang), cz + ring * math.sin(ang)
            spiral = hex_spiral(slots_needed + 7)  # a ring of spare land keeps growth from moving anyone
            for j, bld in enumerate(members):
                q, r = spiral[j]
                x, z = axial_xy(q, r, TILE)
                bld.update({"x": round(dx + x, 2), "z": round(dz + z, 2), "region": region, "district": dname})
                world_buildings.append(bld)
            for q, r in spiral:
                x, z = axial_xy(q, r, TILE)
                tiles.append({"x": round(dx + x, 2), "z": round(dz + z, 2), "region": region, "d": dname})
            district_out.append({"region": region, "name": dname, "x": round(dx, 2), "z": round(dz, 2), "n": len(members),
                                 "partner": dname.startswith("partner:")})

    # ---------- roads: evidence edges between placed buildings ----------
    at = {b["path"]: b for b in world_buildings}
    roads = []
    for e in edges.get("edges", []):
        s, d = e["from"].replace("clients/fahmy-2026-08", "partners/fahmy"), e["to"].replace("clients/fahmy-2026-08", "partners/fahmy")
        toks = {ev.rsplit("(", 1)[-1].rstrip(")") for ev in e["evidence"]}
        if s not in at or d not in at or toks <= GENERIC or at[s]["district"] == at[d]["district"]:
            continue
        if True:
            kind = "ore" if "mine" in (at[s]["region"], at[d]["region"]) else "money" if "partner:halo" in (at[s]["district"], at[d]["district"]) and "hq" in (at[s]["district"], at[d]["district"]) else (
                "code" if at[s]["region"] == "library" or at[d]["region"] == "library" else "knowledge" if at[s]["region"] != "port" else "training")
            roads.append({"a": s, "b": d, "w": len(e["evidence"]), "kind": kind, "why": sorted(toks)[:3]})
    roads.sort(key=lambda r: -r["w"])
    roads = roads[:140]

    # ---------- citizens: herdr seats, placed by what their tab says they work on (inferred, marked as such) ----------
    agents = []
    try:
        raw = json.loads(subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=10).stdout)
        for a in raw["result"]["agents"]:
            title = a.get("terminal_title_stripped") or "agent"
            t = title.lower()
            guess = next((p for k, p in [("bykonz", "SISO_Agency/partners/fahmy/bykonzyard"), ("estate", "SISO_Agents/siso-estate"),
                                         ("shanghai", "SISO_Agents/siso-estate"), ("crm", "SISO_Agency/partners/halo/crm/repo"),
                                         ("model ui", "SISO_Agency/partners/halo/oracle/core"), ("oracle", "SISO_Agency/partners/halo/oracle"),
                                         ("agent zero", "SISO_Agents/agent-zero/siso-firstmate")] if k in t), None)
            agents.append({"name": title[:40], "harness": a.get("agent"), "status": a.get("agent_status"),
                           "at": guess if guess in at else None, "inferred": True})
    except Exception:
        pass

    # ---------- the rest of the estate ----------
    hz = load("plan/home-zones.json", {}).get("zones", {})
    homes = hz.get("agent homes", {})
    live = set(homes.get("live") or [".claude", ".claude.json", ".codex", ".omp", ".agents", ".opencode"])
    # personal areas that are not repos (personal/DOMAIN-MAP.md), measured 25 Sep; legal and private are a sealed keep
    landmarks = [{"region": "personal", "name": n, "gb": g, "sealed": n in ("legal", "private")} for n, g in
                 [("data (house search)", 11.0), ("private", 8.8), ("math-bounties", 2.4), ("coursework", 1.9),
                  ("trading-for-dad", 1.5), ("legal", 0.85), ("apps", 0.33), ("goals", 0.1)]]
    bedrock = [  # measured 25 Sep (plan/goal.json A13); the machine itself, drawn as strata
        {"name": "macOS and ~/Library", "gb": 23.1}, {"name": "caches (silt)", "gb": 22.0},
        {"name": "~/.codex sessions", "gb": 23.9}, {"name": "toolchains", "gb": 8.0}]
    census = load("machines/laptop/census.json", {})
    last = hist[-1] if hist else {}
    world = {
        "generated_at": city.get("at"), "era": last.get("era"), "score": last.get("score"),
        "regions": [{"id": k, **{kk: vv for kk, vv in v.items() if kk != "at"}, "x": v["at"][0], "z": v["at"][1]} for k, v in REGIONS.items()],
        "districts": district_out, "tiles": tiles, "buildings": world_buildings, "roads": roads, "agents": agents,
        "vault": vault[:120], "vault_total": len(vault), "landmarks": landmarks,
        "harness_homes": [{"name": n, "live": n in live} for n in (homes.get("entries") or [])],
        "bedrock": bedrock,
        "horizon": ["Anthropic", "OpenCode Go", "Kaggle", "Groq", "Cloudflare", "Plane", "Convex", "GitHub"],
        "machines": city.get("machines", {}),
        "weather": {"loose_keys": 2, "backup_errors": (city.get("backup") or {}).get("error", 0),
                    "unregistered_servers": sum(1 for s in city.get("servers", []) if not s.get("registered")),
                    "wrong_births": (load("machines/laptop/births.json", {}).get("by_verdict") or {}).get("wrong", 0),
                    "up_to_code": (city.get("code") or {}).get("up_to_code"), "repos_scored": (city.get("code") or {}).get("repos")},
        "counts": {"buildings": len(world_buildings), "vault": len(vault), "roads": len(roads), "agents": len(agents),
                   "checkouts": (census.get("repos") or {}).get("checkouts")},
    }
    out = ROOT / "plan/world"
    out.mkdir(parents=True, exist_ok=True)
    (out / "world.json").write_text(json.dumps(world, ensure_ascii=False))
    page = (out / "template.html").read_text().replace("/*WORLD*/", json.dumps(world, ensure_ascii=False).replace("</", "<\\/"))
    (out / "index.html").write_text(page)
    print(json.dumps(world["counts"]), "->", out / "world.json")


if __name__ == "__main__":
    main()
