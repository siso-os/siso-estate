#!/usr/bin/env python3
"""The landing packet: everything a citizen needs to work in one building, pre-assembled.

  packet.py <path-or-name> [--budget 8000] [--json]

Principle 2 of the estate model (`docs/MODEL.md` section 3): context arrives, it is not hunted. A landing
packet answers, in about 8k tokens, what an agent otherwise spends 21 tool calls finding: what this building
is and whose law it stands under (Card), what it says it is (Door: its AGENTS.md), where it stands now (Now:
the last `## State` in `.agents/HANDOFF.md`), what it remembers (Memory), what work is open (Tasks), what
the estate inbox says about it (Letters), and the ten rules of working in the estate (Rules: `docs/MODEL.md`
section 12, verbatim).

The target is an existing path (absolute, relative to the cwd, or relative to the workspace) or a building
name matched against the last part of each `path` in the machine's `code.json`; a name that matches several
buildings prints them and exits 2. The Card is that row, so `estate code` (tools/code.py) must have run; every
other section is read from the building itself. Read-only, no network, Python 3 standard library only.

Over budget, the longest section is cut first, marked `[... truncated, full file: <path>]`; Card and Rules are
never cut, so a budget smaller than those two is not honoured. ESTATE_WS, ESTATE_CODE, ESTATE_INBOX,
ESTATE_MODEL and ESTATE_MACHINE override the roots.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import legend  # a partner's law follows the partner, wherever its folder sits

HOME = os.path.expanduser("~")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_WS = os.path.join(HOME, "SISO_Workspace")

ISLANDS = {"SISO_Agency": "agency", "SISO_Agents": "engine", "Great_Library_of_SISO": "library",
           "HALO_Agency": "halo", "personal": "home"}
LAWS = {"halo": "Cam's code stays in camronkellman/halocrm; never copy it off", "home": "private"}
CARD_FIELDS = ("path", "district", "origin", "owner", "seat", "lifecycle", "idle_days", "score")

ORDER = ("Card", "Door", "Now", "Memory", "Tasks", "Letters", "Rules")   # how the packet reads
ALWAYS = ("Card", "Rules")                                              # never truncated
ALLOC = ("Now", "Door", "Memory", "Tasks", "Letters")                   # the order the rest take room in
MIN_CHARS = 240                                                         # smallest useful truncation
MARK = "\n[... truncated, full file: %s]"


def load(p, d=None):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return d


def read(p):
    try:
        return open(p, errors="replace").read()
    except OSError:
        return None


def envp(name, default):
    return os.environ.get(name) or default


class Sec:
    """One section: its heading, its text, and where the text came from."""
    def __init__(self, name, source, text):
        self.name, self.source, self.text = name, source, text.strip("\n")

    @property
    def head(self):
        return "## %s — source: %s\n" % (self.name, self.source)

    @property
    def rendered(self):
        return self.head + self.text + "\n"

    @property
    def tokens(self):
        return (len(self.rendered) + 3) // 4


def truncate(sec, keep):
    """Cut a section to keep characters of rendered text, at a line boundary, and mark where it was cut."""
    room = keep - len(sec.head) - len(MARK % sec.source)
    body = sec.text[:max(0, room)]
    cut = body.rfind("\n")
    if cut > 0:
        body = body[:cut]
    sec.text = body + MARK % sec.source


def fit(secs, budget):
    """Fit the packet to the budget.

    Card and Rules always stay whole. The rest take room in the order Now, Door, Memory, Tasks, Letters: a
    section that fits in what is left is kept whole, and from the first one that does not fit, everything is
    liable to be cut, longest first, with a marker naming its full file.
    """
    limit = budget * 4
    room = limit - sum(len(s.rendered) for s in secs if s.name in ALWAYS)
    cuttable = []
    for name in ALLOC:
        s = next((x for x in secs if x.name == name), None)
        if s is None:
            continue
        if not cuttable and len(s.rendered) <= room:
            room -= len(s.rendered)
        else:
            cuttable.append(s)
    total = sum(len(s.rendered) for s in secs)
    while total > limit and cuttable:
        big = max(cuttable, key=lambda s: len(s.rendered))
        before = len(big.rendered)
        if before <= len(big.head) + MIN_CHARS:     # too small to cut: drop it
            cuttable.remove(big)
            secs.remove(big)
            total -= before
            continue
        truncate(big, max(len(big.head) + MIN_CHARS, before - (total - limit)))
        total += len(big.rendered) - before
    return secs


def rel_to(p, ws):
    p = os.path.normpath(p)
    if p == ws:
        return "."
    return p[len(ws) + 1:] if p.startswith(ws + os.sep) else p


def resolve(arg, ws, code):
    """The building folder for a path or a name: (root, rel, candidates); candidates non-empty means ambiguous."""
    arg = arg.rstrip("/")
    if os.path.isabs(arg) or "/" in arg:
        for base in (None, os.getcwd(), ws):
            p = os.path.normpath(os.path.join(base, arg) if base else arg)
            if os.path.isfile(p):
                p = os.path.dirname(p)
            if os.path.isdir(p):
                return p, rel_to(p, ws), []
    seen, cands = set(), []
    for r in code.get("results", []):
        rp = (r.get("path") or "").rstrip("/")
        if rp == arg or os.path.basename(rp) == arg:
            p = os.path.normpath(os.path.join(ws, rp))
            if p not in seen:
                seen.add(p)
                cands.append(p)
    if len(cands) == 1:
        return cands[0], rel_to(cands[0], ws), []
    return None, None, cands


def island_of(rel):
    top = rel.split("/")[0] if rel not in ("", ".") else "(the map)"
    return ISLANDS.get(top, top)


def card(row, rel):
    """The building's row in code.json as compact YAML-ish lines, with its island and its law."""
    island = island_of(rel)
    po = legend.partner_of(rel)
    lines = ["path: " + str(row.get("path", rel)), "island: " + island] + (["partner: " + po[0] + (" · client " + po[1] if po[1] else "")] if po else []) + [
             "law: " + ((legend.law(po[0]) if po else None) or LAWS.get(island, "SISO's"))]
    for f in CARD_FIELDS[1:]:
        v = row.get(f)
        lines.append("%s: %s" % (f, v if v not in (None, "") else "(none)"))
    lines.append("failed rules: " + (", ".join(row.get("failed") or []) or "none"))
    return "\n".join(lines)


def door(root):
    """The building's door: its whole AGENTS.md."""
    return read(os.path.join(root, "AGENTS.md"))


def now(root):
    """The last `## State` section of HANDOFF.md, else the file's last 60 lines."""
    text = read(os.path.join(root, ".agents", "HANDOFF.md"))
    if text is None:
        return None
    lines = text.split("\n")
    starts = [i for i, l in enumerate(lines) if re.match(r"^##\s+State\b", l)]
    if not starts:
        return "\n".join(lines[-60:]).strip() or None
    end = len(lines)
    for j in range(starts[-1] + 1, len(lines)):
        if re.match(r"^##\s", lines[j]):
            end = j
            break
    return "\n".join(lines[starts[-1]:end]).strip() or None


def memory(root):
    """The building's memory index."""
    return read(os.path.join(root, ".agents", "memory", "MEMORY.md"))


def tasks(root, limit=15):
    """Ids and titles of open tasks: neither their folder nor their task.json says completed or cancelled."""
    base = os.path.join(root, ".agents", "tasks")
    if not os.path.isdir(base):
        return None
    found, out, seen = [], [], set()
    for bucket in sorted(os.listdir(base)):
        bp = os.path.join(base, bucket)
        if bucket.startswith(".") or not os.path.isdir(bp):
            continue
        if os.path.isfile(os.path.join(bp, "task.json")):
            found.append(bp)
        else:
            found += [os.path.join(bp, k) for k in sorted(os.listdir(bp))
                      if os.path.isfile(os.path.join(bp, k, "task.json"))]
    for p in found:
        if os.path.basename(os.path.dirname(p)) in ("completed", "cancelled"):
            continue
        d = load(os.path.join(p, "task.json"), {})
        if str(d.get("status", "")).lower() in ("completed", "cancelled"):
            continue
        tid = d.get("id") or os.path.basename(p)
        if tid in seen:
            continue
        seen.add(tid)
        out.append("%s — %s" % (tid, (d.get("title") or "").strip()))
        if len(out) >= limit:
            break
    return "\n".join(out) if out else None


def letters(inbox, root, seat, limit=10):
    """The estate inbox's last lines that mention this building's folder name or its seat."""
    text = read(inbox)
    if text is None:
        return None
    keys = [os.path.basename(root.rstrip("/")).lower()]
    if seat and str(seat) not in ("-", "(none)"):
        keys.append(str(seat).lower())
    hits = [l for l in text.split("\n") if l.strip() and any(k in l.lower() for k in keys)]
    return "\n".join(hits[-limit:]) if hits else None


def rules(model):
    """The ten numbered items of docs/MODEL.md section 12, verbatim, continuations included."""
    text = read(model)
    if text is None:
        return None
    m = re.search(r"^##\s+12\..*?$(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    if not m:
        return None
    items = [p.rstrip() for p in re.split(r"^(?=\d+\.\s)", m.group(1), flags=re.M)
             if re.match(r"^\d+\.\s", p)]
    return "\n".join(items) if items else None


def build(arg, budget=8000, ws=None, code_path=None, inbox=None, model=None):
    """Assemble one building's landing packet. Returns building, island, sections and total tokens."""
    ws = os.path.normpath(ws or envp("ESTATE_WS", DEFAULT_WS))
    code_path = code_path or envp("ESTATE_CODE", os.path.join(
        REPO, "machines", envp("ESTATE_MACHINE", "laptop"), "code.json"))
    inbox = inbox or envp("ESTATE_INBOX", os.path.join(REPO, ".agents", "INBOX.md"))
    model = model or envp("ESTATE_MODEL", os.path.join(REPO, "docs", "MODEL.md"))

    code = load(code_path, {})
    root, rel, cands = resolve(arg, ws, code)
    if root is None:
        return {"error": "no building" if not cands else "ambiguous building",
                "candidates": [rel_to(c, ws) for c in cands], "arg": arg}

    row = next((r for r in code.get("results", []) if rel_to(os.path.join(ws, (r.get("path") or "").rstrip("/")), ws) == rel), {})
    src = {"Card": code_path, "Door": os.path.join(root, "AGENTS.md"),
           "Now": os.path.join(root, ".agents", "HANDOFF.md"),
           "Memory": os.path.join(root, ".agents", "memory", "MEMORY.md"),
           "Tasks": os.path.join(root, ".agents", "tasks"), "Letters": inbox, "Rules": model}
    reg = load(envp("ESTATE_REGISTER", os.path.join(os.path.dirname(os.path.dirname(code_path)), "register.json")), {})
    rrow = next((x for x in reg.get("buildings", []) if x.get("path") == rel), None)
    cardtext = card(row, rel)
    if rrow:  # the register (tools/register.py) adds what code.json does not know
        cardtext += "\n" + "\n".join("%s: %s" % (k, rrow.get(k)) for k in
                                     ("postcode", "compound", "provenance", "upstream", "built_on", "runs", "commits_14d") if rrow.get(k) not in (None, [], ""))
    text = {"Card": cardtext, "Door": door(root), "Now": now(root), "Memory": memory(root),
            "Tasks": tasks(root), "Letters": letters(inbox, root, row.get("seat")), "Rules": rules(model)}
    secs = [Sec(n, rel_to(src[n], ws), text[n]) for n in ORDER if text[n]]
    secs = fit(secs, budget)
    out = [{"name": s.name, "source": s.source, "tokens": s.tokens, "text": s.text}
           for s in sorted(secs, key=lambda s: ORDER.index(s.name))]
    return {"building": rel, "island": island_of(rel), "sections": out,
            "tokens": sum(s["tokens"] for s in out), "budget": budget}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    arg, budget, as_json = None, 8000, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            as_json = True
        elif a in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        elif a == "--budget" or a.startswith("--budget="):
            v = argv[i + 1] if a == "--budget" else a.split("=", 1)[1]
            if a == "--budget":
                i += 1
            try:
                budget = int(v)
            except (IndexError, ValueError):
                sys.stderr.write("packet: --budget needs a number\n")
                return 1
        elif arg is None:
            arg = a
        else:
            sys.stderr.write("packet: one target only\n")
            return 1
        i += 1
    if not arg:
        sys.stderr.write("packet: usage: packet.py <path-or-name> [--budget 8000] [--json]\n")
        return 1

    r = build(arg, budget)
    if r.get("error"):
        sys.stderr.write("packet: %s: %s\n" % (r["error"], arg))
        for c in r["candidates"]:
            sys.stderr.write("  %s\n" % c)
        return 2 if r["error"] == "ambiguous building" else 1
    if as_json:
        print(json.dumps(r, indent=1))
    else:
        for s in r["sections"]:
            print(Sec(s["name"], s["source"], s["text"]).rendered.rstrip("\n"))
            print()
        print("packet: %d tokens (budget %d)" % (r["tokens"], budget))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except BrokenPipeError:      # packet.py <building> | head: the reader went away, not an error
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        rc = 0
    sys.exit(rc)
