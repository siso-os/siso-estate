#!/usr/bin/env python3
"""Apply the estate's written fix policies (plan/fix-policies.json) to repos that fall below the building code.

  fix.py            the plan: which repo gets which fix, and what is skipped and why
  fix.py --run      do it: one path-limited commit per repo per fix, secret-scanned, pushed only when it is the
                    branch's one unpushed commit; a receipt line per fix in machines/<m>/fixes.jsonl

Reads machines/<m>/code.json (run `estate code` first). Shaan decided on 25 Sep that these routine fixes need no owner's go.
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.join(os.path.expanduser("~"), "SISO_Workspace")
sys.path.insert(0, HERE)
from doors import first_line, gh_description  # noqa: E402  the same one-line description the district doors use

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("untrack_state", os.path.join(HERE, "untrack-state.py"))
_ut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ut)
RUNTIME = _ut.RUNTIME                     # what the untrack codemod can safely take out of git; the rest is the owner's call
POL = json.load(open(os.path.join(REPO, "plan", "fix-policies.json")))
OWNERS = json.load(open(os.path.join(REPO, "plan", "owners.json")))
NEVER = tuple(n["path"] for n in POL["never"] if "path" in n)
MSG = "\n\nsiso-estate fix policy `{}` (plan/fix-policies.json; routine fixes decided by Shaan, 25 Sep).\n\nCo-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"


def git(top, *a):
    return subprocess.run(["git", "-C", top, *a], capture_output=True, text=True)


def working_agents():
    try:
        d = json.loads(subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=10).stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return [a.get("foreground_cwd") or a.get("cwd") or "" for a in d.get("result", {}).get("agents", []) if a.get("agent_status") == "working"]


def second_copy(top):
    """A submodule checkout that is a copy of a repo whose home is elsewhere. The map's own submodules are homes, and so
    is a checkout a project repo pins in place with its own .git directory (sisodias/halo, 27 Sep: the compound pins
    its buildings where they already live; an absorbed or cloned-by-submodule checkout has a .git file)."""
    sp = git(top, "rev-parse", "--show-superproject-working-tree").stdout.strip()
    return sp not in ("", WS) and not os.path.isdir(os.path.join(top, ".git"))


def skip_reason(x, busy):
    rel, top = x["path"], os.path.join(WS, x["path"])
    if rel.startswith(NEVER) or any(rel == n for n in NEVER):
        return "never: " + next(n["why"] for n in POL["never"] if n.get("path") and (rel == n["path"] or rel.startswith(n["path"] + "/")))
    if x["owner"] != "own":
        return "not our house"
    if x.get("lifecycle") == "dormant":
        return "dormant: goes dark instead"
    if second_copy(top):
        return "a submodule checkout: fix the repo at its own home, never a second copy (it forks from GitHub)"
    if busy is None:
        return "cannot see which agents are working (herdr): not touching anything"
    if any(b == top or b.startswith(top + "/") for b in busy) and rel != "SISO_Agents/siso-estate":
        return "an agent is working in it now"
    if git(top, "diff", "--cached", "--quiet").returncode:
        return "something is already staged"
    return ""


SEEN = {}   # (repo, policy) -> epoch first seen: machines/<m>/mess-seen.json (P8: time from mess to fix)


def seen_path(machine):
    return os.path.join(REPO, "machines", machine, "mess-seen.json")


def receipt(machine, rel, policy, commit, pushed, note=""):
    first = SEEN.get(f"{rel}\t{policy}")
    with open(os.path.join(REPO, "machines", machine, "fixes.jsonl"), "a") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "repo": rel, "policy": policy, "commit": commit,
                            "pushed": pushed, **({"waited_min": round((time.time() - first) / 60)} if first and commit else {}),
                            **({"note": note} if note else {})}) + "\n")


def commit(top, paths, subject, policy):
    c = git(top, "commit", "-q", "-m", subject + MSG.format(policy), "--", *paths)
    if c.returncode:
        return None, False, (c.stderr or c.stdout).strip()[-160:]
    sha = git(top, "rev-parse", "--short", "HEAD").stdout.strip()
    scan = subprocess.run(["gitleaks", "git", "--no-banner", "--redact", "--log-opts=-1", top], capture_output=True, text=True)
    ahead = git(top, "rev-list", "--count", "@{u}..HEAD").stdout.strip()
    pushed = scan.returncode == 0 and ahead == "1" and git(top, "push", "-q").returncode == 0
    return sha, pushed, "" if scan.returncode == 0 else "secret scan flagged the commit: not pushed"


def door_text(x):
    top = os.path.join(WS, x["path"])
    line = first_line(top)
    if line and (re.match(r"^[a-z][\w.-]* -", line) or re.search(r"[|$=]{1,2}|select\(|&&", line)):
        line = ""                                   # the README opens with a command, not a sentence
    what = line or gh_description([re.sub(r"\.git$", "", "/".join(x["origin"].rstrip("/").split("/")[-2:]).split(":")[-1])]) \
        or f"the {os.path.basename(top)} repository"
    seat = x.get("seat", "")
    who = OWNERS["seats"].get(seat, seat)
    if x.get("state_land"):
        who = f"not yet assigned; {who} holds it until it names one (siso-estate plan/owners.json)"
    return (f"# {os.path.basename(top)}\n\n**In one line:** {what}. District: `{x['district']}` (`~/SISO_Workspace/{x['path']}`). "
            f"Owner: {who}.\n\nThe rest is in `README.md` if it has one. Find anything else on this machine with `estate where <words>`.\n")


def moved_citations():
    """Door citations a recorded move explains, from the last docs check (tools/docs_check.py)."""
    try:
        d = json.load(open(os.path.join(REPO, "machines", "laptop", "docs-check.json")))
    except (OSError, ValueError):
        return {}
    return {b["path"]: b["moved"] for b in d.get("by_door", []) if b.get("moved")}


MOVED = moved_citations()


def repointed(tok, suggest):
    """The suggestion written the way the door wrote the old path."""
    if tok.startswith("~/"):
        return "~/" + os.path.relpath(suggest, os.path.expanduser("~")) + ("/" if tok.endswith("/") else "")
    return suggest + ("/" if tok.endswith("/") and not suggest.endswith("/") else "")


DOORLINE = re.compile(r"District: `([^`]+)` \(`~/SISO_Workspace/([^`]+)`\)")


def door_line(x):
    """(old, new) for a door header whose district or path no longer fits where the repo is, else None."""
    top = os.path.join(WS, x["path"])
    door = os.path.join(top, "AGENTS.md")
    if not os.path.isfile(door) or second_copy(top):
        return None                              # a submodule checkout carries its home repo's door
    m = DOORLINE.search(open(door, errors="replace").read())
    if not m:
        return None
    d = m.group(1).rstrip("/")
    if m.group(2) == x["path"] and (x["path"] == d or x["path"].startswith(d + "/")):
        return None
    nd = "/".join(x["path"].split("/")[:len(d.split("/"))])
    return m.group(0), f"District: `{nd}` (`~/SISO_Workspace/{x['path']}`)"


QUIET_H = 6   # push-ahead waits until the newest commit is this old: an owner mid-session pushes their own


def push_plan(top):
    """(remote, branch, set_upstream, ahead) when the checked-out branch can reach GitHub by a fast-forward only, else
    None: its upstream (or origin's same-named branch when none is set) is an ancestor of HEAD, and it is ours."""
    branch = git(top, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if not branch:
        return None                                  # detached HEAD: nothing to push to
    up = git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").stdout.strip()
    set_up = False
    if not up:
        if git(top, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}").returncode:
            return None                              # the branch is not on GitHub at all: the backup holds it
        up, set_up = f"origin/{branch}", True
    remote, _, rbranch = up.partition("/")
    url = git(top, "remote", "get-url", remote).stdout.strip()
    if not re.search(r"github\.com[:/](sisodias|Lordsisodia|lordsisodia)/", url):
        return None                                  # someone else's remote is never pushed to
    if git(top, "merge-base", "--is-ancestor", up, "HEAD").returncode:
        return None                                  # diverged or behind: the owner merges, never the law
    ahead = int(git(top, "rev-list", "--count", f"{up}..HEAD").stdout.strip() or 0)
    return (remote, rbranch, set_up, ahead) if (ahead or set_up) else None


def plan_for(x):
    top = os.path.join(WS, x["path"])
    fixes = []
    if door_line(x):
        fixes.append("door-line")
    if x["path"] in MOVED and os.path.isfile(os.path.join(top, "AGENTS.md")):
        fixes.append("door-paths")
    if "no-tool-state" in x["failed"] and any(RUNTIME.search(f) for f in git(top, "ls-files").stdout.splitlines()):
        fixes.append("tool-state")
    if "worktrees" in x["failed"] and "stale record" in x["rules"]["worktrees"].get("note", ""):
        fixes.append("stale-worktrees")
    if "agents" in x["failed"] and not os.path.lexists(os.path.join(top, ".agents")) and \
            git(top, "check-ignore", "-q", "--no-index", ".agents/HANDOFF.md").returncode != 0:
        fixes.append("agents-home")
    if "pushed" in x["failed"] and push_plan(top):
        fixes.append("push-ahead")
    has_a, has_c = os.path.isfile(os.path.join(top, "AGENTS.md")), os.path.lexists(os.path.join(top, "CLAUDE.md"))
    chose = git(top, "check-ignore", "-q", "--no-index", "AGENTS.md").returncode == 0 or \
        git(top, "check-ignore", "-q", "--no-index", "CLAUDE.md").returncode == 0 or \
        (has_a and git(top, "ls-files", "--error-unmatch", "AGENTS.md").returncode != 0)
    if chose:
        pass                                  # the repo ignores or does not track its instruction files: the owner's choice
    elif not has_a:
        fixes.append("door")
    elif not has_c:
        fixes.append("shim")
    return fixes


def stats(machine):
    """P8: how the city governs itself. Fixes in the last 7 days, the median wait from a mess first seen to its fix,
    and the routine messes still open (with the oldest's age)."""
    import statistics
    cut = time.time() - 7 * 86400
    waits, n = [], 0
    for line in open(os.path.join(REPO, "machines", machine, "fixes.jsonl")):
        r = json.loads(line)
        try:
            t = time.mktime(time.strptime(r["at"][:19], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            continue
        if t >= cut and r.get("commit"):
            n += 1
            if "waited_min" in r:
                waits.append(r["waited_min"])
    try:
        open_ = json.load(open(seen_path(machine)))
    except (OSError, ValueError):
        open_ = {}
    oldest = max((time.time() - v for v in open_.values()), default=0) / 3600
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "fixes_7d": n, "timed": len(waits),
           "median_wait_min": statistics.median(waits) if waits else None, "open": len(open_), "oldest_open_h": round(oldest, 1)}
    with open(os.path.join(REPO, "machines", machine, "law-stats.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"law, last 7 days: {n} fixes; median wait from first seen to fixed "
          f"{out['median_wait_min']} min over {len(waits)} timed; {len(open_)} routine messes open (oldest {out['oldest_open_h']} h, most are skipped: busy, dormant, someone's edits)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    if a.stats:
        return stats(a.machine)
    code = json.load(open(os.path.join(REPO, "machines", a.machine, "code.json")))
    busy = working_agents()
    done = 0
    try:
        SEEN.update(json.load(open(seen_path(a.machine))))
    except (OSError, ValueError):
        pass
    now_seen = {}
    untrack = False
    for x in code["results"]:
        fixes = plan_for(x)
        for f in fixes:                              # when each mess was first seen, kept while it lasts
            k = f"{x['path']}\t{f}"
            now_seen[k] = SEEN.setdefault(k, time.time())
        if not fixes:
            continue
        why = skip_reason(x, busy)
        print(f"{'skip' if why else 'fix '} {x['path']}: {', '.join(fixes)}" + (f"  [{why}]" if why else ""))
        if why or not a.run:
            continue
        top = os.path.join(WS, x["path"])
        for f in fixes:
            if f == "tool-state":
                untrack = True                       # one pass of untrack-state.py covers every repo below
            elif f == "stale-worktrees":
                before = git(top, "worktree", "list", "--porcelain").stdout.count("worktree ")
                git(top, "worktree", "prune")
                after = git(top, "worktree", "list", "--porcelain").stdout.count("worktree ")
                receipt(a.machine, x["path"], f, None, None, f"worktree records {before} -> {after}")
                print(f"     pruned {before - after} stale worktree records")
                done += 1
            elif f == "door-line":
                if git(top, "status", "--porcelain", "--", "AGENTS.md").stdout.strip():
                    print("     skipped: AGENTS.md has someone's uncommitted change")
                    continue
                if git(top, "diff", "--cached", "--name-only").stdout.strip():
                    print("     skipped: something is already staged")
                    continue
                old_line, new_line = door_line(x)
                door = os.path.join(top, "AGENTS.md")
                with open(door, errors="replace") as fh:
                    text = fh.read()
                with open(door + ".estate-tmp", "w") as fh:
                    fh.write(text.replace(old_line, new_line, 1))
                os.replace(door + ".estate-tmp", door)
                git(top, "add", "--", "AGENTS.md")
                sha, pushed, note = commit(top, ["AGENTS.md"], "AGENTS.md: the header says where it sits now", f)
                receipt(a.machine, x["path"], f, sha, pushed, note or f"{old_line} -> {new_line}")
                print(f"     door-line: {sha or 'not committed'}; {'pushed' if pushed else 'not pushed'}" + (f" ({note})" if note else ""))
                done += 1 if sha else 0
            elif f == "door-paths":
                door = os.path.join(top, "AGENTS.md")
                if git(top, "status", "--porcelain", "--", "AGENTS.md").stdout.strip():
                    print("     skipped: AGENTS.md has someone's uncommitted change")
                    continue
                text = open(door, errors="replace").read()
                n = 0
                for m in MOVED[x["path"]]:
                    new = repointed(m["token"], m["suggest"])
                    if f"`{m['token']}`" in text and os.path.exists(m["suggest"]):
                        text, n = text.replace(f"`{m['token']}`", f"`{new}`"), n + 1
                if not n:
                    continue
                open(door, "w").write(text)
                git(top, "add", "--", "AGENTS.md")
                staged = set(git(top, "diff", "--cached", "--name-only").stdout.split())
                if staged != {"AGENTS.md"}:
                    print(f"     not committed: the index holds {sorted(staged)}; undo by hand")
                    continue
                sha, pushed, note = commit(top, ["AGENTS.md"], f"AGENTS.md: {n} path(s) repointed to where a recorded move put them", f)
                receipt(a.machine, x["path"], f, sha, pushed, note or f"{n} citation(s)")
                print(f"     door-paths: {n} repointed; {sha or 'not committed'}; {'pushed' if pushed else 'not pushed'}" + (f" ({note})" if note else ""))
                done += 1 if sha else 0
            elif f == "push-ahead":
                plan = push_plan(top)
                if not plan:
                    continue
                remote, rbranch, set_up, ahead = plan
                # the owner's newest commit: the law's own commits (door tops, agent folders) never make a branch look busy
                newest = int(git(top, "log", "-1", "--format=%ct", "--invert-grep", "--grep=siso-estate fix policy",
                                 f"{remote}/{rbranch}..HEAD").stdout.strip() or 0)
                if ahead and time.time() - newest < QUIET_H * 3600:
                    print(f"     skipped: the newest commit is under {QUIET_H} h old (the owner may push it)")
                    continue
                branch = git(top, "symbolic-ref", "--short", "HEAD").stdout.strip()
                if ahead:
                    scan = subprocess.run(["gitleaks", "git", "--no-banner", "--redact", f"--log-opts={remote}/{rbranch}..HEAD", top],
                                          capture_output=True, text=True)
                    if scan.returncode:
                        receipt(a.machine, x["path"], f, None, False, "secret scan flagged a commit: not pushed")
                        print("     push-ahead: the secret scan flagged a commit; not pushed")
                        continue
                    pr = git(top, "push", "-q", remote, f"HEAD:refs/heads/{rbranch}")
                    if pr.returncode:
                        receipt(a.machine, x["path"], f, None, False, (pr.stderr or "push refused").strip()[-160:])
                        print(f"     push-ahead: refused ({(pr.stderr or '').strip()[-120:]})")
                        continue
                if set_up:
                    git(top, "branch", "--set-upstream-to", f"{remote}/{rbranch}", branch)
                head = git(top, "rev-parse", "--short", "HEAD").stdout.strip()
                receipt(a.machine, x["path"], f, head, bool(ahead), f"{ahead} commit(s) fast-forwarded to {remote}/{rbranch}" +
                        ("; upstream set" if set_up else ""))
                print(f"     push-ahead: {ahead} commit(s) -> {remote}/{rbranch}" + ("; upstream set" if set_up else ""))
                done += 1
            elif f == "agents-home":
                if git(top, "diff", "--cached", "--name-only").stdout.strip():
                    print("     skipped: something is already staged")
                    continue
                os.makedirs(os.path.join(top, ".agents", "memory"), exist_ok=True)
                name = os.path.basename(top)
                files = {".agents/HANDOFF.md": f"# {name}: handoff\n\nThe newest `## State (<date> <time>, <seat>)` section goes at the bottom: what changed, what waits "
                                               "on whom, the next job. Shaan's own words go in `.agents/source/`, verbatim.\n",
                         ".agents/memory/MEMORY.md": f"# Memory: {name}\n\nOne line per memory: `- [Title](file.md): one-line hook`. Durable project facts, one "
                                                     "file each; cross-project facts go in `~/SISO_Workspace/.agents/memory/`.\n"}
                for rel, body in files.items():
                    with open(os.path.join(top, rel), "x") as fh:
                        fh.write(body)
                git(top, "add", "--", *files)
                staged = set(git(top, "diff", "--cached", "--name-only").stdout.split())
                if staged != set(files):
                    print(f"     not committed: the index holds {sorted(staged)}; undo by hand")
                    continue
                sha, pushed, note = commit(top, list(files), ".agents/: the agent folder (HANDOFF.md, memory index), per the building code", f)
                if not sha:                              # e.g. the repo's own pre-commit hook refused: leave it as it was
                    git(top, "restore", "--staged", "--", *files)
                    for rel in files:
                        os.remove(os.path.join(top, rel))
                    for d in (".agents/memory", ".agents"):
                        try:
                            os.rmdir(os.path.join(top, d))
                        except OSError:
                            pass
                receipt(a.machine, x["path"], f, sha, pushed, note)
                print(f"     agents-home: {sha or 'not committed'}; {'pushed' if pushed else 'not pushed'}" + (f" ({note})" if note else ""))
                done += 1 if sha else 0
            elif f in ("door", "shim"):
                if git(top, "status", "--porcelain", "--", "AGENTS.md", "CLAUDE.md").stdout.strip():
                    print("     skipped: AGENTS.md or CLAUDE.md has someone's uncommitted change")
                    continue
                # a repo that ignores its instruction files, or keeps AGENTS.md untracked, has chosen to: owner's call (25 Sep)
                if git(top, "check-ignore", "-q", "--no-index", "AGENTS.md").returncode == 0 or \
                        git(top, "check-ignore", "-q", "--no-index", "CLAUDE.md").returncode == 0 or \
                        (os.path.isfile(os.path.join(top, "AGENTS.md")) and git(top, "ls-files", "--error-unmatch", "AGENTS.md").returncode):
                    receipt(a.machine, x["path"], f, None, None, "skipped: the repo ignores or does not track its instruction files (owner's call)")
                    print("     skipped: the repo ignores or does not track AGENTS.md/CLAUDE.md (owner's call)")
                    continue
                cl = os.path.join(top, "CLAUDE.md")
                if f == "door" and os.path.isfile(cl) and not os.path.islink(cl) and open(cl, errors="replace").read().strip() not in ("", "@AGENTS.md"):
                    git(top, "mv", "CLAUDE.md", "AGENTS.md")
                    open(cl, "w").write("@AGENTS.md\n")
                    git(top, "add", "--", "CLAUDE.md")
                    subject = "AGENTS.md is the instruction file; CLAUDE.md imports it (@AGENTS.md)"
                elif f == "door":
                    open(os.path.join(top, "AGENTS.md"), "w").write(door_text(x))
                    if not os.path.lexists(cl) or open(cl, errors="replace").read().strip() == "":
                        open(cl, "w").write("@AGENTS.md\n")
                    git(top, "add", "--", "AGENTS.md", "CLAUDE.md")
                    subject = "AGENTS.md: the front door (what this is, its district, its owner); CLAUDE.md imports it"
                else:
                    open(cl, "w").write("@AGENTS.md\n")
                    git(top, "add", "--", "CLAUDE.md")
                    subject = "CLAUDE.md imports AGENTS.md, the one instruction file"
                staged = set(git(top, "diff", "--cached", "--name-only").stdout.split())
                if not staged or not staged <= {"AGENTS.md", "CLAUDE.md"}:
                    print(f"     not committed: the index holds {sorted(staged)}; undo by hand")
                    continue
                sha, pushed, note = commit(top, ["AGENTS.md", "CLAUDE.md"], subject, f)
                receipt(a.machine, x["path"], f, sha, pushed, note)
                print(f"     {f}: {sha or 'not committed'}; {'pushed' if pushed else 'not pushed'}" + (f" ({note})" if note else ""))
                done += 1 if sha else 0
    if untrack and a.run:
        r = subprocess.run([sys.executable, os.path.join(HERE, "untrack-state.py"), "--run"], capture_output=True, text=True)
        print("tool-state:", (r.stdout.strip().splitlines() or ["nothing"])[-1])
        receipt(a.machine, "*", "tool-state", None, None, (r.stdout.strip().splitlines() or [""])[-1])
    if a.run:
        print(f"{done} fixes applied")
        with open(seen_path(a.machine) + ".tmp", "w") as fh:   # messes still open, with when each was first seen
            json.dump(now_seen, fh, indent=0)
        os.replace(seen_path(a.machine) + ".tmp", seen_path(a.machine))


if __name__ == "__main__":
    main()
