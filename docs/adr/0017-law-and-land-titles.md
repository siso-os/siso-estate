# 0017. Law and land titles: every building has an owner, and routine fixes are written law the estate applies itself

Date: 2026-09-25 · Status: accepted · Reform 1 of the second plan (`plan/goal-2050.json`); supersedes the "message the owner
first" rule of 0009 for the routine fixes listed in `plan/fix-policies.json` only

**Why:** Shaan, 25 Sep, answering P8: "yes do this yes", and asking how the estate goes from a village to Shanghai from
first principles. Measured the same day: 54 of our 108 buildings had no owner; the Estate Manager made 92 commits since
23 Sep while other agents reported no mess; the instruction to ask the map reached 8% of sessions, while the HALO lead,
once it owned the worktree rule, put its next 11 worktrees in the right place. Order held by one planner is a decree;
order held by owners and by rules the system applies is law.

**Decision:**
- **Land titles.** `plan/owners.json` names the owning seat of every building, CODEOWNERS-style (longest path prefix
  wins). Land no seat has claimed is state land: the top Agent Zero holds it until it names an owner, and the operations
  centre counts it. `tools/code.py` records each repo's seat and lifecycle (active, warm or dormant, by its own commits;
  the estate's tidying commits do not count).
- **Law.** `plan/fix-policies.json` lists the routine fixes the Estate Manager makes in any lane's repo without asking:
  tool state untracked, stale worktree records pruned, a missing front door written (or CLAUDE.md renamed to AGENTS.md),
  a missing CLAUDE.md shim added. It also lists what it never touches: Cam's code (0007), Oracle ("oracle owns itself"),
  others' and clients' repos, a repo with anything staged, a repo an agent is working in, a dormant building, and a repo
  that ignores or does not track its instruction files. `tools/fix.py` (`estate fix --run`) applies them nightly: one
  path-limited commit per fix, the index checked before it, secret-scanned, pushed only as the branch's one unpushed
  commit, a receipt in `machines/<m>/fixes.jsonl`. A new routine fix is a new line in the policies file.
- **Births.** `tools/births.py` (`estate births`) reads the agents' own history nightly: where each repo, clone and
  worktree was created and whether it was the right place, and whether agents ask the map or search the disk.

**First run (25 Sep):** 26 fix commits in 26 repos, each touching only AGENTS.md or CLAUDE.md; 3 repos skipped because
their `.gitignore` excludes those files (their owners' choice). Up to code went from 9 to 15 of 141.
