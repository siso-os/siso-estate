#!/usr/bin/env python3
"""Stop tracking tool and harness runtime state that ADR 0015 says never belongs in git, one path-limited commit per repo.

  untrack-state.py            the plan: which repos, which paths, how many files
  untrack-state.py --run      do it

Only paths matching RUNTIME are touched: `git rm -r --cached` keeps every file on disk (the machine-wide ignore then
hides it), and the commit names only those paths, so other agents' staged or unstaged work is never included. A repo is
skipped when something is already staged in it, when its origin is not sisodias (third-party code tracks what it
likes), or when its lane owns itself (Oracle: Shaan, 24 Sep "oracle owns itself"). The commit is pushed only when it is
the branch's one commit its upstream lacks. Each commit is secret-scanned first (gitleaks on the commit).
"""
import json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.join(os.path.expanduser("~"), "SISO_Workspace")
RUNTIME = re.compile(r"(^|/)(\.omc(/|$)|\.claude/(session-context|feedback)(/|$)|\.claude/(instance-uuid|settings\.local)\.json$|"
                     r"\.wrangler(/|$)|supabase/\.temp(/|$)|\.playwright-cli(/|$)|\.serena/cache(/|$))")
OWN_THEMSELVES = ("HALO_Agency/oracle", "SISO_Agency/partners/halo/oracle", "SISO_Agency/partners/halo/oracle/core")


def git(top, *a):
    return subprocess.run(["git", "-C", top, *a], capture_output=True, text=True)


def repos_with_state():
    out = {}
    for r in json.load(open(os.path.join(REPO, "machines", "laptop", "repos.json")))["repos"]:
        top = str(r["path"])
        if r.get("kind") == "worktree" or not top.startswith(WS + "/") or "/_archive/" in top or "/_reference/" in top \
                or "/_data/" in top or not os.path.isdir(top):
            continue
        files = [f for f in git(top, "ls-files", "-z").stdout.split("\0") if f and RUNTIME.search(f)]
        if files:
            out[top] = files
    return out


def main():
    run = "--run" in sys.argv
    plan = repos_with_state()
    total = 0
    for top, files in sorted(plan.items()):
        rel = os.path.relpath(top, WS)
        origin = git(top, "remote", "get-url", "origin").stdout.strip()
        skip = ("its lane owns itself" if rel.startswith(OWN_THEMSELVES) else
                "not a sisodias repo" if "github.com/sisodias/" not in origin.lower() and "github.com:sisodias/" not in origin.lower() else
                "something is already staged" if git(top, "diff", "--cached", "--quiet").returncode else
                "a submodule copy: its own home gets the commit" if git(top, "rev-parse", "--show-superproject-working-tree").stdout.strip() not in ("", WS)
                not in ("", WS) else "")        # the map (the workspace root) pins every top repo: that is not a copy
        kinds = sorted({RUNTIME.search(f).group(2).split("/")[0] + ("/" + RUNTIME.search(f).group(2).split("/")[1] if "/" in RUNTIME.search(f).group(2) else "") for f in files})
        print(f"{'skip' if skip else 'go  '} {rel}: {len(files)} files ({', '.join(kinds)})" + (f"  [{skip}]" if skip else ""))
        if skip or not run:
            continue
        paths = sorted({re.sub(r"(^|.*/)(\.omc|\.wrangler|\.playwright-cli|supabase/\.temp|\.serena/cache|\.claude/session-context|\.claude/feedback)(/.*)?$",
                               lambda m: m.group(1) + m.group(2), f) for f in files})
        r = git(top, "rm", "-r", "-q", "--cached", "--", *paths)
        if r.returncode:
            print("     rm failed:", r.stderr.strip()[-160:])
            continue
        staged = [l for l in git(top, "diff", "--cached", "--name-status").stdout.splitlines() if l]
        if any(not (l.startswith("D") and RUNTIME.search(l.split("\t", 1)[1])) for l in staged):
            git(top, "reset", "-q", "--", *paths)      # someone else staged something meanwhile: take ours back out
            print("     skipped: other changes appeared in the index")
            continue
        # no pathspec: a pathspec commit re-reads the files from disk and records nothing for `rm --cached` (24 Sep)
        c = git(top, "commit", "-q", "-m", "Stop tracking tool runtime state (.omc, .claude session files, .wrangler, ...)\n\n"
                "siso-estate ADR 0015: tool state is never tracked; the files stay on disk and the machine-wide ignore hides them.")
        if c.returncode:
            print("     commit failed:", (c.stderr or c.stdout).strip()[-160:])
            continue
        scan = subprocess.run(["gitleaks", "git", "--no-banner", "--redact", "--log-opts=-1", top], capture_output=True, text=True)
        ahead = git(top, "rev-list", "--count", "@{u}..HEAD").stdout.strip()
        pushed = scan.returncode == 0 and ahead == "1" and git(top, "push", "-q").returncode == 0
        total += len(files)
        with open(os.path.join(REPO, "machines", "laptop", "repoints.jsonl"), "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "repo": rel, "act": "untrack runtime state",
                                "files": len(files), "paths": paths[:20], "commit": git(top, "rev-parse", "--short", "HEAD").stdout.strip(),
                                "pushed": pushed}) + "\n")
        print(f"     committed {git(top, 'rev-parse', '--short', 'HEAD').stdout.strip()}; " +
              ("pushed" if pushed else "not pushed (other unpushed commits, no upstream, or the scan flagged it)"))
    if run:
        print(f"untracked {total} files")


if __name__ == "__main__":
    main()
