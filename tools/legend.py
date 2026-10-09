#!/usr/bin/env python3
"""The legend's partners, for code (docs/LEGEND.md §3, plan/legend.json). A partner agency (HALO, Fahmy's) sits inside SISO
Agency at `SISO_Agency/partners/<agency>/`, its clients at `.../clients/<brand>/`. Every tool that places, lists, backs up
or files something asks this module, so a partner move changes one file (plan/legend.json), not ten.

  legend.py <path>...     print the partner and client each workspace-relative path belongs to
  legend.py --homes       print each partner's home on this disk

Each partner's `homes` (and each client's `client_homes`) list the planned place first, then where it sits today; a path
is placed by the longest match, so the answer is the same before and after a move.
"""
import json, os, sys

HOME = os.path.expanduser("~")
WS = os.environ.get("ESTATE_WS", os.path.join(HOME, "SISO_Workspace"))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    try:
        return json.load(open(os.environ.get("ESTATE_LEGEND", os.path.join(REPO, "plan", "legend.json"))))
    except (OSError, ValueError):
        return {"accounts": []}


LEGEND = _load()
PARTNERS = {a["id"]: a for a in LEGEND["accounts"] if a.get("homes")}
PARTNERS_DIR = "SISO_Agency/partners"


def _roots():
    """(root, partner id, brand or None), longest root first."""
    out = []
    for pid, a in PARTNERS.items():
        out += [(h, pid, None) for h in a["homes"]]
        for brand, hs in (a.get("client_homes") or {}).items():
            out += [(h, pid, brand) for h in hs]
    return sorted(out, key=lambda r: -len(r[0]))


ROOTS = _roots()


def partner_of(rel):
    """(partner id, client brand or None) for a workspace-relative path, or None when no partner owns it."""
    rel = rel.strip("/")
    for root, pid, brand in ROOTS:
        if rel == root or rel.startswith(root + "/"):
            if brand is None:
                rest = rel[len(root) + 1:].split("/") if rel != root else []
                brand = rest[1] if len(rest) >= 2 and rest[0] == "clients" else None
            return pid, brand
    parts = rel.split("/")   # a partner the legend does not know yet still reads as one
    if rel.startswith(PARTNERS_DIR + "/") and len(parts) >= 3:
        return parts[2], (parts[4] if len(parts) >= 5 and parts[3] == "clients" else None)
    return None


def client_partner(brand):
    """The partner a client brand came through, or None (a direct client)."""
    for pid, a in PARTNERS.items():
        if brand in (a.get("client_homes") or {}) or brand in (a.get("clients") or []):
            return pid
    return None


def law(pid):
    return (PARTNERS.get(pid) or {}).get("law")


def home(pid, ws=None):
    """The partner's home on this disk: the first of its homes that exists, else the planned one."""
    ws = ws or WS
    hs = PARTNERS[pid]["homes"]
    return next((h for h in hs if os.path.isdir(os.path.join(ws, h))), hs[0])


def main():
    if "--homes" in sys.argv:
        for pid in PARTNERS:
            print(f"{pid:8} {home(pid)}")
        return 0
    for p in sys.argv[1:]:
        print(p, "->", partner_of(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
