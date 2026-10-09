#!/usr/bin/env python3
"""Rewrite old paths to new ones in consumer files, after moves (machines/<m>/moves.jsonl).

  repoint.py FILE... [--dry-run] [--relative] [--src OLD_PATH ...]

Every changed file is first copied to ~/SISO_Workspace/_archive/2026-09-23-estate-repoint/ (flattened
name + timestamp), and a line goes to machines/<m>/repoints.jsonl. Only whole path prefixes are replaced
(the old path must be followed by / " ' < > whitespace or the end), longest first. Symlinks passed as FILE
are re-targeted instead of edited. --relative also rewrites workspace-relative forms (`SISO_Agency/apps/x`, as the
estate's own records and AGENTS.md tables write them), only where a quote, space, backtick or ( opens the path; pass it
only for files that write paths relative to ~/SISO_Workspace.
"""
import argparse, json, os, re, shutil, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
BACKUP = os.path.join(HOME, "SISO_Workspace", "_archive", "2026-09-23-estate-repoint")


def chained(machine):
    """Moves in order, each dst followed through later moves (A->B then B/x->C gives A/x->C too)."""
    ms = [(m["src"], m["dst"]) for m in map(json.loads, open(os.path.join(REPO, "machines", machine, "moves.jsonl")))
          if "src" in m and "dst" in m and not m.get("why", "").startswith("empty")]  # github-rename/map-placement use from/to
    out = []
    for i, (s, d) in enumerate(ms):
        for s2, d2 in ms[i + 1:]:
            if d == s2 or d.startswith(s2 + "/"):
                d = d2 + d[len(s2):]
            elif s2.startswith(d + "/"):
                out.append((s + s2[len(d):], d2))
        out.append((s, d))
    # a path moved twice from the same place (clients/x -> clients/x/code/x, later clients/x -> clients/halo/x) means
    # what the latest move says: keep only the last pair per old path, or the first would stack both (x/code/x/code/x)
    last = {}
    for s, d in out:
        last[s] = d
    return list(last.items())


def pairs(machine, relative=False, only=None):
    out = []
    for src, dst in chained(machine):
        if only and not any(src == o or src.startswith(o + "/") for o in only):
            continue
        out.append((src, dst))
        if src.startswith(HOME) and dst.startswith(HOME):
            s, d = src[len(HOME):], dst[len(HOME):]
            for pre in ("~", "$HOME", "${HOME}"):
                out.append((pre + s, pre + d))
            # joined forms: join(HOME, 'SISO_Workspace/x/...'), after a quote, space or ( only. Never for a folder
            # directly under home or outside the workspace: a bare "SISO_Agency/..." or "devspace" is a different thing.
            if s.startswith("/SISO_Workspace/") and d.startswith("/SISO_Workspace/"):
                out.append(("\x00" + s.lstrip("/"), d.lstrip("/")))
                if relative and "/" in s[len("/SISO_Workspace/"):]:  # a real path, never a bare word like "workers"
                    out.append(("\x00" + s[len("/SISO_Workspace/"):], d[len("/SISO_Workspace/"):]))
    return sorted(out, key=lambda p: -len(p[0].lstrip("\x00")))


def rewrite(text, prs):
    n = 0
    for old, new in prs:
        if old.lstrip("\x00") not in text:  # a match needs the old path as a plain substring: skip the regex (fast)
            continue
        if old.startswith("\x00"):  # a bare relative form: only where a quote, space or ( opens it
            g = ("(?!" + re.escape(new[len(old) - 1:]) + ")") if new.startswith(old[1:] + "/") else ""
            rx = re.compile(r"(?<=[\"'`\s(])" + re.escape(old[1:]) + g + r"(?=[/\"'`<>\s:),]|$)", re.M)
            text, k = rx.subn(new, text)
            n += k
            continue
        guard = ""
        if new.startswith(old + "/"):  # destination inside the source: never apply twice
            guard = "(?!" + re.escape(new[len(old):]) + r"(?:[/\"'`<>\s:),]|$))"
        rx = re.compile(re.escape(old) + guard + r"(?=[/\"'`<>\s:),]|$)", re.M)
        text, k = rx.subn(new, text)
        n += k
    return text, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--relative", action="store_true", help="also workspace-relative forms (SISO_Agency/...)")
    ap.add_argument("--src", action="append", help="apply only moves from this path (repeatable); default: every move")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    prs = pairs(a.machine, a.relative, [os.path.abspath(os.path.expanduser(x)).rstrip("/") for x in a.src or []])
    os.makedirs(BACKUP, exist_ok=True)
    log = open(os.path.join(REPO, "machines", a.machine, "repoints.jsonl"), "a")
    stamp = time.strftime("%Y%m%dT%H%M%S")
    for f in a.files:
        f = os.path.expanduser(f)
        if os.path.islink(f):
            t = os.readlink(f)
            nt, n = rewrite(t, prs)
            if n:
                print(f"link {f}: {t} -> {nt}")
                if not a.dry_run:
                    os.unlink(f)
                    os.symlink(nt, f)
                    log.write(json.dumps({"at": stamp, "link": f, "old": t, "new": nt}) + "\n")
            continue
        try:
            text = open(f, encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError) as e:
            print(f"skip {f}: {e.__class__.__name__}")
            continue
        new, n = rewrite(text, prs)
        if not n:
            continue
        print(f"file {f}: {n} replacements")
        if a.dry_run:
            continue
        b = os.path.join(BACKUP, f.replace("/", "__").strip("_") + "." + stamp)
        shutil.copy2(f, b)
        st = os.stat(f)
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(new)
        os.chmod(f, st.st_mode)
        log.write(json.dumps({"at": stamp, "file": f, "replacements": n, "backup": b}) + "\n")


if __name__ == "__main__":
    main()
