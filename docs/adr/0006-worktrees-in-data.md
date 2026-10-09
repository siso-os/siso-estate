# 0006. Worktrees live in _data/worktrees/<repo>/<lane>

Date: 2026-09-23 · Status: accepted

**Why:** Job 4: six worktree conventions collapsed to one.

**Decision:** Every git worktree sits at `~/SISO_Workspace/_data/worktrees/<repo>/<lane>`, never inside or beside its repo. Move one with `git worktree move` (it updates both sides); a repo moved with worktrees inside it needs `git worktree repair`; stale locked registrations are unlocked and pruned once their commits are proven on GitHub.

**Consequences:** Lanes create worktrees there from the start; the census can count strays.

**How it is checked:** `git worktree list` for each repo shows only `_data/worktrees/` paths.
