#!/usr/bin/env python3
"""The state-planner run (docs/PLANNER.md): one accountability card per building, written by Opus, read-only (Shaan 26 Sep: "nothing should be using haiku").
  python3 tools/planner.py list [--all]        the buildings to card (live ones of ours on this laptop; --all adds dormant, archived, dark)
  python3 tools/planner.py run [--all] [-j 2] [--limit N]   card every building without a card, N at a time
  python3 tools/planner.py check              resolve each card's evidence and join it with the edge scan (tools/edges.py)
                                              -> plan/planner/joined.json, the compact input for the flow map
Cards land in plan/planner/cards/<path with / as __>.json; a log line per building in plan/planner/run.jsonl.
Brief: plan/planner/card-brief.md. It stops when the laptop is overloaded (load above 40) and resumes where it left off."""
import argparse, concurrent.futures as cf, datetime, json, os, pathlib, re, subprocess, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT.parent.parent
CARDS = ROOT / "plan/planner/cards"
LOG = ROOT / "plan/planner/run.jsonl"
# the claude-siso login (Shaan, 25 Sep: plain `claude` is Fahmy's basket, reserved for BYK)
ENV = dict(os.environ, CLAUDE_CONFIG_DIR=os.path.expanduser("~/.claude-siso"))
TOOLS = "Read,Grep,Glob,Bash(ls:*),Bash(git log:*),Bash(cat:*),Bash(grep:*),Bash(rg:*),Bash(head:*),Bash(wc:*)"


def buildings(everything):
    reg = json.loads((ROOT / "machines/register.json").read_text())
    life = ("active", "warm", "dormant") + (("archived", "dark", "unscored") if everything else ())
    return [b["path"] for b in reg["buildings"] if b.get("provenance") in ("ours", "adopted", "client")
            and "laptop" in (b.get("built_on") or []) and b.get("lifecycle") in life and (WS / b["path"]).is_dir()
            and b["path"] != "." and not b["path"].startswith(".")]  # the map itself and task folders are not buildings


def card_path(p):
    return CARDS / (p.replace("/", "__") + ".json")


def one(path):
    while os.getloadavg()[0] > 40:  # the laptop comes first
        time.sleep(60)
    t0 = time.time()
    r = subprocess.run(["claude", "-p", "--model", "opus", "--allowedTools", TOOLS, "--output-format", "json",
                        (ROOT / "plan/planner/card-brief.md").read_text()],
                       cwd=WS / path, stdin=subprocess.DEVNULL, env=ENV, capture_output=True, text=True, timeout=1800)
    line = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "path": path, "secs": round(time.time() - t0)}
    try:
        d = json.loads(r.stdout)
        line.update(cost=d.get("total_cost_usd"), turns=d.get("num_turns"))
        c = json.loads(re.search(r"\{.*\}", d["result"], re.S).group(0))
        c["path"], c["_by"] = path, f"claude-opus (planner.py), {line['at']}"
        card_path(path).write_text(json.dumps(c, indent=1, ensure_ascii=False) + "\n")
        line["ok"] = True
    except Exception as e:  # a failed card is logged and retried on the next run
        line.update(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}", stderr=r.stderr[-300:])
    with LOG.open("a") as f:
        f.write(json.dumps(line) + "\n")
    return line


def resolves(base, ev):
    """file:line evidence that names a file in the building with at least that many lines"""
    m = re.match(r"\s*([^\s:(]+):(\d+)", ev or "")
    if not m:
        return False
    f = WS / base / m.group(1)
    try:
        return f.is_file() and sum(1 for _ in f.open(errors="ignore")) >= int(m.group(2))
    except OSError:
        return False


TOKEN = re.compile(r"([A-Za-z0-9_@.][A-Za-z0-9_@./-]*):(\d+)")


def normalize(base, ev):
    """A card often cites a real file:line after a description ("registry/; AGENTS.md:51"). Move the first cited file:line
    that resolves to the front, unchanged; evidence with no resolving citation stays as written (and does not count)."""
    if not ev or resolves(base, ev):
        return ev
    for m in TOKEN.finditer(ev):
        tok = f"{m.group(1).lstrip('./')}:{m.group(2)}"
        if resolves(base, tok):
            rest = (ev[:m.start()] + ev[m.end():]).strip(" ;,-")
            return f"{tok} ({rest})" if rest else tok
    return ev


def check():
    edges = json.loads((ROOT / "plan/planner/edges.json").read_text())
    by = {}
    for e in edges["edges"]:
        by.setdefault(e["from"], {"uses": [], "used_by": []})["uses"].append(e["to"])
        by.setdefault(e["to"], {"uses": [], "used_by": []})["used_by"].append(e["from"])
    out, ok_all, n_all = [], 0, 0
    for f in sorted(CARDS.glob("*.json")):
        c = json.loads(f.read_text())
        p = c["path"]
        ev = [e for e in c.get("fed_by", []) + c.get("feeds", [])]
        moved = 0
        for e in ev:
            n = normalize(p, e.get("evidence"))
            if n != e.get("evidence"):
                e["evidence_as_written"], e["evidence"], moved = e.get("evidence"), n, moved + 1
        if moved:
            f.write_text(json.dumps(c, indent=1, ensure_ascii=False) + "\n")
        ok = sum(resolves(p, e.get("evidence")) for e in ev)
        ok_all, n_all = ok_all + ok, n_all + len(ev)
        s = by.get(p, {"uses": [], "used_by": []})
        out.append({"path": p, "one_line": c.get("one_line"), "kind": c.get("kind"), "serves": c.get("serves"), "alive": c.get("alive"),
                    "fed_by": [f"{e.get('what')} <- {e.get('from')}" for e in c.get("fed_by", [])],
                    "feeds": [f"{e.get('what')} -> {e.get('to')}" for e in c.get("feeds", [])],
                    "evidence_ok": f"{ok}/{len(ev)}", "scan_uses": sorted(set(s["uses"])), "scan_used_by": sorted(set(s["used_by"])),
                    "slop": c.get("slop", []), "questions": c.get("questions", []), "guesses": c.get("guesses", [])})
    (ROOT / "plan/planner/joined.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(out)} cards joined; {ok_all} of {n_all} card edges resolve to a real file and line")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "run", "check"])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("-j", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "check":
        return check()
    todo = [p for p in buildings(a.all) if not card_path(p).exists()]
    if a.limit:
        todo = todo[:a.limit]
    if a.cmd == "list":
        print("\n".join(todo)); print(f"{len(todo)} to card", flush=True); return
    CARDS.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(a.j) as ex:
        futs = [ex.submit(one, p) for p in todo]
        for f in cf.as_completed(futs):
            l = f.result()
            print(("ok  " if l["ok"] else "FAIL") + f" {l['path']} {l['secs']}s ${l.get('cost')}", flush=True)


if __name__ == "__main__":
    main()
