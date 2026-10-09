# 0015. The hidden estate: one agent folder per repo, harness folders hold config only, tool state is never tracked

Date: 2026-09-24 · Status: accepted · Extends 0001 (one home) to the hidden folders inside every repo

**Why:** Shaan, 24 Sep: "surely we should have standardized ways to do dot agent folders and how the ui hub works ...
the hidden estate infrastructure". The census found 828 hidden folders under 150 names in the workspace, outside
worktrees: `.claude` 153, `.omc` 67, `.agents` 53, `.serena` 20, `.docs` 18, `.uihub` 13, `.codex` 9, and 30 more
agent conventions (`.memory`, `.orchestrate`, `.relay`, `.plans`, `.scratch`, `.skills`, `.siso-wiki`, `.tasks` ...).
Runtime state was tracked in git (`.omc/` in 20 repos, `.claude/instance-uuid.json`), agent state was split between up to
five folders per repo, and six repos held worktrees inside themselves. An agent starting in a repo could not tell which
folder is the real one.

**Decision:** the standard is siso-project-os's template (`SISO_Agents/siso-project-os/template/`), which the estate adopts.

| Hidden folder | What it holds | Tracked |
|---|---|---|
| `.agents/` | **the one agent folder**, whatever the harness: `HANDOFF.md`, `memory/` (one fact per file + `MEMORY.md`), `source/` (Shaan's words), `tasks/` (the task registry), `skills/` (project skills), `briefs/`, `runs/`, `sprints/`, `missions/`; `scratch/` is ignored | yes |
| `.uihub/` | the UI loop (siso-project-os `docs/ui-loop.html`): `campaigns/`, `generated/`, `adapters/`, `_templates/` | yes, except its server pid and caches |
| `.claude/` | Claude Code's config only: `settings.json`, `skills/`, `agents/`, `hooks/`, `commands/`, `rules/` | yes |
| `.codex/`, `.cursor/`, `.opencode/` | that harness's config only (hooks, rules); no skill copies (skills live in `.agents/skills/`) | yes |
| `.github/`, `.husky/`, `.vscode/`, `.devcontainer/`, `.cargo/`, `.storybook/`, `.yarn/`, `.sqlx/` | the project's own tooling | yes |
| tool state: `.omc/`, `.serena/cache/`, `.playwright-cli/`, `.wrangler/`, `.firecrawl/`, `.vercel/`, `.idea/`, `.claude/session-context/`, `.claude/instance-uuid.json`, `.claude/feedback/` | runtime state a tool rewrites | **never**: ignored machine-wide (`~/.config/git/ignore`) |
| `.worktrees/` | nothing: a worktree lives in `~/SISO_Workspace/_data/worktrees/<repo>/<lane>` (0006) | never |

Agent conventions that predate this fold into `.agents/` when their owner next works in the repo: `.memory/` → `memory/`,
`.plans/` → `sprints/` or `briefs/`, `.orchestrate/` and `.relay/` → `runs/` or `HANDOFF.md`, `.scratch/` → `scratch/`,
`.skills/` → `skills/`, `.tasks/` → `tasks/`, `.briefs/` → `briefs/`, `.siso/` and `.siso-wiki/` → `memory/` or `docs/`.

**How it holds:** `tools/dots.py` classifies every hidden folder against this table on every `estate refresh` and
nightly (`machines/<m>/dots.json`); `estate doctor` warns on tracked tool state, retired conventions and worktrees inside a
repo, naming each. The machine-wide ignore stops new tool state reaching any repo. Nothing is moved inside another lane's
repo without its owner; the dots report tells each owner what to fold.

**Consequences:** one place per repo for everything an agent writes; `git status` stops showing tool noise; the
backup's snapshots stop carrying tool caches. Tool state already tracked is untracked (`git rm --cached`) by one
path-limited commit per repo, never touching other work.
