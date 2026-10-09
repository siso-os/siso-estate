#!/usr/bin/env python3
"""The operations centre (goal-2050 P4): the whole city on one live page at one stable URL.

  city.py build            -> ~/SISO_Workspace/_data/estate/city/index.html (+ city.json, the same facts for agents)
  city.py serve [--port N]  serve that folder on 127.0.0.1 and rebuild it every 2 minutes (launchd com.siso.estate-city)

The skyline is the map drawn as a city: districts as zones, each repo a building (height = commits in 30 days, colour =
building-code score, lit = a server runs from it, a dot on the roof = an agent works in it), empty map places as vacant
lots. Below it: alerts, agents at work, servers and ports (against plan/ports.json), the building code, the week's work,
backups and machines. Facts come from the disk, git, lsof, herdr and the estate's records; each section says when.
The building code (tools/code.py) is re-scored when its record is over 55 minutes old.
"""
import argparse, collections, html as H, json, math, os, re, subprocess, sys, threading, time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.join(os.path.expanduser("~"), "SISO_Workspace")
HOME = os.path.expanduser("~")
OUT = os.path.join(WS, "_data", "estate", "city")
SHELL = os.path.join(WS, "Great_Library_of_SISO", "banks", "siso-shell")
MACHINE = os.environ.get("ESTATE_MACHINE", "laptop")
DISTRICTS = ["SISO_Agency", "SISO_Agents", "Great_Library_of_SISO", "personal", "(the map)"]
e = H.escape


def run(*a, cwd=None, timeout=20):
    try:
        r = subprocess.run(a, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def load(p, default=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def age(ts):
    """'3 min ago' from an ISO time with or without a colon in its offset."""
    try:
        t = time.mktime(time.strptime(re.sub(r"([+-]\d\d):?(\d\d)$", "", ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return "unknown"
    s = time.time() - t
    return f"{int(s // 60)} min ago" if s < 3600 else f"{s / 3600:.1f} h ago" if s < 172800 else f"{int(s // 86400)} days ago"


def repo_of(path, repos):
    """The deepest repo (workspace-relative) holding an absolute path, or None."""
    if not path or not path.startswith(WS + "/"):
        return None
    if path.startswith(os.path.join(WS, "_data", "worktrees") + "/"):   # a worktree belongs to its repo's building
        common = run("git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        if common.endswith("/.git"):
            path = common[:-5]
    rel = path[len(WS) + 1:]
    best = None
    for r in repos:
        if r != "." and (rel == r or rel.startswith(r + "/")) and (best is None or len(r) > len(best)):
            best = r
    return best or "."


# ---------------------------------------------------------------- facts

def code_record():
    p = os.path.join(REPO, "machines", MACHINE, "code.json")
    d = load(p)
    if not d or time.time() - os.path.getmtime(p) > 55 * 60:
        subprocess.run([sys.executable, os.path.join(HERE, "code.py"), "--machine", MACHINE], capture_output=True, timeout=300)
        d = load(p, {"results": []})
    return d


_ACT = {}  # repo -> (logs/HEAD mtime, day, counts): git runs only for a repo that moved since the last build


def activity(repos):
    out, day = {}, time.strftime("%Y-%m-%d")
    for r in repos:
        top = os.path.join(WS, r)
        g = os.path.join(top, ".git")
        if os.path.isfile(g):                                  # a worktree or submodule: .git names the real git dir
            try:
                with open(g) as f:
                    g = os.path.join(top, f.read().split("gitdir:", 1)[1].strip())
            except (OSError, IndexError):
                pass
        try:
            key = os.path.getmtime(os.path.join(g, "logs", "HEAD"))
        except OSError:
            key = None
        hit = _ACT.get(r)
        if key is not None and hit and hit[0] == key and hit[1] == day:   # windows slide daily, so a day change recounts
            out[r] = hit[2]
            continue
        out[r] = {"d30": int(run("git", "-C", top, "rev-list", "--count", "--since=30.days", "HEAD").strip() or 0),
                  "d7": int(run("git", "-C", top, "rev-list", "--count", "--since=7.days", "HEAD").strip() or 0)}
        _ACT[r] = (key, day, out[r])
    return out


def servers(repos):
    reg = load(os.path.join(REPO, "plan", "ports.json"), {"ports": {}})
    ports = {**{k: v for k, v in reg.get("machine", {}).items() if not k.startswith("_")}, **reg["ports"]}
    reg_ranges = [(tuple(map(int, k.split("-"))), v) for k, v in reg.get("ranges", {}).items() if not k.startswith("_")]
    rows = {}
    for line in run("lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn").splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("c"):
            cmd = line[1:]
        elif line.startswith("n"):
            m = re.search(r":(\d+)$", line)
            if m and (m.group(1), pid) not in rows:
                rows[(m.group(1), pid)] = {"port": int(m.group(1)), "pid": pid, "cmd": cmd, "public": not line[1:].startswith(("127.", "[::1]", "localhost"))}
    cwd = {}
    for pid in {r["pid"] for r in rows.values()}:
        n = [l[1:] for l in run("lsof", "-a", "-p", pid, "-d", "cwd", "-Fn").splitlines() if l.startswith("n")]
        cwd[pid] = n[0] if n else ""
    out = []
    for (port, pid), r in sorted(rows.items(), key=lambda kv: kv[1]["port"]):
        reg = ports.get(str(r["port"])) or next((v for k, v in reg_ranges if k[0] <= r["port"] <= k[1]), None)
        where = repo_of(cwd[pid], repos)
        if r["port"] >= 49152 and not reg:
            continue                                   # an ephemeral port the OS handed out, not a service
        out.append({**r, "cwd": cwd[pid].replace(HOME, "~"), "repo": where, "registered": reg})
    return out


def launchd_health():
    """The laptop's launchd jobs from plan/services.json: running (a pid), ok (last exit 0), failed (non-zero or not
    loaded while it should be)."""
    try:
        import services as S
        live, man = S.loaded(), S.load_manifest()["services"]
    except Exception:
        return []
    rows = []
    for s in man:
        pid, code = live.get(s["label"], (None, None))
        if s.get("state", "on") != "on":
            h = None
        elif pid:
            h = "running"
        elif code is None:
            h = "failed"
        else:
            h = "ok" if code == "0" else "failed"
        rows.append({"unit": s["label"], "health": h or "off", "what": s.get("what", ""), "owner": s.get("owner", ""),
                     "detail": ("not loaded" if code is None else f"last exit {code}") if not pid else f"pid {pid}"})
    return rows


def remote_servers(machines):
    """W2: every machine's services, containers and launchd jobs on one screen with their health (laptop: launchd via
    tools/services.py; servers: the nightly ssh scan, tools/servers.py -> machines/<m>/servers.json)."""
    good = {"running": True, "ok": True, "failed": False}
    html, total, bad = "", 0, 0
    jobs = launchd_health()
    if jobs:
        nb = sum(j["health"] == "failed" for j in jobs)
        total, bad = total + len(jobs), bad + nb
        html += f"<h3>laptop: {len(jobs)} launchd jobs, {chip(f'{nb} failing', not nb)}</h3>"
        html += table(["Job", "Health", "What", "Owner"],
                      [[f"<code>{e(j['unit'])}</code>", chip(f"{j['health']} ({j['detail']})", good.get(j["health"])), e(j["what"][:90]), e(j["owner"])]
                       for j in sorted(jobs, key=lambda j: (j["health"] != "failed", j["unit"]))])
    for name in machines:
        rec = load(os.path.join(REPO, "machines", name, "servers.json"))
        if not rec:
            continue
        units = rec.get("services", []) + [dict(c, unit=c["name"], cwd="container " + c.get("image", "")) for c in rec.get("containers", [])]
        nb = sum(u.get("health") == "failed" for u in units)
        total, bad = total + len(units), bad + nb
        kinds = ", ".join(f"{v} {k}" for k, v in sorted(rec.get("summary", {}).get("checkouts", {}).items(), key=lambda kv: -kv[1]))
        html += (f"<h3>{e(name)}: {len(rec.get('services', []))} services, {len(rec.get('containers', []))} containers, "
                 f"{chip(f'{nb} failing', not nb)} <small>(scanned {age(rec.get('at'))}; checkouts: {e(kinds)})</small></h3>")
        html += table(["Service", "Health", "Runs from"],
                      [[f"<code>{e(u['unit'])}</code>", chip(u.get("health", "?") + (f" ({u['status']})" if u.get("status") else ""), good.get(u.get("health"))),
                        f"<code>{e((u.get('cwd') or u.get('program') or '').lstrip('!'))}</code>"]
                       for u in sorted(units, key=lambda u: (u.get("health") != "failed", u["unit"]))])
    return (f"<p><b>{total} services, containers and jobs on {1 + sum(1 for n in machines if load(os.path.join(REPO, 'machines', n, 'servers.json')))} machines; "
            f"{chip(f'{bad} failing', not bad)}.</b></p>" + html)


def flows(code):
    """W1: every carded building, its owner, score and what feeds what (plan/planner/cards, the ledger), one click from
    a flow to the building it names and from the building to its repo."""
    cards = {}
    cdir = os.path.join(REPO, "plan", "planner", "cards")
    for f in sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []:   # listdir: the map's own card is "..json"
        c = load(os.path.join(cdir, f)) if f.endswith(".json") else None
        if c and c.get("path"):
            cards[c["path"]] = c
    score = {r["path"]: r for r in code.get("results", [])}
    anchor = lambda p: "b-" + re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-").lower()
    known = sorted(cards, key=len, reverse=True)

    def link(target):
        t = str(target or "")
        hit = next((k for k in known if k != "." and k in t), None)
        return f'<a href="#{anchor(hit)}">{e(t)}</a>' if hit else e(t)

    rows = []
    for p, c in sorted(cards.items()):
        r = score.get(p, {})
        origin = (r.get("origin") or "").removesuffix(".git")
        name = f'<code>{e(p)}</code>'
        name = f'<a href="{e(origin)}">{name}</a>' if origin.startswith("https://") else name
        sc = r.get("score")
        rows.append([f'<span id="{anchor(p)}"></span>{name}', e(r.get("seat") or ""),
                     chip(f"{round(sc * 100)}%", sc >= 0.75) if isinstance(sc, (int, float)) else "",
                     e(c.get("one_line", "")),
                     "<br>".join(f"{e(x.get('what', ''))} ← {link(x.get('from'))}" for x in c.get("fed_by", [])),
                     "<br>".join(f"{e(x.get('what', ''))} → {link(x.get('to'))}" for x in c.get("feeds", []))])
    edges = sum(len(c.get("fed_by", [])) + len(c.get("feeds", [])) for c in cards.values())
    return len(cards), edges, table(["Building (its repo)", "Owner", "Code", "What it is", "Fed by", "Feeds"], rows)


def agents(repos):
    d = json.loads(run("herdr", "agent", "list") or "{}")
    out = []
    for a in d.get("result", {}).get("agents", []):
        where = a.get("foreground_cwd") or a.get("cwd") or ""
        out.append({"name": a.get("name") or "(unnamed)", "harness": a.get("agent", ""), "status": a.get("agent_status", ""),
                    "where": where.replace(HOME, "~"), "repo": repo_of(where, repos)})
    return out


def vacant():
    m = load(os.path.join(WS, ".estate", "map.json"), {"repos": {}})["repos"]
    c = collections.Counter()
    for p in m:
        if not os.path.exists(os.path.join(WS, p, ".git")) and not p.startswith(("_reference/", "_archive/", "_data/")):
            c[p.split("/")[0] if "/" in p else "(the map)"] += 1
    return c, len(m)


# ---------------------------------------------------------------- the skyline

def tone(x):
    if x["owner"] == "foreign":
        return "#8b8d98"
    return "#30a46c" if x["up_to_code"] else "#f5a524" if x["score"] >= 0.75 else "#e5484d"


def skyline(code, act, lit, busy, lots):
    rows = [x for x in code["results"]]
    by = collections.defaultdict(list)
    for x in rows:
        by[x["district"] if x["district"] in DISTRICTS else "(the map)"].append(x)
    W, G, ground = 700, 10, 222
    ds = [d for d in DISTRICTS if by.get(d) or lots.get(d)]
    weight = {d: max(22, len(by.get(d, [])) + 0.15 * lots.get(d, 0)) for d in ds}
    total = sum(weight.values())
    top = max([act.get(x["path"], {}).get("d30", 0) for x in rows if x["owner"] != "foreign"] + [1])
    svg = [f'<svg viewBox="0 0 {W} 290" width="100%" role="img" aria-label="The estate as a skyline" style="color:currentColor;display:block">',
           f'<line x1="0" y1="{ground}" x2="{W}" y2="{ground}" stroke="currentColor" stroke-opacity=".35"/>']
    x0 = 10
    for d in ds:
        w = (W - 20 - G * (len(ds) - 1)) * weight[d] / total
        bs = sorted(by.get(d, []), key=lambda x: (x["owner"] == "foreign", -act.get(x["path"], {}).get("d30", 0)))
        n = len(bs) + (1 if lots.get(d) else 0)
        bw = max(1.5, min(22.0, w / max(n, 1) - 1))
        svg.append(f'<rect x="{x0:.1f}" y="{ground}" width="{w:.1f}" height="4" fill="currentColor" opacity=".12"/>')
        for i, b in enumerate(bs):
            c = act.get(b["path"], {}).get("d30", 0)
            h = 8 if b["owner"] == "foreign" else 10 + 180 * math.log1p(c) / math.log1p(top)
            bx, by_ = x0 + i * (bw + 1), ground - h
            tip = f'{b["path"]} · owner {b.get("seat", "?")}' + (" (unassigned)" if b.get("state_land") else "") + f' · {b.get("lifecycle", "?")} · {int(b["score"] * 100)}% of code' + (f' · fails: {", ".join(b["failed"])}' if b["failed"] else " · up to code") + \
                  f' · {c} commits in 30 days' + (" · a server runs here" if b["path"] in lit else "") + (" · an agent works here" if b["path"] in busy else "")
            svg.append(f'<g><title>{e(tip)}</title><rect x="{bx:.1f}" y="{by_:.1f}" width="{bw:.1f}" height="{h:.1f}" rx="1" fill="{tone(b)}" opacity=".9"/>')
            if b["path"] in lit:
                for k in range(int(min(h - 6, 60) // 9)):
                    svg.append(f'<rect x="{bx + bw * .25:.1f}" y="{by_ + 4 + k * 9:.1f}" width="{bw * .5:.1f}" height="4" fill="#ffd60a"/>')
            if b["path"] in busy:
                svg.append(f'<circle cx="{bx + bw / 2:.1f}" cy="{by_ - 7:.1f}" r="3.5" fill="#3e63dd"/>')
            svg.append("</g>")
        if lots.get(d):
            lx = x0 + len(bs) * (bw + 1)
            svg.append(f'<g><title>{lots[d]} places on the map with nothing checked out here (on GitHub only, or missing)</title>'
                       f'<rect x="{lx:.1f}" y="{ground - 12}" width="{max(bw, 8):.1f}" height="12" fill="none" stroke="currentColor" stroke-dasharray="2 2" opacity=".5"/></g>')
        label = {"(the map)": "the map", "Great_Library_of_SISO": "Great Library"}.get(d, d.replace("_", " "))
        up = sum(1 for b in bs if b["up_to_code"])
        cx = x0 + w / 2
        svg.append(f'<text x="{cx:.1f}" y="{ground + 20}" text-anchor="middle" font-size="12" font-weight="700" fill="currentColor">{e(label)}</text>'
                   f'<text x="{cx:.1f}" y="{ground + 36}" text-anchor="middle" font-size="10" fill="currentColor" opacity=".75">{len(bs)} · {up} up to code</text>' +
                   (f'<text x="{cx:.1f}" y="{ground + 50}" text-anchor="middle" font-size="10" fill="currentColor" opacity=".55">{lots[d]} vacant</text>' if lots.get(d) else ""))
        x0 += w + G
    svg.append("</svg>")
    key = ('<p class="small muted" style="margin-top:8px">Height: commits in the last 30 days. '
           '<span style="color:#30a46c">■</span> up to code · <span style="color:#f5a524">■</span> 75% or more · '
           '<span style="color:#e5484d">■</span> below 75% · <span style="color:#8b8d98">■</span> someone else\'s code in a district · '
           '<span style="color:#ffd60a">■</span> windows lit: a server runs from it · <span style="color:#3e63dd">●</span> an agent works in it · '
           'dashed: vacant lots (on the map, not checked out here). Hover a building for its name and score.</p>')
    return "".join(svg) + key


# ---------------------------------------------------------------- the page

def table(head, rows):
    h = "".join(f"<th>{x}</th>" for x in head)
    b = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in r) + "</tr>" for r in rows)
    return f'<div style="overflow-x:auto;max-width:100%"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def chip(text, ok):
    col = {True: "#30a46c", False: "#e5484d", None: "#8b8d98"}[ok]
    return f'<span style="color:{col};font-weight:600">{e(text)}</span>'


def build():
    t0 = time.time()
    now = time.strftime("%Y-%m-%d %H:%M")
    code = code_record()
    rules = load(os.path.join(REPO, "plan", "building-code.json"))["rules"]
    repos = [x["path"] for x in code["results"]]
    act = activity(repos)
    srv = servers(repos)
    ags = agents(repos)
    lots, map_n = vacant()
    lit = {s["repo"] for s in srv if s["repo"]}
    busy = {a["repo"] for a in ags if a["repo"] and a["status"] == "working"}
    backup = load(os.path.join(REPO, "machines", MACHINE, "backup.json"), {"results": []})
    bstat = collections.Counter(r.get("status") for r in backup["results"])
    census = load(os.path.join(REPO, "machines", MACHINE, "census.json"), {})
    machines = load(os.path.join(REPO, "plan", "machines.json"), {"machines": {}})["machines"]
    inbox = open(os.path.join(REPO, ".agents", "INBOX.md")).read() if os.path.exists(os.path.join(REPO, ".agents", "INBOX.md")) else ""
    inbox_open = len(re.findall(r"^- \[ \]", inbox, re.M))
    unreg = [s for s in srv if not s["registered"]]
    below = [x for x in code["results"] if not x["up_to_code"]]
    keys = [x for x in code["results"] if "no-keys" in x["failed"]]
    ours = {x["path"] for x in code["results"] if x["owner"] != "foreign"}
    week = sum(v["d7"] for r, v in act.items() if r in ours)

    alerts = []
    if bstat.get("error") or bstat.get("held") or bstat.get("missing"):
        alerts.append(f"Last nightly backup ({age(backup.get('ran_at'))}): {bstat.get('error', 0)} errors, {bstat.get('held', 0)} held by the secret scan, {bstat.get('missing', 0)} missing.")
    fleet = load(os.path.join(REPO, "machines", "fleet.json"), {}).get("machines", {})
    down = [f"{n} ({r['state']} since {(r.get('down_since') or '')[:10]})" for n, r in fleet.items()
            if r.get("state") not in (None, "ok", "not-probed")]
    if down:
        alerts.append("Machines not answering: " + ", ".join(e(x) for x in down) + " (estate fleet).")
    if keys:
        alerts.append(f"{len(keys)} repos keep key files outside the store: " + ", ".join(e(x["path"]) for x in keys[:6]) + ".")
    hist = [json.loads(l) for l in open(os.path.join(REPO, "machines", MACHINE, "code-history.jsonl"))] if os.path.exists(os.path.join(REPO, "machines", MACHINE, "code-history.jsonl")) else []
    if hist and hist[-1].get("fell"):
        alerts.append("Fell below the building code at the last scoring: " + ", ".join(e(p) for p in hist[-1]["fell"]) + ".")
    if unreg:
        alerts.append(f"{len(unreg)} servers listen on ports nobody registered in plan/ports.json" +
                      (f", {sum(1 for s in unreg if s['public'])} of them on every network interface" if any(s["public"] for s in unreg) else "") + ".")
    if inbox_open:
        alerts.append(f"{inbox_open} open items in the Estate Manager's inbox (.agents/INBOX.md).")
    for name, m in machines.items():
        if str(m.get("status", "")).startswith("BLOCKED") and name not in fleet:   # a probed box speaks for itself above
            alerts.append(f"Machine {e(name)}: {e(m['status'])}.")

    ch = []
    ch.append(("skyline", "The skyline", f"every building on the map, scored {age(code.get('at'))}; activity and servers as of {now}",
               skyline(code, act, lit, busy, lots)))
    ch.append(("alerts", "Alerts", "what needs someone, worst first",
               "<ul>" + "".join(f"<li>{a}</li>" for a in alerts) + "</ul>" if alerts else "<p>Nothing needs anyone right now.</p>"))
    ch.append(("agents", "Agents at work", f"herdr agent list, {now}",
               table(["Agent", "Harness", "Status", "Where"], [[f"<b>{e(a['name'])}</b>", e(a["harness"]), chip(a["status"], {"working": True, "done": None, "idle": None}.get(a["status"])),
                                                              f"<code>{e(a['repo'] or a['where'])}</code>"] for a in ags])))
    ch.append(("servers", "Servers and ports", f"lsof, {now}; a port is registered when plan/ports.json names it",
               table(["Port", "Process", "Runs from", "Registered"], [[f"<b>{s['port']}</b>" + (" <span class='muted'>(all interfaces)</span>" if s["public"] else ""), e(s["cmd"]),
                                                                     f"<code>{e(s['repo'] or s['cwd'] or '?')}</code>",
                                                                     chip(s["registered"]["service"], True) if s["registered"] else chip("no", False)] for s in srv])))
    worst = sorted(below, key=lambda x: (x["owner"] != "own", x["score"]))[:25]
    ch.append(("code", "The building code", f"{code.get('up_to_code', 0)} of {code.get('repos', 0)} buildings up to code; scored {age(code.get('at'))} by tools/code.py against plan/building-code.json",
               table(["Rule", "Buildings failing", "The fix"], [[f"<b>{e(r['name'])}</b><br><span class='muted'>{e(r['rule'])}</span>", str(code["failing_by_rule"].get(r["id"], 0)), e(r["fix"])] for r in rules]) +
               "<h3>Furthest below code</h3>" +
               table(["Building", "Score", "Fails"], [[f"<code>{e(x['path'])}</code>", f"{int(x['score'] * 100)}%", e(", ".join(x["failed"]))] for x in worst]) +
               "<p class='small muted'>One building's certificate: <code>python3 tools/code.py &lt;words&gt;</code> in siso-estate.</p>"))
    seats = load(os.path.join(REPO, "plan", "owners.json"), {"seats": {}})["seats"]
    league = sorted(code.get("by_seat", {}).items(), key=lambda kv: -kv[1]["mean_score"])
    ch.append(("owners", "Owners", "every building has an owner who answers for its score (plan/owners.json); unassigned land is held by the top Agent Zero until it names one",
               table(["Owner", "Buildings", "Up to code", "Mean score", "Unassigned"],
                     [[f"<b>{e(k)}</b><br><span class='muted'>{e(seats.get(k, ''))}</span>", str(v["buildings"]), str(v["up_to_code"]),
                       f"{int(v['mean_score'] * 100)}%", str(v["state_land"]) if v["state_land"] else ""] for k, v in league])))
    births = load(os.path.join(REPO, "machines", MACHINE, "births.json"), {})
    if births:
        cut = "2026-09-23T05"
        recent = collections.Counter((r["kind"], r["verdict"]) for r in births.get("rows", []) if r["at"] >= cut and r["kind"] in ("worktree", "clone", "init", "generator"))
        lk = births.get("lookups_last_7_days", {})
        share = int(100 * lk.get("asked", 0) / max(1, lk.get("asked", 0) + lk.get("searched", 0)))
        ch.append(("births", "Where new things are born", f"every repo, clone and worktree an agent created, from the agents' own history (tools/births.py, {age(births.get('at'))})",
                   f"<p><b>Ask the city, or search the disk:</b> in the last 7 days other agents asked the map (<code>estate where</code>) {lk.get('asked', 0)} times "
                   f"and searched the disk by hand {lk.get('searched', 0)} times: <b>{share}%</b> asked. The target (P3) is 80%.</p>" +
                   table(["Born since 23 Sep", "In the right place", "In the wrong place", "Could not tell"],
                         [[k, str(recent.get((k, "right"), 0)), str(recent.get((k, "wrong"), 0)), str(recent.get((k, "unknown"), 0))] for k in ("worktree", "clone", "init", "generator")])))
    dark = sorted([x for x in code["results"] if x.get("lifecycle") == "dormant" and x["owner"] != "foreign"], key=lambda x: -x.get("idle_days", 0))
    ch.append(("dark", "Going dark", "our buildings with no work of their own in 90 days: they go to GitHub only (reform 2), after a check that GitHub holds everything",
               table(["Building", "Owner", "Idle"], [[f"<code>{e(x['path'])}</code>", e(x.get("seat", "")), "never worked in" if x.get("idle_days", 0) > 9999 else f"{int(x['idle_days'])} days"] for x in dark])
               if dark else "<p>No dormant buildings.</p>"))
    fixes = [json.loads(l) for l in open(os.path.join(REPO, "machines", MACHINE, "fixes.jsonl"))] if os.path.exists(os.path.join(REPO, "machines", MACHINE, "fixes.jsonl")) else []
    day = [f for f in fixes if f["at"] >= time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() - 86400))]
    ch.append(("law", "The law at work", "routine fixes the Estate Manager made on its own in the last 24 hours, under plan/fix-policies.json (decided by Shaan, 25 Sep)",
               table(["When", "Building", "Fix", "Commit"], [[e(f["at"][11:16]), f"<code>{e(f['repo'])}</code>", e(f["policy"]),
                                                             e((f.get("commit") or "") + (" · pushed" if f.get("pushed") else "") + (f" · {f['note']}" if f.get("note") else ""))] for f in day[-40:]])
               if day else "<p>No fixes in the last 24 hours.</p>"))
    by_d = collections.defaultdict(int)
    for r, v in act.items():
        if r in ours:
            by_d[r.split("/")[0] if r != "." else "(the map)"] += v["d7"]
    top7 = sorted(((v["d7"], r) for r, v in act.items() if v["d7"] and r in ours), reverse=True)[:15]
    ch.append(("week", "This week", f"commits in the last 7 days on each of our buildings' checked-out branch (others' code not counted), {now}",
               table(["District", "Commits"], [[e(d), str(n)] for d, n in sorted(by_d.items(), key=lambda kv: -kv[1]) if n]) +
               "<h3>Busiest buildings</h3>" + table(["Building", "Commits"], [[f"<code>{e(r)}</code>", str(n)] for n, r in top7])))
    nb, ne, ftable = flows(code)
    ch.append(("flows", "Buildings and how they flow", f"{nb} buildings carded, {ne} flows, each with a file:line (the ledger, plan/planner/cards)",
               "<p>Click a building for its repo; click a flow for the building it names.</p>" + ftable))
    ch.append(("machines", "Backups and machines", f"last nightly backup {age(backup.get('ran_at'))}; census {age(census.get('at'))}",
               table(["Backup result", "Repos"], [[e(k or "?"), str(v)] for k, v in bstat.most_common()]) +
               table(["Machine", "Where it stands"], [[f"<b>{e(n)}</b>", e(str(m.get("status", "")))] for n, m in machines.items()]) +
               remote_servers(machines)))

    status_val = f"{code.get('up_to_code', 0)} of {code.get('repos', 0)} buildings up to code"
    page = {
        "meta": {"title": "Estate · Operations centre", "description": "The SISO estate, live: every building on the map, its code score, the agents and servers at work.",
                 "stream": "projects", "crumb": [{"label": "SISO_Agents", "href": "#"}, {"label": "siso-estate", "href": "#"}, {"label": "the city"}], "actions": [],
                 "source": "tools/city.py (siso-shell U7), rebuilt every 2 minutes by launchd com.siso.estate-city"},
        "heading": {"breadcrumb": [{"label": "SISO_Workspace", "href": "#"}, {"label": "the operations centre", "href": "#"}],
                    "kicker": f"live <span class=\"sep\">·</span> {now}",
                    "title": "The SISO estate, as it runs.",
                    "subtitle": "Every repo on the map as a building: its code score, whether a server runs from it, whether an agent is in it. Refreshes itself every 2 minutes.",
                    "badges": [],
                    "status": {"kicker": "The city now", "value": status_val,
                               "note": f"{len(alerts)} alerts · {sum(1 for a in ags if a['status'] == 'working')} agents working · {len(srv)} servers · {week} commits this week",
                               "facts": [{"k": "places on the map", "v": str(map_n)}, {"k": "vacant lots", "v": str(sum(lots.values()))},
                                         {"k": "study shelf / package clones", "v": str(code.get("not_buildings", 0))}]},
                    "metrics": [{"value": f"{int(code.get('mean_score', 0) * 100)}%", "label": "mean code score", "tone": "projects", "href": "#code"},
                                {"value": str(len(alerts)), "label": "alerts", "tone": "research", "href": "#alerts"},
                                {"value": str(len(unreg)), "label": "unregistered servers", "tone": "research", "href": "#servers"},
                                {"value": str(week), "label": "commits this week", "tone": "projects", "href": "#week"}],
                    "jump": [{"label": x[1], "href": "#" + x[0]} for x in ch]},
        "sections": {"abstract": {"n": "00", "title": "In one screen", "sub": "the city now"},
                     "evidence": {"n": "08", "title": "Evidence · limits"}, "related": {"n": "09", "title": "Related"}, "agent": {"n": "10", "title": "Agent entry"}},
        "abstract": {"lede": "The operations centre of the SISO estate (goal-2050 P4): the map drawn as a city, rebuilt from the disk every 2 minutes.",
                     "callout": "<strong>Now:</strong> " + (alerts[0] if alerts else "nothing needs anyone."),
                     "facts": [{"k": "built", "v": now}, {"k": "code scored", "v": age(code.get("at"))}, {"k": "for agents", "v": "city.json beside this page"}]},
        "contents": {"from": "from its sections"},
        "chapters": [{"id": i, "n": f"{n + 1:02d}", "title": t, "sub": s, "html": h} for n, (i, t, s, h) in enumerate(ch)],
        "evidence": {"facts": [{"claim": "Scores", "receipt": "machines/laptop/code.json (tools/code.py)", "at": age(code.get("at"))},
                               {"claim": "Servers", "receipt": "lsof -iTCP -sTCP:LISTEN + each process's cwd", "at": now},
                               {"claim": "Agents", "receipt": "herdr agent list", "at": now}],
                     "proposals": [], "limits": ["One machine so far: the laptop. The VPS and the Mini join as P7 lands.",
                                                 "Activity counts commits on each repo's checked-out branch only."],
                     "version": {"label": now, "href": "#", "at": "tools/city.py"}},
        "related": [{"title": "The new plan (Shanghai 2050)", "href": "http://127.0.0.1:8891/card/bb3a2e86-9f9-mug950gv/html", "type": "page", "tone": "research", "why": "what this page is step one of"}],
        "quickstart": {"entry": {"path": "SISO_Agents/siso-estate/tools/city.py", "href": "#"}, "owner": {"name": "the Estate Manager (ESTATE)", "last": now},
                       "verification": "python3 tools/city.py build", "commands": ["estate city", "python3 tools/code.py <words>", "estate where <words>"]},
    }
    os.makedirs(OUT, exist_ok=True)
    data, nav = os.path.join(OUT, "page-data.json"), os.path.join(OUT, "page-nav.json")
    json.dump(page, open(data, "w"), ensure_ascii=False)
    json.dump({"title": "SISO estate", "mark": "S", "home": "#", "groups": [{"id": "g", "label": "The city", "destinations": [
        {"id": x[0], "label": x[1], "href": "#" + x[0], "icon": "layout-dashboard"} for x in ch]}]}, open(nav, "w"))
    tmp = os.path.join(OUT, "index.tmp.html")
    subprocess.run(["node", os.path.join(SHELL, "bin", "compose"), "U7", data, "--nav", nav, "--base", "", "--out", tmp], check=True, cwd=SHELL, capture_output=True, timeout=60)
    page_html = open(tmp).read()
    for href, p in {"/shell/tokens.css": "shell/tokens.css", "/shell/rail.css": "shell/rail.css", "/parts/parts.css": "parts/parts.css"}.items():
        page_html = page_html.replace(f'<link rel="stylesheet" href="{href}">', f"<style>/* {href} from siso-shell */\n{open(os.path.join(SHELL, p)).read()}\n</style>")
    for src, p in {"/shell/rail.js": "shell/rail.js", "/parts/catalogue.js": "parts/catalogue.js"}.items():
        page_html = page_html.replace(f'<script src="{src}" defer></script>', f"<script>/* {src} from siso-shell */\n{open(os.path.join(SHELL, p)).read()}\n</script>")
    page_html = page_html.replace("<head>", '<head><meta http-equiv="refresh" content="120">', 1)
    open(tmp, "w").write(page_html)
    os.replace(tmp, os.path.join(OUT, "index.html"))
    facts = {"at": now, "code": {k: code.get(k) for k in ("at", "repos", "up_to_code", "mean_score", "failing_by_rule", "by_district")},
             "alerts": [re.sub(r"<[^>]+>", "", a) for a in alerts], "agents": ags, "servers": srv, "activity": act, "vacant": dict(lots),
             "backup": dict(bstat), "machines": {n: m.get("status") for n, m in machines.items()}}
    tmpj = os.path.join(OUT, "city.tmp.json")
    json.dump(facts, open(tmpj, "w"), indent=1, default=str)
    os.replace(tmpj, os.path.join(OUT, "city.json"))
    for p in (data, nav):
        os.remove(p)
    return f"built {OUT}/index.html in {time.time() - t0:.1f}s: {len(code['results'])} buildings, {len(alerts)} alerts, {len(srv)} servers, {len(ags)} agents"


def serve(port):
    # P4: the page is under a minute old while anyone looks at it. A build is cheap now (activity() caches each
    # repo's git log on its HEAD reflog mtime), so it runs every 40-50 s while the page was viewed in the last 10 min
    # and every 15 min otherwise; a visit to a stale page rebuilds behind it at once.
    seen = {"at": 0.0}
    wake = threading.Event()

    def loop():
        last = 0.0
        while True:
            try:
                print(time.strftime("%H:%M:%S"), build(), flush=True)
            except Exception as x:                    # a failed build keeps the last good page up
                print(time.strftime("%H:%M:%S"), "build failed:", repr(x)[:300], flush=True)
            last = time.time()
            while True:
                wake.wait(10)
                wake.clear()
                viewed = time.time() - seen["at"] < 600
                if time.time() - last >= (40 if viewed else 900):
                    break

    os.makedirs(OUT, exist_ok=True)
    threading.Thread(target=loop, daemon=True).start()

    class Quiet(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=OUT, **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            seen["at"] = time.time()
            wake.set()
            super().do_GET()

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

    ThreadingHTTPServer(("127.0.0.1", port), Quiet).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "serve"])
    ap.add_argument("--port", type=int, default=8895)
    a = ap.parse_args()
    if a.cmd == "build":
        print(build())
    else:
        serve(a.port)


if __name__ == "__main__":
    main()
