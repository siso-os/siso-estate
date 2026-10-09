#!/usr/bin/env python3
"""Remove a third-party clone only when upstream provably holds it, and keep how to get it back.

  reference-index.py drop PATH... --why "reason" [--dry-run]   prove, record in _reference/INDEX.json, delete
  reference-index.py list                                      what the index holds
  reference-index.py dup PATH --keep HOME --why "reason" [--dry-run]   drop our own second clone of a repo whose live home stays

A `dup` (a sisodias repo checked out twice, ADR 0003) needs the same proof except the origin check: HOME must be a live
checkout of the same GitHub repo, and PATH may hold no stash and no branch commit that origin lacks. It is logged to
machines/<machine>/removed.jsonl (ADR 0004), not to the third-party index.

Proof, per clone, all required: origin is not ours (not sisodias/Lordsisodia); no uncommitted or untracked files; no
ignored files beyond rebuildable build output; `git ls-remote origin` shows a ref whose tip IS the local HEAD (so a
fresh clone at that ref gives these exact bytes back), or, after a fetch, an upstream branch contains HEAD; no running process has its cwd inside. The index entry keeps
url, ref, HEAD, old path, size and the clone command. Every drop also gets a line in
_archive/2026-09-23-estate-hazards/MANIFEST.txt.
"""
import argparse, json, os, re, shutil, subprocess, sys, time

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
INDEX = os.path.join(WS, "_reference", "INDEX.json")
MANIFEST = os.path.join(WS, "_archive", "2026-09-23-estate-hazards", "MANIFEST.txt")
OURS = re.compile(r"github\.com[:/](sisodias|lordsisodia)/", re.I)
REBUILDABLE = re.compile(r"(^|/)(node_modules|\.next|dist|build|\.build|out|target|\.cache|\.turbo|coverage|__pycache__|"
                         r"\.venv|venv|\.pytest_cache|\.mypy_cache|\.lake|\.DS_Store|[^/]+\.egg-info|[^/]+\.pyc)(/|$)")


def git(path, *a, timeout=60):
    r = subprocess.run(["git", "-C", path, *a], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip()


def slug(url):
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url or "")
    return m.group(1).lower() if m else None


def prove(path, ours=False):
    if not os.path.isdir(os.path.join(path, ".git")):
        return None, "not a git clone"
    _, url = git(path, "remote", "get-url", "origin")
    if not url or bool(OURS.search(url)) != ours:
        return None, f"origin is {'not ' if ours else ''}ours or missing: {url!r}"
    _, head = git(path, "rev-parse", "HEAD")
    _, wts = git(path, "worktree", "list", "--porcelain")
    linked = [l.split(" ", 1)[1] for l in wts.splitlines() if l.startswith("worktree ")][1:]
    if linked:  # removing the repo orphans them (24 Sep: a playbook lane in _data/worktrees lost its gitdir)
        return None, f"{len(linked)} linked worktrees: {linked[:2]}"
    _, st = git(path, "status", "--porcelain")
    if st:
        return None, f"{len(st.splitlines())} uncommitted/untracked files"
    if ours:
        _, stash = git(path, "stash", "list")
        git(path, "fetch", "--quiet", "--no-tags", "origin", timeout=300)
        _, ahead = git(path, "rev-list", "--branches", "--not", "--remotes=origin")
        if stash or ahead:
            return None, f"{len(stash.splitlines())} stashes, {len(ahead.splitlines())} branch commits not on origin"
    _, ign = git(path, "status", "--porcelain", "--ignored")
    local = [l[3:] for l in ign.splitlines() if l.startswith("!!") and not REBUILDABLE.search(l[3:])]
    if local:
        return None, f"ignored local files: {local[:3]}"
    code, refs = git(path, "ls-remote", "origin", timeout=60)
    if code:
        return None, "ls-remote failed"
    hit = [l.split("\t")[1] for l in refs.splitlines() if l.startswith(head)]
    if not hit:  # upstream moved on: fetch it and accept HEAD if an upstream branch still contains it
        code, _ = git(path, "fetch", "--quiet", "--no-tags", "origin", timeout=300)
        _, contains = git(path, "branch", "-r", "--contains", "HEAD")
        hit = ["refs/heads/" + b.strip().split("origin/", 1)[1] for b in contains.splitlines()
               if b.strip().startswith("origin/") and "->" not in b]
        if code or not hit:
            return None, "upstream has no branch that contains this HEAD"
    ref = next((h for h in hit if h.startswith("refs/heads/")), hit[0])
    r = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fn"], capture_output=True, text=True)
    if any(l[1:] == path or l[1:].startswith(path + "/") for l in r.stdout.splitlines() if l.startswith("n")):
        return None, "a process has its cwd inside"
    kb = int(subprocess.run(["du", "-sk", path], capture_output=True, text=True).stdout.split()[0])
    return {"path": path, "url": url, "ref": ref, "head": head, "kb": kb,
            "restore": f"git clone {url} '{path}' && git -C '{path}' checkout {head}"}, None


def load():
    try:
        return json.load(open(INDEX))
    except FileNotFoundError:
        return {"_what": "Third-party code that is not on this disk but provably on its upstream: how to get each back. "
                         "Written by SISO_Agents/siso-estate/tools/reference-index.py.", "removed": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["drop", "dup", "list"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--why")
    ap.add_argument("--keep", help="dup: the live home that stays")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    idx = load()
    if a.cmd == "list":
        for e in idx["removed"]:
            print(f"{e['kb'] / 1e6:6.2f} GB  {e['path'].replace(HOME, '~')}  {e['url']} @ {e['head'][:10]}")
        return
    if not a.why:
        sys.exit("--why is required")
    if a.cmd == "dup":
        return dup(a)
    freed = 0
    for p in a.paths:
        p = os.path.abspath(os.path.expanduser(p))
        e, err = prove(p)
        if err:
            print(f"KEEP {p.replace(HOME, '~')}: {err}")
            continue
        print(f"{'WOULD DROP' if a.dry_run else 'DROP'} {p.replace(HOME, '~')} ({e['kb'] / 1e6:.2f} GB, {e['url']} {e['ref']} = {e['head'][:10]})")
        if a.dry_run:
            continue
        e.update({"removed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "why": a.why})
        idx["removed"].append(e)
        os.makedirs(os.path.dirname(INDEX), exist_ok=True)
        json.dump(idx, open(INDEX, "w"), indent=1)
        shutil.rmtree(p)
        freed += e["kb"]
        with open(MANIFEST, "a") as m:
            m.write(f"{e['removed_at']} DROPPED third-party clone {p.replace(HOME, '~')} ({e['kb'] / 1e6:.2f} GB): upstream "
                    f"{e['url']} {e['ref']} holds its HEAD {e["head"]}. {a.why}. Restore: {e['restore']}\n")
    print(f"freed {freed / 1e6:.2f} GB")


def dup(a):
    keep = os.path.abspath(os.path.expanduser(a.keep or ""))
    _, kurl = git(keep, "remote", "get-url", "origin") if os.path.isdir(keep) else (1, "")
    machine = os.environ.get("ESTATE_MACHINE", "laptop")
    log = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "machines", machine, "removed.jsonl")
    for p in a.paths:
        p = os.path.abspath(os.path.expanduser(p))
        e, err = prove(p, ours=True)
        if not err and (p == keep or keep.startswith(p + "/") or not slug(kurl) or slug(kurl) != slug(e["url"])):
            err = f"--keep {keep.replace(HOME, '~')} is not a separate live checkout of {e['url']}"
        if err:
            print(f"KEEP {p.replace(HOME, '~')}: {err}")
            continue
        _, khead = git(keep, "rev-parse", "HEAD")
        print(f"{'WOULD DROP' if a.dry_run else 'DROP'} {p.replace(HOME, '~')} ({e['kb'] / 1e6:.2f} GB, second clone of "
              f"{slug(e['url'])}; home {keep.replace(HOME, '~')})")
        if a.dry_run:
            continue
        shutil.rmtree(p)
        with open(log, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "path": p.replace(HOME, "~"), "bytes": e["kb"] * 1024,
                                "why": a.why, "proof": f"second clone of {slug(e['url'])}: clean, no stash, every branch on "
                                f"origin; HEAD {e['head'][:10]} is origin {e['ref']}; the home {keep.replace(HOME, '~')} "
                                f"stays (HEAD {khead[:10]})", "restore": e["restore"]}) + "\n")


if __name__ == "__main__":
    main()
