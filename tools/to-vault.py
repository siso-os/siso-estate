#!/usr/bin/env python3
"""Move a cold folder off the mini's internal disk onto its encrypted vault drive, verified, with a pointer left behind.

  to-vault.py PATH [PATH ...]          (folders or single files) the plan: size, last write, open files, git links that would break
  to-vault.py PATH [PATH ...] --run    rsync -a to /Volumes/SISO-STORAGE-VAULT/cold-storage/mini/<date>-<name>/, verify
                                       with a checksum pass (rsync -ac --dry-run lists nothing), leave <name>.IN-VAULT.md
                                       beside the old place, a receipt line, then remove the source

Refuses: a folder a process holds, one written in the last 72 h, a git repo whose worktrees live outside it (or a
worktree whose repo lives outside it), and anything when the drive is not mounted. Run it over ssh, never from launchd
(macOS privacy blocks launchd from the external drive). Restore: rsync -a <vault path>/ <old path>/.
Shaan via Agent Zero, 14 Oct: the mini hosts the research seats; 60 GB free on its data disk. Python 3.9.
"""
import argparse, glob, importlib.util, json, os, plistlib, shutil, subprocess, sys, tempfile, time

VAULT = "/Volumes/SISO-STORAGE-VAULT"
DEST = os.path.join(VAULT, "cold-storage", "mini")
RECEIPTS = os.environ.get("ESTATE_REMOVED") or os.path.expanduser("~/.local/state/estate-removed.jsonl")
HOT_HOURS = 72


def run(*a, **k):
    return subprocess.run(a, capture_output=True, text=True, **k)


def mounted():
    if (" on %s (" % VAULT) not in run("mount").stdout:
        return False
    result = run("diskutil", "info", "-plist", VAULT)
    try:
        return result.returncode == 0 and plistlib.loads(result.stdout.encode()).get("FileVault") is True
    except (ValueError, plistlib.InvalidFileException):
        return False


def held(p):
    result = run("lsof", "-u", str(os.getuid()), "-Fn", "-w")
    if result.returncode or not result.stdout:
        raise RuntimeError("cannot observe open files; source retained")
    out = result.stdout
    return sorted({l[1:] for l in out.splitlines() if l.startswith("n" + p + "/") or l == "n" + p})


def newest(p):
    if HOT_HOURS <= 0:
        return None
    # a fetch, a remote ref or an estate pin is not work; a commit, checkout or edit is (HEAD, logs/HEAD, index, files)
    skip = []
    for pat in ("*/.git/objects", "*/.git/index", "*/.git/refs/remotes", "*/.git/refs/estate-kept", "*/.git/logs/refs/remotes"):
        skip += ["-path", pat, "-prune", "-o"]
    hot = run("find", p, *skip, "-type", "f", "-mmin", "-%d" % (HOT_HOURS * 60), "-not", "-name", "FETCH_HEAD",
              "-print", "-quit").stdout.strip()
    return hot


def git_links(p):
    """Worktree links that cross the folder's edge, either way: moving would break them."""
    bad = []
    for g in run("find", p, "-name", ".git").stdout.split():
        if os.path.isdir(g):
            binding = run('git', '--git-dir', g, 'config', '--path', '--get', 'core.worktree')
            if binding.returncode not in (0, 1):
                bad.append('Git worktree binding unavailable')
            elif binding.returncode == 0:
                value = binding.stdout.strip()
                target = os.path.realpath(os.path.join(g, value))
                if os.path.isabs(value) or not target.startswith(os.path.realpath(p) + '/'):
                    bad.append('Git worktree binding cannot safely follow the transfer')
        alternate = os.path.join(g, 'objects', 'info', 'alternates')
        if os.path.isfile(alternate):
            with open(alternate) as f:
                for line in f:
                    target = os.path.realpath(os.path.join(g, 'objects', line.strip()))
                    if line.strip() and not target.startswith(os.path.realpath(p) + '/'):
                        bad.append('Git object store outside the transfer root')
        if os.path.isfile(g):  # a worktree: its repo must be inside too
            gd = open(g).read().strip().split(": ", 1)[-1]
            gd = os.path.join(os.path.dirname(g), gd)  # relative gitdirs are relative to the .git file
            if not os.path.realpath(gd).startswith(os.path.realpath(p) + "/"):
                bad.append("%s -> %s" % (g, gd))
        elif os.path.isdir(os.path.join(g, "worktrees")):  # a repo: its worktrees must be inside too
            for w in glob.glob(os.path.join(g, "worktrees", "*", "gitdir")):
                wt = os.path.join(os.path.dirname(w), open(w).read().strip())
                if not os.path.realpath(wt).startswith(os.path.realpath(p) + "/"):
                    bad.append("%s has worktree %s" % (g, wt))
    return bad


def kib(p):
    out = run("du", "-sk", p).stdout
    return int(out.split()[0]) if out else 0


def checked_transfer(p, dest, snapshot=None, verify_existing=False):
    """Fresh destination, checked rsync exits, quarantine and fresh lsof before release."""
    isfile = os.path.isfile(p)
    if verify_existing:
        if not os.path.isdir(dest) or os.path.islink(dest):
            return False
    else:
        os.makedirs(dest, exist_ok=False)
    if held(p):
        print('  KEPT: source acquired an open handle before quarantine', flush=True)
        return False
    quarantine = p + ".estate-retiring-" + str(os.getpid())
    if os.path.lexists(quarantine):
        return False
    os.rename(p, quarantine)
    try:
        if held(quarantine):
            print('  KEPT: source became active during quarantine', flush=True)
            return False
        source = quarantine if isfile else quarantine + "/"
        destination = os.path.join(dest, os.path.basename(p)) if isfile else dest + "/"
        if not verify_existing:
            # Copy the isolated tree, so a census/status poll cannot rewrite
            # Git's index halfway through a long copy. Later writers at p stay.
            copy = run("nice", "-n", "19", "rsync", "-a", source, destination)
            if copy.returncode:
                return False
        verify = run("nice", "-n", "19", "rsync", "-acni", "--delete", source, destination)
        if verify.returncode or verify.stdout.strip():
            print('  KEPT: checksum command exit %d; %d changed entries' % (verify.returncode, len(verify.stdout.splitlines())), flush=True)
            return False
        if held(quarantine):
            print('  KEPT: quarantined source has an open handle', flush=True)
            return False
        if snapshot and not snapshot_matches(quarantine, snapshot):
            print('  KEPT: quarantined authored contents or refs differ from the snapshot', flush=True)
            return False
        os.remove(quarantine) if isfile else shutil.rmtree(quarantine)
        return True
    finally:
        if os.path.lexists(quarantine) and not os.path.lexists(p):
            os.rename(quarantine, p)


def retired_services():
    """Explicit service-retirement manifests, rather than a folder's name or age."""
    paths = []
    root = os.path.expanduser("~/SISO_Workspace/_archive")
    for path in glob.glob(root + "/*"):
        manifest = os.path.join(path, "MANIFEST.md")
        if not os.path.isfile(manifest):
            continue
        text = open(manifest).read()
        if "ARCHIVED_UNREGISTERED" in text:
            import re
            names = set(re.findall(r"runner=(siso-mini-halo-\d+)", text))
            status = run("gh", "api", "repos/HALO-AGENCY/halo-streaming/actions/runners", "--paginate", "--jq", ".runners[].name")
            if not names or status.returncode or names & set(status.stdout.splitlines()):
                continue
            paths.append(path)
        elif "HALO off the Mac mini" in text and "both ran clean on the box before these were unloaded" in text:
            paths.append(path)
    return paths


def pnpm_generations():
    """Offload inactive store generations; preserve local-tarball packages too."""
    active = run('pgrep', '-f', r'(^|/)(pnpm|corepack).* (install|add|fetch|store|dlx|update)( |$)|pnpm\.cjs.* (install|add|fetch|store|dlx|update)( |$)')
    if active.returncode != 1:
        return []
    current = run('pnpm', 'store', 'path')
    if current.returncode:
        return []
    current = os.path.realpath(current.stdout.strip())
    root = os.path.expanduser('~/Library/pnpm/store')
    if os.path.dirname(current) != root:
        return []
    return [p for p in glob.glob(root + '/v[0-9]*') if os.path.isdir(p) and not os.path.islink(p) and os.path.realpath(p) != current]


def wip_tool():
    spec = importlib.util.spec_from_file_location('vault_wip', os.path.join(os.path.dirname(__file__), 'worktree-wip.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot_matches(path, sha):
    wip = wip_tool()
    if not wip.already_remote(path, clean=False):
        return False
    with tempfile.TemporaryDirectory(prefix='.siso-ephemeral-vault-index-') as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, 'index'))
        if wip.git(path, 'read-tree', sha, env=env).returncode or wip.git(path, 'diff', '--quiet', sha, '--', env=env).returncode:
            return False
        extras = wip.git(path, 'ls-files', '--others', '--exclude-standard', '-z', env=env)
        if extras.returncode:
            return False
        return not any(name and not wip.keep_out(name, os.lstat(os.path.join(path, name)).st_size)
                       for name in extras.stdout.split('\0'))


def preserve_history(path, snapshot, wip, owner, name):
    """One WIP merge commit keeps every unpublished branch/tag history reachable."""
    if wip.already_remote(path, clean=False):
        return snapshot
    stashes = wip.git(path, 'stash', 'list', '--format=%H')
    advertised = wip.git(path, 'ls-remote', 'origin')
    local = wip.git(path, 'for-each-ref', '--format=%(objectname)', 'refs/heads', 'refs/tags')
    if stashes.returncode or stashes.stdout.strip() or advertised.returncode or local.returncode:
        return None
    known = {line.split()[0] for line in advertised.stdout.splitlines() if line.split()} & set(local.stdout.splitlines())
    # Include local remote refs in the fresh coverage proof, as already_remote does.
    cached = wip.git(path, 'for-each-ref', '--format=%(objectname)', 'refs/remotes')
    if cached.returncode:
        return None
    known |= {line.split()[0] for line in advertised.stdout.splitlines() if line.split()} & set(cached.stdout.splitlines())
    missing = wip.git(path, 'rev-list', 'HEAD', '--branches', '--tags', '--not', *sorted(known))
    if missing.returncode:
        return None
    uncovered = set(missing.stdout.splitlines())
    parents = [snapshot]
    for tip in sorted(set(local.stdout.splitlines())):
        commit = wip.git(path, 'rev-parse', tip + '^{commit}')
        if commit.returncode:
            return None
        value = commit.stdout.strip()
        if value in uncovered and value not in parents:
            parents.append(value)
    tree = wip.git(path, 'rev-parse', snapshot + '^{tree}')
    if tree.returncode:
        return None
    args = ['-c', 'user.name=SISO Estate', '-c', 'user.email=estate@siso.local', 'commit-tree', tree.stdout.strip()]
    for parent in parents:
        args += ['-p', parent]
    result = wip.git(path, *args, '-m', 'estate WIP: preserve all local histories before vault transfer')
    sha = result.stdout.strip()
    if result.returncode or wip.leaks(path, sha):
        return None
    branch = 'estate/wip-' + wip.DAY.replace('-', '') + '-vault-history-' + sha[:16]
    old = wip.git(path, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + branch)
    if old.returncode not in (0, 1) or old.returncode == 0 and old.stdout.strip() != sha:
        return None
    if wip.git(path, 'update-ref', 'refs/heads/' + branch, sha).returncode:
        return None
    if not wip.push_wip(path, branch, owner, name):
        return None
    remote = wip.git(path, 'ls-remote', 'origin', 'refs/heads/' + branch)
    if remote.returncode or not remote.stdout.split() or remote.stdout.split()[0] != sha or not wip.already_remote(path, clean=False):
        return None
    wip.receipt(path=path, result='all local histories pushed and verified', sha=sha, branch=branch, parents=len(parents))
    return sha


def cold_clones(paths):
    wip = wip_tool()
    roots = paths or glob.glob(os.path.join(wip.WS, '_data/worktrees/*/*'))
    return [p for p in roots if os.path.isdir(os.path.join(p, '.git')) and
            not wip.PRIVATE.search(p) and not wip.NO_REMOVE.search(p)]


def main():
    global HOT_HOURS
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--idle-hours", type=float, default=72)
    ap.add_argument("--retired-services", action="store_true")
    ap.add_argument("--pnpm-generations", action="store_true")
    ap.add_argument("--cold-clones", action="store_true", help='push verified primary-clone WIP before vault transfer')
    ap.add_argument('--verify-existing', help='verify an earlier complete copy without overwriting it')
    ap.add_argument('--snapshot', help='previously pushed commit for an existing-copy recovery')
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.idle_hours < 0:
        ap.error('--idle-hours must be nonnegative')
    HOT_HOURS = a.idle_hours
    if not mounted():
        print("vault drive not mounted: nothing moved")
        return 1
    free0 = shutil.disk_usage(os.path.expanduser("~")).free
    paths = retired_services() if a.retired_services else pnpm_generations() if a.pnpm_generations else cold_clones(a.paths) if a.cold_clones else a.paths
    if a.verify_existing:
        if not a.cold_clones or len(paths) != 1 or not a.snapshot or not __import__('re').fullmatch(r'[0-9a-f]{40}', a.snapshot):
            ap.error('--verify-existing requires one cold clone and its pushed --snapshot')
        if not os.path.realpath(a.verify_existing).startswith(os.path.realpath(DEST) + '/'):
            ap.error('existing copy must be under the mounted encrypted estate vault')
    elif a.snapshot:
        ap.error('--snapshot requires --verify-existing')
    for p in [os.path.abspath(os.path.expanduser(x)).rstrip("/") for x in paths]:
        if os.path.islink(p) or not (os.path.isdir(p) or os.path.isfile(p)):
            print("skip   %s: not a folder or file" % p)
            continue
        isfile = os.path.isfile(p)
        why = []
        h = held(p)
        if h:
            why.append("a process holds %d files (e.g. %s)" % (len(h), h[0]))
        n = newest(p)
        if n:
            why.append("written in the last %g h (%s)" % (HOT_HOURS, n))
        why += [] if isfile else ["git link: " + b for b in git_links(p)]
        size = kib(p)
        if why:
            print("keep   %s  %.1f GiB: %s" % (p, size / 1048576, "; ".join(why)))
            continue
        dest = os.path.join(DEST, "%s-%s-%s-%d" % (time.strftime("%Y-%m-%d"), os.path.basename(os.path.dirname(p)), os.path.basename(p), time.time_ns()))
        if a.verify_existing:
            dest = os.path.abspath(a.verify_existing)
        print("%s %s  %.1f GiB -> %s" % ("move  " if a.run else "would ", p, size / 1048576, dest))
        if not a.run:
            continue
        snapshot = a.snapshot
        if a.cold_clones:
            wip = wip_tool()
            if snapshot:
                auth = wip.process(p, False, False, wip.open_paths(), wip.seat_cwds())
                if auth.get('result') != 'would preserve':
                    print('  KEPT: existing-copy source is not authorized and idle')
                    continue
            else:
                result = wip.process(p, True, False, wip.open_paths(), wip.seat_cwds())
                wip.receipt(**result)
                if result.get('result') != 'committed' or not (result.get('pushed') or result.get('preserved')):
                    print('  KEPT: primary WIP not verified')
                    continue
                snapshot = result.get('sha') or wip.git(p, 'rev-parse', 'HEAD').stdout.strip()
            url = wip.git(p, 'remote', 'get-url', 'origin')
            match = __import__('re').search(r'github\.com[:/](sisodias|lordsisodia|siso-os|halo-agency)/([^/]+?)(?:\.git)?$', url.stdout.strip(), __import__('re').I)
            if url.returncode or not match:
                print('  KEPT: original remote authorization unavailable')
                continue
            snapshot = preserve_history(p, snapshot, wip, match.group(1).lower(), match.group(2))
            if not snapshot or not snapshot_matches(p, snapshot):
                print('  KEPT: authored data is not covered by the pushed snapshot')
                continue
        if not checked_transfer(p, dest, snapshot=snapshot, verify_existing=bool(a.verify_existing)):
            print("  KEPT: copy, checksum or fresh inactivity proof failed")
            continue
        with open(os.path.join(os.path.dirname(p), os.path.basename(p) + ".IN-VAULT.md"), "w") as f:
            f.write("# %s is in the vault\n\nMoved %s by ESTATE (tools/to-vault.py), checksum-verified, %.1f GiB.\n"
                    "Vault: `%s` (the mini's encrypted SISO-STORAGE-VAULT drive).\nRestore: `rsync -a '%s/' '%s'`\n"
                    % (os.path.basename(p), time.strftime("%Y-%m-%d %H:%M"), size / 1048576, dest, dest, os.path.dirname(p) + "/" if isfile else p + "/"))
        os.makedirs(os.path.dirname(RECEIPTS), exist_ok=True)
        with open(RECEIPTS, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": "moved to the vault drive", "path": p,
                                "bytes": size * 1024, "to": dest, "why": "mini data disk for the research seats",
                                "proof": "rsync -ac --dry-run: 0 files differ", "by": "ESTATE tools/to-vault.py"}) + "\n")
    print("free %+.1f GiB" % ((shutil.disk_usage(os.path.expanduser("~")).free - free0) / 2**30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
