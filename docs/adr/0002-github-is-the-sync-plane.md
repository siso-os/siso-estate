# 0002. GitHub is the sync plane

Date: 2026-09-24 · Status: accepted (refined by [0014](0014-big-client-files.md))

**Why:** Shaan, 24 Sep: "they need to be pushed to github and sync to github github's our main syncing plane".

**Decision:** Everything worth keeping lives on GitHub (private `sisodias/*` unless public on purpose), including client files and media. A project nobody needs on this machine is synced, verified on GitHub, and taken off the machine; its place on the map stays. No other bulk store (the Mac Mini vault is not the plan).

**Consequences:** GitHub's real limits shape the work: one file under 100 MB (zips get unzipped, or the file is dropped/LFS), repos a sensible size (a client's files go in that client's own repo). The nightly backup's 200 MB WIP cap is a safety net, not a reason to keep work off GitHub.

**How it is checked:** `estate census`: too_big_for_backup = 0; projects.json: nothing laptop-only for a project marked GitHub only.
