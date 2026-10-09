# 0016. The building code and the operations centre: every repo is scored, and the whole city is on one live page

Date: 2026-09-25 · Status: accepted · First move of the second plan (`plan/goal-2050.json`, pillars P2 and P4)

**Why:** Shaan, 25 Sep: "now maybe it's like north korea in 2020 how do we get it to china shanghai in 2050". The first
plan made the estate orderly after the fact: a guard, a doctor, one manager sweeping by hand. Nobody could see whether
a single repo met the rules the ADRs set, or see the city working, without asking an agent to go and look.

**Decision:**
- **The building code** is `plan/building-code.json`: twelve rules every live repo on the map is held to (belongs in
  its district, a front door, CLAUDE.md only `@AGENTS.md`, one `.agents/`, no retired conventions, no tool state in
  git, keys in the store, on GitHub, pushed, backed up, worktrees in the one place, named after its GitHub repo). Each
  rule names its ADR and its fix. `tools/code.py` (`estate code`) scores every repo read-only into
  `machines/<m>/code.json`, one line per run in `code-history.jsonl`; `estate code <words>` prints one repo's
  certificate. Our repos meet every rule; a client's repo (`client_owners`) only the rules that do not put our house in
  theirs; anyone else's repo in a district fails `ours` alone. `_reference/`, `_archive/`, `_data/` and
  package-manager clones (`.lake/packages`, `node_modules`) are not buildings.
- **The operations centre** is one stable URL, `http://127.0.0.1:8895/`, served by launchd `com.siso.estate-city`
  (`tools/city.py serve`) and rebuilt from the disk every 2 minutes: the map drawn as a skyline (height = commits in
  30 days, colour = code score, lit = a server runs from it, a dot = an agent works in it, dashed = vacant lots),
  alerts, agents at work (herdr), servers and ports against the registry `plan/ports.json`, the building code, the
  week's commits, backups and machines. `city.json` beside the page holds the same facts for agents.
- Launchd jobs the estate owns are kept as source in `plan/launchd/` and copied into `~/Library/LaunchAgents/`.

**How it holds:** the nightly and the full `estate refresh` re-score; the city page re-scores when the record is over
55 minutes old; `estate doctor` warns with the count and when the page is down. Bringing a repo up to code is the
owner's job (ADR 0009) until Shaan decides P8 (self-government); `untrack-state.py` is the first codemod.

**First score (25 Sep 08:41):** 9 of 141 live repos up to code, mean 62%. 106 ours, 2 clients', 33 someone else's code
sitting in a district. Most failed: one agent folder 76, the CLAUDE.md shim 49, others' code 33, named 30, pushed 28.
