#!/usr/bin/env python3
"""Every project in the city, one row each, so Shaan can say which ones are still needed on this laptop.

  projects.py [--machine laptop]   -> machines/<m>/projects.json, and a table on stdout

A project is a folder a person would name: each app, client and factory repo in SISO_Agency, hq, each folder in
SISO_Agents, the Great Library and its collections, the repos in personal, plus our GitHub repos with no
place on the map yet (from census.json). Per project: what it is (the AGENTS.md "In one line", else manifest.md, else
README), when it last changed (newest commit or loose file), size on this laptop, and what is on this laptop only
(commits no remote has, files no repo tracks). "Can leave the laptop now" = nothing is on this laptop only.
Nothing is changed. Needs `estate inventory` and `estate census` first.
"""
import json, os, re, stat, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import legend  # partners and their clients (plan/legend.json)

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = re.compile(r"/(node_modules|\.t|\.lake|vendor)/")
# the estate's own housekeeping commits (22-24 Sep: one-line headers, repoints, memory, snapshots) are not activity
HOUSEKEEPING = re.compile(r"^(AGENTS\.md|CLAUDE\.md|estate[: ]|memory: commit this project's agent memory|Automatic backup|"
                          r"Repoint paths|gitignore|city map|umbrella|hq[:/]|docs: the Library building|find\.mjs: search index|"
                          r"remove the embedded second clone|gitleaks: allowlist|\.gitleaks)")


def last_real_commit(repo, sub=None):
    """Newest commit (unix time) that is not estate housekeeping."""
    code, out = sh("git", "-C", repo, "log", "-n", "60", "--format=%ct%x09%s", *(["--", sub] if sub else []))
    for line in out.splitlines():
        ts, _, subj = line.partition("\t")
        if ts.isdigit() and not HOUSEKEEPING.match(subj):
            return int(ts)
    return 0


def sh(*a, timeout=600):
    r = subprocess.run(a, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode(errors="replace")


def one_line(p):
    """What the project is, in one line: AGENTS.md's "In one line", else the first real sentence of README.md,
    manifest.md or AGENTS.md (front matter, headings, lists, boot instructions skipped)."""
    texts = {}
    for name in ("AGENTS.md", "README.md", "readme.md", "manifest.md"):
        f = os.path.join(p, name)
        if os.path.isfile(f):
            texts[name] = re.sub(r"\A---\n.*?\n---\n", "", open(f, errors="replace").read(6000), flags=re.S)
    m = re.search(r"\*\*In one line:\*\*\s*(.+)", texts.get("AGENTS.md", ""))
    if m:
        return clean(m.group(1))
    for name in ("README.md", "readme.md", "manifest.md", "AGENTS.md"):
        for l in texts.get(name, "").splitlines():
            l = l.strip()
            if len(l) > 25 and not l.startswith(("#", "<", "!", "|", "---", "```", "[!", ">", "-", "*", "Read ", "Before ")) \
                    and not re.match(r"^[a-z_]+:\s", l):
                return clean(l)
    return ""


def clean(line):
    return re.sub(r"[*_`]|\[([^\]]+)\]\([^)]+\)", r"\1", line).strip()[:160]


TOUCHED = set()  # files the estate itself rewrote (repoints, CLAUDE.md -> @AGENTS.md), and relinked symlinks: not activity


def loose(repo, sub=None):
    """Files in `repo` (under `sub`, if given) that no repo tracks: count, bytes, newest mtime (estate edits aside)."""
    code, out = sh("git", "-C", repo, "ls-files", "-o", "--exclude-standard", "-z", *(["--", sub] if sub else []))
    n = b = newest = 0
    if code:
        return None
    for f in filter(None, out.split("\0")):
        if f.endswith("/") or SKIP.search("/" + f):
            continue
        try:
            st = os.lstat(os.path.join(repo, f))
        except OSError:
            continue
        n, b = n + 1, b + st.st_size
        full = os.path.join(repo, f)
        if os.path.basename(f) not in ("CLAUDE.md", "AGENTS.md") and full not in TOUCHED and not stat.S_ISLNK(st.st_mode):
            newest = max(newest, st.st_mtime)
    return n, b, newest


def du(p):
    code, out = sh("du", "-sk", p, timeout=1800)
    return int(out.split()[0]) * 1024 if out.strip() else 0


def subdirs(rel):
    p = os.path.join(WS, rel)
    return sorted((n for n in os.listdir(p) if not n.startswith(".") and os.path.isdir(os.path.join(p, n))), key=str.lower) if os.path.isdir(p) else []


def projects():
    out = []
    def add(district, rel, kind):
        p = os.path.join(WS, rel)
        if os.path.isdir(p) and not os.path.islink(p):
            po = legend.partner_of(rel)   # the legend decides partner rows wherever they sit today (docs/LEGEND.md §3)
            if po and kind in ("client", "partner", "partner client"):
                kind = "partner client" if po[1] else "partner"
            out.append({"district": district, "path": rel, "group": kind, "partner": po[0] if po else None,
                        "client": po[1] if po else None})
    for sub, kind in (("apps", "app"), ("clients", "client"), ("factory", "factory")):
        for n in subdirs(f"SISO_Agency/{sub}"):
            add("SISO_Agency", f"SISO_Agency/{sub}/{n}", kind)
    for agency in subdirs(legend.PARTNERS_DIR):     # SISO_Agency/partners/<agency>: its systems, then its clients
        for n in subdirs(f"{legend.PARTNERS_DIR}/{agency}"):
            if n != "clients":
                add("SISO_Agency", f"{legend.PARTNERS_DIR}/{agency}/{n}", "partner")
        for n in subdirs(f"{legend.PARTNERS_DIR}/{agency}/clients"):
            add("SISO_Agency", f"{legend.PARTNERS_DIR}/{agency}/clients/{n}", "partner client")
    add("SISO_Agency", "SISO_Agency/hq", "agency hq")
    for n in sorted(os.listdir(os.path.join(WS, "SISO_Agents")), key=str.lower):
        if not n.startswith(".") and n != "scripts":
            add("SISO_Agents", f"SISO_Agents/{n}", "agent system")
    for rel in ("foundry", "knowledge", "people-graph", "banks/siso-component-bank", "banks/siso-repo-bank", "banks/siso-shell", "banks/siso-ui-base"):
        add("Great_Library_of_SISO", f"Great_Library_of_SISO/{rel}", "library")
    for rel in ("team-entrepreneurship", "apps/freeflow", "tools/kaneo"):
        add("personal", f"personal/{rel}", "personal")
    return out


def main():
    machine = sys.argv[sys.argv.index("--machine") + 1] if "--machine" in sys.argv else os.environ.get("ESTATE_MACHINE", "laptop")
    inv = [r for r in json.load(open(os.path.join(REPO, "machines", machine, "repos.json")))["repos"]
           if r["kind"] == "repo" and not SKIP.search(r["path"] + "/")]
    census = json.load(open(os.path.join(REPO, "machines", machine, "census.json")))
    rp = os.path.join(REPO, "machines", machine, "repoints.jsonl")
    if os.path.exists(rp):
        for l in open(rp):
            f = json.loads(l).get("file")
            if f:
                TOUCHED.add(os.path.expanduser(f))
    rows = projects()
    # the parent repo that holds each project's non-repo files (SISO_Agency, the Library, the umbrella, ...)
    repo_paths = sorted((r["path"] for r in inv), key=len, reverse=True)

    def parent_repo(p):
        return next((r for r in repo_paths if p.startswith(r + "/")), None)

    def measure(row):
        p = os.path.join(WS, row["path"])
        inside = [r for r in inv if r["path"] == p or r["path"].startswith(p + "/")]
        commits = sum(r.get("unpushed_commits") or 0 for r in inside)
        dirty = sum(r.get("dirty") or 0 for r in inside)
        no_remote = [os.path.relpath(r["path"], WS) for r in inside if not r.get("remotes")]
        n = b = newest = 0
        for r in inside:
            lo = loose(r["path"])
            if lo:
                n, b, newest = n + lo[0], b + lo[1], max(newest, lo[2])
        last = max([last_real_commit(r["path"]) for r in inside] or [0])
        par = None if any(r["path"] == p for r in inside) else parent_repo(p)
        if par:  # a plain folder: its files belong to the repo around it
            lo = loose(par, os.path.relpath(p, par))
            if lo:
                n, b, newest = n + lo[0], b + lo[1], max(newest, lo[2])
            last = max(last, last_real_commit(par, os.path.relpath(p, par)))
        last = max(last, newest)
        remotes = sorted({g["owner"] + "/" + g["repo"] for r in inside for g in (r.get("github") or []) if g.get("remote") == "origin"})
        st = re.search(r"^status:\s*(.+)$", open(os.path.join(p, "manifest.md"), errors="replace").read(3000), re.M) \
            if os.path.isfile(os.path.join(p, "manifest.md")) else None
        row.update({
            "what": one_line(p), "status": st.group(1).strip() if st else None, "repos": len(inside), "remotes": remotes[:6],
            "is_repo": any(r["path"] == p for r in inside), "held_by": os.path.relpath(par, WS) if par else None,
            "last_active": time.strftime("%Y-%m-%d", time.localtime(last)) if last else None,
            "days_idle": int((time.time() - last) / 86400) if last else None,
            "size_bytes": du(p),
            "laptop_only": {"commits": commits, "changed_files": dirty, "loose_files": n, "loose_bytes": b, "repos_without_remote": no_remote},
        })
        row["can_leave_now"] = not (commits or dirty or n or no_remote)
        return row

    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(measure, rows))
    for name in census["github"]["not_on_map"].get("GitHub only, no place on the map", []):
        rows.append({"district": "GitHub only", "path": f"github.com/sisodias/{name}", "group": "not on the laptop", "what": "",
                     "repos": 1, "is_repo": True, "size_bytes": 0, "can_leave_now": True, "laptop_only": {}, "last_active": None})
    gh = {g["name"]: g for g in json.load(open(os.path.join(REPO, "machines", "github", "sisodias.json")))}
    for r in rows:
        if r["district"] == "GitHub only":
            g = gh.get(r["path"].rsplit("/", 1)[1], {})
            r["last_active"] = (g.get("pushedAt") or "")[:10] or None
            r["github_mb"] = (g.get("diskUsage") or 0) // 1024
    for i, r in enumerate(rows, 1):
        r["n"] = i
    doc = {"machine": machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "projects": rows}
    dst = os.path.join(REPO, "machines", machine, "projects.json")
    json.dump(doc, open(dst, "w"), indent=1)
    open(dst, "a").write("\n")
    for r in rows:
        lo = r["laptop_only"]
        only = (f"{lo.get('commits', 0)}c {lo.get('changed_files', 0)}ch {lo.get('loose_files', 0)}f {lo.get('loose_bytes', 0) / 1024 ** 3:.2f}GB"
                if not r["can_leave_now"] else "all on GitHub")
        print(f"{r['n']:3} {r['path'][:52]:52} {r['size_bytes'] / 1024 ** 3:6.2f}GB {r.get('last_active') or '':10} {only:32} {r['what'][:60]}")


if __name__ == "__main__":
    main()
