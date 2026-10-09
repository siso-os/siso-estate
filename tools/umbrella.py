#!/usr/bin/env python3
"""Build the estate map (sisodias/siso-city): ~/SISO_Workspace on every machine is a checkout of the same private repo.

  umbrella.py build [--machine laptop]    write .gitmodules, gitlinks and .estate/repos.json (no commit)
  umbrella.py snapshot [--push]           re-pin every repo at its current HEAD, commit, optionally push

What the umbrella records:
  - a gitlink (submodule) for every top-most repo under ~/SISO_Workspace, with `ignore = dirty` so
    other agents' edits never show up as umbrella noise;
  - .estate/repos.json: every repo on the machine (nested ones too, which git cannot hold as direct
    submodules of the umbrella) with path, remote, branch, HEAD and where its disk-only work is backed up;
  - .estate/overlays/<machine>/*.bundle: local work on public or third-party repos (see backup.py).
Restore on a new machine: clone the umbrella, then `estate restore` (clones every repo in repos.json at
its recorded HEAD, then fetches refs/backup/<machine>/<slug>/* for the work that was only on this disk).
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
GH_URL = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$")
SKIP = re.compile(r"/\.t/|/node_modules/|/\.lake/packages/|/\.scratch-binding-")   # package-manager clones are the tool's, not buildings (ADR 0016)


def sh(*cmd, cwd=None, env=None, timeout=600):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def best_url(rec, backup):
    """The URL a fresh machine should clone this repo from."""
    b = backup.get(rec["path"], {})
    remotes = rec.get("remotes") or {}
    ranked = []
    for name, url in remotes.items():
        m = GH_URL.search(url)
        if not m:
            continue
        owner = m.group(1)
        ranked.append(((owner != "sisodias", name != "origin", name != "mirror"), f"https://github.com/{m.group(1)}/{m.group(2)}.git"))
    if b.get("target") and b.get("action") in ("own-private", "new-repo"):
        ranked.append(((False, False, False), f"https://github.com/{b['target']}.git"))
    ranked.sort()
    return ranked[0][1] if ranked else None


def moved_from(machine):
    """new path prefix -> old path prefix, from moves.jsonl (backups were taken under the old paths)."""
    pairs = []
    mp = os.path.join(REPO, "machines", machine, "moves.jsonl")
    for line in open(mp) if os.path.exists(mp) else []:
        try:
            m = json.loads(line)
            pairs.append((m["dst"], m["src"]))
        except Exception:
            pass
    return sorted(pairs, key=lambda x: -len(x[0]))


def placements(machine):
    """local path -> map path, for repos that live outside ~/SISO_Workspace on this machine (e.g. /opt on a VPS)."""
    pp = os.path.join(REPO, "machines", machine, "placements.json")
    return json.load(open(pp)).get("placements", {}) if os.path.exists(pp) else {}


def github_heads(repos):
    """sisodias/x -> its HEAD commit on GitHub (None for an empty repo), cached in .estate/github-heads.json so a
    failed lookup keeps the last known head."""
    from concurrent.futures import ThreadPoolExecutor
    cache_p = os.path.join(WS, ".estate", "github-heads.json")
    cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}

    def one(repo):
        # HEAD, else main/master, else any branch: some repos have branches but no default branch set
        code, out, _ = sh("git", "ls-remote", f"https://github.com/{repo}.git", timeout=60)
        if code:
            return repo, cache.get(repo)
        refs = {l.split()[1]: l.split()[0] for l in out.splitlines() if len(l.split()) == 2}
        for r in ("HEAD", "refs/heads/main", "refs/heads/master"):
            if r in refs:
                return repo, refs[r]
        heads = sorted(r for r in refs if r.startswith("refs/heads/"))
        return repo, refs[heads[0]] if heads else None
    with ThreadPoolExecutor(16) as ex:
        heads = dict(ex.map(one, sorted(set(repos))))
    cache.update({k: v for k, v in heads.items() if v})
    json.dump(dict(sorted(cache.items())), open(cache_p, "w"), indent=1)
    return heads


def github_placements():
    """map path -> sisodias repo for repos no machine checks out (plan/github-placements.json)."""
    gp = os.path.join(REPO, "plan", "github-placements.json")
    pl = json.load(open(gp)).get("placements", {}) if os.path.exists(gp) else {}
    # a repo deleted from GitHub (folded into the vault, tools/vault.py) leaves the map; its bundle is its record
    gl = os.path.join(REPO, "machines", "github", "sisodias.json")
    live = {r["name"].lower() for r in json.load(open(gl))} if os.path.exists(gl) else None
    if live is None:
        return pl
    def gone(v):
        r = str(v.get("repo") if isinstance(v, dict) else v).removesuffix(".git").lower()
        return r.startswith("sisodias/") and r.split("/")[-1] not in live  # only our own repos; upstreams are not listed
    return {p: v for p, v in pl.items() if not gone(v)}


def map_path(local, place):
    real = os.path.realpath(local)
    for lp, mp in place.items():
        if real == os.path.realpath(lp) or real.startswith(os.path.realpath(lp) + "/"):
            return mp + real[len(os.path.realpath(lp)):]
    if local.startswith(WS + "/"):
        return os.path.relpath(local, WS)
    return None


def build(machine):
    """This machine's restore rows -> .estate/machines/<machine>.json; then the ONE map for every machine:
    .gitmodules and gitlinks for the union of all machines' top-level repos at their map paths (D7: the same paths
    everywhere; a machine checks out only what it needs, the rest is on GitHub at the same place)."""
    inv = json.load(open(os.path.join(REPO, "machines", machine, "repos.json")))
    plan_p = os.path.join(REPO, "machines", machine, "backup-plan.json")
    plan_old = {i["path"]: i for i in json.load(open(plan_p))["items"]} if os.path.exists(plan_p) else {}
    pairs = moved_from(machine)

    class Plan(dict):
        def get(self, p, default=None):
            if p in plan_old:
                return plan_old[p]
            for dst, src in pairs:
                if p == dst or p.startswith(dst + "/"):
                    q = src + p[len(dst):]
                    if q in plan_old:
                        return plan_old[q]
            return default
    plan = Plan()
    place = placements(machine)
    rows = []
    for r in inv["repos"]:
        if r.get("broken") or SKIP.search(r["path"]):
            continue
        b = plan.get(r["path"], {})
        mp = map_path(r["path"], place)
        rows.append({
            "path": "~" + r["path"][len(HOME):] if r["path"].startswith(HOME) else r["path"],
            "map_path": mp, "kind": r["kind"], "url": best_url(r, plan), "branch": r.get("branch"), "head": r.get("head"),
            "backup": {"action": b.get("action"), "target": b.get("target"),
                       "refs": f"refs/backup/{machine}/{b.get('slug')}/*" if b.get("action") in ("own-private", "new-repo", "worktree-of-new-repo") else None,
                       "bundle": f".estate/overlays/{machine}/{b.get('slug')}.bundle" if b.get("action") == "overlay" else None},
        })
    est = os.path.join(WS, ".estate", "machines")
    os.makedirs(est, exist_ok=True)
    doc = {"machine": machine, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "repos": rows}
    json.dump(doc, open(os.path.join(est, f"{machine}.json"), "w"), indent=1)
    if machine == "laptop":  # restore.py's default map, kept for old instructions
        json.dump(doc, open(os.path.join(WS, ".estate", "repos.json"), "w"), indent=1)
    # the union: every machine's repos by map path; a checkout here wins, else the machine that has it
    union, on = {}, {}
    for f in sorted(os.listdir(est)):
        if not f.endswith(".json"):
            continue
        m = f[:-5]
        for r in json.load(open(os.path.join(est, f)))["repos"]:
            mp = r.get("map_path")
            if not mp or r["kind"] == "worktree" or not r.get("url") or not r.get("head"):
                continue
            if mp.startswith(("_archive/", "_data/")) or SKIP.search("/" + mp + "/"):
                continue
            on.setdefault(mp, set()).add(m)
            if mp not in union or m == machine:
                union[mp] = dict(r, machine=m)
    # D7: every GitHub repo has a place, checked out or not
    gp = {mp: v for mp, v in github_placements().items() if mp not in union}
    heads = github_heads([v["repo"] for v in gp.values()]) if gp else {}
    for mp, v in gp.items():
        union[mp] = {"url": f"https://github.com/{v['repo']}.git", "head": heads.get(v["repo"]), "kind": "repo", "machine": None,
                     "map_path": mp, "why": v.get("why")}
        on.setdefault(mp, set())
    top = []
    for mp in sorted(union):
        if not any(mp.startswith(t + "/") for t in top):
            top.append(mp)
    lines, links = [], []
    for mp in top:
        r = union[mp]
        if not r.get("head"):
            continue  # an empty GitHub repo: listed in map.json, nothing to pin
        lines += [f'[submodule "{mp}"]', f"\tpath = {mp}", f"\turl = {r['url']}", "\tignore = dirty"]
        links.append((r["head"], mp))
    json.dump({"built_at": doc["built_at"], "by": machine,
               "repos": {mp: {"url": union[mp]["url"], "on": sorted(on[mp]), **({"why": union[mp]["why"]} if union[mp].get("why") else {}),
                              **({} if union[mp].get("head") else {"empty_on_github": True})} for mp in sorted(union)}},
              open(os.path.join(WS, ".estate", "map.json"), "w"), indent=1)
    open(os.path.join(WS, ".gitmodules"), "w").write("\n".join(lines) + "\n")
    keep = {rel for _, rel in links}
    _, staged, _ = sh("git", "-C", WS, "ls-files", "-s")
    for line in staged.splitlines():
        mode, _sha, _stage, rel = line.split(None, 3)
        if mode == "160000" and rel not in keep:
            sh("git", "-C", WS, "rm", "-q", "--cached", rel)
    for head, rel in links:
        # a repo this machine does not check out is an empty folder at its map path (an uninitialised submodule);
        # without it git reads the gitlink as deleted and a later `add -A` would drop it from the map
        if not os.path.lexists(os.path.join(WS, rel)):
            os.makedirs(os.path.join(WS, rel), exist_ok=True)
        code, _, err = sh("git", "-C", WS, "update-index", "--add", "--cacheinfo", f"160000,{head},{rel}")
        if code != 0:
            print("gitlink failed:", rel, err[-200:], file=sys.stderr)
    here = sum(1 for _, rel in links if os.path.exists(os.path.join(WS, rel, ".git")))
    print(json.dumps({"machine": machine, "submodules": len(links), "checked_out_here": here,
                      "repos_in_map": len(union), "rows_this_machine": len(rows)}))


def overlay_paths(machine):
    """Repos the backup keeps as overlay bundles (public own repos, third-party clones): their local delta lives in the
    private map's .estate/overlays, never on their public remote."""
    try:
        res = json.load(open(os.path.join(REPO, "machines", machine, "backup.json"))).get("results", [])
    except (OSError, ValueError):
        return set()
    return {os.path.relpath(r["path"], WS) for r in res if r.get("action") == "overlay" and r.get("path", "").startswith(WS + "/")}


def public_pin(top, head):
    """A16: the pin must exist on the repo's remote. An overlay repo's HEAD may be local-only (its delta is in the
    overlay bundle), so pin the newest commit its upstream has: HEAD itself if a remote branch holds it, else the
    merge-base with its upstream (or origin's default branch)."""
    if sh("git", "-C", top, "branch", "-r", "--contains", head)[1].strip():
        return head
    for up in ("@{u}", "origin/HEAD", "origin/main", "origin/master"):
        c, base, _ = sh("git", "-C", top, "merge-base", head, up)
        if c == 0 and base:
            return base
    return head


def mapped_paths():
    """Every submodule path .gitmodules names. Not `git submodule status`: it stops at the first gitlink with no
    .gitmodules line, and from 27 Sep 40 archived checkouts in _archive/ made it skip every repo sorted after them."""
    _, cfg, _ = sh("git", "-C", WS, "config", "-z", "-f", ".gitmodules", "--get-regexp", r"\.path$")
    return [e.split("\n", 1)[1] for e in cfg.split("\0") if "\n" in e]


def snapshot(push, machine="laptop"):
    n = 0
    overlays = overlay_paths(machine)
    pins = {}
    for rel in mapped_paths():
        if not os.path.exists(os.path.join(WS, rel, ".git")):
            continue  # not checked out on this machine: its pin comes from the machine that has it
        c, head, _ = sh("git", "-C", os.path.join(WS, rel), "rev-parse", "HEAD")
        if c == 0 and rel in overlays:
            head = public_pin(os.path.join(WS, rel), head)
            pins[rel] = head
        if c == 0:
            sh("git", "-C", WS, "update-index", "--cacheinfo", f"160000,{head},{rel}")
            n += 1
    if machine == "laptop":
        sh("git", "-C", WS, "add", "-A", "--", ".")  # the laptop also owns the map's own files (AGENTS.md, docs, memory)
    else:
        sh("git", "-C", WS, "add", "--", ".estate", ".gitmodules")  # other machines add only their records
    for rel, head in pins.items():   # `git add -A` re-stages each submodule at its HEAD: put the public pins back
        sh("git", "-C", WS, "update-index", "--cacheinfo", f"160000,{head},{rel}")
    mapped = set(mapped_paths())     # `add -A` takes a folder holding .git (an archived checkout) as a gitlink: drop those
    _, staged, _ = sh("git", "-C", WS, "ls-files", "-s")
    for line in staged.splitlines():
        mode, _sha, _stage, rel = line.split(None, 3)
        if mode == "160000" and rel not in mapped:   # index only; `rm --cached` refuses an entry add -A just staged
            sh("git", "-C", WS, "update-index", "--force-remove", "--", rel)
    c, _, err = sh("git", "-C", WS, "commit", "-qm", f"estate snapshot {machine} {time.strftime('%Y-%m-%d %H:%M')}: {n} repos pinned")
    print("committed" if c == 0 else f"nothing to commit ({err[-120:]})")
    if push:
        c, _, _ = sh("gitleaks", "git", WS, "--log-opts=-1", "--redact", "--no-banner", timeout=1800)
        if c != 0:
            print("push held: the secret scan flagged the snapshot commit (run gitleaks git ~/SISO_Workspace --log-opts=-1)")
            return
        sh("git", "-C", WS, "config", "http.postBuffer", "524288000")
        c, _, err = sh("git", "-C", WS, "push", "-q", "origin", "HEAD:main", timeout=3600)
        if c != 0:  # another machine pushed first: take its records, keep ours (per-machine files never collide)
            r, _, _ = sh("git", "-C", WS, "pull", "-q", "--rebase", "-X", "theirs", "origin", "main", timeout=600)
            r = take_own_pins(r)
            if r != 0:  # never leave the map mid-rebase: on 26 Sep one stuck from 08:35 and every snapshot after it committed on a detached HEAD
                sh("git", "-C", WS, "rebase", "--abort")
            c, _, err = sh("git", "-C", WS, "push", "-q", "origin", "HEAD:main", timeout=3600)
        print("pushed" if c == 0 else "push failed: " + err[-300:])


def take_own_pins(r):
    """A stopped rebase whose only conflicts are gitlinks: both machines pinned the same repo. `-X theirs` cannot settle
    a gitlink, so take the pin of the snapshot being replayed (this machine's) and continue. 2 Oct: the mini's 03:45
    snapshot conflicted on siso-firstmate and the laptop's hourly push failed from 13:10 until a hand rebase."""
    for _ in range(50):
        if r == 0:
            return 0
        _, unmerged, _ = sh("git", "-C", WS, "ls-files", "-u")
        paths = {l.split("\t", 1)[1] for l in unmerged.splitlines()}
        if not paths or any(l.split()[0] != "160000" for l in unmerged.splitlines()):
            return r
        for rel in paths:
            c, sha, _ = sh("git", "-C", WS, "rev-parse", f"REBASE_HEAD:{rel}")
            if c != 0:
                return r
            sh("git", "-C", WS, "update-index", "--cacheinfo", f"160000,{sha.strip()},{rel}")
        r, _, _ = sh("git", "-C", WS, "-c", "core.editor=true", "rebase", "--continue", timeout=600)
    return r


def verify(machine):
    """A16's test: every commit the committed map pins (gitlinks in HEAD of sisodias/siso-city) exists on its GitHub
    repo (reachable from any ref there, backup refs included). Writes machines/<m>/map-verify.json; exit 1 on a miss."""
    import concurrent.futures as cf
    _, tree, _ = sh("git", "-C", WS, "ls-tree", "-r", "HEAD")
    links = [(l.split("\t", 1)[1], l.split()[2]) for l in tree.splitlines() if l.split()[1:2] == ["commit"]]
    _, cfg, _ = sh("git", "-C", WS, "config", "-z", "-f", ".gitmodules", "--list")   # -z: names and paths may hold spaces
    kv = [e.split("\n", 1) for e in cfg.split("\0") if "\n" in e]
    url = {k[:-len(".url")]: v for k, v in kv if k.endswith(".url")}
    repo_of = {v: url.get(k[:-len(".path")], "") for k, v in kv if k.endswith(".path")}

    def one(item):
        path, sha = item
        m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", repo_of.get(path, ""))
        if not m:
            return path, sha, "no github url"
        c, out, err = sh("gh", "api", f"repos/{m.group(1)}/commits/{sha}", "--jq", ".sha", timeout=60)
        return path, sha, "ok" if c == 0 and out.strip() == sha else ("missing" if "No commit found" in err or "422" in err else "error: " + err[-120:])
    with cf.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, links))
    bad = [dict(path=p, sha=s, status=st) for p, s, st in res if st != "ok"]
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "pinned": len(res), "on_github": len(res) - len(bad), "problems": bad}
    with open(os.path.join(REPO, "machines", machine, "map-verify.json"), "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(f"{out['on_github']} of {out['pinned']} pinned commits exist on GitHub" + "".join(f"\n  {b['status']:<10} {b['path']} {b['sha'][:12]}" for b in bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "snapshot", "verify"])
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    if a.cmd == "verify":
        sys.exit(verify(a.machine))
    if a.cmd == "build":
        build(a.machine)
    else:
        snapshot(a.push, a.machine)


if __name__ == "__main__":
    main()
