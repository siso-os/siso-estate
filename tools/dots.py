#!/usr/bin/env python3
"""The hidden estate: every hidden folder in ~/SISO_Workspace, classified against ADR 0015.

  dots.py [--machine laptop]      -> machines/<m>/dots.json, and a summary on stdout

Classes: agent (.agents), uihub, harness config (.claude, .codex, .cursor, .opencode, .pi), project tooling (.github,
.vscode, ...), tool state (never tracked: .omc, .serena, .playwright-cli, ...), retired convention (folds into .agents/),
worktrees inside a repo (belong in _data/worktrees), root infrastructure (the workspace root's own), unknown.
Findings: tool state git tracks, tracked runtime files inside .claude/.uihub, retired conventions, worktrees inside repos.
Skips _archive, _reference and _data (records, other people's code, runtime data), node_modules and .git, and never
descends into a hidden folder. Nothing is changed.
"""
import argparse, collections, json, os, re, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.join(os.path.expanduser("~"), "SISO_Workspace")
SKIP = {"node_modules", ".git", "_archive", "_reference", "_data", "venv", ".venv", "__pycache__", "dist", "build",
        "target", ".next", ".turbo", ".nuxt", ".svelte-kit"}
AGENT = {".agents"}
UIHUB = {".uihub"}
HARNESS = {".claude", ".codex", ".cursor", ".opencode", ".pi", ".gemini", ".kiro", ".windsurf", ".roo", ".clinerules", ".superpowers",
           ".omo", ".omp", ".grok", ".amazonq", ".augment", ".continue", ".agentsroom", ".a0proj", ".stoneforge", ".bmad",
           ".codegraph", ".aider", ".serena"}
TOOLING = {".github", ".husky", ".vscode", ".devcontainer", ".cargo", ".storybook", ".yarn", ".sqlx", ".githooks",
           ".changeset", ".circleci", ".gitlab", ".docker", ".config", ".well-known", ".obsidian", ".gates", ".graphify-baseline",
           ".build", ".vitepress", ".dependabot", ".codesandbox", ".tanstack", ".react-router", ".qlty", ".greptile", ".lake",
           ".nuget", ".vs", ".pre-commit-hooks", ".supply-chain", ".clerk", ".bundle", ".mvn", ".jenkins", ".buildkite",
           ".pristine"}
TOOL_STATE = {".omc", ".playwright-cli", ".wrangler", ".firecrawl", ".vercel", ".idea", ".cache", ".pytest_cache",
              ".mypy_cache", ".ruff_cache", ".nx", ".parcel-cache", ".expo", ".gradle", ".dart_tool", ".deepeval", ".dylibs",
              ".local", ".runtime", ".tmp", ".temp", ".temporary", ".playwright", ".vite", ".eslintcache", ".swc",
              ".teable-runtime", ".oracle-builds", ".npm-cache", ".logs", ".replay-work", ".codex-artifacts"}
SECRET_DIRS = {".credentials", ".keys", ".secrets"}          # a key store outside ~/SISO_Workspace/.credentials
RETIRED = {".memory": ".agents/memory/", ".plans": ".agents/sprints/ or briefs/", ".orchestrate": ".agents/runs/ or HANDOFF.md",
           ".relay": ".agents/runs/ or HANDOFF.md", ".scratch": ".agents/scratch/ (ignored)", ".skills": ".agents/skills/",
           ".tasks": ".agents/tasks/", ".briefs": ".agents/briefs/", ".siso": ".agents/ (memory/, runs/) or docs/",
           ".siso-wiki": ".agents/memory/ or docs/", ".docs": "docs/", ".project-os": ".agents/ (the current project-os layout)",
           ".prompts": ".agents/briefs/", ".handoffs": ".agents/runs/ or HANDOFF.md", ".agent": ".agents/",
           ".integration-imports": ".agents/runs/", ".openai": ".codex/ (harness config)", ".research": ".agents/ or docs/research/",
           ".handoff-history": ".agents/runs/", ".blackbox5": "_archive (the old BlackBox agent system)", ".locks": ".agents/work-claims/"}
RUNTIME_INSIDE = {".claude": ("session-context", "instance-uuid.json", "feedback", "settings.local.json"),
                  ".uihub": (".server.pid",), ".agents": ("scratch",), ".serena": ("cache",)}


def git(path, *a):
    r = subprocess.run(["git", "-C", path, *a], capture_output=True, text=True)
    return r.returncode, r.stdout


TEMP = re.compile(r"^\.[\w-]+\.[A-Za-z0-9]{6}$|^\.backup-")          # mktemp-style leftovers, dated backups
SECRETY = re.compile(r"^(\.env(\.[\w-]+)?|credentials(\.\w+)?|[\w-]*\.token|tokens?\.json|.*\.pem|.*\.p12|id_(rsa|ed25519)|.*\.key)$", re.I)


def classify(rel, name, at_root):
    if at_root:
        return "root infrastructure"
    if TEMP.search(name):
        return "tool state"
    if name == ".worktrees":
        return "worktrees inside a repo"
    if name in SECRET_DIRS:
        return "key store outside the store"
    for cls, names in (("agent", AGENT), ("uihub", UIHUB), ("harness config", HARNESS), ("project tooling", TOOLING),
                       ("tool state", TOOL_STATE)):
        if name in names:
            return cls
    if name in RETIRED:
        return "retired convention"
    return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    found = []
    for root, dirs, _ in os.walk(WS):
        keep = []
        for d in dirs:
            p = os.path.join(root, d)
            if d in SKIP or os.path.islink(p):
                continue
            if d.startswith("."):
                found.append(os.path.relpath(p, WS))
                continue
            keep.append(d)
        dirs[:] = keep
    items, findings = [], collections.defaultdict(list)
    for rel in sorted(found):
        name = os.path.basename(rel)
        full = os.path.join(WS, rel)
        parent = os.path.dirname(full)
        cls = classify(rel, name, "/" not in rel)
        code, out = git(parent, "ls-files", "--", name)
        tracked = [l for l in out.splitlines() if l] if code == 0 else []
        code, top = git(parent, "rev-parse", "--show-toplevel")
        repo = os.path.relpath(top.strip(), WS) if code == 0 and top.strip() else None
        it = {"path": rel, "name": name, "class": cls, "repo": repo, "tracked_files": len(tracked)}
        if cls == "tool state" and tracked:
            findings["tool state tracked in git"].append({"path": rel, "tracked_files": len(tracked)})
        if cls == "retired convention":
            it["fold_into"] = RETIRED[name]
            findings["retired conventions"].append({"path": rel, "fold_into": RETIRED[name], "tracked_files": len(tracked)})
        if cls == "worktrees inside a repo":
            findings["worktrees inside a repo"].append({"path": rel, "entries": len(os.listdir(full))})
        if cls == "unknown":
            findings["unknown"].append({"path": rel, "tracked_files": len(tracked)})
        for sub in RUNTIME_INSIDE.get(name, ()):
            hits = [t for t in tracked if t.split("/", 2)[1:2] == [sub] or t.endswith("/" + sub)]
            if hits:
                findings["runtime files tracked"].append({"path": f"{rel}/{sub}", "tracked_files": len(hits)})
        if cls == "key store outside the store":
            findings["key stores outside .credentials"].append({"path": rel, "tracked_files": len(tracked)})
        if cls in ("tool state", "unknown", "retired convention", "key store outside the store"):
            for r2, _, fs in os.walk(full):
                if r2.count(os.sep) - full.count(os.sep) > 2 or "node_modules" in r2:
                    continue
                for fn in fs:
                    if SECRETY.search(fn) and not fn.endswith((".example", ".sample", ".template", ".md", ".ts", ".js", ".py")):
                        findings["key-looking files in hidden folders"].append({"path": os.path.relpath(os.path.join(r2, fn), WS)})
        items.append(it)
    by_class = collections.Counter(i["class"] for i in items)
    by_name = collections.Counter(i["name"] for i in items)
    out = {"machine": a.machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "adr": "docs/adr/0015-the-hidden-estate.md",
           "total": len(items), "by_class": dict(by_class.most_common()), "by_name": dict(by_name.most_common()),
           "findings": {k: v for k, v in findings.items()}, "items": items}
    dst = os.path.join(REPO, "machines", a.machine, "dots.json")
    with open(dst + ".tmp", "w") as f:
        json.dump(out, f, indent=1)
    os.replace(dst + ".tmp", dst)
    print(f"{len(items)} hidden folders: " + ", ".join(f"{k} {v}" for k, v in by_class.most_common()))
    for k, v in findings.items():
        print(f"  {k}: {len(v)}" + (f" (e.g. {', '.join(x['path'] for x in v[:3])})" if v else ""))


if __name__ == "__main__":
    main()
