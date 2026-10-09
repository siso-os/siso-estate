# 0003. Every repo has exactly one place

Date: 2026-09-23 · Status: accepted (refined by [0014](0014-big-client-files.md))

**Why:** Workspace rule 1; D7; 24 Sep: "places for everything".

**Decision:** A repo lives at one path; its folder name is its GitHub name (client code houses are `clients/<brand>/code/`, the one standing exception). Every sisodias GitHub repo has a place on the map, checked out or not (`plan/github-placements.json`). Second checkouts of the same repo are duplicate homes and are removed; worktrees are not homes.

**Consequences:** New repos go where `estate where` and the district's AGENTS.md say; `umbrella.py` includes placements; `estate restore --only <path> --run` brings any placed repo back.

**How it is checked:** `estate census`: GitHub repos without a place = 0; live duplicate homes = 0 (or an owner's reason logged).
