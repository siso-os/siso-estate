#!/usr/bin/env python3
"""HALO box placements: every folder, repo, service and env file on halo-vps mapped to its project and keeper.

  placements.py                 survey halo-vps over ssh -> machines/halo-vps/placements.json + BOX-DOOR.md
  placements.py --install       also put BOX-DOOR.md on the box as ~/AGENTS.md (and ~/CLAUDE.md = @AGENTS.md)

The survey reads names only: repo remotes and branches, unit names and states, env file paths and their KEY names
(never a value, never process arguments). The mapping (RULES) is the one hand-kept fact here; everything else is
observed. A folder or unit no rule claims is listed under `unplaced` so the next survey shows what is new.
Source of the box's front door: edit RULES / door() here, never ~/AGENTS.md on the box. (HALO-ESTATE, 27 Sep 2026)
"""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(REPO, "machines", "halo-vps")
H = "/home/shaan"

PROJECTS = {
    "halo-crm": {"name": "HALO CRM", "keeper": "CRM (HALO-DOORS / Cam's lanes)", "repo": "HALO-AGENCY/halocrm",
                 "law": "Cam's code: it lives only on HALO-AGENCY/halocrm (moved from camronkellman/halocrm 27 Sep); never copy it anywhere else (ADR 0007)",
                 "laptop": "SISO_Agency/partners/halo/crm/repo"},
    "halo-donors": {"name": "HALO donor apps (Twenty, AFFiNE, Plane)", "keeper": "CRM",
                    "repo": "upstream (twentyhq/twenty, toeverything/AFFiNE, makeplane/plane) run for HALO",
                    "laptop": "SISO_Agency/partners/halo/crm"},
    "halo-buzz": {"name": "HALO Buzz", "keeper": "CRM", "repo": "HALO-AGENCY/halo-buzz", "laptop": "SISO_Agency/partners/halo/halo-buzz"},
    "oracle": {"name": "Oracle streaming", "keeper": "STREAMING", "repo": "HALO-AGENCY/halo-streaming (box shell); HALO-AGENCY/halo-streaming-project (gatehouse)",
               "law": "Oracle's services and Convex are STREAMING's: others read only",
               "laptop": "SISO_Agency/partners/halo/oracle"},
    "estate": {"name": "Estate backups", "keeper": "ESTATE (HALO-ESTATE)", "repo": "sisodias/siso-estate (tools/halo)",
               "laptop": "SISO_Agents/siso-estate"},
    "agents": {"name": "Agent runtime", "keeper": "Agent Zero (A0)", "repo": "-", "laptop": "SISO_Agents/agent-zero"},
    "box": {"name": "The box itself", "keeper": "ESTATE", "repo": "-", "laptop": "SISO_Agents/siso-estate/machines/halo-vps"},
}

# (path prefix, project, what it is). Longest prefix wins.
RULES = [
    (f"{H}/halo/app", "halo-crm", "the live HALO CRM checkout (app.haloangels.net), branch per deploy"),
    (f"{H}/halo/dev", "halo-crm", "the dev tree: dev checkout, lanes, previews"),
    (f"{H}/halo/dev/app", "halo-crm", "the dev checkout (dev-backend); lanes are its worktrees"),
    (f"{H}/halo/dev/lanes", "halo-crm", "lane worktrees of ~/halo/dev/app, one per task"),
    (f"{H}/halo/dev/lc-main", "halo-crm", "a detached main checkout for comparisons"),
    (f"{H}/halo/dev/twenty", "halo-donors", "dev Twenty config"),
    (f"{H}/halo/worktrees", "halo-crm", "older worktrees of Cam's repo (Sep 17 lanes, vps-port, ui-audit)"),
    (f"{H}/halo/parity", "halo-crm", "parity staging checkout (no remote: a local copy of Cam's code at parity-staging)"),
    (f"{H}/halo/releases", "halo-crm", "built releases"),
    (f"{H}/halo/portal", "halo-crm", "the ops portal"),
    (f"{H}/halo/config", "halo-crm", "service env (donor backup, ops door)"),
    (f"{H}/halo/ops", "halo-crm", "ops scripts"),
    (f"{H}/halo/backups", "halo-crm", "on-box dumps: pre-migration halo_live dumps and Cam's donor-databases/"),
    (f"{H}/halo/exports", "halo-crm", "exports"),
    (f"{H}/halo/camofox", "halo-crm", "headless browser for checks"),
    (f"{H}/halo/dashlab", "halo-crm", "dashboard lab"),
    (f"{H}/halo/twenty", "halo-donors", "Twenty (upstream twentyhq/twenty)"),
    (f"{H}/halo/affine", "halo-donors", "AFFiNE (config, staging)"),
    (f"{H}/halo/plane", "halo-donors", "Plane"),
    (f"{H}/halo/buzz", "halo-buzz", "Buzz source (HALO-AGENCY/halo-buzz) and config"),
    (f"{H}/halo", "halo-crm", "HALO's working root: logs, one-off imports, backups of serve.mjs (a pile; the CRM seat owns it)"),
    (f"{H}/.local/state/halo-native", "halo-crm", "the Postgres 16 cluster (127.0.0.1:55432): all six HALO databases"),
    ("/opt/halo-crm", "halo-crm", "self-hosted CRM infra"),
    ("/srv/oracle", "oracle", "Oracle's box: vps shell (HALO-AGENCY/halo-streaming), OME, operator board, sessions, Convex backups"),
    ("/opt/oracle-convex", "oracle", "Oracle's self-hosted Convex"),
    ("/opt/ciadpi", "oracle", "byedpi (upstream hufrea/byedpi) for Oracle egress"),
    ("/etc/oracle", "oracle", "Oracle service env"),
    ("/srv/oracle-archive", "estate", "the Hetzner (oracle-edge-1) archive, saved as HALO-AGENCY/siso-data-halo-hetzner-archive"),
    ("/opt/containerd", "box", "the system container runtime"),
    (f"{H}/.credentials", "box", "agent credentials on the box (halo-agent.env for halo-agent-session)"),
    ("/srv/halo-outbox", "estate", "root-only nightly dumps of the six databases (3 kept); shipped to siso-vps"),
]
UNITS = [  # (unit name prefix, project)
    ("halo-estate-dump", "estate"), ("oracle-offbox-backup", "estate"),
    ("halo-twenty", "halo-donors"), ("halo-dev-twenty", "halo-donors"), ("halo-affine", "halo-donors"),
    ("halo-plane", "halo-donors"), ("halo-buzz", "halo-buzz"), ("halo-", "halo-crm"),
    ("oracle-", "oracle"), ("herdr", "agents"),
]

SURVEY = r'''
import json, os, subprocess, glob
def sh(*c):
    r = subprocess.run(c, capture_output=True, text=True); return r.stdout.strip()
H = "/home/shaan"; out = {"repos": [], "units": [], "env": [], "folders": [], "tools": []}
for g in subprocess.run(["find", H, "/srv", "/opt", "-maxdepth", "5", "-name", ".git", "-not", "-path", "*/node_modules/*"],
                        capture_output=True, text=True).stdout.split():
    d = os.path.dirname(g)
    url = sh("git", "-C", d, "remote", "get-url", "origin")
    if "@" in url.split("//")[-1].split("/")[0]: url = url.split("//")[0] + "//" + url.split("@", 1)[1]
    out["repos"].append({"path": d, "origin": url or None, "branch": sh("git", "-C", d, "branch", "--show-current") or "(detached)",
                         "dirty": len([l for l in sh("git", "-C", d, "status", "--porcelain").splitlines() if l])})
for f in sorted(glob.glob("/etc/systemd/system/*.service") + glob.glob("/etc/systemd/system/*.timer")):
    n = os.path.basename(f)
    if n.startswith(("halo-", "oracle-", "herdr")):
        out["units"].append({"unit": n, "scope": "system", "state": sh("systemctl", "is-active", n)})
uid = sh("id", "-u", "shaan")
for f in sorted(glob.glob(H + "/.config/systemd/user/*.service") + glob.glob(H + "/.config/systemd/user/*.timer")):
    n = os.path.basename(f)
    st = sh("sudo", "-u", "shaan", "XDG_RUNTIME_DIR=/run/user/" + uid, "systemctl", "--user", "is-active", n)
    out["units"].append({"unit": n, "scope": "user (shaan)", "state": st})
skip = (".example", ".bak", ".e2e", ".test", "testing-server", ".json.example")
for root in (H + "/halo", "/srv", "/etc/oracle", "/opt", H + "/.credentials"):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in ("node_modules", ".git", "lanes", "worktrees", "staging", ".deploy-backup", "lockdown-backup", "backups")
                  and dp.count("/") - root.count("/") < 4]
        for f in fns:
            if (f.endswith(".env") or f.startswith(".env")) and not any(s in f for s in skip) and "bak" not in f:
                p = os.path.join(dp, f)
                try: keys = sorted({l.split("=", 1)[0].replace("export ", "").strip() for l in open(p, errors="replace")
                                    if "=" in l and not l.lstrip().startswith("#")})
                except OSError: keys = []
                out["env"].append({"path": p, "keys": [k for k in keys if k.replace("_", "").isalnum()]})
out["folders"] = sorted(glob.glob(H + "/halo/*/") + glob.glob("/srv/*/") + glob.glob("/srv/oracle/*/") + glob.glob("/opt/*/"))
out["tools"] = sorted(os.listdir(H + "/.local/bin"))
print(json.dumps(out))
'''


def place(path):
    best = None
    for pre, proj, what in RULES:
        if (path == pre or path.startswith(pre.rstrip("/") + "/") or path.startswith(pre)) and (best is None or len(pre) > len(best[0])):
            best = (pre, proj, what)
    return best


def unit_project(n):
    for pre, proj in UNITS:
        if n.startswith(pre):
            return proj
    return None


def survey():
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "halo-vps", "sudo python3 -"], input=SURVEY, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        sys.exit("survey failed: " + r.stderr[-300:])
    return json.loads(r.stdout)


def build(s):
    doc = {"machine": "halo-vps", "host": "62.171.132.124 (Contabo; Cam has root)", "surveyed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "generated_by": "siso-estate tools/halo/placements.py", "projects": PROJECTS, "folders": [], "repos": [], "units": [],
           "env": [], "unplaced": [], "tools": s["tools"]}
    for f in s["folders"]:
        f = f.rstrip("/")
        p = place(f)
        (doc["folders"] if p else doc["unplaced"]).append({"path": f, "project": p[1], "what": p[2]} if p else {"path": f})
    for r in s["repos"]:
        p = place(r["path"])
        doc["repos"].append(dict(r, project=p[1] if p else None))
    for u in s["units"]:
        doc["units"].append(dict(u, project=unit_project(u["unit"])))
    for e in s["env"]:
        p = place(e["path"])
        doc["env"].append(dict(e, project=p[1] if p else None))
    return doc


def door(doc):
    L = ["# halo-vps: the front door for agents on this box", "",
         f"Generated by siso-estate `tools/halo/placements.py` from `machines/halo-vps/placements.json` (survey {doc['surveyed_at']}).",
         "Do not edit this file on the box: change the placements and re-run the generator.", "",
         "**Laws here.** Cam's code lives only on HALO-AGENCY/halocrm (moved from camronkellman/halocrm 27 Sep): never copy it to another repo, never push it anywhere",
         "else (estate ADR 0007). Oracle's services and Convex are STREAMING's: read only unless you are STREAMING. Never print",
         "a secret: env files are listed below by key name only. Don't restart a service or touch Caddy",
         "(`/etc/caddy`) unless that project's keeper asked you to. Don't delete anything that isn't your own.", "",
         "## Projects on this box", "", "| Project | Keeper | Repo |", "|---|---|---|"]
    for k, p in PROJECTS.items():   # laptop map paths stay in placements.json: the private map never goes on a client box (ADR 0007)
        L.append(f"| {p['name']} (`{k}`) | {p['keeper']} | {p['repo']} |")
    L += ["", "## Which folder is which", "", "| Folder | Project | What |", "|---|---|---|"]
    for f in doc["folders"]:
        L.append(f"| `{f['path'].replace(H, '~')}` | {f['project']} | {f['what']} |")
    if doc["unplaced"]:
        L += ["", "Not yet placed (tell ESTATE): " + ", ".join(f"`{u['path'].replace(H, '~')}`" for u in doc["unplaced"])]
    L += ["", "## Repos", "", "| Checkout | Origin | Branch | Dirty files | Project |", "|---|---|---|---|---|"]
    for r in doc["repos"]:
        L.append(f"| `{r['path'].replace(H, '~')}` | {r['origin'] or '(no remote)'} | {r['branch']} | {r['dirty']} | {r['project']} |")
    L += ["", "## Services", "", "User units run as shaan: `systemctl --user status <unit>`. System units: `systemctl status <unit>`.", ""]
    by = {}
    for u in doc["units"]:
        by.setdefault(u["project"], []).append(f"{u['unit'].rsplit('.', 1)[0]}{'' if u['unit'].endswith('.service') else ' (timer)'}"
                                               f"{'' if u['state'] == 'active' else ' [' + u['state'] + ']'}")
    for k, us in by.items():
        L.append(f"- **{PROJECTS.get(k, {}).get('name', k)}** ({u_scope(doc, k)}): " + ", ".join(sorted(set(us))))
    L += ["", "## Where each project's env lives (key names only)", ""]
    for k in PROJECTS:
        es = [e for e in doc["env"] if e["project"] == k]
        if es:
            L.append(f"**{PROJECTS[k]['name']}**")
            for e in es:
                keys = ", ".join(e["keys"][:14]) + (f" and {len(e['keys']) - 14} more" if len(e["keys"]) > 14 else "")
                L.append(f"- `{e['path'].replace(H, '~')}`: {keys or '(no keys)'}")
            L.append("")
    L += ["## Backups", "",
          "All six databases (halo_live, halo_oracle_convex, twenty, halo_affine, plane, buzz) are dumped nightly at 05:40 into",
          "`/srv/halo-outbox` (`halo-estate-dump`). At 06:15 `oracle-offbox-backup` copies them and the Convex zips to siso-vps",
          "through a key that can only add files. At 06:45 siso-vps keeps 7 daily and 4 weekly copies and encrypts them into",
          "`HALO-AGENCY/siso-data-halo-databases`. Take a dump before a migration, as HALO-DOORS does in `~/halo/backups`;",
          "the nightly copy is not a pre-migration snapshot.", "",
          "## Starting a lane", "",
          "1. Ask the project's keeper (table above) and take a task. For HALO CRM, Cam's lanes are worktrees of the dev checkout:",
          "   `git -C ~/halo/dev/app fetch origin && git -C ~/halo/dev/app worktree add ~/halo/dev/lanes/<lane> -b cam/<lane> origin/main`.",
          "2. Env for a lane: copy the dev checkout's `.env.postgres` into the lane (it is gitignored); never commit an env file.",
          "3. Run your agent inside the lane: `~/.local/bin/omp` (DeepSeek worker), `~/.local/bin/codex`, or a herdr pane",
          "   (`herdr.service` runs as shaan; the binary is `~/.local/bin/herdr`, which is not on a non-login PATH).",
          "4. Push only to the lane's own branch on its origin (`gh` is logged in as the box user). When it is merged, remove",
          "   the worktree (`git worktree remove`).",
          "5. Tools in `~/.local/bin`: " + ", ".join(doc["tools"]) + ".", ""]
    return "\n".join(L)


def u_scope(doc, k):
    s = {u["scope"] for u in doc["units"] if u["project"] == k}
    return " and ".join(sorted(s))


def main():
    doc = build(survey())
    os.makedirs(OUT, exist_ok=True)
    json.dump(doc, open(os.path.join(OUT, "placements.json"), "w"), indent=1)
    md = door(doc)
    open(os.path.join(OUT, "BOX-DOOR.md"), "w").write(md + "\n")
    print(f"placements: {len(doc['folders'])} folders, {len(doc['repos'])} repos, {len(doc['units'])} units, {len(doc['env'])} env files, "
          f"{len(doc['unplaced'])} unplaced")
    if "--install" in sys.argv:
        src = os.path.join(OUT, "BOX-DOOR.md")
        subprocess.run(["scp", "-q", src, "halo-vps:AGENTS.md"], check=True)
        subprocess.run(["ssh", "halo-vps", "test -e CLAUDE.md || echo '@AGENTS.md' > CLAUDE.md; chmod 644 AGENTS.md CLAUDE.md"], check=True)
        print("installed ~/AGENTS.md and ~/CLAUDE.md on halo-vps")


if __name__ == "__main__":
    main()
