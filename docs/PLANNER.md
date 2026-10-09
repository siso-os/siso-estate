# The state-planner run

**26 Sep: every card is now written by Opus, 10 at a time** (Shaan: "don't be using haiku ... redo everything, nothing
should be using haiku"). The Haiku notes below are the history of the pilot.

Proposed 25 Sep 2026. Shaan: "every single thing here exists to either provide training data for another or used as a
part of another or a set part of the process ... they all flow like water ... I want everything accountable so then
it's easy to clean ... then we can just de-slopify". This is how the estate gets read end to end without 48 hours of Opus.

## 1. Why a run, and why it is cheap

Nobody has read the whole estate. The map says where each building is; nothing says what it is for, what feeds it and
what it feeds. The register counts 298 buildings, and 102 of them are live, ours, and on this laptop. Reading them is
mechanical work; understanding how they fit together is not. So the reading goes to Haiku and a script, and one Opus
pass does the understanding.

- **Haiku can write a card.** The pilot on `banks/siso-shell` took 48 s and cost $0.13 (26 turns). It named the right
  purpose, its inputs and outputs with file evidence, two real slop items and a stale path (`SISO_Knowledge`, retired 23 Sep).
- **Haiku cannot see who uses a building.** From inside siso-shell it could not see that every estate page and agent
  report is composed with it. Incoming edges therefore come from a script that searches every other building for a
  building's names, never from the model.
- **The whole run is hours, not 48.** 102 cards at 2 at a time is about 1 hour and about $14 at list price, on the
  `claude` (the other usage bucket). The Opus distillation is one fresh session of about 2 hours.
- **RAM:** each headless Haiku is about 300-400 MB; two at a time. The runner waits whenever the load is above 40.

## 2. The phases

| # | Phase | Who | Output | Time |
|---|---|---|---|---|
| 1 ✅ | **Who uses what.** For each building, its names (folder, GitHub repo, package name, bin commands, ports, launchd label) are searched across every other building; each hit is an edge with file:line. | a script (`tools/edges.py`) | `plan/planner/edges.json`: 101 buildings, 152 names, 1,092 edges | 1 minute |
| 2 ✅ | **The history.** The laptop on 22 Sep (`machines/laptop/repos-before-moves-2026-09-23.json`, `dirs-2026-09-23.jsonl.gz`), every move and removal (`moves.jsonl`, `removed.jsonl`), each plan made (MODEL, LEGEND, MERGES, the 2050 goal) and what became of it. | a script (`tools/history.py`) | `docs/HISTORY.md` | seconds |
| 3 ⏳ | **The cards.** One per building (`plan/planner/card-brief.md`): what it is, what it serves, alive or not, fed by, feeds (each with file:line), guesses, slop, questions. | Haiku, `tools/planner.py run` | `plan/planner/cards/*.json` | about 1 hour |
| 4 ✅ | **The check.** Every card's evidence is resolved against the files, and every card edge is compared with phase 1. A card edge nobody can find, or a script edge the card missed, goes on a list. | `tools/planner.py check` | `plan/planner/joined.json` | seconds |
| 5 | **The flow map.** One Opus session reads the cards, edges and history, and writes how the estate flows; the dead ends; what is stupid; what nobody has looked at; the route updated. | Opus, fresh, from `plan/planner/flow-brief.md` | `docs/FLOWS.md`, `docs/SCORE.md` | about 2 hours |

The score rises by itself as the cards land: the ledger tier (15 points) counts carded buildings and evidence that resolves.

## 3. The flows to test (first-principles guesses; phase 5 confirms or kills each)

1. **Money.** HALO pays SISO. Fahmy's agency pays for Bykonz, MelanoTresses and myGUMM work. Partner work under
   `SISO_Agency/partners/` is where the money comes from; everything else either earns through it or makes it cheaper.
2. **Client work → frameworks → the Library.** Each client build meets real problems; what repeats becomes a framework
   (Action Model's, the industries in `hq/`), and frameworks become Works in the Great Library.
3. **The Library → every build.** The banks (8,538 components, 23,778 vetted repos, the UI base, siso-shell's page
   templates) feed the factory, every product, every client app and every page an agent shows Shaan.
4. **The factory → products and client apps.** The Software Factory and superapp-forge turn banks and frameworks into
   `apps/` and partner client code.
5. **Agents do all of it, and their work is training data.** 4,357 sessions in August and September. Sessions become
   lessons, memory and skills (the agent stack), which make the next session better. Which sessions actually feed
   anything is unmeasured.
6. **The estate keeps it findable and safe.** The map tells every agent where things are and where new things go;
   backups keep everything; the score measures the whole.
7. **The window.** SISO Internal Labs is where a human sees and steers all of it (the Agents page, the console).

Only a building that sits on a flow earns its place. One that feeds nothing and is fed by nothing is a slop candidate
for the vault; the cards make that list for the first time.

## 4. Already found while writing this

- **25 Sep changed almost nothing on disk.** 23 Sep: 202 moves, 1,009 repoints, 429 files filed. 24 Sep: 145 housed,
  84 removed with proof, 63 moves. 25 Sep: 31 routine fixes and 4 other record lines; the rest of the day was plans
  (legend, merges, score). That is the "half job" Shaan felt: the plans got ahead of the moves (`docs/HISTORY.md` §2).
- **Haiku's evidence is half right.** In the first two cards, 11 of 22 edges resolve to a real file and line. Cards are
  leads; the check step and the edge scan are what make them evidence.

- **The estate-keeper skill still carries the district table from before the legend.** It has no `partners/`, and HALO
  is still `HALO_Agency/<area>`, so agents that follow it place things by the old shape. The skill should send agents to
  `estate new` and the legend's three questions instead of its own table. It is the agent stack's (siso-skills-hub).
  The workspace root is clean today: every entry is in `plan/layout.json`.
- **Retired names live on inside buildings.** The pilot card found `SISO_Knowledge` in siso-shell's sources. The docs
  check found 69 broken paths in AGENTS.md files; the cards will find the rest.

## 5. Run it

```bash
python3 tools/planner.py list          # the 102 buildings still to card
python3 tools/planner.py run -j 2      # card them on the claude-siso login, 2 at a time; safe to stop and resume
python3 tools/score.py                 # the ledger tier moves as cards land
```
