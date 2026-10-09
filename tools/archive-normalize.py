#!/usr/bin/env python3
"""Make every top folder of ~/SISO_Workspace/_archive readable: `YYYY-MM-DD-subject/` with a MANIFEST.md (what, why,
source path, restore), or a VAULT-POINTER.md when the folder is only an empty directory skeleton (its files went to
the SISO vault on the Mac Mini).

  archive-normalize.py plan
  archive-normalize.py run

Date: from the name (YYYY-MM-DD, YYYYMMDD, YYYY-MM), else the folder's creation date. Subject: the rest of the name,
lower-case, `_` -> `-`. A rename is one os.rename plus a moves.jsonl line (so tools/repoint.py can follow it).
Manifests are generated from machines/<m>/moves.jsonl (every move whose destination is inside the folder gives its
source path, time and reason), a README's first lines, and the folder's top entries with sizes. Existing MANIFEST.md
files are kept; an existing MANIFEST.txt or README is referenced, never rewritten. Empty skeletons: the directory
listing (3 levels) goes into VAULT-POINTER.md and the empty directories are removed (they hold no files).
"""
import json, os, re, subprocess, sys, time

HOME = os.path.expanduser("~")
AR = os.path.join(HOME, "SISO_Workspace", "_archive")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOVES = os.path.join(REPO, "machines", "laptop", "moves.jsonl")


def born(p):
    st = os.stat(p)
    return time.strftime("%Y-%m-%d", time.localtime(getattr(st, "st_birthtime", st.st_mtime)))


def new_name(name, path):
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-(.+)$", name)
    if m:
        return name
    date, rest = None, name
    for rx, fmt in ((r"(\d{4})-(\d{2})-(\d{2})", "{0}-{1}-{2}"), (r"(\d{4})(\d{2})(\d{2})", "{0}-{1}-{2}"),
                    (r"(\d{4})-(\d{2})(?!\d)", "{0}-{1}-01")):
        m = re.search(rx, name)
        if m and 2020 <= int(m.group(1)) <= 2030:
            date = fmt.format(*m.groups())
            rest = (name[:m.start()] + name[m.end():]).strip("-_ ")
            break
    date = date or born(path)
    subject = re.sub(r"[^a-z0-9]+", "-", rest.lower()).strip("-") or "misc"
    return f"{date}-{subject}"


def has_files(p):
    for _, _, fs in os.walk(p):
        if fs:
            return True
    return False


def listing(p, depth=3, cap=120):
    out = []
    for root, dirs, _ in os.walk(p):
        d = root[len(p):].count(os.sep)
        if d >= depth:
            dirs[:] = []
            continue
        for x in sorted(dirs):
            out.append("  " * d + x + "/")
            if len(out) >= cap:
                return out + ["  ..."]
    return out


def size(p):
    r = subprocess.run(["du", "-sk", p], capture_output=True, text=True)
    return int(r.stdout.split()[0]) if r.stdout else 0


def moves_into(path):
    out = []
    if os.path.exists(MOVES):
        for line in open(MOVES):
            m = json.loads(line)
            if m["dst"] == path or m["dst"].startswith(path + "/"):
                out.append(m)
    return out


def manifest(path, name, old_name):
    ms = moves_into(path)
    top = sorted(os.listdir(path))
    readme = next((f for f in top if f.lower().startswith("readme")), None)
    others = [f for f in top if f.upper().startswith(("MANIFEST", "LEDGER"))]
    lines = [f"# {name}", ""]
    what = []
    if readme:
        for l in open(os.path.join(path, readme), errors="replace"):
            l = l.strip()
            if l and not l.startswith("#"):
                what.append(l)
            if len(what) >= 3:
                break
    lines.append("**What:** " + (" ".join(what)[:600] if what else f"{len(top)} entries, listed below."))
    if ms:
        lines += ["", "**Moved here by the estate** (`machines/laptop/moves.jsonl`):"]
        for m in ms[:40]:
            lines.append(f"- `{m['src'].replace(HOME, '~')}` -> `{m['dst'].replace(HOME, '~')}` ({m.get('at', '')[:10]}): {m.get('why', '')}")
    else:
        lines += ["", "**Source path:** not recorded (archived before the estate kept manifests)."
                  + (f" The folder was called `{old_name}`." if old_name != name else "")]
    if others:
        lines += ["", "Older records inside: " + ", ".join(f"`{o}`" for o in others) + "."]
    lines += ["", "**Contents:**", ""]
    for f in top[:60]:
        p = os.path.join(path, f)
        lines.append(f"- `{f}{'/' if os.path.isdir(p) else ''}` ({size(p) / 1024:.1f} MB)")
    if len(top) > 60:
        lines.append(f"- ... {len(top) - 60} more")
    lines += ["", "**Why:** kept as history; nothing here is a live source. The vault is read-only and never deleted.",
              "**Restore:** " + ("move an entry back to the source path above." if ms else
                                 "copy what you need out; do not work inside the vault."), ""]
    return "\n".join(lines)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for name in sorted(os.listdir(AR)):
        path = os.path.join(AR, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        nn = new_name(name, path)
        hollow = not has_files(path)
        if nn != name and os.path.exists(os.path.join(AR, nn)):
            nn = nn + "-2"
        act = []
        if nn != name:
            act.append(f"rename -> {nn}")
        if hollow:
            act.append("hollow: VAULT-POINTER.md")
        elif not os.path.exists(os.path.join(path, "MANIFEST.md")):
            act.append("write MANIFEST.md")
        print(f"{name:55} {'; '.join(act) or 'ok'}")
        if cmd != "run" or not act:
            continue
        if hollow:
            tree = listing(path)
            subprocess.run(["find", path, "-mindepth", "1", "-type", "d", "-empty", "-delete"])
            text = (f"# {nn}: vault pointer\n\nThis folder held only an empty directory skeleton on 2026-09-23 (no files): its "
                    f"contents were offloaded from the laptop to the SISO vault on the Mac Mini "
                    f"(`/Volumes/SISO-STORAGE-VAULT/SISO-VAULT/`), as the 2026 cleanups did.\n\n"
                    f"**Unverified:** the Mini was unreachable on 2026-09-23, so the exact vault path is not checked. Find it with\n"
                    f"`ssh -o RemoteCommand=none mac-mini 'find /Volumes/SISO-STORAGE-VAULT*/SISO-VAULT -maxdepth 5 -iname \"*{re.sub(r'^[0-9-]+', '', nn)[:24]}*\"'`.\n\n"
                    f"Original folder name: `{name}` (created {born(path)}).\n\nThe skeleton that was here (3 levels):\n\n```\n"
                    + "\n".join(tree) + "\n```\n")
            open(os.path.join(path, "VAULT-POINTER.md"), "w").write(text)
        if nn != name:
            os.rename(path, os.path.join(AR, nn))
            with open(MOVES, "a") as f:
                f.write(json.dumps({"at": stamp, "src": path, "dst": os.path.join(AR, nn), "why":
                                    "job 8: _archive folders are YYYY-MM-DD-subject", "compat_link": False}) + "\n")
            path = os.path.join(AR, nn)
        if not hollow and not os.path.exists(os.path.join(path, "MANIFEST.md")):
            open(os.path.join(path, "MANIFEST.md"), "w").write(manifest(path, nn, name))


if __name__ == "__main__":
    main()
