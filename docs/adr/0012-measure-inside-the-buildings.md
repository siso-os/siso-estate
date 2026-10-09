# 0012. Clean means the census reads zero

Date: 2026-09-24 · Status: accepted

**Why:** Shaan, 24 Sep: "Have you cleaned up all the homeless people".

**Decision:** `estate census` runs nightly and counts inside every repo, not just the streets: repos on/off the map, disk-only work, duplicate homes, loose files (and piles too big for the backup), throwaway clones, home-folder strays. A cleanup is done when its census line reads 0, never by assertion.

**Consequences:** Blind spot to remember: folders a parent repo ignores (e.g. the Library's `works/`) are invisible to `ls-files -o`; check them by hand until the census covers them.

**How it is checked:** `estate brief` shows each number and its change since the last session.
