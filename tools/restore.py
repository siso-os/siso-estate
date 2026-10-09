#!/usr/bin/env python3
"""Rebuild a machine's repos from the umbrella's restore map (.estate/repos.json).

  restore.py [--map ~/SISO_Workspace/.estate/repos.json] [--only SUBSTR] [--run]

With --only, repos that are on the map but on no machine (GitHub only, or retired) are restored too, from
.estate/map.json, into their empty folder; their saved local work is on origin/estate/<date>/* branches.

Without --run it prints what it would do. For each repo in the map (worktrees are skipped; recreate
them with `git worktree add` from their main repo):
  1. clone `url` into `path` if the folder is missing (an existing checkout is left completely alone);
  2. fetch the backup refs (refs/backup/<machine>/<slug>/*) into refs/estate-restore/*, when the map has them;
  3. fetch the overlay bundle into refs/estate-restore/*, when the map has one;
  4. check out the recorded HEAD, or the backed-up WIP commit if one exists (as a detached HEAD, so
     nothing is silently merged; the branch names are in refs/estate-restore/heads/*);
  5. once every repo is in place, put its key files (.env and the like) back from the credentials store,
     ~/SISO_Workspace/.credentials/projects/<map path>/ (missing files only; nothing is overwritten).
"""
import argparse, json, os, shutil, subprocess, sys

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
CRED = os.path.join(WS, ".credentials", "projects")


def run(cmd, go):
    print("  $", " ".join(cmd))
    if go:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print("    !", (r.stderr or r.stdout).strip()[-300:])
        return r.returncode == 0
    return True


def only_parent_files(path):
    """True when everything in PATH is tracked by the repo that contains it (a block's signpost, e.g. manifest.md)."""
    for root, dirs, files in os.walk(path):
        for f in files:
            if subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", f], capture_output=True).returncode:
                return False
    return True


def keys_back(path, others, go):
    """Copy the repo's key files from ~/SISO_Workspace/.credentials/projects/<map path>/ into the checkout (missing ones only).
    Files inside another repo on the map that is not checked out here are skipped: that repo gets them when it is restored."""
    src = os.path.join(CRED, os.path.relpath(path, WS))
    n = 0
    for root, _, files in os.walk(src):
        for f in files:
            dst = os.path.join(path, os.path.relpath(os.path.join(root, f), src))
            if os.path.exists(dst) or any(o != path and o.startswith(path + "/") and dst.startswith(o + "/")
                                          and not os.path.exists(os.path.join(o, ".git")) for o in others):
                continue
            n += 1
            if go:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(root, f), dst)
                os.chmod(dst, 0o600)
    if n:
        print(f"  {os.path.relpath(path, WS)}: {n} key file(s) {'put back' if go else 'would come back'} from the credentials store")


def default_branch(url):
    out = subprocess.run(["git", "ls-remote", "--symref", url, "HEAD"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("ref: refs/heads/"):
            return line.split()[1][len("refs/heads/"):]
    return "main"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=os.path.join(WS, ".estate", "repos.json"))
    ap.add_argument("--only")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    m = json.load(open(a.map))
    todo = [r for r in m["repos"] if r["kind"] != "worktree" and (not a.only or a.only in r["path"])]
    # repos on the map that no machine checks out (GitHub only, or retired from this laptop): .estate/map.json
    seen = {os.path.expanduser(r["path"]) for r in todo}
    mapj = os.path.join(WS, ".estate", "map.json")
    if a.only and os.path.exists(mapj):
        for mp, e in json.load(open(mapj))["repos"].items():
            if a.only in mp and os.path.join(WS, mp) not in seen and not e.get("empty_on_github"):
                todo.append({"path": "~/SISO_Workspace/" + mp, "url": e["url"], "kind": "repo", "on_github_only": True})
    restored = []
    for r in sorted(todo, key=lambda r: r["path"].count("/")):  # parents before nested repos
        path = os.path.expanduser(r["path"])
        b = r.get("backup") or {}
        print(f"{r['path']}")
        if not r.get("url"):
            print("  (no GitHub copy recorded; skipped)")
            continue
        if os.path.exists(os.path.join(path, ".git")):
            print("  exists; left alone (restore never touches a live checkout)")
            continue
        inplace = os.path.isdir(path) and bool(os.listdir(path))
        if inplace and not only_parent_files(path):
            print("  a non-empty folder without .git is in the way; left alone")
            continue
        if inplace:
            # a block house (ADR 0014): the parent repo tracks its signpost (manifest.md) inside it and the house ignores
            # it, so the house is fetched into the folder in place; the checkout refuses to overwrite anything
            br = default_branch(r["url"])
            if not all(run(c, a.run) for c in (["git", "-C", path, "init", "-q", "-b", br], ["git", "-C", path, "remote", "add", "origin", r["url"]],
                                                ["git", "-C", path, "fetch", "-q", "origin"],
                                                ["git", "-C", path, "checkout", "-q", "-B", br, "--track", f"origin/{br}"])):
                continue
        else:
            if a.run:
                os.makedirs(os.path.dirname(path), exist_ok=True)
            if not run(["git", "clone", "-q", r["url"], path], a.run):
                continue
        if b.get("refs"):
            src = b["refs"]
            run(["git", "-C", path, "fetch", "-q", f"https://github.com/{b['target']}.git",
                 f"+{src}:{src.replace('refs/backup/', 'refs/estate-restore/', 1)}"], a.run)
        if b.get("bundle"):
            bundle = os.path.join(WS, b["bundle"])
            if os.path.exists(bundle) or not a.run:
                run(["git", "-C", path, "fetch", "-q", bundle, "+refs/*:refs/estate-restore/bundle/*"], a.run)
        target = r.get("head")
        if target:
            run(["git", "-C", path, "checkout", "-q", "--detach", target], a.run)
        if r.get("on_github_only") and a.run:
            est = subprocess.run(["git", "-C", path, "branch", "-r", "--list", "origin/estate/*"], capture_output=True, text=True).stdout.split()
            if est:
                print("  work saved when it left a machine (uncommitted edits, unpushed branches):", ", ".join(est))
        restored.append(path)
    # the repo's keys (.env files and the like) come back from the credentials store once every repo is in place, so a
    # key file never sits in a nested repo's folder before that repo is cloned; an existing file is never overwritten
    others = {os.path.expanduser(r["path"]) for r in m["repos"]} | {os.path.join(WS, mp) for mp in
                                                                  (json.load(open(mapj))["repos"] if os.path.exists(mapj) else {})}
    for path in restored:
        keys_back(path, others, a.run)
    if not a.run:
        print("\n(dry run; add --run to do it)")


if __name__ == "__main__":
    main()
