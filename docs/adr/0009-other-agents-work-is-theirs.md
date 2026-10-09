# 0009. Other agents' work is theirs

Date: 2026-09-23 · Status: accepted

**Why:** Workspace rule 8 and the lanes' ground rules.

**Decision:** Never stage, reset, stash or commit what you did not write. To commit only your change in a shared file: stage HEAD's version plus your edit (`hash-object` + `update-index --cacheinfo`). To save someone's unpushed work, push it to `refs/heads/estate/<date>/*` on its own remote (snapshots use a temp index); never rewrite their branches. Brain, hooks, skills and harness homes belong to the agent stack; Oracle apps to the Oracle lane; HALO to the HALO lead: message them with the exact change and act on their go.

**Consequences:** Coordination is logged in `plan/COORDINATION.md`; a no-go or later is honoured.

**How it is checked:** `git diff --cached` before every commit shows only your paths.
