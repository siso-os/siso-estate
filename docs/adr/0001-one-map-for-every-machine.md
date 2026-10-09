# 0001. One map for every machine

Date: 2026-09-23 · Status: accepted

**Why:** Shaan, 23 Sep: "the whole estate map's actually there ... that repo might only have like the repos it needs actually downloaded".

**Decision:** `~/SISO_Workspace` on every machine is a checkout of one private repo, `sisodias/siso-city`. Every repo has one path in it. A machine checks out only what it needs; the rest is an empty folder at the same path (a gitlink, or a `map.json` entry when nested). `.estate/map.json` says which machines have each repo (`on`).

**Consequences:** `estate-bootstrap <machine>` puts a machine on the map. `umbrella.py build` writes the union of every machine's records; `snapshot --push` pins it nightly. A path means the same thing everywhere.

**How it is checked:** `estate census`: live repos not on the map = 0; `map.json` `on` matches each machine's inventory.
