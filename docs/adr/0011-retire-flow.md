# 0011. Taking a project off a machine

Date: 2026-09-24 · Status: accepted

**Why:** Shaan, 24 Sep: "i'd like to have them push to github added to the city but then we might just remove them off the laptop".

**Decision:** `houses.py retire FOLDER --why ... --run`: refuse if any file is in no repo, a process uses it, or a worktree lives elsewhere; save keys; push every branch, stash and uncommitted edit (secret-scanned, verified by `ls-remote`); bundle work on third-party repos into the map; then remove the checkouts, leave empty folders, add map placements, update the manifest. Archived GitHub repos are unarchived for the push and archived again.

**Consequences:** Retiring is reversible: `estate restore --only <path> --run`.

**How it is checked:** retired.jsonl: every repo verified; the map still lists every retired path.
