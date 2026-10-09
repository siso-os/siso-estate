#!/usr/bin/env python3
"""Back up every repo's disk-only work to private GitHub, without touching anyone's checkout.

  backup.py plan  [--machine laptop]           -> machines/<m>/backup-plan.json (no network writes)
  backup.py run   [--machine laptop] [--only SUBSTR] [--jobs 6] [--create-repos]
                                                -> machines/<m>/backup.json

What "disk-only work" means for one checkout (repo, worktree or submodule):
  - commits on local branches that no remote-tracking ref has, and
  - the working tree (tracked changes + untracked, non-ignored files), captured as one WIP commit
    built in a temporary index. HEAD, the real index and the files are never touched.

Where it goes (first match wins):
  own-private  a private remote on an owned account (sisodias, HALO-AGENCY) (Lordsisodia/X is resolved to its sisodias mirror):
               push to refs/backup/<machine>/<slug>/{heads/<branch>,wip}. Custom refs: no CI, no deploys.
  new-repo     no usable remote: create private sisodias/<name>, add it as `origin` (or `estate` if an
               origin exists), push all branches, tags and the wip ref.
  overlay      public own repo or third-party clone: a git bundle of just the local delta, written to
               ~/SISO_Workspace/.estate/overlays/<machine>/<slug>.bundle for the machine umbrella.
  skip         ephemeral paths (test temp dirs, harness caches) and checkouts with nothing local.
Secrets: gitleaks scans what would leave the machine. Flagged WIP files are left out of the snapshot;
flagged commits hold that checkout (status=held) for a human look, and its disk-only work goes off the machine
age-encrypted instead (held_copy: private sisodias/siso-held-backups, one ref per checkout).
"""
import argparse, glob, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
OWNER = "sisodias"
# accounts whose repos are ours to push backup refs into: HALO's repos moved to the HALO-AGENCY org on 27 Sep, and
# until this read them as third-party their disk-only work left as overlay bundles (A0 tell 3 Oct: "backup refs only")
OWNED = (OWNER, "HALO-AGENCY")
MAX_FILE = 10 * 1024 * 1024          # uncommitted files over 10 MB are data, not work in progress (listed, not pushed)
MAX_WIP_FILES = 20000
MAX_WIP_BYTES = 200 * 1024 * 1024
MAX_BUNDLE = 95 * 1024 * 1024
SCAN_COMMITS = True   # scans only commits not pushed before (see pushed-tips.json), so repeat runs stay fast
TIPS_FILE = os.path.join(REPO, "machines", "{machine}", "pushed-tips.json")

# Ephemeral by reading: test temp dirs (oracle-streaming acceptance tests mkdtemp into .t/),
# harness caches and harness-made backups of third-party plugins.
SKIP = [
    (re.compile(r"/\.t/"), "test temp dir (acceptance tests mkdtemp here)"),
    (re.compile(r"^~/\.codex/(\.tmp|backups|vendor_imports)/"), "Codex cache/backup of third-party code"),
    (re.compile(r"^~/\.claude/plugins/marketplaces/"), "Claude plugin marketplace cache"),
    (re.compile(r"/\.scratch-binding-"), "agent scratch binding"),
    (re.compile(r"/\.hermit/"), "hermit package cache (cargo git checkouts of third-party crates; held on 3 Oct as sisodias/9f192c9)"),
    (re.compile(r"/_data/(backup-repos|estate-stage)/"), "local working copy of an encrypted data plane (its GitHub repo is the backup)"),
    # The vault: a checkout retired to _archive/ is held by its vault bundle (tools/vault.py) and its MANIFEST. Backing
    # it up re-created deleted repos (siso-worker-node) and made ~100 repos of archived test temps on 26 Sep.
    (re.compile(r"^~/SISO_Workspace/_archive/"), "retired to the vault (_archive/): held by its bundle, never a GitHub repo"),
]


GH_URL = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$")


def sh(cmd, cwd=None, env=None, timeout=900, input=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, input=input)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def git(path, *args, env=None, timeout=900):
    code, out, err = sh(["git", "-C", path, *args], env=env, timeout=timeout)
    return code, out, re.sub(r"://[^/@\s]+@", "://<redacted>@", err)  # never log credentials from a remote URL


def tilde(p):
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def slug_of(path):
    rel = os.path.relpath(path, HOME)
    s = rel.replace("/", "__")
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_") or "home"
    if len(s) > 100:  # GitHub rejects very long ref names; keep the readable end plus a hash
        import hashlib
        s = hashlib.sha1(rel.encode()).hexdigest()[:10] + "__" + s[-80:].lstrip("_")
    return s


def github_list(max_age=3600, owner=OWNER):
    """Our GitHub repos (machines/github/<owner>.json), refreshed from GitHub when older than an hour. A stale list made
    every repo created after it look deleted (24 Sep: sisodias/kellman and sisodias/halo-agency were read as deleted and
    would have been bundled instead of pushed), so a failed refresh keeps the old list and says so."""
    f = os.path.join(REPO, "machines", "github", f"{owner}.json")
    if not os.path.exists(f) or time.time() - os.path.getmtime(f) > max_age:
        code, out, err = sh(["gh", "repo", "list", owner, "--limit", "5000",
                             "--json", "name,nameWithOwner,visibility,isArchived,isFork,diskUsage,pushedAt,description"], timeout=180)
        try:
            rows = json.loads(out) if code == 0 else None
        except ValueError:
            rows = None
        if rows:
            rows.sort(key=lambda r: r.get("pushedAt") or "", reverse=True)
            with open(f + ".tmp", "w") as fh:
                json.dump(rows, fh, separators=(",", ":"))
            os.replace(f + ".tmp", f)
        elif not os.path.exists(f):
            raise SystemExit(f"github list for {owner} unavailable ({(err or out)[-200:]})")
        else:
            print(f"github list not refreshed ({(err or out)[-200:]}); using the copy from "
                  f"{time.strftime('%F %H:%M', time.localtime(os.path.getmtime(f)))}", file=sys.stderr)
    return json.load(open(f))


def load(machine):
    inv = json.load(open(os.path.join(REPO, "machines", machine, "repos.json")))
    gh = {r["name"].lower(): r for r in github_list()}
    for o in OWNED[1:]:     # other owned accounts sit under "owner/name", so a same-named repo never collides
        gh.update({r["nameWithOwner"].lower(): r for r in github_list(owner=o)})
    return inv, gh


def owned(owner):
    return (owner or "").lower() in {o.lower() for o in OWNED}


def resolve_github(owner, repo, gh):
    """Return ("owner/name", visibility) of the owned repo behind a GitHub owner/repo, following the Lordsisodia migration."""
    r = repo.lower()
    if owner == OWNER and r in gh:
        return f"{OWNER}/{gh[r]['name']}", gh[r]["visibility"]
    if owned(owner) and owner != OWNER and f"{owner}/{r}".lower() in gh:
        x = gh[f"{owner}/{r}".lower()]
        return x["nameWithOwner"], x["visibility"]
    if owner == "Lordsisodia":
        for cand in (f"legacy-lordsisodia-{r}", r):
            if cand in gh:
                return f"{OWNER}/{gh[cand]['name']}", gh[cand]["visibility"]
    return None, None


def live_remote_names(rec, gh, bypath):
    """Remotes whose far end still exists: their tracking refs prove a commit is safe elsewhere."""
    live = []
    for name, url in (rec.get("remotes") or {}).items():
        m = GH_URL.search(url)
        if m:
            owner, repo = m.group(1), m.group(2)
            if owned(owner) and resolve_github(owner, repo, gh)[0] is None:
                continue          # our repo deleted
            if owner == "Lordsisodia" and resolve_github(owner, repo, gh)[0] is None:
                continue          # account gone and no sisodias mirror: its tracking refs prove nothing
            live.append(name)
        elif url.startswith(("/", "~", "file:")):
            if os.path.exists(os.path.expanduser(url.replace("file://", ""))):
                live.append(name)
        elif ":" in url:          # ssh host alias (e.g. another machine): not a backup
            continue
    return live


# Client code that must never be copied to a SISO GitHub account, not even as a bundle in the private map (Shaan,
# 2026-09-23: "HALO code lives only on camronkellman/halocrm"). Its work is pushed by its own lane to the client's repo.
NEVER_COPY = {"camronkellman/halocrm": "HALO code lives only on camronkellman/halocrm (Shaan): no sisodias copy, no bundle",
              # the CRM moved here on 27 Sep (its GitHub description); its lanes push their own work, the estate never does
              "halo-agency/halocrm": "HALO's CRM (HALO-AGENCY/halocrm, moved from camronkellman/halocrm 27 Sep): no copy, no bundle (ADR 0007)"}
NEVER_COPY_PATHS = re.compile(r"/(HALO_Agency/crm|partners/halo/crm|clients/halocrm|clients/halo/crm|worktrees/halocrm|projects/halocrm|\.treehouse/halocrm)(/|$)")


def vaulted():
    """Repos folded into sisodias/siso-vault (machines/*/vault.jsonl)."""
    out = set()
    for f in glob.glob(os.path.join(REPO, "machines", "*", "vault.jsonl")):
        out |= {json.loads(l)["repo"].lower() for l in open(f) if l.strip()}
    return out


VAULTED = vaulted()


def classify(rec, bypath, gh, depth=0):
    """Decide target for one checkout. Returns dict(action, target, visibility, why, ...)."""
    p = tilde(rec["path"])
    if rec["path"] == WS:
        return {"action": "skip", "why": "workspace root: handled by the umbrella step (its origin is wrongly sisodias/oracle)"}
    for rx, why in SKIP:
        if rx.search(p):
            return {"action": "skip", "why": why}
    if NEVER_COPY_PATHS.search(rec["path"]):
        return {"action": "skip", "why": NEVER_COPY["camronkellman/halocrm"] + " (HALO path)"}
    for g in rec.get("github") or []:
        why = NEVER_COPY.get(f"{g.get('owner')}/{g.get('repo')}".lower())
        if why:
            return {"action": "skip", "why": why}
    if rec.get("broken"):
        return {"action": "skip", "why": rec["broken"]}
    local = (rec.get("unpushed_commits") or 0) + (rec.get("dirty") or 0) + (rec.get("untracked") or 0)
    held = [g for g in rec.get("github") or [] if f"{g.get('owner')}/{g.get('repo')}".lower() in VAULTED]
    if held:  # the vault holds this repo as a bundle; pushing here would keep (or re-create) the repo it replaced
        return {"action": "skip", "why": "held by the vault (tools/vault.py)" + (
            f"; but {local} local changes are not in it: re-house them" if local else "")}
    remotes = rec.get("remotes") or {}
    ghs = rec.get("github") or []
    live = live_remote_names(rec, gh, bypath)
    base = {"live_remotes": live, "fix_remotes": []}
    order = sorted(ghs, key=lambda g: (g["remote"] != "origin", g["remote"] != "mirror"))
    own_public = missing_own = dead_lord = None
    for g in order:
        name, vis = resolve_github(g["owner"], g["repo"], gh)
        if name and g["owner"] == "Lordsisodia":
            base["fix_remotes"].append({"remote": g["remote"], "url": f"https://github.com/{name}.git"})
    for g in order:
        name, vis = resolve_github(g["owner"], g["repo"], gh)
        if name and vis == "PRIVATE":
            return dict(base, action="own-private" if local else "clean", target=name, visibility="private")
        if name and vis == "PUBLIC" and not own_public:
            own_public = name
        if not name and owned(g["owner"]) and not missing_own:
            missing_own = f"{g['owner']}/{g['repo']}"
        if not name and g["owner"] == "Lordsisodia" and not dead_lord:
            dead_lord = g["repo"]
    if own_public:
        return dict(base, action="overlay" if local else "clean", target=own_public, visibility="public")
    # A sisodias repo that no longer exists was deleted on purpose (Shaan, 2026-09-23: the HALO mirror
    # "we deleted it for a reason"). Never recreate it; fall through to the live remotes, or bundle.
    live_third = [g for g in ghs if not owned(g["owner"]) and g["owner"] != "Lordsisodia"]
    if live_third:
        g = live_third[0]
        return dict(base, action="overlay" if local else "clean", target=f"{g['owner']}/{g['repo']}", visibility="third-party")
    for name, url in remotes.items():
        if url.startswith(("/", "~", "file:")):
            src = os.path.realpath(os.path.expanduser(url.replace("file://", "")))
            if src.endswith(".git") and src[:-4] in bypath:
                src = src[:-4]
            if src in bypath and depth < 4 and src != rec["path"]:
                c = classify(bypath[src], bypath, gh, depth + 1)
                if c["action"] in ("own-private", "clean") and c.get("visibility") == "private":
                    return dict(base, action="own-private" if local else "clean", target=c["target"],
                                visibility="private", via=tilde(src))
    if rec.get("head") is None and not local:
        return {"action": "skip", "why": "empty repo"}
    if missing_own:
        return dict(base, action="overlay" if local else "clean", target=None, visibility="deleted-remote",
                    why=f"its GitHub repo {missing_own} was deleted; kept as a bundle, never recreated")
    # no usable remote: does a same-named sisodias repo already hold this history?
    leaf = os.path.basename(rec["path"])
    cands = [leaf, f"{os.path.basename(os.path.dirname(rec['path']))}-{leaf}", dead_lord or ""]
    roots = None
    for cand in filter(None, cands):
        if cand.lower() in gh:
            if roots is None:
                _, out, _ = git(rec["path"], "rev-list", "--max-parents=0", "--all")
                roots = out.split()[:3]
            for r in roots:
                if sh(["gh", "api", f"repos/{OWNER}/{gh[cand.lower()]['name']}/commits/{r}", "--jq", ".sha"])[0] == 0:
                    vis = gh[cand.lower()]["visibility"].lower()
                    return dict(base, action=("own-private" if vis == "private" else "overlay") if local else "clean",
                                target=f"{OWNER}/{gh[cand.lower()]['name']}", visibility=vis,
                                add_remote=f"https://github.com/{OWNER}/{gh[cand.lower()]['name']}.git",
                                why="no remote configured; same-named repo shares its root commit")
    host = building_of(rec["path"], bypath)
    if host:  # a repo inside a building is part of it, never a new GitHub repo (docs/MERGES.md: the mirror root cause)
        return dict(base, action="overlay" if local else "clean", target=None, visibility="nested",
                    why=f"inside the building {tilde(host)}: bundled with the estate overlays; house it on purpose "
                        "(tools/houses.py house) or absorb it (tools/vault.py absorb), never a mirror repo")
    return dict(base, action="new-repo", target=f"{OWNER}/{dead_lord}" if dead_lord and dead_lord.lower() not in gh else None,
                visibility="private", why="no GitHub copy exists" if not ghs else "its GitHub account is gone")


def building_of(path, bypath):
    """The nearest repo enclosing path that is a building, not a district: a district is the map itself or a top-level
    folder (SISO_Agency, HALO_Agency, ...), where a new repo is a new building; deeper, it is a room of one."""
    if not path.startswith(WS + "/"):
        return None
    cur = os.path.dirname(path)
    while cur.startswith(WS + "/"):
        rel = os.path.relpath(cur, WS)
        if cur in bypath and bypath[cur].get("kind") != "worktree":
            return cur if rel.count("/") >= 1 and not rel.startswith(("_archive/", "_data/", "_reference/")) else None
        cur = os.path.dirname(cur)
    return None


def new_repo_name(path, taken):
    parts = [x for x in os.path.relpath(path, WS if path.startswith(WS) else HOME).split("/") if x]
    generic = {"repo", "code", "app", "site", "admin", "tracker", "runtime", "src", "upstream", "files"}
    leaf = parts[-1]
    name = leaf if leaf.lower() not in generic or len(parts) < 2 else f"{parts[-2]}-{leaf}"
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.").lower() or "unnamed"
    base, n = name, 2
    while name.lower() in taken:
        name = f"{base}-{n}"
        n += 1
    taken.add(name.lower())
    return name


def plan(machine):
    inv, gh = load(machine)
    recs = [r for r in inv["repos"]]
    bypath = {r["path"]: r for r in recs}
    taken = set(gh.keys())
    out = []
    def main_repo(r):
        try:
            txt = open(os.path.join(r["path"], ".git"), errors="replace").read()
            gd = txt.split("gitdir:", 1)[1].strip()
            gd = gd if os.path.isabs(gd) else os.path.normpath(os.path.join(r["path"], gd))
            return gd.split("/.git/worktrees/")[0] if "/.git/worktrees/" in gd else None
        except Exception:
            return None
    np_ = os.path.join(REPO, "machines", machine, "names.json")
    names = json.load(open(np_)) if os.path.exists(np_) else {}  # optional: hand names, SKIP/HOLD/OVERLAY lines
    main_cls = {}
    for r in recs:
        if r["kind"] != "worktree":
            c = classify(r, bypath, gh)
            nm = names.get(os.path.relpath(r["path"], HOME))
            if nm and nm.startswith("SKIP"):
                c = {"action": "skip", "why": nm[5:].strip()}
            elif nm and nm.startswith("HOLD"):
                c = {"action": "hold", "why": nm[5:].strip()}
            elif nm and nm.startswith("OVERLAY"):
                c = dict(c, action="overlay", target=None, why=nm[8:].strip())
            elif c["action"] == "new-repo" and not c.get("target"):
                c["target"] = f"{OWNER}/{nm}" if nm else f"{OWNER}/{new_repo_name(r['path'], taken)}"
            main_cls[r["path"]] = c
    for r in recs:
        if r["kind"] == "worktree":
            c = classify(r, bypath, gh)
            m = main_repo(r)
            if c["action"] not in ("skip",) and m in main_cls:
                mc = main_cls[m]
                local = (r.get("unpushed_commits") or 0) + (r.get("dirty") or 0) + (r.get("untracked") or 0)
                act = mc["action"] if mc["action"] not in ("clean", "skip") else (
                    "own-private" if mc.get("visibility") == "private" else "overlay")
                if mc["action"] == "skip":
                    act = "skip"
                elif not local:
                    act = "clean"
                elif act == "new-repo":
                    act = "worktree-of-new-repo"
                c = {"action": act, "target": mc.get("target"), "visibility": mc.get("visibility"),
                     "main": tilde(m), "fix_remotes": [], "live_remotes": mc.get("live_remotes", []),
                     "shared_target": mc.get("shared_target")}
        else:
            c = dict(main_cls[r["path"]])
        if c["action"] == "new-repo" and not c.get("target"):
            c["target"] = f"{OWNER}/{new_repo_name(r['path'], taken)}"
        c.update({"path": r["path"], "kind": r["kind"], "slug": slug_of(r["path"]),
                  "unpushed": r.get("unpushed_commits"), "dirty": r.get("dirty"), "untracked": r.get("untracked")})
        out.append(c)
    doc = {"machine": machine, "planned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "items": out}
    path = os.path.join(REPO, "machines", machine, "backup-plan.json")
    json.dump(doc, open(path, "w"), indent=1)
    from collections import Counter
    print(json.dumps(Counter(i["action"] for i in out)), "->", path)
    return doc


# ---------- execution ----------

def gitleaks_commits(path, logopts):
    """(findings, error). A timeout comes back as an error, so the caller holds the checkout."""
    fd, rep = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        cfg = os.path.join(path, ".gitleaks.toml")     # the repo's own allowlist, else the estate's (houses.py leaks_config)
        cfg = cfg if os.path.exists(cfg) else os.path.join(WS, ".gitleaks.toml")
        code, _, err = sh(["gitleaks", "git", path, "--config", cfg, "--log-opts", logopts, "--redact", "--no-banner",
                           "--report-format", "json", "--report-path", rep, "--exit-code", "3"],
                          timeout=int(os.environ.get("ESTATE_GITLEAKS_TIMEOUT", "1800")))
    except subprocess.TimeoutExpired:
        os.unlink(rep)
        return None, "timeout"
    try:
        found = json.load(open(rep)) if os.path.getsize(rep) else []
    except Exception:
        found = []
    os.unlink(rep)
    if code not in (0, 3):
        return None, err[-300:]
    return [{"file": f.get("File"), "rule": f.get("RuleID"), "commit": (f.get("Commit") or "")[:10]} for f in found], None


FIREFOX_SECRETS = re.compile(r"(^|/)(cookies\.sqlite|logins\.json|key[34]\.db|cert9\.db)(-wal|-shm|-journal)?$")


def browser_state(path, files):
    """Browser user-data folders (a folder holding `Local State` or `Cookies`) and Firefox credential files among
    the files a snapshot would take. They carry session cookies and saved logins in encrypted databases the secret scan
    cannot read, so no snapshot or bundle ever includes them."""
    out, seen = set(), {}
    for f in files:
        if FIREFOX_SECRETS.search(f):
            out.add(f)
            continue
        parts = f.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if d not in seen:
                ab = os.path.join(path, d)
                seen[d] = os.path.exists(os.path.join(ab, "Local State")) or os.path.exists(os.path.join(ab, "Cookies"))
            if seen[d]:
                out.add(d)
                break
    return sorted(out)


def left_out_folders(path, files):
    """Repo-relative folders a snapshot never takes, and never counts against the WIP cap: HALO paths inside any repo
    (NEVER_COPY_PATHS; `clients/halo/crm/archive/` carries a password, and on 24 Sep only the cap kept it out of
    siso-agency's snapshot), and folders an encrypted data plane backs up (plan/data-planes.json: tools/data-backup.py)."""
    out = set()
    rel = os.path.relpath(path, WS)
    pre = "/" + ("" if rel == "." else rel + "/")     # match on the workspace path: HALO_Agency/crm is `crm/` inside its repo
    for f in files:
        m = NEVER_COPY_PATHS.search(pre + f)
        if m:
            end = m.start() + 1 + len(m.group(1))
            out.add((pre + f)[len(pre):end] if end > len(pre) else f.split("/")[0])   # else: the repo sits inside HALO
    try:
        planes = [q.rstrip("/") for pl in json.load(open(os.path.join(REPO, "plan", "data-planes.json"))).get("planes", [])
                  for q in pl.get("paths", [])]
    except (OSError, ValueError):
        planes = []
    out |= {q[len(rel) + 1:] for q in planes if q.startswith(rel + "/")}
    return sorted(out)


def wip_snapshot(path, machine, exclude=()):
    """Build a commit of the working tree in a temp index. Returns (sha|None, info)."""
    code, head, _ = git(path, "rev-parse", "-q", "--verify", "HEAD^{commit}")
    head = head if code == 0 else None
    code, idx, _ = git(path, "rev-parse", "--git-path", "index")
    idx = idx if os.path.isabs(idx) else os.path.join(path, idx)
    tmpdir = tempfile.mkdtemp(prefix="estate-idx-")
    tmp = os.path.join(tmpdir, "index")
    try:
        if os.path.exists(idx):
            shutil.copyfile(idx, tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp)
        # leave out files GitHub would refuse, and browser profiles (cookies, saved logins: gitleaks cannot read them)
        big, count, total = [], 0, 0
        _, out, _ = git(path, "ls-files", "-z", "-m", "-o", "--exclude-standard", env=env)
        files = list(filter(None, out.split("\0")))
        browser = browser_state(path, files)
        held = left_out_folders(path, files)
        for f in files:
            if any(f == d or f.startswith(d + "/") for d in browser) or any(f == d or f.startswith(d + "/") for d in held):
                continue
            fp = os.path.join(path, f)
            try:
                if os.path.isfile(fp) and not os.path.islink(fp):
                    sz = os.path.getsize(fp)
                    if sz > MAX_FILE:
                        big.append(f)
                    else:
                        count, total = count + 1, total + sz
            except OSError:
                pass
        # refuse before `git add`: adding first wrote every blob of a pile it then refused into .git (533 MB of
        # browser-profile copies in catgpt-gateway, 23 Sep)
        if count > MAX_WIP_FILES or total > MAX_WIP_BYTES:
            return None, {"error": f"wip too large: {count} files, {total // 1048576} MB", "big_skipped": big, "browser_state_left_out": browser}
        spec = ["."] + [f":(exclude,literal){f}" for f in list(big) + list(exclude) + browser + held]
        code, _, err = git(path, "add", "-A", "--ignore-errors", "--", *spec, env=env, timeout=1800)
        code, tree, err = git(path, "write-tree", env=env)
        if code != 0:
            return None, {"error": "write-tree: " + err[-200:]}
        if head:
            _, htree, _ = git(path, "rev-parse", f"{head}^{{tree}}")
            if tree == htree:
                return None, {"wip": "none", "big_skipped": big}
        # size guard
        cmp = [head] if head else ["4b825dc642cb6eb9a060e54bf8d69288fbee4904"]  # empty tree
        _, diff, _ = git(path, "diff-tree", "-r", "--no-renames", "--raw", cmp[0], tree)
        lines = [l for l in diff.splitlines() if l.startswith(":")]
        new_blobs = [l.split()[3] for l in lines if l.split()[3] != "0" * 40 and l.split()[1] != "160000"]
        size = 0
        if new_blobs:
            _, sizes, _ = sh(["git", "-C", path, "cat-file", "--batch-check=%(objectsize)"], input="\n".join(new_blobs))
            size = sum(int(x) for x in sizes.split() if x.isdigit())
        if len(lines) > MAX_WIP_FILES or size > MAX_WIP_BYTES:
            return None, {"error": f"wip too large: {len(lines)} files, {size//1048576} MB", "big_skipped": big}
        msg = f"estate: WIP snapshot of {tilde(path)} on {machine}, {time.strftime('%Y-%m-%d %H:%M')}\n\n[skip ci]"
        args = ["commit-tree", tree, "-m", msg] + (["-p", head] if head else [])
        env2 = dict(os.environ, GIT_AUTHOR_NAME="siso-estate", GIT_AUTHOR_EMAIL="estate@siso.local",
                    GIT_COMMITTER_NAME="siso-estate", GIT_COMMITTER_EMAIL="estate@siso.local")
        code, sha, err = git(path, *args, env=env2)
        if code != 0:
            return None, {"error": "commit-tree: " + err[-200:]}
        return sha, {"wip": sha[:10], "files": len(lines), "bytes": size, "big_skipped": big, "browser_state_left_out": browser,
                     "left_out_folders": held}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


_REL = {}


def related(path, target):
    """True when this checkout shares history with the GitHub repo it would push into."""
    _, roots, _ = git(path, "rev-list", "--max-parents=0", "HEAD")
    for r in roots.split()[:3]:
        key = (target, r)
        if key not in _REL:
            _REL[key] = sh(["gh", "api", f"repos/{target}/commits/{r}", "--jq", ".sha"])[0] == 0
        if _REL[key]:
            return True
    return False


_TIPS = {}


def load_tips(machine):
    global _TIPS
    try:
        _TIPS = json.load(open(TIPS_FILE.format(machine=machine)))
    except Exception:
        _TIPS = {}


def known_tips(path):
    """Commits this tool already pushed from this checkout (refs/backup is not a remote-tracking ref)."""
    out = []
    for sha in _TIPS.get(path, []):
        if git(path, "cat-file", "-e", f"{sha}^{{commit}}")[0] == 0:
            out.append(sha)
    return out


def not_elsewhere(live, path=None):
    extra = known_tips(path) if path else []
    if not live and not extra:
        return []
    return ["--not"] + [f"--remotes={r}" for r in live] + extra


def unpushed_branches(path, live):
    _, out, _ = git(path, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    res = []
    for b in filter(None, out.splitlines()):
        _, n, _ = git(path, "rev-list", "--count", f"refs/heads/{b}", *not_elsewhere(live, path))
        if n.isdigit() and int(n) > 0:
            res.append((b, int(n)))
    return res


# A checkout the secret scan holds still gets a copy off the machine (A0, 3 Oct: Oracle core's 2,600 commits sat only on
# the laptop behind a CamSoda key in a task note): every local branch, detached worktree HEAD and the WIP snapshot that
# its live remotes lack, as one git bundle, age-encrypted to the estate key (identity ~/.config/siso/age/estate-backup.key,
# a copy on siso-vps), pushed as an orphan commit to refs/heads/<machine>/<slug> of one private repo. The secret never
# reaches the target remote or the map in the clear; unchanged tips are not re-uploaded.
HELD_REPO = f"{OWNER}/siso-held-backups"
HELD_GIT = os.path.join(WS, "_data", "backup-repos", "siso-held.git")
HELD_URL = f"https://github.com/{HELD_REPO}.git"
AGE_RECIPIENTS = os.path.join(REPO, "plan", "age-recipient.txt")


def held_copy(path, machine, slug, live, wip, create, whole=True):
    """whole: a repo's own item keeps every branch and detached worktree HEAD; a worktree's item only its HEAD and WIP."""
    tmp = tempfile.mkdtemp(prefix=".siso-ephemeral-held-")
    tmp_refs = []
    try:
        _, wt, _ = git(path, "worktree", "list", "--porcelain")
        heads = [l.split()[1] for b in wt.split("\n\n") if "\ndetached" in b for l in b.splitlines() if l.startswith("HEAD ")]
        if not whole:
            heads = [git(path, "rev-parse", "HEAD")[1]]
        for n, sha in enumerate(heads + ([wip] if wip else [])):
            ref = f"refs/estate-tmp/{slug}/held-{n}"
            git(path, "update-ref", ref, sha)
            tmp_refs.append(ref)
        refs = (["--branches"] if whole else []) + tmp_refs
        excl = ["--not"] + [f"--remotes={r}" for r in live] if live else []
        _, n, _ = git(path, "rev-list", "--count", *refs, *excl)
        if not n.isdigit() or int(n) == 0:
            return {"status": "nothing-to-keep"}
        _, tips, _ = git(path, "for-each-ref", "--format=%(objectname) %(refname)", "refs/heads") if whole else (0, "", "")
        tips += "\n" + " ".join(heads) + "\n" + (git(path, "rev-parse", f"{wip}^{{tree}}")[1] if wip else "")  # WIP by tree: its commit is new every run
        fp = hashlib.sha256(tips.encode()).hexdigest()[:16]
        ref = f"refs/heads/{machine}/{slug}"
        url = HELD_URL
        if not os.path.isdir(HELD_GIT):
            sh(["git", "init", "-q", "--bare", HELD_GIT])
            sh(["git", "-C", HELD_GIT, "remote", "add", "origin", url])
        _, msg, _ = sh(["git", "-C", HELD_GIT, "log", "-1", "--format=%B", ref])
        _, local, _ = sh(["git", "-C", HELD_GIT, "rev-parse", "--verify", "-q", ref])
        code, remote, err = sh(["git", "ls-remote", url, ref], timeout=120)
        if code != 0 and create and "not found" in err.lower():
            code, _, err = sh(["gh", "repo", "create", HELD_REPO, "--private", "--description",
                               "age-encrypted bundles of checkouts the estate secret scan holds (siso-estate tools/backup.py)"])
            if code != 0:
                return {"status": "error", "error": "create: " + err[-300:]}
            sh(["gh", "api", "-X", "PUT", f"repos/{HELD_REPO}/actions/permissions", "-F", "enabled=false"])
            remote = ""
        elif code != 0:
            return {"status": "error", "error": "ls-remote: " + err[-300:]}
        if f"fingerprint {fp}" in msg and local and remote.split()[:1] == [local]:
            return {"status": "unchanged", "repo": HELD_REPO, "ref": ref, "commit": local[:12], "fingerprint": fp}
        bfile, efile = os.path.join(tmp, "work.bundle"), os.path.join(tmp, "work.bundle.age")
        code, _, err = git(path, "bundle", "create", bfile, *refs, *excl, timeout=1800)
        if code != 0:
            return {"status": "error", "error": "bundle: " + err[-300:]}
        code, _, err = sh(["age", "-R", AGE_RECIPIENTS, "-o", efile, bfile], timeout=1800)
        os.unlink(bfile)
        if code != 0:
            return {"status": "error", "error": "age: " + err[-300:]}
        size, entries = os.path.getsize(efile), []
        with open(efile, "rb") as f:
            for i, chunk in enumerate(iter(lambda: f.read(MAX_BUNDLE), b"")):
                part = os.path.join(tmp, f"part{i}")
                with open(part, "wb") as fh:
                    fh.write(chunk)
                _, blob, _ = sh(["git", "-C", HELD_GIT, "hash-object", "-w", part])
                os.unlink(part)
                entries.append(f"100644 blob {blob}\twork.bundle.age.{i:03d}")
        readme = (f"# {tilde(path)} ({machine})\n\nHeld by the secret scan on {time.strftime('%F %T%z')}; this is its "
                  f"disk-only work (branches, detached worktree HEADs, WIP) not on {', '.join(live) or 'any remote'}.\n\n"
                  "Restore (needs the estate age identity, ~/.config/siso/age/estate-backup.key):\n\n"
                  "    cat work.bundle.age.* | age -d -i ~/.config/siso/age/estate-backup.key > work.bundle\n"
                  "    git -C <checkout> fetch work.bundle 'refs/*:refs/restored/*'\n")
        _, blob, _ = sh(["git", "-C", HELD_GIT, "hash-object", "-w", "--stdin"], input=readme)
        entries.append(f"100644 blob {blob}\tREADME.md")
        _, tree, _ = sh(["git", "-C", HELD_GIT, "mktree"], input="\n".join(sorted(entries, key=lambda e: e.split("\t")[1])) + "\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="siso-estate", GIT_AUTHOR_EMAIL="estate@siso.local",
                   GIT_COMMITTER_NAME="siso-estate", GIT_COMMITTER_EMAIL="estate@siso.local")
        _, commit, _ = sh(["git", "-C", HELD_GIT, "commit-tree", tree, "-m",
                           f"held {tilde(path)} ({machine})\n\nfingerprint {fp}\nbytes {size}"], env=env)
        sh(["git", "-C", HELD_GIT, "update-ref", ref, commit])
        code, _, err = sh(["git", "-C", HELD_GIT, "push", "-q", "--force", "origin", f"{commit}:{ref}"], timeout=3600)
        if code != 0:
            return {"status": "error", "error": "push: " + err[-300:]}
        _, remote, _ = sh(["git", "ls-remote", url, ref], timeout=120)
        ok = remote.split()[:1] == [commit]
        return {"status": "pushed" if ok else "error", "repo": HELD_REPO, "ref": ref, "commit": commit[:12],
                "bytes": size, "chunks": len(entries) - 1, "fingerprint": fp, **({} if ok else {"error": "ls-remote mismatch"})}
    except subprocess.TimeoutExpired as e:
        return {"status": "error", "error": f"timeout: {e.cmd[:4]}"}
    finally:
        for r in tmp_refs:
            git(path, "update-ref", "-d", r)
        shutil.rmtree(tmp, ignore_errors=True)


def run_item(it, machine, create):
    path, act = it["path"], it["action"]
    res = {"path": path, "action": act, "target": it.get("target"), "slug": it["slug"]}
    t0 = time.time()
    try:
        if not os.path.isdir(path):
            res["status"] = "missing"
            return res
        # 1. fix dead Lordsisodia remotes (local config only)
        for fx in it.get("fix_remotes") or []:
            _, cur, _ = git(path, "remote", "get-url", fx["remote"])
            if "Lordsisodia" in cur:
                git(path, "remote", "set-url", fx["remote"], fx["url"])
                res.setdefault("fixed_remotes", []).append(f"{fx['remote']}: {cur} -> {fx['url']}")
        if it.get("add_remote"):
            _, rl, _ = git(path, "remote")
            rn = "origin" if "origin" not in rl.split() else "estate"
            if rn not in rl.split():
                git(path, "remote", "add", rn, it["add_remote"])
                res["remote_added"] = f"{rn} {it['add_remote']}"
        if act in ("skip", "clean"):
            res["status"] = act
            return res
        if act == "hold":
            res["status"], res["error"] = "held", it.get("why")
            return res
        is_repo = it["kind"] != "worktree"
        live = it.get("live_remotes") or []
        branches = unpushed_branches(path, live) if is_repo else []
        if act == "worktree-of-new-repo":
            act = "new-repo"  # its main repo is created first (serial), then this pushes wip only
            is_repo = False
        _, orphan, _ = git(path, "rev-list", "--count", "HEAD", "--not", "--branches", *[f"--remotes={r}" for r in live])
        orphan_head = orphan.isdigit() and int(orphan) > 0
        # 2. secrets in commits: only on request. Commits were made on purpose and go to the repo's own
        #    private remote, which `git push` would do anyway; the WIP snapshot (never meant to leave) is
        #    always scanned below.
        #    A detached HEAD's commits go into an overlay bundle too, so they are scanned with the branches.
        if (branches or orphan_head) and SCAN_COMMITS:
            scan = [f"refs/heads/{b}" for b, _ in branches] + (["HEAD"] if orphan_head else [])
            leaks, err = gitleaks_commits(path, " ".join(scan + not_elsewhere(live, path)))
            if leaks or (err and "timeout" in err):
                res["status"] = "held"
                if leaks:
                    res["leaks_in_commits"], res["leak_count"] = leaks[:20], len(leaks)
                else:
                    res["error"] = "commit secret scan timed out; nothing pushed in the clear"
                wip, _ = wip_snapshot(path, machine)
                res["held_copy"] = held_copy(path, machine, it["slug"], live, wip, create, whole=is_repo)
                return res
            if err:
                res["gitleaks_error"] = err
        # 3. WIP snapshot, re-built without any file gitleaks flags
        wip, info = wip_snapshot(path, machine)
        res["wip_info"] = info
        if wip:
            leaks, err = gitleaks_commits(path, f"-1 {wip}")
            if err == "timeout":
                res["wip_skipped"] = "secret scan of the uncommitted files timed out; WIP not pushed"
                wip = None
            if leaks:
                files = sorted({l["file"] for l in leaks if l["file"]})
                res["wip_excluded_for_secrets"] = files
                wip, info = wip_snapshot(path, machine, exclude=files)
                res["wip_info"] = info
        ns = f"refs/backup/{machine}/{it['slug']}"
        if act in ("own-private", "new-repo"):
            url = f"https://github.com/{it['target']}.git"
            if act == "new-repo":
                if not create:
                    res["status"] = "needs-create"
                    return res
                code, out, err = sh(["gh", "repo", "view", it["target"], "--json", "name"])
                if code != 0:
                    code, out, err = sh(["gh", "repo", "create", it["target"], "--private",
                                         "--description", f"Backup of {tilde(path)} (siso-estate)"])
                    if code != 0:
                        res["status"], res["error"] = "error", "create: " + err[-300:]
                        return res
                    res["created"] = True
                    sh(["gh", "api", "-X", "PUT", f"repos/{it['target']}/actions/permissions", "-F", "enabled=false"])
                _, remotes, _ = git(path, "remote")
                rname = "origin" if "origin" not in remotes.split() else "estate"
                if rname not in remotes.split():
                    git(path, "remote", "add", rname, url)
                else:
                    git(path, "remote", "set-url", rname, url)
                res["remote_added"] = rname
                if is_repo and res.get("created") and not it.get("shared_target"):
                    specs = ["+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"]
                elif is_repo:
                    specs = [f"+refs/heads/{b}:{ns}/heads/{b}" for b, _ in unpushed_branches(path, [])]
                else:
                    specs = []
            else:
                if not related(path, it["target"]):
                    act = "overlay"
                    res["note"] = f"history unrelated to {it['target']}; bundled instead of pushing foreign history into it"
                specs = [f"+refs/heads/{b}:{ns}/heads/{b}" for b, _ in branches]
            if act == "overlay":
                specs = []
            if orphan_head:
                specs.append(f"+HEAD:{ns}/head")
            if wip:
                specs.append(f"+{wip}:{ns}/wip")
            if act != "overlay" and not specs:
                res["status"] = "nothing-to-push"
                return res
            if act != "overlay":
                code, out, err = git(path, "push", "--no-verify", "--porcelain", url, *specs, timeout=3600)
                if code != 0 and ("408" in err or "postBuffer" in err or "rewind" in err or "unexpected disconnect" in err):
                    code, out, err = git(path, "-c", "http.postBuffer=524288000", "push", "--no-verify", "--porcelain", url, *specs, timeout=3600)
                res["refs"] = specs
                res["status"] = "pushed" if code == 0 else "error"
                if code == 0:
                    tips = [git(path, "rev-parse", f"refs/heads/{b}")[1] for b, _ in branches]
                    res["pushed_tips"] = [t for t in tips if t]
                if code != 0:
                    res["error"] = err[-600:]
                if code != 0 and "archived" in err:
                    act = "overlay"
                    res["note"] = "target repo is archived (read-only); kept as an overlay bundle instead"
        if act == "overlay":
            tmp_refs = []
            for b, _ in branches:
                tmp_refs.append(f"refs/heads/{b}")
            if orphan_head:
                _, hs, _ = git(path, "rev-parse", "HEAD")
                git(path, "update-ref", f"refs/estate-tmp/{it['slug']}/head", hs)
                tmp_refs.append(f"refs/estate-tmp/{it['slug']}/head")
            if wip:
                git(path, "update-ref", f"refs/estate-tmp/{it['slug']}/wip", wip)
                tmp_refs.append(f"refs/estate-tmp/{it['slug']}/wip")
            if not tmp_refs:
                res["status"] = "nothing-to-push"
                return res
            outdir = os.path.join(WS, ".estate", "overlays", machine)
            os.makedirs(outdir, exist_ok=True)
            bfile = os.path.join(outdir, f"{it['slug']}.bundle")
            code, out, err = git(path, "bundle", "create", bfile, *tmp_refs, *not_elsewhere(live), timeout=1800)
            for t in ("wip", "head"):
                git(path, "update-ref", "-d", f"refs/estate-tmp/{it['slug']}/{t}")
            if code != 0:
                res["status"], res["error"] = "error", "bundle: " + err[-300:]
            elif os.path.getsize(bfile) > MAX_BUNDLE:
                res["status"], res["error"] = "error", f"bundle too big ({os.path.getsize(bfile)//1048576} MB); not kept"
                os.unlink(bfile)
            else:
                res["status"], res["bundle"], res["bundle_bytes"] = "bundled", tilde(bfile), os.path.getsize(bfile)
                res["base_remote"] = it.get("target")
    except subprocess.TimeoutExpired as e:
        res["status"], res["error"] = "error", f"timeout: {e.cmd[:4]}"
    except Exception as e:  # keep the batch going; record the failure
        res["status"], res["error"] = "error", repr(e)[-300:]
    res["secs"] = round(time.time() - t0, 1)
    return res


def run(machine, only, jobs, create, resume=False):
    doc = json.load(open(os.path.join(REPO, "machines", machine, "backup-plan.json")))
    items = [i for i in doc["items"] if not only or only in i["path"]]
    if resume:
        done = set()
        lp = os.path.join(REPO, "machines", machine, "backup.log")
        for line in open(lp) if os.path.exists(lp) else []:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("status") in ("pushed", "bundled", "clean", "skip", "nothing-to-push", "held"):
                done.add(r["path"])
        items = [i for i in items if i["path"] not in done]
    out_path = os.path.join(REPO, "machines", machine, "backup.json")
    prev = {}
    if os.path.exists(out_path):
        prev = {r["path"]: r for r in json.load(open(out_path)).get("results", [])}
    if not only and not resume:  # a full run keeps only checkouts still planned (gone ones read as held/error forever)
        planned = {i["path"] for i in doc["items"]}
        prev = {p: r for p, r in prev.items() if p in planned}
    log = open(os.path.join(REPO, "machines", machine, "backup.log"), "a")
    load_tips(machine)
    results = []
    # new repos are created one at a time (GitHub rate-limits repo creation); pushes run in parallel
    serial = [i for i in items if i["action"] == "new-repo"] + [i for i in items if i["action"] == "worktree-of-new-repo"]
    para = [i for i in items if i["action"] not in ("new-repo", "worktree-of-new-repo")]

    def go(i):
        r = run_item(i, machine, create)
        print(json.dumps({k: r.get(k) for k in ("status", "action", "path", "error")}), file=log, flush=True)
        return r
    with ThreadPoolExecutor(jobs) as ex:
        results += list(ex.map(go, para))
    for i in serial:
        results.append(go(i))
    for r in results:
        prev[r["path"]] = r
        if r.get("pushed_tips"):
            _TIPS[r["path"]] = sorted(set(_TIPS.get(r["path"], [])) | set(r["pushed_tips"]))[-50:]
    json.dump(_TIPS, open(TIPS_FILE.format(machine=machine), "w"), indent=0)
    json.dump({"machine": machine, "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "results": sorted(prev.values(), key=lambda r: r["path"])}, open(out_path, "w"), indent=1)
    from collections import Counter
    print(json.dumps(Counter(r["status"] for r in results)), "->", out_path)


A0_INBOX = os.path.join(WS, "SISO_Agents", "agent-zero", "siso-firstmate", ".agents", "a0", "inbox.log")
CONSOLE_POST = os.path.join(HOME, ".claude", "console", "bin", "console-post")


def held_safe(r):
    return (r.get("held_copy") or {}).get("status") in ("pushed", "unchanged", "nothing-to-keep")


def alert(machine):
    """Make a held checkout loud (A0, 3 Oct: "where does Shaan/A0 see held?"). Held with no good encrypted copy: a console
    card and an A0 inbox line every night until it is fixed. Newly held with a safe copy: one card, because the secret it
    holds may need rotating (Shaan's call). Exit 1 when any held checkout is not backed up."""
    res = json.load(open(os.path.join(REPO, "machines", machine, "backup.json"))).get("results", [])
    seen_path = os.path.join(REPO, "machines", machine, "held-seen.json")
    seen = json.load(open(seen_path)) if os.path.exists(seen_path) else {}
    held = [r for r in res if r.get("status") == "held"]
    unsafe = [r for r in held if not held_safe(r)]
    new = [r for r in held if held_safe(r) and r["path"] not in seen]
    def why(r):
        hc = r.get("held_copy") or {}
        lk = f"{r.get('leak_count') or len(r.get('leaks_in_commits') or [])} secret-scan hits" if r.get("leaks_in_commits") else r.get("error", "held")
        return f"{tilde(r['path'])}: {lk}; " + (f"encrypted copy {hc.get('repo')} {hc.get('ref', '').split('/', 3)[-1]} ({hc.get('status')})"
                                                 if held_safe(r) else f"NOT backed up ({hc.get('error') or hc.get('status') or 'no copy'})")
    if unsafe or new:
        parts = (["Held, NOT backed up:"] + [f"- {why(r)}" for r in unsafe] if unsafe else []) + (
            ["Newly held, kept encrypted (the flagged secret may need rotating):"] + [f"- {why(r)}" for r in new] if new else [])
        body = "\n".join(parts + [f"Findings: SISO_Agents/siso-estate/machines/{machine}/backup.json (leaks_in_commits)"])
        title = f"estate backup: {len(unsafe)} held repo(s) NOT backed up ({machine})" if unsafe else f"estate backup: {len(new)} repo(s) newly held, kept encrypted ({machine})"
        if os.access(CONSOLE_POST, os.X_OK):
            sh([CONSOLE_POST, "-a", "ESTATE", "-k", "card", "-t", title, body])
        if unsafe and os.path.exists(A0_INBOX):
            with open(A0_INBOX, "a") as f:
                f.write(f"{time.strftime('%H:%M')} ESTATE: BLOCKED backup ({machine}): {'; '.join(why(r) for r in unsafe)}\n")
    json.dump({r["path"]: seen.get(r["path"], time.strftime("%F")) for r in held}, open(seen_path, "w"), indent=1)
    for r in held:
        print(("ok    " if held_safe(r) else "UNSAFE ") + why(r))
    return 1 if unsafe else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run", "alert"])
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--only")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--create-repos", action="store_true")
    ap.add_argument("--resume", action="store_true", help="skip checkouts the log already shows as done")
    ap.add_argument("--scan-commits", action="store_true", help="(default) gitleaks the commits being pushed")
    ap.add_argument("--no-scan-commits", action="store_true", help="skip the secret scan of pushed commits (never in the nightly)")
    a = ap.parse_args()
    global SCAN_COMMITS
    SCAN_COMMITS = not a.no_scan_commits
    if a.cmd == "alert":
        sys.exit(alert(a.machine))
    if a.cmd == "plan":
        plan(a.machine)
    else:
        run(a.machine, a.only, a.jobs, a.create_repos, a.resume)


if __name__ == "__main__":
    main()
