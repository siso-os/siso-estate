#!/usr/bin/env python3
"""Commit everyone's uncommitted work to a branch, push it, then remove the worktree if nothing needs it.

  worktree-wip.py [--only PATH ...] [--no-remove] [--run]

Shaan, 20 Oct: "any agent may commit and push anyone's uncommitted work to a branch". Per worktree:
  1. the dirty work (tracked edits + untracked files, ignored files excluded) becomes ONE commit on
     estate/wip-2026-10-20-<lane>, built with a private index (the worktree's own index, files and branch never change);
     its parent is the worktree's HEAD, so unpushed commits ride along;
  2. secrets/.env/databases/transcripts/keys and files over 50 MB stay out of git (gitleaks must pass on the commit);
  3. the branch is pushed to origin (a worktree with no origin is only reported);
  4. removal only when: the pushed sha matches, no process has its cwd or a file open inside, it is not a live seat's cwd,
     it is not on the keep list. Files kept out of git are MOVED to ~/SISO_Workspace/.agents/runs/wip-held-20261020/
     (a data plane) before the folder goes; ignored regenerable output just goes. Nothing is reset, stashed or forced.
A receipt line per worktree goes to machines/laptop/wip-20261020.jsonl and removed.jsonl.
"""
import argparse, datetime as dt, glob, json, os, re, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
from estate_state import record_path

WS = os.path.expanduser("~/SISO_Workspace")
ROOTS = [os.path.join(WS, "_data", "worktrees"), os.path.expanduser("~/.local/state/siso-code/worktrees"),
         os.path.expanduser("~/.codex/worktrees")]
DAY = dt.date.today().isoformat()
MACHINE = os.environ.get("ESTATE_MACHINE") or ("mini" if "mini" in socket.gethostname().lower() else "laptop")
HELD = os.path.join(WS, ".agents", "runs", "wip-held-" + DAY)
RECEIPTS = record_path(REPO, "machines", MACHINE, "wip-" + DAY + ".jsonl")
REMOVED = record_path(REPO, "machines", MACHINE, "removed.jsonl")
SEATS = os.path.join(WS, "_data", "agent-base-state", "state", "t3-seats.json")
NO_REMOVE = re.compile(r"/(main|live|mini-runtime-20261018|stall-tolerant-sends|db-stall|pair-ab|ship-runner|release-1|queue-base|browser-20261007)$")
PRIVATE = re.compile(r"(^|/)(halocrm|fahmy|bykonz[^/]*|[^/]*whatsapp[^/]*|life|lifelock)(/|$)", re.I)  # a live session's fix; the shared monorepo checkout
KEEP_OUT = re.compile(r"(^|/)(\.env[^/]*|.*\.(pem|key|p12|pfx|sqlite3?|db|db-wal|db-shm|sqlite-wal|sqlite-shm|jsonl|log|mp4|mov|zip|tar|gz|tgz|7z|dmg|bin)|"
                      r"id_rsa[^/]*|credentials[^/]*\.json|secrets?[^/]*|\.npmrc)$", re.I)
MAXB = 50 * 1024 * 1024
JSONL_OK = re.compile(r"(^|/)(package-lock|tsconfig[^/]*)\.json$|\.test\.jsonl$|fixtures?/|/tests?/|/docs?/|/data/seed", re.I)
REGEN = ("node_modules", ".next", "dist", ".turbo", "target", "build", ".vite", ".cache", "coverage", ".astro", "__pycache__",
         ".pytest_cache", ".venv", "venv", ".parcel-cache", ".svelte-kit", "storybook-static", ".oracle-builds", ".t3")


def sh(*a, env=None, cwd=None):
    return subprocess.run(a, capture_output=True, text=True, env=env, cwd=cwd)


def git(p, *a, env=None):
    return sh("git", "-C", p, *a, env=env)


def worktrees():
    out = []
    for root in ROOTS:
        for dp, dn, fn in os.walk(root):
            depth = dp[len(root):].count(os.sep)
            if ".git" in fn or ".git" in dn:
                out.append(dp)
                dn[:] = []
            elif depth >= 2:
                dn[:] = []
    return sorted(set(out))


def seat_cwds():
    try:
        d = json.load(open(SEATS))
    except Exception:
        return []
    return [v.get("cwd") for v in d.values() if isinstance(v, dict) and v.get("cwd")] + \
           [v.get("worktree") for v in d.values() if isinstance(v, dict) and v.get("worktree")]


def open_paths():
    """Every path any process of ours has open or as cwd, one lsof call."""
    result = sh("lsof", "-u", str(os.getuid()), "-Fn", "-w")
    if result.returncode or not result.stdout:
        raise RuntimeError("cannot establish open files; preservation held")
    out = result.stdout
    return {l[1:] for l in out.splitlines() if l.startswith("n/")}


def inside(paths, p):
    pre = p.rstrip("/") + "/"
    return next((x for x in paths if x == p or x.startswith(pre)), None)


def _planes():
    try:
        d = json.load(open(os.path.join(REPO, "plan", "data-planes.json")))
        return [os.path.join(WS, x).rstrip("/") + "/" for p in d.get("planes", d) if isinstance(p, dict) for x in (p.get("paths") or [])]
    except Exception:
        return []


PLANES = _planes()


def keep_out(rel, size, full=None):
    if full and any(full.startswith(pl) for pl in PLANES):  # already backed up by its data plane (run evidence, artifacts...)
        return True
    if re.search(r"(^|/)(life|lifelock|whatsapp(?:-link)?|fahmy)(/|$)", rel, re.I):
        return True
    if size > MAXB:
        return True
    if KEEP_OUT.search(rel):
        return True
    return False


def untracked_and_changed(p):
    r = git(p, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    items = [x for x in r.stdout.split("\0") if x]
    out, i = [], 0
    while i < len(items):
        e = items[i]
        st, path = e[:2], e[3:]
        if st[0] in "RC":
            i += 1
        out.append((st, path))
        i += 1
    return out


def wip(p, lane, extra_excl=()):
    """Build the wip commit with a private index. Returns (sha, excluded files, dirty count)."""
    ch = untracked_and_changed(p)
    if not ch:
        return None, [], 0
    excl = list(extra_excl)
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(td, "idx"))
        if git(p, "read-tree", "HEAD", env=env).returncode:  # unborn HEAD
            return None, [], len(ch)
        add, rm = [], []
        for st, path in ch:
            fp = os.path.join(p, path)
            if "D" in st and not os.path.exists(fp):
                rm.append(path)
                continue
            if keep_out(path, os.lstat(fp).st_size, fp):
                excl.append(path)
                continue
            if os.path.islink(fp):
                add.append(path)
                continue
            if not os.path.isfile(fp):
                continue
            if path in excl:
                continue
            if st == "??" or keep_out(path, os.path.getsize(fp), fp):
                if keep_out(path, os.path.getsize(fp), fp):
                    excl.append(path)
                    continue
            add.append(path)
        for i in range(0, len(add), 500):
            if git(p, "add", "--", *add[i:i + 500], env=env).returncode:
                raise RuntimeError("private index add failed")
        for i in range(0, len(rm), 500):
            if git(p, "rm", "--cached", "-q", "--", *rm[i:i + 500], env=env).returncode:
                raise RuntimeError("private index remove failed")
        tree_result = git(p, "write-tree", env=env)
        if tree_result.returncode:
            raise RuntimeError("private index write failed")
        tree = tree_result.stdout.strip()
        head = git(p, "rev-parse", "HEAD").stdout.strip()
        msg = f"estate wip {DAY} {lane}: uncommitted work saved by the Estate Manager (Shaan, 20 Oct: any agent may commit anyone's work)"
        sha = sh("git", "-C", p, "-c", "user.name=SISO Estate", "-c", "user.email=estate@siso.local",
                 "commit-tree", tree, "-p", head, "-m", msg).stdout.strip()
    return sha, excl, len(ch)


def leaks(p, sha):
    r = sh("gitleaks", "git", p, "--log-opts", f"{sha} --not --remotes", "--no-banner", "-r", "-", "-f", "json", "--exit-code", "7")
    if r.returncode == 0:
        return []
    if r.returncode != 7:
        return ["*"]
    try:
        return sorted({x["File"] for x in json.loads(r.stdout)})
    except Exception:
        return ["*"]


def receipt(**kw):
    kw["t"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(RECEIPTS), exist_ok=True)
    with open(RECEIPTS, "a") as f:
        f.write(json.dumps(kw) + "\n")


def already_remote(p, clean=True):
    """Fresh advertised tips must cover HEAD and every local branch/tag; stashes stay held."""
    checks = [("stash", "list", "--format=%H")]
    if clean:
        checks.append(("status", "--porcelain", "--untracked-files=all"))
    for args in checks:
        result = git(p, *args)
        if result.returncode or result.stdout.strip():
            return False
    remote = git(p, "ls-remote", "origin")
    cached = git(p, "for-each-ref", "--format=%(objectname)", "refs/remotes", "refs/heads", "refs/tags")
    if remote.returncode or cached.returncode:
        return False
    tips = {line.split()[0] for line in remote.stdout.splitlines() if line.split()} & set(cached.stdout.splitlines())
    if not tips:
        return False
    missing = git(p, "rev-list", "HEAD", "--branches", "--tags", "--not", *sorted(tips))
    return missing.returncode == 0 and not missing.stdout.strip()


def push_wip(p, branch, owner, name):
    ref = f"refs/heads/{branch}:refs/heads/{branch}"
    pushed = git(p, "push", "-q", "origin", ref)
    if pushed.returncode == 0 or owner != "sisodias":
        return pushed.returncode == 0
    repo = f"{owner}/{name}"
    archived = sh("gh", "api", f"repos/{repo}", "--jq", ".archived")
    if archived.returncode or archived.stdout.strip() != "true":
        return False
    opened = sh("gh", "api", "-X", "PATCH", f"repos/{repo}", "-F", "archived=false")
    if opened.returncode:
        return False
    try:
        # GitHub's Git service can lag the archive-state PATCH briefly.
        for attempt in range(3):
            pushed = git(p, "push", "-q", "origin", ref)
            if pushed.returncode == 0:
                break
            if attempt < 2:
                time.sleep(2)
    finally:
        closed = sh("gh", "api", "-X", "PATCH", f"repos/{repo}", "-F", "archived=true")
    if closed.returncode:
        raise RuntimeError("repo re-archive failed; local work retained")
    check = sh("gh", "api", f"repos/{repo}", "--jq", ".archived")
    return pushed.returncode == 0 and check.returncode == 0 and check.stdout.strip() == "true"


def finish_primary(p, res, run, held_paths, seats):
    """A standalone clone may leave only with zero dirty/unpushed/stashed work."""
    def safe(path):
        if not already_remote(path):
            return False
        worktrees = git(path, 'worktree', 'list', '--porcelain')
        if worktrees.returncode or any(line.startswith('worktree ') and os.path.realpath(line[9:]) != os.path.realpath(path) for line in worktrees.stdout.splitlines()):
            return False
        extras = git(path, 'ls-files', '--others', '-z')
        if extras.returncode or any(f and not any(part in REGEN for part in f.split('/')) for f in extras.stdout.split('\0')):
            return False
        for base, dirs, files in os.walk(path):
            if base != path and ('.git' in dirs or '.git' in files):
                return False
            dirs[:] = [d for d in dirs if d != '.git']
        return True
    if not safe(p):
        res['result'] = 'kept: primary clone has unique data, nested repo or outside worktree'
        return res
    res.update(dirty=0, unpushed=0, stashes=0, preserved='fresh remote coverage of every branch/tag and HEAD')
    if not run:
        res['result'] = 'would retire verified standalone clone'
        return res
    if inside(open_paths(), p) or inside(seat_cwds(), p):
        res['result'] = 'kept: primary clone became busy'
        return res
    quarantine = p + '.estate-retiring-' + str(os.getpid())
    if os.path.lexists(quarantine):
        res['result'] = 'kept: quarantine exists'
        return res
    os.rename(p, quarantine)
    try:
        if inside(open_paths(), quarantine) or not safe(quarantine):
            res['result'] = 'kept: primary clone changed or became busy'
            return res
        shutil.rmtree(quarantine)
        res['result'] = 'removed'
        os.makedirs(os.path.dirname(REMOVED), exist_ok=True)
        with open(REMOVED, 'a') as f:
            f.write(json.dumps(dict(res, t=dt.datetime.now(dt.timezone.utc).isoformat())) + '\n')
        return res
    finally:
        if os.path.isdir(quarantine) and not os.path.lexists(p):
            os.rename(quarantine, p)


def process(p, run, remove, held_paths, seats):
    rel = p.replace(WS + "/", "").replace(os.path.expanduser("~") + "/", "~/")
    lane = re.sub(r"[^A-Za-z0-9._-]+", "-", "-".join(p.rstrip("/").split("/")[-2:]))
    res = {"path": p, "lane": lane}
    if PRIVATE.search(p) or NO_REMOVE.search(p):
        res["result"] = "kept: private or standing"
        return res
    if inside(held_paths, p) or inside(seats, p):
        res["result"] = "kept: process or seat inside"
        return res
    primary = os.path.isdir(os.path.join(p, '.git'))
    if not primary and not os.path.isfile(os.path.join(p, '.git')):
        res['result'] = 'kept: not a Git tree'
        return res
    if git(p, "rev-parse", "--git-dir").returncode:
        res["result"] = "orphaned"
        return res
    url = git(p, "remote", "get-url", "origin").stdout.strip()
    match = re.search(r"github\.com[:/](sisodias|lordsisodia|siso-os|halo-agency)/([^/]+?)(?:\.git)?$", url, re.I)
    if not match:
        res["result"] = "kept: remote not authorised"
        return res
    owner = match.group(1).lower()
    common = git(p, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode or not common.stdout.strip():
        res["result"] = "kept: cannot establish repository boundary"
        return res
    # App-created lanes can be named core/t3-<id>; their shared Git store still
    # identifies the HALO repository. Keep both path and remote ownership gates.
    is_halo = bool(re.search(r"halo|oracle|operator-app|kellman", p + "\n" + common.stdout.strip(), re.I))
    if is_halo != (owner == "halo-agency"):
        res["result"] = "kept: repository boundary mismatch"
        return res
    if primary and remove:
        return finish_primary(p, res, run, held_paths, seats)
    if not run:
        res["result"] = "would preserve and verify before removal" if remove else "would preserve"
        return res
    branch = f"estate/wip-{DAY.replace('-', '')}-{lane}"
    if already_remote(p):
        sha = git(p, "rev-parse", "HEAD").stdout.strip()
        res.update(dirty=0, unpushed=0, stashes=0, preserved="fresh remote reachability")
        return finish(p, res, sha, [], run, remove, held_paths, seats, "existing remote refs")
    stashes = git(p, "stash", "list", "--format=%H")
    if stashes.returncode or stashes.stdout.strip():
        res["result"] = "held: stashes require separate preservation"
        return res
    sha, excl, n = wip(p, lane)
    res["dirty"] = n
    if sha:
        bad = leaks(p, sha)
        attempts = 0
        while bad and bad != ["*"]:  # push without the files gitleaks flagged; they go to the held plane
            if attempts >= 3 or all(b in excl for b in bad):
                res["result"] = "held: secret in existing commits"
                return res
            attempts += 1
            excl += [b for b in bad if b not in excl]
            sha, excl, n = wip(p, lane, excl)
            bad = leaks(p, sha) if sha else []
        if bad == ["*"]:
            res["result"] = "gitleaks-unreadable"
            return res
    else:
        sha = git(p, "rev-parse", "HEAD").stdout.strip()  # clean: push HEAD itself so it is on GitHub
    if leaks(p, sha):
        res["result"] = "held: secret scan failed"
        return res
    res["excluded"] = len(excl)
    if run:
        prior = git(p, "ls-remote", "origin", f"refs/heads/{branch}")
        if prior.returncode:
            res["result"] = "remote verification failed"
            return res
        if prior.stdout.split() and prior.stdout.split()[0] != sha:
            branch += "-" + sha[:12]
        if git(p, "update-ref", f"refs/heads/{branch}", sha).returncode:
            res["result"] = "could not preserve ref"
            return res
        ok = push_wip(p, branch, owner, match.group(2))
        if ok:
            ls = git(p, "ls-remote", "origin", f"refs/heads/{branch}").stdout.split()
            ok = bool(ls) and ls[0] == sha
        res["pushed"] = ok
        if not ok:
            res["result"] = "push or remote verification failed"
            return res
    if primary:
        res.update(result='committed', sha=sha, primary=True)
        return res
    return finish(p, res, sha, excl, run, remove, held_paths, seats, branch)


def move_worktree(p, target, restore=False):
    entries = git(p, "ls-files", "--stage", "-z")
    links = [e.split("\t", 1)[1] for e in entries.stdout.split("\0") if e.startswith("160000 ")]
    lock = git(p, "rev-parse", "--git-path", "locked")
    if entries.returncode or lock.returncode or not lock.stdout.strip() or os.path.lexists(os.path.join(p, lock.stdout.strip())):
        return subprocess.CompletedProcess([], 1, "", "worktree metadata unavailable or locked")
    # Git does not inventory authored files below an unpopulated gitlink.
    if not restore and any(os.path.lexists(os.path.join(p, f)) and
                           (not os.path.isdir(os.path.join(p, f)) or os.path.islink(os.path.join(p, f)) or os.listdir(os.path.join(p, f)))
                           for f in links):
        return subprocess.CompletedProcess([], 1, "", "nonempty submodule directory")
    moved = git(p, "worktree", "move", p, target)
    if not moved.returncode:
        return moved
    # Git refuses even unpopulated submodule pins. An atomic rename plus repair
    # provides the same quarantine, but only empty/absent pins may move forward.
    if "working trees containing submodules cannot be moved or removed" not in moved.stderr or not links or os.path.lexists(target):
        return moved
    admin = git(p, "rev-parse", "--absolute-git-dir")
    if admin.returncode or not admin.stdout.strip():
        return moved
    modules = os.path.join(admin.stdout.strip(), "modules")
    if os.path.lexists(modules) and (not os.path.isdir(modules) or os.path.islink(modules) or os.listdir(modules)):
        return moved  # pruning this worktree must never discard nested Git stores
    common = git(p, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common.returncode or not common.stdout.strip():
        return moved
    os.rename(p, target)
    try:
        repaired = sh("git", "--git-dir", common.stdout.strip(), "worktree", "repair", target)
        if not repaired.returncode:
            return repaired
    except BaseException:
        if not os.path.lexists(p):
            os.rename(target, p)
            sh("git", "--git-dir", common.stdout.strip(), "worktree", "repair", p)
        raise
    if not os.path.lexists(p):
        os.rename(target, p)
        sh("git", "--git-dir", common.stdout.strip(), "worktree", "repair", p)
    return repaired


def finish(p, res, sha, excl, run, remove, held_paths, seats, branch, quarantined=False):
    lane = res["lane"]
    if not remove:
        res["result"] = "committed" if run else "plan"
        return res
    if NO_REMOVE.search(p):
        res["result"] = "kept: live work"
        return res
    if any(s == p or s.startswith(p.rstrip("/") + "/") for s in seats if s):  # a seat cwd at or inside this worktree (a seat rooted above it does not count)
        res["result"] = "kept: a seat's cwd"
        return res
    hit = inside(held_paths, p)
    if hit:
        res["result"] = "kept: process/open file"
        return res
    if not run:
        res["result"] = "would remove"
        return res
    if not quarantined:
        # Atomically remove the public lane path before the last content check.
        # A later writer to the original path cannot write into the directory
        # being retired. Git move preserves dirty files and updates metadata.
        target = p + ".estate-retiring-" + str(os.getpid())
        if os.path.lexists(target) or move_worktree(p, target).returncode:
            res["result"] = "kept: cannot quarantine linked worktree"
            return res
        try:
            result = finish(target, res, sha, excl, run, remove, held_paths, seats, branch, quarantined=True)
        except Exception:
            if os.path.isdir(target) and not os.path.lexists(p):
                move_worktree(target, p, restore=True)
            raise
        if os.path.isdir(target) and not os.path.lexists(p):
            restored = move_worktree(target, p, restore=True)
            if restored.returncode:
                result["result"] = "held: quarantined worktree could not restore original path"
                result["held_worktree"] = target
        return result
    # Fresh gates after the network/scan: no process, no new HEAD, no changed content.
    if inside(open_paths(), p) or inside(seat_cwds(), p):
        res["result"] = "kept: became busy"
        return res
    if git(p, "merge-base", "--is-ancestor", "HEAD", sha).returncode:
        res["result"] = "kept: HEAD changed after snapshot"
        return res
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(td, "verify-index"))
        loaded = git(p, "read-tree", sha, env=env)
        if loaded.returncode:
            res["result"] = "kept: cannot read saved snapshot"
            return res
        # read-tree does not carry the worktree index's sparse flags into a new
        # index. Only absent paths already marked skip-worktree may stay absent;
        # a file recreated outside the cone must still be compared with the save.
        sparse = git(p, "ls-files", "-v", "-z")
        if sparse.returncode:
            res["result"] = "kept: cannot read sparse preservation state"
            return res
        absent = [entry[2:] for entry in sparse.stdout.split("\0")
                  if entry and entry[0].upper() == "S" and not os.path.lexists(os.path.join(p, entry[2:]))]
        if absent:
            marked = subprocess.run(["git", "-C", p, "update-index", "--skip-worktree", "-z", "--stdin"],
                                    input="\0".join(absent) + "\0", capture_output=True, text=True, env=env)
            if marked.returncode:
                res["result"] = "kept: cannot verify sparse preservation state"
                return res
        verify = git(p, "diff", "--name-only", "-z", sha, "--", env=env)
    differ = [f for f in verify.stdout.split("\0") if f and f not in excl]
    if verify.returncode or differ:
        res["result"] = "kept: contents changed after snapshot"
        return res
    for root, dirs, files in os.walk(p):
        if root != p and (".git" in dirs or ".git" in files):
            res["result"] = "kept: nested repository"
            return res
        dirs[:] = [d for d in dirs if d != ".git"]
    # Ignored files are not all disposable (screenshots, notes, .env). Hold every
    # non-regenerable ignored/untracked file outside git, without logging its name.
    extras = git(p, "ls-files", "--others", "-z")
    if extras.returncode:
        res["result"] = "kept: cannot inventory ignored files"
        return res
    saved = set(git(p, "ls-tree", "-r", "--name-only", "-z", sha).stdout.split("\0"))
    hold = set(excl)
    for rel in extras.stdout.split("\0"):
        if not rel:
            continue
        if rel in saved:
            fp = os.path.join(p, rel)
            blob = git(p, "rev-parse", sha + ":" + rel)
            actual = (sh("git", "-C", p, "hash-object", "--", fp).stdout.strip()
                      if not os.path.islink(fp) else "")
            if os.path.islink(fp):
                actual = subprocess.run(["git", "hash-object", "--stdin"], input=os.readlink(fp), capture_output=True, text=True).stdout.strip()
            if blob.returncode or actual != blob.stdout.strip():
                res["result"] = "kept: untracked contents changed after snapshot"
                return res
        elif not any(part in REGEN for part in rel.split("/")):
            hold.add(rel)
    dest = os.path.join(HELD, lane, sha[:12])
    # Never overwrite an earlier held file. A conflict leaves the entire tree intact.
    if any(os.path.lexists(os.path.join(dest, f)) for f in hold if os.path.lexists(os.path.join(p, f))):
        res["result"] = "kept: held destination already exists"
        return res
    common = git(p, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if not common or common.startswith(p.rstrip("/") + "/"):
        res["result"] = "kept: primary git store is inside worktree"
        return res
    for rel in sorted(hold):
        src = os.path.join(p, rel)
        if os.path.lexists(src):
            dst = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
    # Removal is authorised only after the pushed commit matches all remaining
    # authored files; plain git remove cannot remove a preserved dirty checkout.
    shutil.rmtree(p)
    pruned = sh("git", "--git-dir", common, "worktree", "prune")
    if pruned.returncode:
        res["metadata"] = "prune pending; pushed work preserved"
    if os.path.exists(p):
        res["result"] = "remove-failed"
        return res
    os.makedirs(os.path.dirname(REMOVED), exist_ok=True)
    with open(REMOVED, "a") as f:
        f.write(json.dumps({"t": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "machine": MACHINE, "path": res["path"],
                            "why": f"worktree retired after wip commit pushed to {branch} ({sha[:9]}); held files in {dest if excl else 'none'} (Shaan 20 Oct)"}) + "\n")
    res["result"] = "removed"
    res["sha"] = sha[:9]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--no-remove", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--shard", help="i/n: take every n-th worktree starting at i")
    a = ap.parse_args()
    seats = seat_cwds()
    lst = a.only or worktrees()
    if a.shard:
        i, n = map(int, a.shard.split("/"))
        lst = lst[i::n]
    held_paths, stamp = set(), 0
    for p in lst:
        if time.time() - stamp > 90:  # one lsof per 90 s, not per worktree
            held_paths, stamp = open_paths(), time.time()
        try:
            r = process(p, a.run, not a.no_remove, held_paths, seats)
        except Exception as e:  # one bad worktree never stops the sweep
            r = {"path": p, "result": f"error: {e}"[:200]}
        print(json.dumps(r), flush=True)
        if a.run:
            receipt(**r)


if __name__ == "__main__":
    main()
