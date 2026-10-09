#!/usr/bin/env python3
"""Write plan/surfaces.json: every live page SISO has (the Library's "Live" shelf in Agent Base), from the sources themselves.

  surfaces.py            write plan/surfaces.json and print the counts
  surfaces.py --check    print what would change, write nothing

Sources, so the list cannot go stale by hand:
- Cloudflare Pages: every project on the account (`wrangler pages project list --json`), with its domain and last deploy;
- the building that deploys each one: the `name` in a wrangler config or `--project-name` in a package.json inside a
  building on the register (machines/register.json), so a Built row in the Library links to its live page;
- local and app rows (consoles, the estate centre, desktop apps): Agent Base's own seed list (library-surfaces.json),
  which this file carries forward unchanged, because only the machine that runs them knows them.
When Cloudflare cannot be asked (offline, no login), the last file's Cloudflare rows are kept and marked with the error.
Run hourly by tools/refresh.py on the laptop. Agent Base reads it (services/node/src/library.ts, AB_SURFACES).
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.expanduser("~/SISO_Workspace")
OUT = os.path.join(REPO, "plan", "surfaces.json")
SEED = os.path.join(WS, "SISO_Agency/apps/siso-internal-labs/siso-internal-labs-agent-base/services/node/src/library-surfaces.json")
SKIP_DIRS = {"node_modules", ".git", "dist", "target", ".next", "_archive", "vendor", ".wrangler", "build", "out"}
# which shelf a project sits on, by its name; first match wins
GROUPS = [
    (r"^(halo|oracle|collegebesties|halocrm)", "agency", "halo"),
    (r"^(bykonz|mygumm|melanotresses)", "agency", "fahmy"),
    (r"^(kikas|dispo|aged-?care|agedcare|actionist|bbb-|provider-compliance|siso-agency|siso-image-bank)", "agency", None),
    (r"^senior-|career", "family", None),
    (r"", "labs", None),
]


def cloudflare():
    try:
        r = subprocess.run(["wrangler", "pages", "project", "list", "--json"], capture_output=True, text=True, timeout=90, cwd="/tmp")
        rows = json.loads(r.stdout)
        return rows, None
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return None, f"wrangler pages project list: {type(e).__name__}"


def deployers(buildings):
    """Cloudflare project name -> the deepest register building whose tree names it in a wrangler config or deploy script."""
    found = {}
    paths = sorted((b["path"] for b in buildings if b.get("path") and b["path"] != "."), key=len)
    for bp in paths:
        root0 = os.path.join(WS, bp)
        if not os.path.isdir(root0):
            continue
        for root, dirs, files in os.walk(root0):
            depth = root[len(root0):].count(os.sep)
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and depth < 3]
            for f in files:
                if f not in ("wrangler.toml", "wrangler.json", "wrangler.jsonc", "package.json"):
                    continue
                try:
                    t = open(os.path.join(root, f), errors="ignore").read()
                except OSError:
                    continue
                names = re.findall(r"--project-name[= ]([\w-]+)", t)
                if f != "package.json":
                    m = re.search(r'^\s*"?name"?\s*[=:]\s*"([^"]+)"', t, re.M)
                    names += [m.group(1)] if m else []
                for n in names:
                    found[n] = (bp, os.path.relpath(root, WS))  # longer (deeper) building paths come later and win
    return found


def by_name(buildings):
    """Cloudflare project name -> a building whose folder name it is or starts with (bykonzyard-review -> .../bykonzyard)."""
    ours = [b for b in buildings if b.get("path") and b["path"] != "." and b.get("provenance") != "foreign"]
    base = {}
    for b in sorted(ours, key=lambda b: len(b["path"])):  # the shallowest building of a name wins (the repo, not a module)
        base.setdefault(os.path.basename(b["path"]).lower(), b["path"])
    def find(name):
        n = name.lower()
        if n in base:
            return base[n]
        hits = [k for k in base if len(k) >= 4 and n.startswith(k + "-")]
        return base[max(hits, key=len)] if hits else None
    return find


def group_of(name):
    for pat, g, project in GROUPS:
        if re.search(pat, name):
            return g, project


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    reg = json.load(open(os.path.join(REPO, "machines", "register.json")))
    old = json.load(open(OUT)) if os.path.exists(OUT) else {}
    seed = json.load(open(SEED)) if os.path.exists(SEED) else []
    seed = seed if isinstance(seed, list) else seed.get("surfaces", [])
    seed_by = {s["id"]: s for s in seed}
    cf, err = cloudflare()
    rows = []
    if cf is None:
        rows += [dict(s, note=f"kept from {old.get('generated_at')}: {err}") for s in old.get("surfaces", []) if s.get("host") == "cloudflare"]
    else:
        by, guess = deployers(reg.get("buildings", [])), by_name(reg.get("buildings", []))
        seed_url = {s.get("url", "").rstrip("/"): s for s in seed if s.get("url")}
        for p in cf:
            name = p.get("Project Name") or ""
            domains = [d.strip() for d in (p.get("Project Domains") or "").split(",") if d.strip()]
            if not name or not domains:
                continue
            host = next((d for d in domains if not d.endswith(".pages.dev")), domains[0])
            g, project = group_of(name)
            row = {"id": name, "name": name.replace("-", " "), "group": g, "url": f"https://{host}", "host": "cloudflare",
                   "machine": "cloudflare", "deployed": p.get("Last Modified")}
            if project:
                row["project"] = project
            if name in by:
                row["path"], row["edit"] = by[name]
            elif guess(name):
                row["path"] = guess(name)
            s = seed_by.get(name) or seed_url.get(row["url"])
            if s:  # the seed's hand-written facts (name, auth, deploy command, a closer path) win over the guesses
                row.update({k: v for k, v in s.items() if k not in ("url", "host", "machine")})
            rows.append(row)
    ids = {r["id"] for r in rows}
    urls = {r.get("url") for r in rows}
    rows += [s for s in seed if s.get("host") != "cloudflare" and s["id"] not in ids]
    rows += [dict(s, note="in Agent Base's seed but not on the Cloudflare account") for s in seed if s.get("host") == "cloudflare" and s["id"] not in ids and s.get("url") not in urls and cf is not None]
    order = {"agency": 0, "labs": 1, "stack": 2, "family": 3}
    rows.sort(key=lambda r: (order.get(r.get("group"), 9), r.get("project") or "", r["id"]))
    doc = {"_what": "Every live page SISO has, generated hourly by tools/surfaces.py from the Cloudflare account, the buildings' "
                    "deploy configs and Agent Base's seed (local and app rows). Agent Base's Library 'Live' shelf reads it. "
                    "Do not edit by hand: change the source.",
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cloudflare_error": err,
           "counts": {"surfaces": len(rows), "cloudflare": sum(r["host"] == "cloudflare" for r in rows),
                      "linked_to_a_building": sum(bool(r.get("path")) for r in rows)},
           "surfaces": rows}
    before = {r["id"] for r in old.get("surfaces", [])}
    added, gone = sorted(ids - before), sorted(before - {r["id"] for r in rows})
    print(f"surfaces: {doc['counts']}; new {len(added)}, gone {len(gone)}" + (f"; {err}" if err else ""))
    if a.check:
        return 0
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
