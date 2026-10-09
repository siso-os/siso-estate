#!/usr/bin/env python3
"""Retire compat links (old path -> new home) once nothing live uses the old path.

  retire-links.py check [OLD_PATH...]              who still uses each link (all current compat links if none given)
  retire-links.py retire OLD_PATH... [--force WHY]  remove links whose check is clean (or --force with a reason)

A consumer is: a running process whose arguments or PWD name the old path; a live code, config or instruction file
that names it (history, archives, lane worktrees and third-party clones are records, not consumers: lanes pick up
repoints from main on rebase); a symlink whose target runs through it. Compat links come from
machines/<machine>/moves.jsonl (src is still a symlink). A folder left holding nothing is removed too.
Every removal gets a line in _archive/2026-09-23-estate-hazards/MANIFEST.txt and machines/<machine>/retired-links.jsonl.
Undo one: ln -s <dst> <src>.
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
MANIFEST = os.path.join(WS, "_archive", "2026-09-23-estate-hazards", "MANIFEST.txt")
LIVE_DIRS = [WS, HOME + "/.claude", HOME + "/.codex", HOME + "/.agents", HOME + "/bin", HOME + "/.local/bin",
             HOME + "/.npm-global/bin", HOME + "/Library/LaunchAgents", HOME + "/.config", HOME + "/.treehouse"]
RC_FILES = [HOME + f for f in ("/.zshrc", "/.zprofile", "/.zshenv", "/.bashrc", "/.bash_profile", "/.profile")]
# records, not consumers
HISTORY = re.compile(r"/(runs|archive|_archive|_reference|evidence|reports?|records|history|snapshots?|logs?|transcripts|"
                     r"tool-results|sessions|archived_sessions|backups?|_backups|research|audits?|receipts|outputs?|artifacts|"
                     r"fixtures|briefs|journal|ledger|inbox|task-inbox|handoffs|shell_snapshots|shell-snapshots|file-history|"
                     r"projects/-[^/]+/[0-9a-f-]{36}|teams|todos|plugins|paste-cache|debug|\.omc|node_modules|\.git|[^/]*backup[^/]*|"
                     r"ambient-suggestions|token-optimizer|ops|[^/]+\.[A-Za-z0-9]{6})/|/siso-estate/(plan/moves|machines)/"
                     r"|\.(old|out|snapshot|log|bak|jsonl|done)$|\.bak|LOG$|\.pre-|config\.toml\.", re.I)
LANES = re.compile(r"/(\.worktrees|worktrees)/|/\.claude/worktrees/|/\.codex/worktrees/|/\.treehouse/")
CODE_OR_INSTR = re.compile(r"\.(py|sh|zsh|bash|mjs|cjs|js|ts|tsx|json|jsonc|ya?ml|toml|plist|php|env|service|conf)$"
                           r"|(^|/)(AGENTS|CLAUDE|README|SKILL|HANDOFF|MEMORY)\.md$|/\.agents/memory/[^/]+\.md$"
                           r"|/(bin|hooks|scripts)/[^/.]+$|/gitdir$|/\.git$|(^|/)\.[a-z]+rc$")


def compat_links(machine):
    out = {}
    for line in open(os.path.join(REPO, "machines", machine, "moves.jsonl")):
        m = json.loads(line)
        if m.get("compat_link") and os.path.islink(m["src"]):
            out[m["src"]] = m["dst"]
    return out


def forms(src):
    """Regex for every way a file can name src."""
    rel = src[len(HOME) + 1:] if src.startswith(HOME + "/") else None
    alts = [re.escape(src)]
    if rel:
        alts.append(r"(?:~|\$HOME|\$\{HOME\})/" + re.escape(rel))
        if rel.startswith("SISO_Workspace/"):  # joined form; a bare home-level name is too common a word
            alts.append(r"(?<=[\"'`\s(])" + re.escape(rel))
    return "(?:" + "|".join(alts) + r")(?=[/\"'`<>\s:),;]|$)"


def rg_files(pattern):
    cmd = ["rg", "-l", "--no-ignore", "--hidden", "--no-messages", "--max-filesize", "3M", "-P", "-e", pattern,
           "-g", "!**/node_modules/**", "-g", "!**/.git/objects/**", "-g", "!**/*.jsonl", "-g", "!**/.next/**",
           "-g", "!**/dist/**", "-g", "!SISO_Workspace/_archive/**", "-g", "!SISO_Workspace/_reference/**",
           "-g", "!.claude/projects/**", "-g", "!.codex/sessions/**", "-g", "!**/*.sqlite*"]
    dirs = [os.path.relpath(d, HOME) for d in LIVE_DIRS if os.path.exists(d)]
    dirs += [os.path.relpath(f, HOME) for f in RC_FILES if os.path.exists(f)]
    r = subprocess.run(cmd + dirs, cwd=HOME, capture_output=True, text=True)
    return [os.path.join(HOME, p) for p in r.stdout.splitlines()]


def live(path):
    return not HISTORY.search(path) and not LANES.search(path) and bool(CODE_OR_INSTR.search(path))


def process_users(srcs):
    r = subprocess.run(["ps", "-AEww", "-o", "pid=,command="], capture_output=True, text=True)  # -E adds the environment (PWD)
    hits = {s: [] for s in srcs}
    rx = {s: re.compile(re.escape(s) + r"(?=[/\s:]|$)") for s in srcs}
    for line in r.stdout.splitlines():
        if "retire-links.py" in line:
            continue
        # where a shell was before is not a use; nor is a PATH entry (commands are found by name, and the configs that
        # set PATH are repointed; the entry refreshes on the next restart)
        line = re.sub(r"\s(OLDPWD|PATH)=\S*", "", line)
        for s in srcs:
            if rx[s].search(line):
                hits[s].append(line.strip()[:160])
    return hits


def started_before_move(srcs, machine):
    """Processes working inside a moved folder that started before the move. Their cwd already resolves to the new path,
    but they resolved the old one at start (__dirname, config paths), so removing the link breaks them. 24 Sep: the camofox
    server, started from tools/camofox-browser 7 h before its move, failed every new tab once that link went."""
    moved = {}
    for line in open(os.path.join(REPO, "machines", machine, "moves.jsonl")):
        m = json.loads(line)
        if m["src"] in srcs:
            try:
                moved[m["src"]] = (m["dst"], time.mktime(time.strptime(m["at"][:19], "%Y-%m-%dT%H:%M:%S")))
            except (ValueError, KeyError):
                pass
    hits = {s: [] for s in srcs}
    if not moved:
        return hits
    r = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fpn"], capture_output=True, text=True, timeout=120)
    cwd, pid = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("n") and pid:
            cwd[pid] = line[1:]
    for s, (dst, at) in moved.items():
        pids = [p for p, c in cwd.items() if c == dst or c.startswith(dst + "/")]
        if not pids:
            continue
        ps = subprocess.run(["ps", "-o", "pid=,lstart=,command=", "-p", ",".join(pids)], capture_output=True, text=True)
        for line in ps.stdout.splitlines():
            parts = line.split()
            try:
                started = time.mktime(time.strptime(" ".join(parts[1:6]), "%a %d %b %H:%M:%S %Y"))
            except (ValueError, IndexError):
                continue
            if started < at:
                hits[s].append(f"started before the move: {' '.join(parts[:1] + parts[6:])[:140]}")
    return hits


def symlink_users(srcs):
    hits = {s: [] for s in srcs}
    for top in LIVE_DIRS:
        for root, dirs, files in os.walk(top):
            if HISTORY.search(root + "/") or LANES.search(root + "/"):
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".next", "dist")]
            for n in dirs + files:
                p = os.path.join(root, n)
                if not os.path.islink(p) or p in srcs:
                    continue
                try:
                    t = os.readlink(p)
                except OSError:
                    continue
                t = t if os.path.isabs(t) else os.path.normpath(os.path.join(root, t))
                for s in srcs:
                    if t == s or t.startswith(s + "/"):
                        hits[s].append(p)
    return hits


def git_meta(machine):
    """Worktree pointers (.git files, .git/worktrees/*/gitdir) of every inventoried repo."""
    import glob
    inv = json.load(open(os.path.join(REPO, "machines", machine, "repos.json")))
    out = []
    for r in inv["repos"]:
        g = os.path.join(r["path"], ".git")
        out += glob.glob(g + "/worktrees/*/gitdir") if os.path.isdir(g) else [g] if os.path.isfile(g) else []
    return out


def check(srcs, machine=os.environ.get("ESTATE_MACHINE", "laptop")):
    rx = {s: re.compile(forms(s)) for s in srcs}
    candidates = [f for f in rg_files("|".join(forms(s) for s in srcs)) if live(f)] + git_meta(machine)
    files = {s: [] for s in srcs}
    for f in candidates:
        try:
            text = open(f, errors="replace").read()
        except OSError:
            continue
        for s in srcs:
            if rx[s].search(text):
                files[s].append(f)
    procs = process_users(srcs)
    for s, h in started_before_move(srcs, machine).items():
        procs[s] += h
    links = symlink_users(srcs)
    return {s: {"procs": procs[s], "files": files[s], "links": links[s]} for s in srcs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "retire"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--force", metavar="WHY")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    known = compat_links(a.machine)
    srcs = [os.path.abspath(os.path.expanduser(p)) for p in a.paths] or sorted(known)
    for s in srcs:
        if s not in known:
            sys.exit(f"not a current compat link: {s}")
    res = check(srcs, a.machine)
    if a.json:
        print(json.dumps(res, indent=1))
    for s in srcs:
        r = res[s]
        n = len(r["procs"]) + len(r["files"]) + len(r["links"])
        short = s.replace(HOME, "~")
        print(f"{'CLEAN' if not n else 'USED '} {short}  procs={len(r['procs'])} files={len(r['files'])} links={len(r['links'])}")
        if not a.json:
            for k in ("procs", "files", "links"):
                for x in r[k][:6]:
                    print(f"      {k[:-1]}: {x.replace(HOME, '~')}")
    if a.cmd == "check":
        return
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(MANIFEST, "a") as man, open(os.path.join(REPO, "machines", a.machine, "retired-links.jsonl"), "a") as log:
        for s in srcs:
            r = res[s]
            used = r["procs"] or r["files"] or r["links"]
            if used and not a.force:
                print(f"KEEP  {s.replace(HOME, '~')} (still used; --force WHY to override)")
                continue
            dst = known[s]
            os.unlink(s)
            parent = os.path.dirname(s)
            removed_parent = False
            if parent != WS and parent.startswith(WS + "/") and not os.listdir(parent) and parent.count("/") == WS.count("/") + 1:
                os.rmdir(parent)
                removed_parent = True
            why = f"forced: {a.force}" if used else "no process, live file or symlink uses it"
            man.write(f"{stamp} RETIRED compat link {s.replace(HOME, '~')} -> {dst.replace(HOME, '~')} ({why})."
                      f" Undo: ln -s {dst} {s}\n")
            if removed_parent:
                man.write(f"{stamp} removed emptied link folder {parent.replace(HOME, '~')}. Undo: mkdir {parent}\n")
            log.write(json.dumps({"at": stamp, "src": s, "dst": dst, "why": why,
                                  "remaining": {k: len(v) for k, v in r.items()}}) + "\n")
            print(f"GONE  {s.replace(HOME, '~')}")


if __name__ == "__main__":
    main()
