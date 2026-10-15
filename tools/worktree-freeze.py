#!/usr/bin/env python3
"""Freeze idle worktrees (dirty or not) onto the mini's vault drive, verified, then free their checkout.

  worktree-freeze.py REPO/LANE ...          the plan: each named worktree, idle or why it stays
  worktree-freeze.py REPO/LANE ... --run    for each: pin HEAD in its repo at refs/estate-kept/<repo>/<lane> unless it is
                                            on a remote branch or in main; commit every file of the folder (tracked, the
                                            uncommitted edits as they are, untracked, ignored files that do not rebuild)
                                            into <vault>/frozen.git at refs/frozen/<repo>/<lane>; verify by content;
                                            then remove the folder and `git worktree prune` in its repo

Nothing in the lane's repo is staged, reset or stashed (ADR 0009): its branch and commits stay where they are, and the
checkout comes back exactly with
  git -C <repo> worktree add <path> <branch or pinned ref>   then
  git --git-dir=<vault>/frozen.git --work-tree=<path> checkout refs/frozen/<repo>/<lane> -- .
Stays: a process inside, any file outside .git/node_modules written in the last 72 h, git cannot read it (use
worktree-orphans.py). Run over ssh on the mini (launchd cannot open the drive). Python 3.9.
Agent Zero, 14 Oct: the mini hosts the research seats; 60 GB free.
"""
import argparse, importlib.util, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("orph", os.path.join(HERE, "worktree-orphans.py"))
orph = importlib.util.module_from_spec(spec); spec.loader.exec_module(orph)
WS, WT = orph.WS, orph.WT
VAULT = "/Volumes/SISO-STORAGE-VAULT"
HOT_MIN = 72 * 60


def g(p, *a):
    return subprocess.run(["git", "-C", p, *a], capture_output=True, text=True)


def why_stay(p, busy):
    if not os.path.isfile(os.path.join(p, ".git")):
        return "not a linked worktree"
    if g(p, "status", "--porcelain").returncode:
        return "git cannot read it (worktree-orphans.py)"
    if any(b == p or b.startswith(p + "/") for b in busy):
        return "a process is inside"
    hot = subprocess.run(["find", p, "(", "-name", ".git", "-o", "-name", "node_modules", ")", "-prune", "-o",
                          "-type", "f", "-mmin", "-%d" % HOT_MIN, "-print", "-quit"], capture_output=True, text=True).stdout.strip()
    if hot:
        return "written in the last 72 h (%s)" % hot
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("lanes", nargs="+", metavar="REPO/LANE")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.run and (" on %s (" % VAULT) not in subprocess.run(["mount"], capture_output=True, text=True).stdout:
        print("vault drive not mounted: nothing frozen")
        return 1
    vault = os.path.join(VAULT, "cold-storage", "mini", "%s-frozen-worktrees" % time.strftime("%Y-%m-%d"))
    store = os.path.join(vault, "frozen.git")
    busy, free0, done = orph.held(), shutil.disk_usage(WS).free, []
    for rl in a.lanes:
        repo, lane = rl.split("/", 1)
        p = os.path.join(WT, repo, lane)
        if not os.path.isdir(p):
            print("skip   %s: no such worktree" % rl)
            continue
        w = why_stay(p, busy)
        kib = orph.du_kib(p)
        if w:
            print("keep   %s  %.2f GiB: %s" % (rl, kib / 1048576, w))
            continue
        print("%s %s  %.2f GiB" % ("freeze" if a.run else "would ", rl, kib / 1048576))
        if not a.run:
            continue
        main_repo = os.path.dirname(g(p, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
        head = g(p, "rev-parse", "HEAD").stdout.strip()
        branch = g(p, "branch", "--show-current").stdout.strip()
        pinned = ""
        on_remote = bool(g(p, "branch", "-r", "--contains", "HEAD").stdout.strip())
        if not on_remote:  # local-only commits: a ref in the repo keeps them reachable once the checkout is gone
            pinned = "refs/estate-kept/%s/%s" % (repo, lane)
            if g(main_repo, "update-ref", pinned, head).returncode or g(main_repo, "rev-parse", pinned).stdout.strip() != head:
                print("  KEPT: could not pin %s" % pinned)
                continue
        if not os.path.isdir(store):
            os.makedirs(vault, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", store], check=True)
        c, err = orph.vault_one(store, repo, lane, p, "worktree of %s at %s (%s)" % (main_repo, head[:12], branch or "detached"))
        if err:
            print("  KEPT: " + err)
            continue
        shutil.rmtree(p)
        g(main_repo, "worktree", "prune")
        done.append((rl, c, kib, branch, head, pinned, main_repo))
        with open(orph.RECEIPTS, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": "idle worktree frozen to the vault drive",
                                "path": os.path.relpath(p, WS), "bytes": kib * 1024, "repo": main_repo, "branch": branch,
                                "head": head, **({"pinned": pinned} if pinned else {}),
                                "why": "mini data disk for the research seats (Agent Zero, 14 Oct); idle 72 h, no process",
                                "proof": "content-identical commit %s at refs/frozen/%s in %s" % (c, rl, store),
                                "by": "ESTATE tools/worktree-freeze.py"}) + "\n")
    if done:
        new = not os.path.exists(os.path.join(vault, "MANIFEST.md"))
        with open(os.path.join(vault, "MANIFEST.md"), "a") as f:
            if new:
                f.write("# Idle worktrees frozen off the mini's internal disk (ESTATE, tools/worktree-freeze.py)\n\n"
                        "Each worktree's whole folder (uncommitted edits as they were) is one commit in `frozen.git` at "
                        "`refs/frozen/<repo>/<lane>`, verified by content before removal. Branches and commits stayed in "
                        "each repo; local-only HEADs are pinned at refs/estate-kept/<repo>/<lane>.\n**Restore:** "
                        "`git -C <repo> worktree add <path> <branch or pinned ref>`, then `git --git-dir=frozen.git "
                        "--work-tree=<path> checkout refs/frozen/<repo>/<lane> -- .`\n\n")
            for rl, c, kib, branch, head, pinned, main_repo in done:
                f.write("- %s  %.2f GiB  commit %s  repo %s  %s\n" % (rl, kib / 1048576, c[:12], main_repo,
                        "branch %s" % branch if branch else "detached %s" % head[:12]) + ("  pinned %s" % pinned if pinned else "") + "\n")
        print("froze %d; free %+.2f GiB" % (len(done), (shutil.disk_usage(WS).free - free0) / 2**30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
