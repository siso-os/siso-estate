#!/usr/bin/env python3
"""Where the estate's mess is born: every repo, clone, worktree and top-level folder that an agent created, read from
the agents' own history (Claude Code projects, Codex sessions, and both archives), classified by where it landed.

  births.py [--since YYYY-MM-DD, default 60 days ago] [--machine laptop]   -> machines/<m>/births.json, and a summary on stdout

A birth is a shell command an agent ran: `git clone`, `git init`, `git worktree add`, a project generator
(npx/npm/pnpm/bun create, cargo new, uv init, ...), or `mkdir` of a folder at the top of ~ or of the workspace; plus
Claude's Write tool writing a file straight into ~ or the workspace root. Each one is judged right, wrong or ephemeral
by the estate's rules (AGENTS.md, ADR 0006). It also counts where each agent session started (its cwd), because an
agent creates things where it stands. Read-only; prints no command text beyond the target path.
"""
import argparse, collections, glob, gzip, json, os, re, shlex, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
DISTRICTS = ("SISO_Agency", "SISO_Agents", "Great_Library_of_SISO", "personal")
ASKED = re.compile(r"(^|[;&|(\s])estate (where|path) ")
SEARCHED = re.compile(r"(^|[;&|(\s])(find|fd|mdfind|ls)( -[a-zA-Z]+)* (~|/Users/[^/]+|\$HOME)/SISO_Workspace")
LOOKUPS = []                                  # (harness, time, "asked" | "searched"): does an agent ask the city or search the disk?
OURS = re.compile(r"github\.com[:/](sisodias|camronkellman|lordsisodia)/", re.I)
GEN = re.compile(r"^(npx|npm|pnpm|yarn|bun|bunx)$")


def where(p):
    """The kind of place a path is in."""
    if not p:
        return "unresolved"
    if p.startswith(("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")):
        return "tmp"
    if p == WS or p.startswith(WS + "/"):
        rel = p[len(WS) + 1:]
        top = rel.split("/")[0]
        if rel.startswith("_data/worktrees/"):
            return "_data/worktrees"
        if top in ("_reference", "_archive", "_inbox", "_data"):
            return top
        if "/" not in rel:
            return "workspace root"
        if "/.worktrees/" in "/" + rel + "/" or rel.endswith("/.worktrees"):
            return "inside a repo (.worktrees)"
        return "district" if top in DISTRICTS else "workspace other"
    if p.startswith(HOME + "/"):
        rel = p[len(HOME) + 1:]
        top = rel.split("/")[0]
        if top.startswith("."):
            return "tool/harness home"
        if top in ("Desktop", "Downloads", "Documents"):
            return "Desktop/Downloads/Documents"
        return "home root" if "/" not in rel else "elsewhere in ~"
    return "elsewhere"


def expand(tok, cwd):
    s = tok.strip("\"'")
    s = re.sub(r"^(~|\$HOME|\$\{HOME\})(?=/|$)", HOME, s)
    if not s or "$" in s or "`" in s or "*" in s:
        return None
    return os.path.normpath(s if s.startswith("/") else os.path.join(cwd or HOME, s))


def simple_commands(cmd):
    for part in re.split(r"\s*(?:&&|\|\||;|\n|\|)\s*", cmd):
        part = part.strip().lstrip("(").strip()
        if not part:
            continue
        try:
            w = shlex.split(part, comments=True)
        except ValueError:
            w = part.split()
        while w and (w[0] in ("sudo", "command", "env", "nohup", "time", "exec") or re.match(r"^\w+=", w[0])):
            w = w[1:]
        if w:
            yield w


def births_in(cmd, cwd):
    """(kind, target path, detail) for each creation in one shell command."""
    here = cwd
    for w in simple_commands(cmd):
        if w[0] == "cd" and len(w) > 1:
            here = expand(w[1], here) or here
            continue
        if w[0] == "git":
            i, gcwd = 1, here
            while i < len(w) and w[i].startswith("-"):
                if w[i] == "-C" and i + 1 < len(w):
                    gcwd = expand(w[i + 1], here) or here
                    i += 2
                elif w[i] in ("-c", "--git-dir", "--work-tree"):
                    i += 2
                else:
                    i += 1
            sub = w[i] if i < len(w) else ""
            args = [a for a in w[i + 1:] if not a.startswith("-")]
            if sub == "clone":
                pos, skip = [], False
                for a in w[i + 1:]:
                    if skip:
                        skip = False
                        continue
                    if a in ("-b", "--branch", "--depth", "-o", "--origin", "--reference", "--config", "-c", "--filter", "--separate-git-dir", "-j", "--jobs"):
                        skip = True
                        continue
                    if not a.startswith("-"):
                        pos.append(a)
                if pos:
                    url = pos[0]
                    dest = pos[1] if len(pos) > 1 else re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
                    yield "clone", expand(dest, gcwd), "ours" if OURS.search(url) else ("local" if not re.search(r"://|@", url) else "others'")
            elif sub == "init":
                yield "init", expand(args[0], gcwd) if args else gcwd, ""
            elif sub == "worktree" and args[:1] == ["add"]:
                rest, skip, pos = w[i + 2:], False, []
                for a in rest:
                    if skip:
                        skip = False
                        continue
                    if a in ("-b", "-B", "--reason"):
                        skip = True
                        continue
                    if not a.startswith("-"):
                        pos.append(a)
                if pos:
                    yield "worktree", expand(pos[0], gcwd), ""
        elif GEN.match(w[0]) and len(w) > 2 and (w[1] in ("create", "init") or re.match(r"^create-", w[1]) or (w[1] in ("x", "exec", "dlx") and re.match(r"^create-", w[2]))):
            pos = [a for a in w[2:] if not a.startswith("-") and not a.startswith("create-") and "@" not in a]
            yield "generator", expand(pos[0], here) if pos else here, " ".join(w[:2])
        elif w[0] in ("cargo", "uv", "poetry") and len(w) > 2 and w[1] in ("new", "init"):
            pos = [a for a in w[2:] if not a.startswith("-")]
            yield "generator", expand(pos[0], here) if pos else here, " ".join(w[:2])
        elif w[0] == "mkdir":
            skip = False
            for a in w[1:]:
                if skip:
                    skip = False
                    continue
                if a in ("-m",):
                    skip = True
                    continue
                if a.startswith("-"):
                    continue
                p = expand(a, here)
                if p and where(p) in ("home root", "workspace root", "elsewhere in ~", "inside a repo (.worktrees)", "elsewhere"):
                    yield "mkdir", p, ""


def verdict(kind, p, detail):
    w = where(p)
    if w == "unresolved":
        return "unknown"                      # a variable or glob: where it landed cannot be read from the command
    if w in ("tmp",):
        return "wrong" if kind == "worktree" else "ephemeral"
    if kind == "worktree":
        return "right" if w == "_data/worktrees" else "wrong"
    if kind == "clone":
        if w == "_reference":
            return "right"
        if w in ("district", "_data", "_inbox", "_archive"):
            return "right" if detail in ("ours", "local") else "wrong"
        return "wrong" if w not in ("tool/harness home", "unresolved") else "other"
    if kind in ("init", "generator"):
        if w in ("district", "_data", "_inbox"):
            return "right"
        return "wrong" if w not in ("tool/harness home", "unresolved", "_archive", "_reference") else "other"
    if kind in ("mkdir", "write"):
        return "wrong" if w in ("home root", "workspace root", "inside a repo (.worktrees)") else "other"
    return "other"


def lookup(h, ts, cmd, cwd):
    if "/SISO_Agents/siso-estate" in (cwd or ""):
        return                                # the Estate Manager surveys the disk on purpose; count everyone else
    if ASKED.search(cmd):
        LOOKUPS.append((h, ts[:19], "asked"))
    elif SEARCHED.search(cmd):
        LOOKUPS.append((h, ts[:19], "searched"))


def opener(path):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") else open(path, errors="replace")


def month(ts):
    return (ts or "")[:7] or "?"


def session_place(cwd):
    w = where(cwd)
    if w == "district":
        rel = cwd[len(WS) + 1:]
        return "district root" if rel.count("/") == 0 else "a building"
    if cwd == WS:
        return "workspace root"
    if cwd == HOME:
        return "~ (home)"
    return w


def scan_claude(paths, since, emit, sessions):
    for f in paths:
        cwd0 = None
        try:
            for line in opener(f):
                if cwd0 is None and '"cwd"' in line:
                    try:
                        d = json.loads(line)
                        cwd0 = d.get("cwd")
                        if cwd0 and (d.get("timestamp") or "") >= since:
                            sessions.append(("claude", month(d.get("timestamp")), session_place(cwd0)))
                    except ValueError:
                        pass
                if '"tool_use"' not in line or ('"Bash"' not in line and '"Write"' not in line):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = d.get("timestamp") or ""
                if ts < since:
                    continue
                cwd = d.get("cwd") or cwd0 or HOME
                for c in (d.get("message") or {}).get("content") or []:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    inp = c.get("input") or {}
                    if c.get("name") == "Bash" and isinstance(inp.get("command"), str):
                        lookup("claude", ts, inp["command"], cwd)
                        for kind, p, det in births_in(inp["command"], cwd):
                            emit("claude", ts, cwd, kind, p, det)
                    elif c.get("name") == "Write" and isinstance(inp.get("file_path"), str):
                        p = os.path.normpath(inp["file_path"])
                        if where(p) in ("home root", "workspace root") and os.path.dirname(p) in (HOME, WS):
                            emit("claude", ts, cwd, "write", p, "")
        except (OSError, EOFError):
            continue


def scan_codex(paths, since, emit, sessions):
    for f in paths:
        cwd = None
        try:
            for line in opener(f):
                if '"session_meta"' in line or '"turn_context"' in line:
                    try:
                        d = json.loads(line)
                        c = (d.get("payload") or {}).get("cwd")
                        if c:
                            if cwd is None and (d.get("timestamp") or "") >= since:
                                sessions.append(("codex", month(d.get("timestamp")), session_place(c)))
                            cwd = c
                    except ValueError:
                        pass
                    continue
                if '"function_call"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = d.get("timestamp") or ""
                if ts < since:
                    continue
                p = d.get("payload") or {}
                if p.get("type") != "function_call" or p.get("name") not in ("exec_command", "shell", "local_shell", "container.exec"):
                    continue
                try:
                    a = json.loads(p.get("arguments") or "{}")
                except ValueError:
                    continue
                cmd = a.get("cmd") or a.get("command")
                if isinstance(cmd, list):
                    cmd = cmd[-1] if len(cmd) >= 3 and cmd[1] in ("-lc", "-c") else " ".join(cmd)
                if isinstance(cmd, str):
                    lookup("codex", ts, cmd, a.get("workdir") or cwd or HOME)
                    for kind, t, det in births_in(cmd, a.get("workdir") or cwd or HOME):
                        emit("codex", ts, a.get("workdir") or cwd or HOME, kind, t, det)
        except (OSError, EOFError):
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=time.strftime("%Y-%m-%d", time.localtime(time.time() - 60 * 86400)))
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    t0 = time.time()
    arch = os.path.join(WS, "_archive", "2026-09-23-harness-history")
    claude = glob.glob(os.path.join(HOME, ".claude", "projects", "**", "*.jsonl"), recursive=True) + \
        glob.glob(os.path.join(arch, "claude", "**", "*.jsonl*"), recursive=True)
    codex = glob.glob(os.path.join(HOME, ".codex", "sessions", "**", "*.jsonl"), recursive=True) + \
        glob.glob(os.path.join(arch, "codex", "**", "*.jsonl*"), recursive=True)
    rows, sessions, seen = [], [], set()

    def emit(h, ts, cwd, kind, p, det):
        key = (kind, p, ts[:16])
        if key in seen:                      # the same command logged twice (a resumed or forked session)
            return
        seen.add(key)
        rows.append({"h": h, "at": ts[:19], "kind": kind, "where": where(p), "verdict": verdict(kind, p, det), "detail": det,
                     "target": (p or "").replace(HOME, "~"), "from": session_place(cwd)})

    scan_claude(claude, a.since, emit, sessions)
    scan_codex(codex, a.since, emit, sessions)
    by_month = collections.defaultdict(collections.Counter)
    for r in rows:
        by_month[month(r["at"])][(r["kind"], r["verdict"])] += 1
    wrong = [r for r in rows if r["verdict"] == "wrong"]
    out = {"machine": a.machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "since": a.since,
           "files": {"claude": len(claude), "codex": len(codex)}, "births": len(rows),
           "by_verdict": dict(collections.Counter(r["verdict"] for r in rows)),
           "by_kind": {k: dict(collections.Counter(r["verdict"] for r in rows if r["kind"] == k)) for k in sorted({r["kind"] for r in rows})},
           "by_month": {m: {f"{k}/{v}": n for (k, v), n in sorted(c.items())} for m, c in sorted(by_month.items())},
           "wrong_by_where": dict(collections.Counter(r["where"] for r in wrong).most_common()),
           "wrong_by_session_start": dict(collections.Counter(r["from"] for r in wrong).most_common()),
           "wrong_by_harness": dict(collections.Counter(r["h"] for r in wrong)),
           "wrong_targets_top": collections.Counter(re.sub(r"(/[^/]+){3,}$", "/…", r["target"]) for r in wrong).most_common(40),
           "sessions_by_start": {m: dict(collections.Counter(p for h, mm, p in sessions if mm == m)) for m in sorted({mm for _, mm, _ in sessions})},
           "sessions_by_harness": dict(collections.Counter(h for h, _, _ in sessions)),
           "lookups_by_month": {m: dict(collections.Counter(k for _, t, k in LOOKUPS if month(t) == m)) for m in sorted({month(t) for _, t, _ in LOOKUPS})},
           "lookups_last_7_days": dict(collections.Counter(k for _, t, k in LOOKUPS if t >= time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 7 * 86400)))),
           "rows": rows}
    with open(os.path.join(REPO, "machines", a.machine, "births.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(f"{len(claude)} Claude + {len(codex)} Codex histories since {a.since} in {time.time() - t0:.0f}s: {len(rows)} births, "
          f"{out['by_verdict']}; sessions {len(sessions)}")
    for k, v in out["by_kind"].items():
        print(f"  {k:10s} {v}")
    print("  wrong, by where it landed:", out["wrong_by_where"])
    print("  wrong, by where the agent stood:", out["wrong_by_session_start"])


if __name__ == "__main__":
    main()
