#!/usr/bin/env python3
"""Give loose work a house on GitHub, sync a project to GitHub, or retire it from this laptop.

  houses.py keys   FOLDER...                  copy key files (.env*, *.pem, credentials*.json, ...) into the
                                              credentials store: ~/SISO_Workspace/.credentials/projects/<map path>/
  houses.py house  FOLDER --repo NAME         FOLDER (no .git) becomes private sisodias/NAME: .gitignore for keys,
                                              deps and builds; secret scan; commit; create; push; verify
  houses.py sync   FOLDER... [--why TEXT]     every repo under FOLDER: local branches, stashes and uncommitted work
                                              pushed to its own GitHub remote (secret-scanned), then verified
  houses.py retire FOLDER... --why TEXT [--run]
                                              sync + keys, then remove the checkouts, leaving an empty folder at each
                                              map path and a line in machines/<m>/retired.jsonl; without --run it only
                                              plans. Refuses when any file under FOLDER is in no repo (house it first,
                                              or --plane NAME: a data plane that holds every such file; it is pushed
                                              first and those files leave too; files the parent repo tracks stay),
                                              when a repo holds a file GitHub refuses (>95 MB), or a scan flags a secret.

Sync never touches a branch, index, stash or working tree: branches that fast-forward their remote branch are pushed to
it; any other local work goes to refs/heads/estate/<date>/<branch> (and .../wip, .../stash-N) on the repo's own remote.
  --commits-only  branches only: no stash or uncommitted-work snapshot (loose piles stay on disk for their own plan)
  --merge         a branch that diverged from its remote branch is merged into it when git merges it without a conflict
                  (merge-tree + commit-tree, no work tree): the remote branch gains the local commits and the checkout
                  is simply behind; a conflict still goes to estate/<date>/<branch>
  --no-walk       each FOLDER is one repo: do not also sync the repos nested inside it
Third-party remotes are never pushed to: local work on them is bundled into ~/SISO_Workspace/.estate/overlays/<m>/,
which the map carries. HALO paths are refused (HALO code is never copied off Cam's repo).
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRED = os.path.join(WS, ".credentials", "projects")
OWNER = "sisodias"
DATE = time.strftime("%Y-%m-%d")
GH_MAX = 95 * 1024 * 1024
MIN_FREE = 5 * 1024 ** 3  # never write the disk below this: a full disk crashed herdr and every agent (24 Sep)
BATCH = 1500 * 1024 * 1024  # a house bigger than this is pushed in batches (GitHub refuses a push over 2 GB)
KEYS = re.compile(r"(^|/)(\.env(\.[\w.-]+)?|[^/]*\.(pem|key|p12|pfx|keystore|jks)|credentials?[^/]*\.(json|env|txt)|service-account[^/]*\.json|"
                  r"\.npmrc|\.netrc|secrets?\.(json|ya?ml|toml|env)|[^/]*api[-_]?key[^/]*\.(txt|json|env)|\.?mcp\.json)$"
                  r"|/\.(credentials|keys|secrets)/[^/]+$", re.I)   # 24 Sep: 7 key files in such folders had been missed
NOT_KEYS = re.compile(r"\.env\.(example|sample|template)$|/node_modules/|/\.venv/|/site-packages/|cacert\.pem$", re.I)
HALO = re.compile(r"/(HALO_Agency/crm|partners/halo/crm|clients/halocrm|clients/halo/crm|worktrees/halocrm|projects/halocrm|\.treehouse/halocrm|external/halocrm)(/|$)")
GITIGNORE = """# written by siso-estate houses.py
node_modules/
.next/
.turbo/
dist/
build/
.venv/
__pycache__/
.DS_Store
.env
.env.*
!.env.example
*.pem
*.key
"""


def sh(*cmd, cwd=None, env=None, timeout=1800, check=False):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise SystemExit(f"failed: {' '.join(cmd)}\n{(r.stderr or r.stdout)[-400:]}")
    return r.returncode, r.stdout.strip(), re.sub(r"://[^/@\s]+@", "://<redacted>@", r.stderr.strip())


def git(path, *a, **k):
    return sh("git", "-C", path, *a, **k)


def rel(p):
    return os.path.relpath(p, WS)


def is_top(path):
    """A real work tree whose top is PATH: uv's cache writes an empty `.git` file to keep git out, and that is no repo."""
    r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(path)


def repos_under(folder):
    out = []
    for root, dirs, files in os.walk(folder):
        if (".git" in dirs or ".git" in files) and is_top(root):
            out.append(root)
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", ".next")]
    return out


def owner_of(url):
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return (m.group(1).lower().replace("lordsisodia", OWNER), m.group(2)) if m else (None, None)


def loose_outside_repos(folder):
    """Files under folder that no repo under it holds (ignoring deps and OS junk)."""
    repos = repos_under(folder)
    out = []
    for root, dirs, files in os.walk(folder):
        if root != folder and root in repos:
            dirs[:] = []
            continue
        if root == folder and folder in repos:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git") and os.path.join(root, d) not in repos]
        out += [os.path.join(root, f) for f in files if f not in (".DS_Store", "manifest.md")]
    return out


def tracked_by_parent(folder):
    """Files under folder that the repo containing it tracks (a client folder's manifest.md and docs in siso-agency): they
    stay with that repo, so they are not loose."""
    if os.path.exists(os.path.join(folder, ".git")):
        return set()
    code, top, _ = git(folder, "rev-parse", "--show-toplevel")
    if code:
        return set()
    code, out, _ = git(top, "ls-files", "-z", "--", os.path.relpath(folder, top))
    return {os.path.join(top, x) for x in out.split("\0") if x}


def data_backup():
    import importlib.util
    spec = importlib.util.spec_from_file_location("data_backup", os.path.join(REPO, "tools", "data-backup.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- keys ----------
def save_keys(folder):
    """Copy key-like files (tracked, untracked or ignored) under folder into the credentials store."""
    saved = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", ".next", "site-packages")]
        for f in files:
            p = os.path.join(root, f)
            if KEYS.search(p) and not NOT_KEYS.search(p) and os.path.isfile(p) and not os.path.islink(p):
                dst = os.path.join(CRED, rel(p))
                os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
                shutil.copy2(p, dst)
                os.chmod(dst, 0o600)
                saved.append(rel(p))
    if saved:
        index()
    return saved


def index():
    lines = ["# Project keys (names only; values never leave this folder unencrypted)", "",
             "Copied here by siso-estate houses.py before a project is synced or retired, at the project's map path.",
             "Backed up encrypted to sisodias/siso-data-credentials (`estate data run credentials`).", ""]
    for root, _, files in sorted(os.walk(CRED)):
        for f in sorted(files):
            if f != "INDEX.md":
                lines.append(f"- `{os.path.relpath(os.path.join(root, f), CRED)}`")
    open(os.path.join(CRED, "INDEX.md"), "w").write("\n".join(lines) + "\n")


# ---------- scan ----------
SCAN_CACHE = os.path.join(REPO, "machines", os.environ.get("ESTATE_MACHINE", "laptop"), "scan-cache.json")


def leaks_config(path):
    """The allowlist a scan uses: the repo's own .gitleaks.toml, else the estate's (~/SISO_Workspace/.gitleaks.toml), so a
    false positive checked once is not re-flagged in every repo (24 Sep: the old agent base's commit hashes)."""
    own = os.path.join(path, ".gitleaks.toml")
    return own if os.path.exists(own) else os.path.join(WS, ".gitleaks.toml")


def scan_commits(path, rng):
    """gitleaks over the commits in rng; 0 = clean. Results for a single sha are remembered (scan-cache.json): a big
    stash takes minutes to scan and a repo with worktrees would scan it once per checkout."""
    key = rng.split()[0] if re.fullmatch(r"[0-9a-f]{40} --not --remotes=origin", rng) else None
    if key:  # a result holds only for the allowlist it was scanned under: a new .gitleaks.toml must rescan
        try:
            key += ":" + hashlib.sha256(open(leaks_config(path), "rb").read()).hexdigest()[:12]
        except OSError:
            pass
    cache = json.load(open(SCAN_CACHE)) if key and os.path.exists(SCAN_CACHE) else {}
    if key and key in cache:
        return cache[key]
    code, out, err = sh("gitleaks", "git", path, "--config", leaks_config(path), "--log-opts", rng, "--redact", "--no-banner",
                        "--exit-code", "1", timeout=3600)
    if key and code in (0, 1):
        cache = json.load(open(SCAN_CACHE)) if os.path.exists(SCAN_CACHE) else {}
        cache[key] = code
        json.dump(cache, open(SCAN_CACHE, "w"))
    return code


def scan_dir(path):
    code, out, err = sh("gitleaks", "dir", path, "--config", leaks_config(path), "--redact", "--no-banner", "--exit-code", "1", timeout=1800)
    return code, (out + err)[-600:]


# ---------- house ----------
def flagged_files(path, rng="HEAD"):
    fd, rep = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        sh("gitleaks", "git", path, "--config", leaks_config(path), "--log-opts", rng, "--redact", "--no-banner", "--report-format", "json",
           "--report-path", rep, timeout=1800)
        return sorted({x["File"] for x in json.load(open(rep))})
    finally:
        os.unlink(rep)


def park_flagged(folder, env):
    """Files the secret scan flags: copied to the credentials store at their map path, ignored by the house, and
    dropped from its first commit (which was never pushed). Nothing is lost; nothing secret reaches GitHub."""
    files = flagged_files(folder)
    for f in files:
        src = os.path.join(folder, f)
        dst = os.path.join(CRED, rel(src))
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o600)
    with open(os.path.join(folder, ".gitignore"), "a") as g:
        g.write("\n# secret-bearing files: kept in ~/SISO_Workspace/.credentials/projects/" + rel(folder) + "/ (siso-estate)\n")
        g.writelines("/" + f + "\n" for f in files)
    git(folder, "rm", "-q", "--cached", "--", *files, check=True)
    git(folder, "add", ".gitignore", check=True)
    git(folder, "commit", "-q", "--amend", "--no-edit", env=env, check=True)
    git(folder, "reflog", "expire", "--expire=now", "--all")
    git(folder, "gc", "-q", "--prune=now")  # the unpushed first commit held the secrets: gone from this repo too
    index()
    return files


def room(folder):
    """git add writes a second copy of every file into .git: refuse (and drop the fresh .git) when that would leave
    less than MIN_FREE on the disk."""
    _, todo, _ = git(folder, "ls-files", "-z", "-o", "--exclude-standard")
    need = sum(os.path.getsize(os.path.join(folder, f)) for f in todo.split("\0") if f and os.path.isfile(os.path.join(folder, f)))
    free = shutil.disk_usage(folder).free
    if free - need < MIN_FREE:
        shutil.rmtree(os.path.join(folder, ".git"))
        raise SystemExit(f"refused: the house needs {need / 2**30:.1f} GB in .git and only {free / 2**30:.1f} GB is free "
                         f"(keeps {MIN_FREE / 2**30:.0f} GB for the agents; a full disk crashed herdr and every agent on 24 Sep)")
    return need


def rebatch(folder, env):
    """A house over BATCH bytes becomes a chain of commits of at most ~BATCH each, whose last tree is exactly the
    scanned single commit's tree (so the scan still covers every file). Returns the commits in push order, or None."""
    _, lst, _ = git(folder, "ls-tree", "-r", "-l", "-z", "HEAD")
    entries = []
    for e in lst.split("\0"):
        if e:
            meta, path = e.split("\t", 1)
            mode, typ, sha, size = meta.split()
            entries.append((mode, sha, int(size) if size != "-" else 0, path))
    if sum(e[2] for e in entries) <= BATCH:
        return None
    groups, cur, n = [], [], 0
    for e in entries:  # ls-tree order keeps each folder together as far as the size allows
        if cur and n + e[2] > BATCH:
            groups.append(cur)
            cur, n = [], 0
        cur.append(e)
        n += e[2]
    groups.append(cur)
    _, final_tree, _ = git(folder, "rev-parse", "HEAD^{tree}")
    _, msg, _ = git(folder, "log", "-1", "--format=%B")
    tmpd = tempfile.mkdtemp(prefix="estate-rebatch-")
    try:
        ienv = dict(env, GIT_INDEX_FILE=os.path.join(tmpd, "index"))
        parent, chain = None, []
        for i, g in enumerate(groups, 1):
            info = "".join(f"{m} {s}\t{p}\n" for m, s, _, p in g)
            subprocess.run(["git", "-C", folder, "update-index", "--add", "--index-info"], input=info, text=True,
                           env=ienv, check=True, capture_output=True)
            _, tree, _ = git(folder, "write-tree", env=ienv, check=True)
            _, parent, _ = git(folder, "commit-tree", tree, *(["-p", parent] if parent else []),
                               "-m", f"{msg.strip()}\n\n(part {i} of {len(groups)}: GitHub takes at most 2 GB per push)",
                               env=env, check=True)
            chain.append(parent)
        if tree != final_tree:
            raise SystemExit("rebatch: the last batch's tree differs from the scanned commit; nothing pushed")
        git(folder, "update-ref", "refs/heads/main", parent, check=True)
        return chain
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def house(folder, name, why, park=False):
    folder = os.path.abspath(folder)
    if HALO.search(folder + "/"):
        raise SystemExit("refused: HALO path (HALO code is never copied off Cam's repo)")
    if os.path.exists(os.path.join(folder, ".git")):
        _, n, _ = git(folder, "rev-list", "--count", "--all")
        _, rem, _ = git(folder, "remote")
        if rem or n != "1" or git(folder, "log", "-1", "--format=%an")[1] != "siso-estate":
            raise SystemExit(f"{rel(folder)} is already a repo")
        shutil.rmtree(os.path.join(folder, ".git"))  # our own unpushed first attempt (held by the scan): start again
        gi0 = os.path.join(folder, ".gitignore")
        if os.path.exists(gi0):  # drop the block this tool appended last time, keep what was there before it
            kept = open(gi0).read().split("# written by siso-estate houses.py")[0].rstrip("\n")
            open(gi0, "w").write(kept + "\n") if kept else os.unlink(gi0)
    code, out, _ = sh("gh", "repo", "view", f"{OWNER}/{name}", "--json", "name")
    if code == 0:
        raise SystemExit(f"{OWNER}/{name} already exists on GitHub; pick another name")
    saved = save_keys(folder)
    gi = os.path.join(folder, ".gitignore")
    nested = [os.path.relpath(r, folder) for r in repos_under(folder) if r != folder]
    extra = "".join(f"/{n}/\n" for n in nested)
    if os.path.exists(gi):
        open(gi, "a").write("\n" + GITIGNORE + extra)
    else:
        open(gi, "w").write(GITIGNORE + extra)
    git(folder, "init", "-q", "-b", "main", check=True)
    room(folder)
    git(folder, "add", "-A", check=True)
    _, staged, _ = git(folder, "diff", "--cached", "--name-only", "-z")
    big = [f for f in staged.split("\0") if f and os.path.isfile(os.path.join(folder, f)) and os.path.getsize(os.path.join(folder, f)) > GH_MAX]
    if big:
        shutil.rmtree(os.path.join(folder, ".git"))
        raise SystemExit(f"refused: files over GitHub's limit: {big[:5]}")
    env = dict(os.environ, GIT_AUTHOR_NAME="siso-estate", GIT_AUTHOR_EMAIL="estate@siso.local",
               GIT_COMMITTER_NAME="siso-estate", GIT_COMMITTER_EMAIL="estate@siso.local")
    git(folder, "commit", "-q", "-m", f"house: {rel(folder)} as its own repo ({DATE})\n\n{why}", env=env, check=True)
    parked = []
    if scan_commits(folder, "HEAD") != 0:
        if not park:
            raise SystemExit(f"held: the secret scan flagged {rel(folder)}; the local repo stays, nothing was pushed "
                             f"(gitleaks git {folder} --log-opts HEAD; rerun with --park-secrets to keep those files in the "
                             f"credentials store instead of the repo)")
        parked = park_flagged(folder, env)
        if scan_commits(folder, "HEAD") != 0:
            raise SystemExit(f"held: still flagged after parking {len(parked)} files in {rel(folder)}")
    batches = rebatch(folder, env)
    code, out, err = sh("gh", "repo", "create", f"{OWNER}/{name}", "--private", "--source", folder, "--remote", "origin",
                        *([] if batches else ["--push"]), "--description", f"{rel(folder)} (SISO estate house, {DATE})")
    if code:
        raise SystemExit(f"gh repo create failed: {err[-300:]}")
    for sha in batches or []:  # GitHub refuses one push over 2 GB: send the house a batch at a time
        code, _, err = git(folder, "push", "-q", "origin", f"{sha}:refs/heads/main", timeout=7200)
        if code:
            raise SystemExit(f"push of batch {sha[:12]} failed: {err[-300:]}")
    if batches:
        git(folder, "branch", "-q", "--set-upstream-to=origin/main", "main")
    _, head, _ = git(folder, "rev-parse", "HEAD")
    _, remote, _ = git(folder, "ls-remote", "origin", "refs/heads/main")
    ok = remote.split()[0] == head if remote else False
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "act": "house", "path": rel(folder), "repo": f"{OWNER}/{name}", "head": head,
           "verified": ok, "keys_saved": saved, "secret_files_parked": parked, "nested_ignored": nested, "pushed_in_batches": len(batches or []), "why": why}
    log(rec)
    print(json.dumps(rec))
    return ok


# ---------- sync ----------
def snapshot(path):
    """Commit of HEAD + tracked changes + untracked (not ignored) files, built in a temp index. Returns (sha or None,
    key files left out, files over GitHub's limit left out)."""
    code, head, _ = git(path, "rev-parse", "-q", "--verify", "HEAD^{commit}")
    head = head if code == 0 else None
    _, idx, _ = git(path, "rev-parse", "--git-path", "index")
    idx = idx if os.path.isabs(idx) else os.path.join(path, idx)
    tmpd = tempfile.mkdtemp(prefix="estate-houses-")
    try:
        tmp = os.path.join(tmpd, "index")
        if os.path.exists(idx):
            shutil.copyfile(idx, tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp)
        _, lst, _ = git(path, "ls-files", "-z", "-m", "-o", "--exclude-standard", env=env)
        files = [f for f in lst.split("\0") if f]
        big = [f for f in files if os.path.isfile(os.path.join(path, f)) and not os.path.islink(os.path.join(path, f))
               and os.path.getsize(os.path.join(path, f)) > GH_MAX]
        # a file GitHub refuses stays on disk, out of the snapshot (a tracked one keeps its committed version there), so it
        # never holds back the rest of the repo's work; the record names it (ADR 0014 has how to make it fit)
        keys = [f for f in files if KEYS.search("/" + f) and not NOT_KEYS.search("/" + f)]
        spec = ["."] + [f":(exclude,literal){f}" for f in keys + big]
        git(path, "add", "-A", "--ignore-errors", "--", *spec, env=env, timeout=3600)
        code, tree, err = git(path, "write-tree", env=env)
        if code:
            return None, [err], big
        if head and tree == git(path, "rev-parse", f"{head}^{{tree}}")[1]:
            return None, [], big
        env2 = dict(os.environ, GIT_AUTHOR_NAME="siso-estate", GIT_AUTHOR_EMAIL="estate@siso.local",
                    GIT_COMMITTER_NAME="siso-estate", GIT_COMMITTER_EMAIL="estate@siso.local")
        code, sha, err = git(path, "commit-tree", tree, "-m", f"estate: uncommitted work in {rel(path)} on {DATE} (sync)\n\n[skip ci]",
                             *(["-p", head] if head else []), env=env2)
        return (sha if code == 0 else None), keys, big
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def merged(path, up, sha, branch):
    """A merge commit of the remote branch UP and the local SHA, built without a work tree; None on a conflict."""
    code, out, _ = git(path, "merge-tree", "--write-tree", "--no-messages", up, sha)
    if code != 0:
        return None
    env = dict(os.environ, GIT_AUTHOR_NAME="siso-estate", GIT_AUTHOR_EMAIL="estate@siso.local",
               GIT_COMMITTER_NAME="siso-estate", GIT_COMMITTER_EMAIL="estate@siso.local")
    code, m, _ = git(path, "commit-tree", out.split()[0], "-p", up, "-p", sha, "-m",
                     f"estate: merge the laptop's unpushed commits on {branch} (sync)\n\n[skip ci]", env=env)
    return m if code == 0 else None


def sync_repo(path, why, commits_only=False, merge=False):
    """Push every piece of local work in one repo to its own remote. Returns a record with ok True/False."""
    rec = {"path": rel(path), "pushed": [], "held": None}
    if HALO.search(path + "/"):
        rec["held"] = "HALO path: never copied"
        return rec
    _, url, _ = git(path, "remote", "get-url", "origin")
    own, name = owner_of(url)
    rec["url"] = url
    git(path, "fetch", "-q", "origin", timeout=1800)
    _, branches, _ = git(path, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    _, stashes, _ = (0, "", "") if commits_only else git(path, "stash", "list", "--format=%H")
    wip, keys, big = (None, [], []) if commits_only else snapshot(path)
    rec["keys_left_out"] = keys
    if big:
        rec["big_left_out"] = big  # still on disk; over GitHub's limit (tools/bigfiles.py makes one fit)
    todo = []  # (sha, dst ref)
    for line in branches.splitlines():
        b, sha = line.split()
        if git(path, "branch", "-r", "--contains", sha)[1]:
            continue  # already on a remote branch
        _, up, _ = git(path, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{b}")
        ff = not up or git(path, "merge-base", "--is-ancestor", up, sha)[0] == 0
        m = merged(path, up, sha, b) if merge and not ff and own == OWNER else None
        if m:
            rec.setdefault("merged", []).append({"branch": b, "local": sha[:12], "merge": m[:12]})
        todo.append((m or sha, f"refs/heads/{b}" if ff or m else f"refs/heads/estate/{DATE}/{b}"))
    for i, s in enumerate(stashes.splitlines()):
        todo.append((s, f"refs/heads/estate/{DATE}/stash-{i}"))
    if wip:
        todo.append((wip, f"refs/heads/estate/{DATE}/wip-{re.sub(r'[^A-Za-z0-9._-]+', '-', rel(path)).strip('-')}"))  # one per checkout: two checkouts of one repo share a basename
    if not todo:
        rec["ok"] = True
        return rec
    archived = own == OWNER and sh("gh", "api", f"repos/{OWNER}/{name}", "--jq", ".archived", timeout=60)[1].strip() == "true"
    if own != OWNER or archived:
        # someone else's repo, or ours archived on GitHub (it refuses pushes): bundle the local delta into the map's overlays
        rec["archived_on_github"] = archived
        # the map is pushed to GitHub too: a piece of work with a secret in it is held, never bundled
        flagged = [(sha, ref) for sha, ref in todo if scan_commits(path, f"{sha} --not --remotes") != 0]
        if flagged:
            rec["held"] = [{"ref": ref, "sha": sha[:12], "see": f"gitleaks git {rel(path)} --log-opts '{sha} --not --remotes'"}
                           for sha, ref in flagged]
            todo = [t for t in todo if t not in flagged]
            if not todo:
                rec["ok"] = False
                return rec
        dst = os.path.join(WS, ".estate", "overlays", os.environ.get("ESTATE_MACHINE", "laptop"),
                           re.sub(r"[^a-z0-9]+", "-", rel(path).lower()).strip("-") + ".bundle")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp_refs = []
        for i, (sha, ref) in enumerate(todo):
            r = f"refs/estate-tmp/{i}"
            git(path, "update-ref", r, sha)
            tmp_refs.append(r)
        code, _, err = git(path, "bundle", "create", dst, *tmp_refs, "--not", "--remotes")
        for r in tmp_refs:
            git(path, "update-ref", "-d", r)
        rec["bundle"] = rel(dst) if code == 0 else None
        rec["ok"] = code == 0 and os.path.getsize(dst) < GH_MAX and not flagged
        if not rec["ok"]:
            rec["held"] = f"bundle failed or too big: {err[-200:]}"
        return rec
    ok, held = True, []
    for sha, ref in todo:
        if git(path, "ls-remote", "origin", ref)[1].split()[:1] == [sha]:
            rec["pushed"].append({"ref": ref, "sha": sha[:12], "verified": True, "already": True})
            continue  # already there (e.g. a stash another checkout of this repo pushed)
        if scan_commits(path, f"{sha} --not --remotes=origin") != 0:
            # a secret in this piece of work: it stays in the local repo only (never pushed); retire refuses while it exists
            held.append({"ref": ref, "sha": sha[:12], "see": f"gitleaks git {rel(path)} --log-opts '{sha} --not --remotes=origin'"})
            ok = False
            continue
        code, _, err = git(path, "push", "-q", "origin", f"{sha}:{ref}", timeout=3600)
        if code and ref.startswith("refs/heads/") and "/estate/" not in ref:  # not a fast-forward after all
            ref = f"refs/heads/estate/{DATE}/{ref.split('/', 2)[2]}"
            code, _, err = git(path, "push", "-q", "origin", f"{sha}:{ref}", timeout=3600)
        if code and "/estate/" in ref and git(path, "ls-remote", "origin", ref)[1]:
            # an earlier run today already left this estate ref (e.g. a sync, then the retire's own snapshot): keep both
            ref = f"{ref}-{time.strftime('%H%M%S')}"
            code, _, err = git(path, "push", "-q", "origin", f"{sha}:{ref}", timeout=3600)
        remote = git(path, "ls-remote", "origin", ref)[1].split()
        good = code == 0 and remote and remote[0] == sha
        ok &= bool(good)
        rec["pushed"].append({"ref": ref, "sha": sha[:12], "verified": bool(good), **({} if good else {"error": err[-200:]})})
    rec["ok"] = ok
    if held:
        rec["held"] = held
    return rec


def sync(folder, why, commits_only=False, merge=False, walk=True):
    recs = [sync_repo(r, why, commits_only, merge) for r in (repos_under(folder) if walk else [folder])]
    log({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "act": "sync", "path": rel(folder), "repos": recs, "why": why})
    return recs


# ---------- retire ----------
def retire(folder, why, run, plane=None):
    folder = os.path.abspath(folder)
    if HALO.search(folder + "/"):
        raise SystemExit("refused: HALO path")
    kept = tracked_by_parent(folder)
    loose = [p for p in loose_outside_repos(folder) if p not in kept]
    repos = repos_under(folder)
    print(f"{rel(folder)}: {len(repos)} repos, {len(loose)} files in no repo")
    covered = []
    if loose and plane:
        # --plane: loose files may leave only when that encrypted data plane holds every one of them (checked here by
        # its own file walk, so a file it would skip as regenerable counts as not held) and is pushed before removal
        DB = data_backup()
        pl = [x for x in DB.planes() if x["name"] == plane]
        if not pl:
            print(f"  refused: no data plane named {plane} in plan/data-planes.json")
            return False
        held_by_plane = set(DB.gather(pl[0])[0])
        missing = [p for p in loose if p not in held_by_plane]
        if missing:
            print(f"  refused: plane {plane} does not hold {len(missing)} of them:", [rel(p) for p in missing[:8]])
            return False
        covered, loose = loose, []
    if loose:
        print("  refused: house these first (or name a data plane that holds them: --plane NAME):", [rel(p) for p in loose[:8]])
        return False
    code, holders, _ = sh("lsof", "-t", "+D", folder, timeout=120)
    if holders:
        print(f"  refused: processes use files in it: {holders.split()[:5]}")
        return False
    for r in repos:
        _, wts, _ = git(r, "worktree", "list", "--porcelain")
        outside = [l.split(" ", 1)[1] for l in wts.splitlines() if l.startswith("worktree ") and not l.split(" ", 1)[1].startswith(folder)]
        if outside:
            print(f"  refused: {rel(r)} has worktrees outside the folder: {outside}")
            return False
    if not run:
        for r in repos:
            print(f"  would sync {rel(r)} ({git(r, 'remote', 'get-url', 'origin')[1]})")
        if covered:
            print(f"  would push data plane {plane} ({len(covered)} loose files), then remove those files")
        return True
    keys = save_keys(folder)
    if keys:  # the only other copy is about to be removed: the encrypted plane must hold the keys first
        code, out, err = sh(sys.executable, os.path.join(REPO, "tools", "data-backup.py"), "run", "credentials", timeout=1800)
        if code or not re.search(r'"status": "(pushed|unchanged)"', out):
            print(f"  held: the credentials plane did not push ({(err or out)[-200:]}); nothing removed")
            return False
    if covered:
        code, out, err = sh(sys.executable, os.path.join(REPO, "tools", "data-backup.py"), "run", plane, timeout=7200)
        if code or not re.search(r'"status": "(pushed|unchanged)"', out):
            print(f"  held: data plane {plane} did not push ({(err or out)[-200:]}); nothing removed")
            return False
    recs = [sync_repo(r, why) for r in repos]
    bad = [x for x in recs if not x.get("ok")]
    if bad:
        print("  held, nothing removed:", json.dumps(bad)[:600])
        log({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "act": "retire-held", "path": rel(folder), "repos": recs})
        return False
    heads = {rel(r): git(r, "rev-parse", "HEAD")[1] for r in repos}
    freed = int(sh("du", "-sk", folder)[1].split()[0]) * 1024
    top = [r for r in repos if not any(r != o and r.startswith(o + "/") for o in repos)]
    for r in top:
        shutil.rmtree(r)
        os.makedirs(r, exist_ok=True)  # the empty folder at its map path
    for p in covered:  # held by the pushed plane (checked above)
        if os.path.isfile(p) and not os.path.islink(p):
            os.remove(p)
    if covered:  # folders the loose files left empty go too; the repos' empty map folders and tracked files stay
        for root, dirs, files in os.walk(folder, topdown=False):
            if root != folder and root not in top and not any(root.startswith(r + "/") for r in top) and not os.listdir(root):
                os.rmdir(root)
    freed_after = int(sh("du", "-sk", folder)[1].split()[0]) * 1024
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "act": "retire", "path": rel(folder), "why": why, "bytes_freed": freed - freed_after,
           "keys_saved": keys, "repos": [{**x, "head": heads.get(x["path"])} for x in recs],
           "restore": [f"estate restore --only {rel(r)} --run" for r in top]}
    if covered:
        rec["loose_in_plane"] = {"plane": plane, "files": len(covered),
                                 "restore": f"python3 tools/data-backup.py restore {plane} ~/SISO_Workspace (files come back at their paths)"}
    log(rec)
    with open(os.path.join(REPO, "machines", os.environ.get("ESTATE_MACHINE", "laptop"), "retired.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    place_retired(rec)
    print(f"  retired: {rec['bytes_freed'] / 1024 ** 2:.0f} MB freed; {len(recs)} repos verified on GitHub; keys saved: {len(keys)}"
          + (f"; {len(covered)} loose files in plane {plane}" if covered else ""))
    return True


def place_retired(rec):
    """Keep each retired repo on the map (plan/github-placements.json): no machine checks it out any more."""
    gp = os.path.join(REPO, "plan", "github-placements.json")
    doc = json.load(open(gp))
    for x in rec["repos"]:
        own, name = owner_of(x.get("url"))
        if own:
            doc["placements"][x["path"]] = {"repo": f"{'sisodias' if own == OWNER else own}/{name}",
                                            "why": f"retired from the laptop {rec['at'][:10]}: {rec['why']}"}
    doc["placements"] = dict(sorted(doc["placements"].items()))
    json.dump(doc, open(gp, "w"), indent=1)
    open(gp, "a").write("\n")


def log(rec):
    with open(os.path.join(REPO, "machines", os.environ.get("ESTATE_MACHINE", "laptop"), "houses.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["keys", "house", "sync", "retire"])
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--repo")
    ap.add_argument("--why", default="")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--park-secrets", action="store_true", help="house: flagged files go to the credentials store, not the repo")
    ap.add_argument("--commits-only", action="store_true", help="sync: branches only, no stash or uncommitted snapshot")
    ap.add_argument("--merge", action="store_true", help="sync: merge a diverged branch into its remote branch when clean")
    ap.add_argument("--no-walk", action="store_true", help="sync: each folder is one repo, nested repos are not synced")
    ap.add_argument("--plane", help="retire: a data plane that holds the folder's loose files; pushed first, then they go too")
    a = ap.parse_args()
    ok = True
    for f in a.folders:
        f = os.path.abspath(os.path.expanduser(f))
        if a.cmd == "keys":
            print(f, save_keys(f))
        elif a.cmd == "house":
            ok &= bool(house(f, a.repo, a.why, a.park_secrets))
        elif a.cmd == "sync":
            for r in sync(f, a.why, a.commits_only, a.merge, not a.no_walk):
                print(json.dumps(r)[:400])
                ok &= bool(r.get("ok"))
        else:
            ok &= retire(f, a.why, a.run, a.plane)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
