#!/usr/bin/env python3
"""Who uses what (docs/PLANNER.md phase 1): every live building's names searched across every other live building.
A building's names: its folder name, its package.json / pyproject name, and its bin/ commands, when they are distinctive
(5+ characters, not a common word, owned by one building). A hit in another building is an edge, with file:line.
Run: python3 tools/edges.py  -> plan/planner/edges.json (edges, plus per building: used_by and uses counts)."""
import collections, json, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from planner import buildings  # the same building list the cards use

COMMON = {"server", "client", "shared", "common", "utils", "scripts", "config", "backend", "frontend", "website", "mobile",
          "public", "assets", "source", "search", "agents", "memory", "skills", "design", "studio", "system", "engine",
          "worker", "workers", "landing", "platform", "product", "project", "projects", "archive", "reports", "tokens",
          "oracle", "portal", "dashboard", "research", "training", "finance", "legal", "notes", "tasks", "graph", "admin"}


WORDS = {w.strip().lower() for w in open("/usr/share/dict/words")} if pathlib.Path("/usr/share/dict/words").exists() else set()


def distinctive(n):
    """A name worth searching for: not a file, and not a plain English word (bin/estate, bin/heavy are real but too noisy)."""
    if re.search(r"\.(md|txt|json|sh|py|js|mjs)$", n) or n.lower() in COMMON:
        return False
    return bool(re.search(r"[-_0-9]|[a-z][A-Z]", n)) or n.lower() not in WORDS


def names(path):
    d = WS / path
    out = {d.name}
    pj = d / "package.json"
    if pj.exists():
        try:
            n = json.loads(pj.read_text()).get("name")
            if n:
                out.add(n.split("/")[-1])
        except Exception:
            pass
    pp = d / "pyproject.toml"
    if pp.exists():
        m = re.search(r'^name\s*=\s*"([^"]+)"', pp.read_text(), re.M)
        if m:
            out.add(m.group(1))
    if (d / "bin").is_dir():
        out |= {f.name for f in (d / "bin").iterdir() if f.is_file() and not f.name.startswith(".")}
    return {n for n in out if len(n) >= 5 and distinctive(n) and re.fullmatch(r"[A-Za-z0-9._-]+", n)}


def main():
    live = buildings(False)
    owner = collections.defaultdict(set)
    for p in live:
        for n in names(p):
            owner[n].add(p)
    token = {n: next(iter(ps)) for n, ps in owner.items() if len(ps) == 1}
    # longest building path first, so a file maps to the innermost building
    by_len = sorted(live, key=len, reverse=True)
    pat = ROOT / "plan/planner/.edge-patterns"
    pat.write_text("\n".join(sorted(token)) + "\n")
    cmd = ["rg", "--json", "-F", "-w", "-f", str(pat), "--max-filesize", "400K", "--max-columns", "300",
           "-g", "!node_modules", "-g", "!.git", "-g", "!dist", "-g", "!build", "-g", "!.next", "-g", "!*.lock",
           "-g", "!package-lock.json", "-g", "!*.min.*", "-g", "!*.map", "-g", "!.agents/sessions"] + [str(WS / p) for p in live]
    edges = collections.defaultdict(list)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    for line in proc.stdout:
        m = json.loads(line)
        if m["type"] != "match":
            continue
        f = m["data"]["path"]["text"]
        rel = str(pathlib.Path(f).relative_to(WS))
        src = next((b for b in by_len if rel == b or rel.startswith(b + "/")), None)
        for sm in m["data"]["submatches"]:
            dst = token.get(sm["match"]["text"])
            if src and dst and dst != src and not src.startswith(dst + "/") and not dst.startswith(src + "/"):
                key = (src, dst)
                if len(edges[key]) < 5:  # five pieces of evidence per pair is plenty
                    edges[key].append(f"{rel[len(src) + 1:]}:{m['data']['line_number']} ({sm['match']['text']})")
    proc.wait()
    uses, used_by = collections.Counter(), collections.Counter()
    for s, d in edges:
        uses[s] += 1
        used_by[d] += 1
    out = {"_what": "who uses what: a building's distinctive names found inside another building (tools/edges.py)",
           "names": {p: sorted(n for n, o in token.items() if o == p) for p in live},
           "edges": [{"from": s, "to": d, "evidence": ev} for (s, d), ev in sorted(edges.items())],
           "buildings": {p: {"uses": uses[p], "used_by": used_by[p]} for p in live}}
    (ROOT / "plan/planner/edges.json").write_text(json.dumps(out, indent=1) + "\n")
    alone = [p for p in live if not uses[p] and not used_by[p]]
    print(f"{len(live)} buildings, {len(token)} names, {len(edges)} edges; {len(alone)} with no edge either way")


if __name__ == "__main__":
    main()
