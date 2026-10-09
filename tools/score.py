#!/usr/bin/env python3
"""The estate score: one number out of 100 and the city era it buys, so every upgrade shows how far it moves us.
Four tiers (Shaan, 25 Sep: "at 2020 Shanghai it should be way cooler ... first generation best in the world code
ecosystem for agents and projects ... linked up with the siso internal vps so it's got a ui for a human"):
  the unfuck   30  plan/goal.json checks (15 scored): met 2, partial 1; `dropped` is not scored
  the ledger   15  every live building carded (plan/planner/cards/, 10) and its card's evidence resolving (5)
  the city     30  plan/goal-2050.json pillars (10): met 3, partial 1
  the window   25  plan/goal-2050.json window (5), SISO Internal Labs as the human's view: met 5, partial 2
Writes docs/SCORE.md and one line a day in machines/<m>/score-history.jsonl (the timeline; the nightly runs it).
Run: python3 tools/score.py [--machine laptop]"""
import argparse, datetime, json, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = ROOT.parent.parent
TIERS = {"unfuck": {"met": 2, "partial": 1}, "city": {"met": 3, "partial": 1}, "window": {"met": 5, "partial": 2}}
FULL = {"unfuck": 2, "city": 3, "window": 5}
ERAS = [
    (0, "South Sudan, 1980s", [], "no addresses, no titles; agents fight over the same ground"),
    (6, "North Korea, 2020", [], "order by decree: one planner keeps it tidy, nobody else owns anything"),
    (12, "Xiaogang, 1978", [], "every building has an owner; the tools exist but the city does not run itself"),
    (18, "Shenzhen, 1980", ["A5"], "the special zone: the business shape is real on disk (partners, products, clients where they belong)"),
    (24, "Shenzhen, 1985", ["A8", "P1"], "the dual track: new buildings are born right and every repo's door is generated"),
    (30, "Pudong, 1990", ["A1", "A15"], "services first: any agent finds anything cold, and every machine is on the map"),
    (37, "Shanghai, 1997", ["A6"], "grasp the large, let go of the small: the merges and the vault, about 70 live repos"),
    (44, "Shanghai, 2001", ["L"], "the ledger: every building is accountable, and every flow between them has evidence"),
    (51, "Shanghai, 2005", ["P3"], "the maglev: the city answers in a minute, and agents ask it instead of searching"),
    (58, "Shanghai, 2010", ["W1", "W2"], "the Expo: SISO Internal Labs shows every building, flow, machine and service"),
    (66, "Shanghai, 2016", ["W3", "W4"], "talk to the city: any agent on any machine, and every decision, in one window"),
    (75, "Shanghai, 2020", ["P2", "P8"], "the first generation of the best code ecosystem in the world for agents and projects: it governs itself, "
                           "a mess is fixed within the hour, and a new project is born wired into its flows"),
    (85, "Shanghai, 2035", ["P6"], "the city learns: every session feeds the next, and the score rises on its own"),
    (95, "Shanghai, 2050", ["P9", "P10", "W5"], "Shaan only decides"),
]


MET = set()  # ids whose state is met, filled in main(); an era needs its points AND its gates (the upgrades that define it)


def era(score, met=None):
    met = MET if met is None else met
    # eras are cumulative: the first era short of its points or its gates caps the climb
    for n, e in enumerate(ERAS):
        if score < e[0] or not set(e[2]) <= met:
            return ERAS[n - 1]
    return ERAS[-1]


def ledger():
    """Cards over live buildings of ours on the laptop; evidence that names a file in the building that exists."""
    reg = json.loads((ROOT / "machines/register.json").read_text())
    live = [b for b in reg["buildings"] if b.get("provenance") in ("ours", "adopted", "client")
            and "laptop" in (b.get("built_on") or []) and b.get("lifecycle") in ("active", "warm", "dormant")]
    cards = {}
    for f in (ROOT / "plan/planner/cards").glob("*.json"):
        c = json.loads(f.read_text())
        cards[c.get("path")] = c
    carded = [b for b in live if b["path"] in cards]
    edges = ok = 0
    live_paths = {b["path"] for b in live}
    for c in cards.values():
        if c.get("path") not in live_paths:        # a building gone dark keeps its card; its evidence is on GitHub now
            continue
        for e in c.get("fed_by", []) + c.get("feeds", []):
            edges += 1
            m = re.match(r"\s*([^\s:(]+):(\d+)", e.get("evidence", ""))
            f = WS / c["path"] / m.group(1) if m else None
            try:                                   # the file exists AND has that line (stricter than before, 26 Sep)
                ok += bool(f and f.is_file() and sum(1 for _ in f.open(errors="ignore")) >= int(m.group(2)))
            except OSError:
                pass
    share = len(carded) / max(len(live), 1)
    # evidence counts only with a line number, and only as far as the buildings are carded
    pts = 10 * share + 5 * share * (ok / edges if edges else 0)
    return round(pts, 1), len(carded), len(live), ok, edges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default="laptop")
    a = ap.parse_args()
    goal = json.loads((ROOT / "plan/goal.json").read_text())
    city = json.loads((ROOT / "plan/goal-2050.json").read_text())
    items = [dict(c, tier="unfuck") for c in goal["done_when"] if c.get("state") != "dropped"]
    items += [dict(p, tier="city") for p in city["pillars"]] + [dict(w, tier="window") for w in city.get("window", [])]
    for i in items:
        i["pts"] = TIERS[i["tier"]].get(i["state"], 0)
        i["gain"] = FULL[i["tier"]] - i["pts"]
    t = {k: sum(i["pts"] for i in items if i["tier"] == k) for k in TIERS}
    led, carded, live, ok, edges = ledger()
    score = round(t["unfuck"] + led + t["city"] + t["window"], 1)
    MET.update(i["id"] for i in items if i["state"] == "met")
    if led >= 13:
        MET.add("L")
    today = datetime.date.today().isoformat()

    hist_p = ROOT / "machines" / a.machine / "score-history.jsonl"
    hist = [json.loads(l) for l in hist_p.read_text().splitlines() if l.strip()] if hist_p.exists() else []
    line = {"date": today, "score": score, "unfuck": t["unfuck"], "ledger": led, "city": t["city"], "window": t["window"],
            "era": era(score)[1], "met": [i["id"] for i in items if i["state"] == "met"], "scale": "v2 (four tiers)"}
    hist = [h for h in hist if h["date"] != today] + [line]
    hist_p.write_text("".join(json.dumps(h) + "\n" for h in hist))

    # the route: the unfuck first (unblocked, biggest gain), then the ledger run, then the window, then the city;
    # a check waits for the checks it runs `after`; the running total shows the era each upgrade buys
    # the route climbs the ladder: an upgrade that gates an era goes in that era's turn; the rest go with their tier
    gate_era = {g: n for n, e in enumerate(ERAS) for g in e[2]}
    tier_era = {"unfuck": 5, "ledger": 7, "window": 10, "city": 11}
    key = lambda i: (gate_era.get(i["id"], tier_era[i["tier"]]), i.get("blocker", "none") != "none", -i["gain"])
    ups = [i for i in items if i["gain"]]
    ups.append({"id": "L", "name": "The ledger", "tier": "ledger", "gain": round(15 - led, 1),
                "next": f"The state-planner run (docs/PLANNER.md): a card for each of the {live} live buildings, Opus per building (Shaan 26 Sep: no Haiku) plus a script for who uses what; then one Opus pass writes the flow map.",
                "blocker": "none", "estimate": "about 40 minutes of Opus, 10 at a time; then about 2 hours for the flow map"})
    ups.sort(key=key)
    order, left = [], list(ups)
    while left:
        done = {i["id"] for i in order} | {i["id"] for i in items if i["state"] == "met"}
        i = next((i for i in left if set(i.get("after", [])) <= done), left[0])
        order.append(i); left.remove(i)
    run, route, got = score, [], set(MET)
    for i in order:
        run = round(run + i["gain"], 1)
        got.add(i["id"])
        route.append((i, run, era(run, got)[1]))

    now = era(score)
    nxt = ERAS[ERAS.index(now) + 1] if now != ERAS[-1] else None
    gap = [g for g in nxt[2] if g not in MET] if nxt else []
    L = ["# Estate score", "",
         f"Generated by `tools/score.py` on {today} from `plan/goal.json`, `plan/goal-2050.json` and `plan/planner/cards/`; edit those, not this. "
         "The disk check (A13) is dropped: Shaan, 25 Sep, \"disk isn't the issue\".", "",
         "## 1. Where we are", "",
         f"**{score} of 100: {now[1]}.** {now[3][0].upper() + now[3][1:]}.", "",
         f"- The unfuck (the original 15 checks): {t['unfuck']} of 30.",
         f"- The ledger (every building accountable): {led} of 15. {carded} of {live} live buildings carded; {ok} of {edges} card edges point at a file that exists.",
         f"- The city (the 10 Shanghai pillars): {t['city']} of 30.",
         f"- The window (SISO Internal Labs as your view of everything): {t['window']} of 25.",
         (f"- Next era: {nxt[1]}, at {nxt[0]} points" + (f" and when {', '.join(gap)} {'is' if len(gap) == 1 else 'are'} met" if gap else "") + "." ) if nxt else "- The last era.", "",
         "An era needs its points and its gates: the upgrades that make it true. Points alone can buy a name the estate has not earned.", "",
         "The scale changed on 25 Sep (v2): Shanghai 2020 now means the best agent code ecosystem in the world with a window "
         "for a human, not a finished cleanup, so the same estate scores lower than it did under v1 (32).", "",
         "## 2. The eras", "", "| From | Era | Gates | What is true here |", "|---|---|---|---|"]
    L += [f"| {lo} | {'**' + n + '**' if n == now[1] else n} | {', '.join(g) or '-'} | {d} |" for lo, n, g, d in ERAS]
    L += ["", "## 3. The route: each upgrade and what it buys", "",
          "In the order the eras need them. The running total is the score after that upgrade, and the era is the one it has earned by then.", "",
          "| # | Upgrade | Gain | Score after | Next step | Waits on | Time |", "|---|---|---|---|---|---|---|"]
    for n, (i, r, er) in enumerate(route, 1):
        L.append(f"| {n} | {i['id']} {i['name']} | +{i['gain']} | {r} ({er}) | {i.get('next') or i.get('target', '')} | "
                 f"{i.get('blocker', '')} | {i.get('estimate', '')} |")
    L += ["", "## 4. The timeline", "", "One line per day in `machines/" + a.machine + "/score-history.jsonl`, appended by the nightly.", "",
          "| Date | Score | Era | Met |", "|---|---|---|---|"]
    L += [f"| {h['date']} | {h['score']}{' (v1 scale)' if 'scale' not in h else ''} | {h['era']} | {', '.join(h['met']) or 'none'} |" for h in hist]
    (ROOT / "docs/SCORE.md").write_text("\n".join(L) + "\n")
    print(json.dumps(line))


if __name__ == "__main__":
    main()
