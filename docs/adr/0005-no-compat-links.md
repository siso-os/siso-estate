# 0005. No compatibility links; moves repoint their consumers

Date: 2026-09-23 · Status: accepted

**Why:** Job 1 retired all 53 compat links; Shaan wants no links at the root.

**Decision:** `estate move SRC DST --no-link --why ...`. Consumers (configs, docs, scripts) are repointed with `tools/repoint.py` (plan reviewed first), running servers are restarted from the new path first, and inbound symlinks are retargeted. `RETIRED.txt` keeps the old -> new table.

**Consequences:** Moving a folder is a small migration, not a rename: check `lsof +D`, `herdr agent list`, worktrees, inbound symlinks, nested-repo ignore lines in the parent.

**How it is checked:** `estate doctor`: 0 links at the root; `tools/retire-links.py check` finds no consumer of an old path.
