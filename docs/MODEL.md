# The estate model: first principles, the territory, and how it works

Status: v3, 25 Sep 2026 · Owner: the Estate Manager (ESTATE) · Decision: `docs/adr/0018-the-estate-model.md` ·
Machine-readable contract: `plan/model.json` · Shaan's words: `.agents/source/2026-09-23-shaan-verbatim.md` (25 Sep entries).

**Superseded in part (25 Sep, Shaan's correction):** §1 and §4 (the islands) are wrong at the root and are replaced by `docs/LEGEND.md` (proposed; machine form `plan/legend.json`): SISO Agency is the whole business, HALO and Fahmy's agency are its partner agencies, the Great Library is the commons everything reads from, and the estate is the private map. Where this file and the legend disagree, the legend wins.

For agents: read `docs/LEGEND.md` first, then §12 here always. Read the rest when your work touches it. Every number here was measured on 25 Sep
2026 and names its source; re-measure before you rely on one, because receipts age.

## 1. The estate in one screen

The SISO estate is a **territory: all its land and all its water**. Land is where things are kept; water is how they move.

- **Islands** are the top-level folders of the plan (`~/SISO_Workspace`, repo sisodias/siso-city, the same on every SISO
  machine): Agency Island (`SISO_Agency`, the main island: the business), Engine Island (`SISO_Agents`: the seat of
  government and the engine works), Library Island (`Great_Library_of_SISO`), HALO Island (`HALO_Agency`, Cam's, by
  treaty), Home Island (`personal`, private). The underscore folders are service islets: the utility yard (`_data`), the
  vault (`_archive`), customs (`_inbox`), the foreign quarter (`_reference`).
- On an island: **districts** (only where an island holds compounds of different purposes), **compounds** (a project:
  one name, one keeper, one gate, one or more buildings), **buildings** (git repos), each on a **plot** (its path).
- Each island and compound carries a **law**. HALO's code stays in Cam's repo, a client's data stays inside its
  compound, and foreign code is never edited or run.
- **Sites** are machines. Each SISO site keeps the whole plan and builds only the plots it needs; GitHub holds every
  deed and blueprint, so any site can build any plot in about a minute. Client machines only ever hold their own
  island's plots.
- **Citizens** are agent sessions: they live once and remember nothing. **Seats** are the offices that persist.
  **Crews** are cheap workers that apply one written by-law each.
- One **register** records all of it. Every map, list, page and render (including the future 3D island) is a view of
  the register, and no view is a source.

The purpose: any agent, cheap or smart, in any harness, on any site, can find, understand, change, run and hand on
anything SISO has, without Shaan. And the estate becomes easier to read as it grows, not harder.

## 2. Physics: what this estate measures like

| # | Fact | Number (25 Sep) | Source |
|---|---|---|---|
| F1 | Citizens are temporary and forgetful | 4,357 sessions in Aug–Sep (1,717 Claude, 2,640 Codex); none remembers the last | `machines/laptop/births.json` |
| F2 | Reading is the cost | 1.77B tokens/day; 507 read per 1 written; a typical Claude session reads 41k tokens and makes 21 calls (10 of them disk searches) before its first edit, on a 55k boot | `~/.tokentracker`, 294 sessions since 1 Sep |
| F3 | Agents act where they stand, by the cheapest path | Sep: 2,036 disk searches vs 40 map asks; 384 of 712 births landed in the wrong place, mostly from sessions started inside a building (174) or a `.worktrees` folder (171) | births.json |
| F4 | Instructions fade with distance from the action | 8% of sessions used the map although every brain says to; one owner adopting the worktree rule got 11 of 11 right; the guard at the road stopped the rest | births.json, ADR 0017 |
| F5 | Copies drift | 5 project lists (A0 `SISO_Agents/agent-zero/siso-firstmate/siso/plan/projects.json`, `plan/owners.json`, `tools/projects.py`, Library Works, Plane); skills 47 in the hub vs 53 and 56 in the two projections; the Library's Agent Stack v2 names parts that have sat idle for 45–57 days, while harness-lab, skills-hub, agent-brain and Jev carry the work; `machines/laptop/projects.json` is stale since 24 Sep 14:09 because nothing runs its generator | read on disk |
| F6 | Docs are written by hand and rot | up to 124 of 784 paths cited in 93 of our AGENTS.md no longer resolve (an upper bound: some are branch or repo names), in 31 doors; one doc check exists (Oracle's `HALO_Agency/oracle/docs/check.mjs`) | path check over every door |
| F7 | Work is concentrated; most things go quiet | of our 108 buildings: 34 active, 50 warm, 24 dormant; the top 20 hold 96% of the month's commits | `machines/laptop/code.json` |
| F8 | Mess is born continuously | 712 births since Aug; 2.2 GB of runtime inside the workspace's own `.agents/`; 4 stray copies of the Project OS template inside actionmodel | births.json, du |
| F9 | Authority boundaries are real and asymmetric | HALO: Cam holds root on halo-vps and owns camronkellman/halocrm; clients own their data; 33 foreign repos sit in districts; 3 `_reference` clones run in production on siso-vps (buzz, orca, tokentracker) | code.json, `machines/vps-siso/placements.json` |
| F10 | Machines sleep and fail | the Mac Mini unreachable since 23 Sep; the laptop sleeps; siso-vps is the one always-on site | `plan/machines.json` |
| F11 | Most capability is assembled, not written | adopted products on the map: listmonk, Chatwoot, Hi.Events, AFFiNE, kaneo, camofox, buzz; banks of 8,538 components and 23,778 repos; the SISO thesis is assembly from distilled GitHub | projects.json, `upstream` remotes, VISION.md |
| F12 | Capability is rebuilt when it cannot be found | six overlapping agent-OS attempts in Engine Island (siso-os, siso-agent-base, siso-agent-runtime, siso-agent-stack, siso-project-os, siso-project-team); siso-os carried a second skills hub; five pieces "built, correct, unwired" (the software-factory memory) | code.json, READMEs |

## 3. First principles

Each principle is forced by facts above. The mechanism is how it holds without anyone remembering it.

1. **Order lives in the land, not in the citizens** (F1, F4). What an agent must know has to be where the agent stands
   and enforced at the road it takes. Mechanism: doors and cards at every level, the landing packet, guards.
2. **Design for the reader** (F2). The estate is paid for in reading. Doors are one screen; context arrives
   pre-assembled (~8k tokens); the boot does not grow with the estate; finding costs the same at 141 buildings or 141,000.
   Mechanism: the register, `estate where`, the landing packet.
3. **One home, one name, one keeper** (F5, F12). Every thing has one place, one permanent name (its postcode) and one
   owner. Two copies of anything drift, and two lists of anything disagree. Mechanism: the register, postcodes, owners.
4. **Generated, never hand-kept** (F5). Any list, map, index or table is computed from the things themselves, by a
   generator wired to the event that changes its input. The nightly is only the floor. A generator nobody runs is a
   stale document waiting to happen. Mechanism: generators with triggers; the doctor fails hand-kept lists.
5. **Every document is generated, checked or a journal** (F6; Shaan, 25 Sep: "it relies on an agent to go through and
   change it, rather than somehow code changing it"). Generated: facts derived from code or the register, between markers.
   Checked: authored claims that cite code (path, line, identifier, command), verified on every commit and nightly, as
   Oracle's `HALO_Agency/oracle/docs/check.mjs` already does. Journal: dated and append-only (HANDOFF State sections, `source/`,
   `owners.log`, ADRs); a journal claims nothing about the present. An authored, present-tense, uncited paragraph is what
   rots, so keep it to the door's one-screen "why". A citation broken by a recorded move (`moves.jsonl`, `repoints.jsonl`)
   is a pebble a crew repairs; any other broken citation goes to the keeper.
6. **Mechanism beats instruction** (F3, F4). Births go through templates (`estate new`), upkeep through by-laws (crews),
   boundaries through guards and customs. Instructions only explain the mechanism.
7. **Stop the flow, then drain the stock** (F8). Count births per day. When a pebble keeps washing up in the same place,
   fix its source (the template, the guard, the harness). Crews handle what is left.
8. **Keep the living close and the quiet in the registry** (F7, F10). Hot plots are built on the working site, and
   anything that must run lives on the always-on site. Quiet buildings go dark: an empty plot with its deed on GitHub,
   one ferry away. Any site can be rebuilt from the plan.
9. **Law follows land** (F9). Every island and compound declares its law; the water enforces it (customs: secret scan
   before anything leaves, HALO never copied, client data never shipped off its site). Crossing a coast changes the
   rules, and the packet says so.
10. **Find before you build; adopt in the open** (F11, F12). Search the register and the banks before anything is born.
    Code taken from others is either foreign (studied, never run or changed) or adopted: it gets a plot, a keeper, an
    `upstream` remote and a recorded licence. Running or editing a foreign repo adopts it on the spot.
11. **The estate measures itself** (all). A claim about the estate is a number the estate computes (census, code score,
    births, lookups, door checks). "Done" means measured.

## 4. The territory

| Place | Folder | What it is | Law | Governor |
|---|---|---|---|---|
| Agency Island (main) | `SISO_Agency` | the business: `hq/`, `apps/` (products), `clients/` (client compounds), `factory/` | SISO's; client compounds carry the client's law | A0 |
| Engine Island | `SISO_Agents` | government and engine works: `agent-zero/`, city hall `siso-estate`, the Guild (`siso-harness-lab`, `siso-skills-hub`), the agent machinery, compute | SISO's | A0 |
| Library Island | `Great_Library_of_SISO` | knowledge, the catalogue of Works, the banks (the parts depot) | SISO's, public by default | A0 |
| HALO Island | `HALO_Agency` | Cam Kellman's agency: crm, oracle, kellman, collegebesties, inspiration (compounds; no districts yet) | Cam's (ADR 0007) | CRM (the HALO lead) |
| Home Island | `personal` | Shaan's life | private | Shaan |
| Utility yard | `_data` | rooms (`_data/worktrees/<repo>/<lane>`), runtime (`_data/runtime/<postcode>/`), caches, databases | — | ESTATE |
| Vault | `_archive` | retired things, read-only, with MANIFEST lines | — | ESTATE |
| Customs | `_inbox` | arrivals to clear and file | — | ESTATE |
| Foreign quarter | `_reference` | others' code to study; never edited or run | the author's | ESTATE |

**The water** is part of the estate: deeds (git push to GitHub), ferries (a building brought to a site or sent dark),
deploys (a building run on a site or published to a lighthouse, i.e. a public page), intent (Shaan's words to a seat),
letters (messages to seats), events (births, moves, pushes, deploys, breaks), context (packets to citizens), history
(what was decided and tried) and customs (checks at every harbour). §8 has each flow's state.

**Sites** (machines): the laptop is the working site, where Shaan lives and the hot plots are built. siso-vps is the
always-on site and runs 12 placements today. The Mac Mini is the storage site (down since 23 Sep). The OVH box is the
power site (the Qwen pool, not yet in the register). halo-vps is HALO's own site (Cam's root), hetzner is HALO's
streaming pier (closing once Oracle moves onto halo-vps), bykonz-contabo is Bykonz's site, and Cam's Mac hosts HALO's
own citizens (kellman on Codex). hellzinger (77.42.66.40) is unknown and refuses our key. Our sites carry the whole
plan; client sites never do. For them we keep a consulate record: which of our buildings are placed there and whose
rules apply.

**Naming.** A building's **postcode** is its GitHub name: `<repo>` for ours (sisodias names are unique; ADR 0003 already
makes the folder name equal the repo name), `<owner>/<repo>` otherwise. A postcode never changes when a building moves.
Its **address** (`<island>/<district>/<compound>/<building>`) is where it sits now; the register derives it and keeps
old addresses as aliases. Durable text (briefs, memory, handoffs, docs) names things by postcode; paths are looked up,
never written down.

## 5. The anatomy

Every level has the same two things: a **door** (AGENTS.md, one screen, for agents) and a **card** (a small JSON, for
tools). A fact lives at the lowest level that owns it, and everything above is generated from below. A level exists
only when it is needed: a compound gets a gatehouse repo at its second building, and an island gets districts when its
compounds differ in purpose.

| Level | Door and card | Holds | Never holds |
|---|---|---|---|
| Territory | root AGENTS.md (prose by ESTATE; tables generated) · the register | laws (ADRs), the register, seats, sites, the event log, cross-island memory | anything a lower level can hold; runtime |
| Island | island door · `island.json` (law, governor, sites, districts) | its treaty and who may cross | another island's code or data |
| District | door generated from its compounds · `district.json` | its purpose and governor | copies of compound facts |
| Compound | gatehouse AGENTS.md · `project.json` (buildings and kinds, keeper, page URL, client) | HANDOFF (the project's now), `source/` (Shaan's words to it), tasks spanning buildings, what the project knows as checked data (Oracle's `HALO_Agency/oracle/docs/data` + its check), `intake/` for clients, `owners.log` | building code, runtime |
| Building | AGENTS.md · `building.json` (postcode, kind, compound, provenance, upstream, runs-on, ports, URLs, depends-on) | `.agents/`: HANDOFF.md, owners.log, memory/, skills/, tasks/; its kind's extras | runtime, rooms, keys, other buildings' facts |
| Room | inherits the building's | a branch and a task, in `_data/worktrees/<repo>/<lane>` | a second `.agents/` |
| Seat | seat card (brief, mailbox, holdings) | its letters and titles | a pane id or a machine |

**Compounds on disk today.** Three shapes exist:
- A gate repo holding its buildings: Oracle is sisodias/oracle-streaming holding core, operator-app, agent-zero,
  uihub and archive. Fahmy's account repo holds 6.
- A folder with a door but no repo: siso-internal-labs holds 3.
- A loose folder: HALO_Agency/crm holds Cam's repo plus piles.

The rule for all of them is the 5 Sep link rule: every building has exactly one compound, and the building's card
and the gatehouse's `project.json` must agree. A gatehouse stays small; in Oracle's own words, "Keep this repository
small: the README, this file, docs/, the live-test skills and the submodule pins".

**Building kinds:** app, service, package, site, infra, data (`-source`, `-sessions`, corpora), research, docs, tool,
gatehouse. **Provenance:** ours, adopted (an `upstream` remote or fork parent, licence recorded), client (their code
under their law), foreign. Foreign code has four roles, and the register names each one:
- shared study, in the foreign quarter;
- study bound to one work, e.g. the Erdős baselines or the 19 Shopify theme references inside home-essentials, kept
  untracked where that work's tool puts it;
- vendored into a building, e.g. SISOCRM's `.teable-runtime`; prefer a package, or adopt it;
- in use, meaning run or changed, e.g. camofox, devspace, openwa or Fahmy's Chatwoot and Postiz; it must be adopted.

Dormant foreign clones on an island are stale and belong in the vault. The Project OS lifecycle (missions, sprints, runs, claims, verification,
delivery; `SISO_Agents/siso-project-os`) is an extra for buildings that run heavy agent work. It is real in 3 buildings
today (actionmodel, siso-agent-runtime, siso-agent-stack), and it is not a default.

**Templates.** Each level and kind has one template with one home: siso-project-os/template for buildings and
gatehouses, trimmed to the core plus a kind overlay; siso-estate for islands, rooms and the register;
agent-builder/siso-owner for seats; siso-shell for pages (24 families). A slot joins a template only when most
buildings of that kind fill it. Templates are versioned, and the building code checks each building against the
version it was born from.

## 6. Invariants the register enforces

I1 one permanent postcode per building · I2 exactly one compound per building, one island per compound · I3 an owner
seat for every building, else its island's governor · I4 a provenance for every building, with an upstream for adopted
and foreign · I5 nothing foreign is edited or run · I6 no runtime, rooms or keys inside a building, and no tracked tool
state · I7 every list of estate things is generated · I8 an island's law travels with its code · I9 every built plot
and running service on every site is registered, with its port · I10 every birth comes from `estate new`, and any
other birth is a reported pebble · I11 every document block is generated, checked or a journal.
`plan/model.json` names what checks each one today and what is still to build.

## 7. Lifecycle

`born (estate new)` → `active` (own work within 14 days) ⇄ `warm` (within 90) ⇄ `dormant` (none for 90). These moves are
measured, not decided. `dormant` → `dark` happens by by-law once GitHub is proven to hold everything. `dark` → `active`
is `estate open`, under a minute. Moving to `archived` (vault) or `retired` is a decision (ADR 0004, 0011). A foreign
repo becomes `adopted` the moment it is run or changed.

## 8. The water today

| Flow | Mechanism now | State |
|---|---|---|
| deeds | git push; `estate backup` nightly | running |
| ferries | `estate restore`, `houses.py retire`; target `estate open` | partial |
| deploys | `/opt` placements on siso-vps (12); the publish skill | partial; ports unregistered |
| intent | `source/` verbatim, the console, A0 routing | running |
| letters | herdr-send (pane-bound), `estate report` (INBOX.md), console inbox; target an addressed post that reaches a seat on any site | partial |
| events | nightly and hourly refresh; target a radio that updates the register within 60 s | batch only |
| context | boot plus disk search; target the landing packet | missing |
| history | 4,357 sessions on disk, unindexed; target Library Island per postcode | missing |
| mess | births/census → by-laws (`tools/fix.py`) → receipts; target crews + a 1-in-10 audit | by-laws run by the manager |
| customs | gitleaks before any push, NEVER_COPY, the guard | running |

## 9. Documents

Documents follow principle 5, and the anatomy says where each kind lives:

- **Generated**: district door tables (`tools/doors.py`), LAPTOP-MAP, the brain (harness-lab), skill projections,
  every list of projects. Each generator is wired to its trigger. Today `projects.py` has none, and the skill
  projections drift (47/53/56).
- **Checked**: a door's commands, paths and claims about code. Target: `estate docs check`, which generalises Oracle's
  `check.mjs`, runs on commit and nightly, and is born with every building.
- **Journal**: HANDOFF State sections, `source/`, `owners.log`, ADRs, receipts. Each is dated and appended to, never
  rewritten. "What is true now" is generated from the latest entries.

## 10. Pebbles, rocks and crews

A **pebble** is small mess that washes up every day: a stray file, tracked tool state, an empty or duplicate folder, a
finished worktree, a path broken by a recorded move, a missing door line, a scratch pile, runtime in `.agents/`, a
stray template copy. A fix counts as a pebble fix only if all four hold: a by-law in `plan/fix-policies.json` covers
it, it touches only named paths, it can be undone, and it leaves a receipt in `machines/<site>/fixes.jsonl`. Anything
else is a **rock** and goes to the keeper or to Shaan.

**Who sweeps: code first, crew second, keeper third.**
- A pebble whose fix is deterministic is swept by code under its by-law, with no model involved. Examples: a door
  path repointed from `moves.jsonl`, tool state untracked, a finished worktree pruned.
- A pebble that needs light judgment goes to a **crew**: a cheap worker (DeepSeek via `omp-worker`, or the Qwen
  pool) given one by-law, running nightly and on events. Examples: writing a missing door line, classifying a stray
  file.
- Everything else goes to the keeper. The Estate Manager writes by-laws and audits one receipt in ten; it does not sweep. Today there are 4 by-laws
(tool state, finished worktrees, door lines, CLAUDE.md shims), and their 26 fixes were swept by the manager on Opus:
the wrong crew. A pebble that returns to the same place three times means a hole in the sea wall, so fix its source.

## 11. Views

The chart (root AGENTS.md and generated district doors), `estate where`, the operations centre
(http://127.0.0.1:8895/), the console (:8891), the Work page (sisolabs.space/agents), the Library site, the
lighthouses (project pages), and later the **island render**: a three.js voxel scene built from the register and
published to a Cloudflare URL for the app to embed. Its contract is `plan/model.json` → `render`. Islands are land
masses; buildings are towers (footprint by size, height by 14-day activity); colour shows lifecycle; a red roof means
below code; a light means running; a dark building is an empty plot outline; flows are boats on the water. The render
needs nothing beyond the register.

## 12. How an agent works in the estate

1. **Know where you stand**: your building's postcode, its compound, its island and so its law, and its keeper. If you
   do not know, run `estate where .`.
2. **Ask, don't search.** `estate where <words|postcode>` finds any building. A disk search for a building is a map
   miss; report it.
3. **Create only through the road** (`estate new`, once it exists). Until then, run `estate where` first and place new
   work in its island and compound. Never create at a root or in `~`.
4. **Work in a room**: `_data/worktrees/<repo>/<lane>`, never beside or inside the building.
5. **Write by postcode** in anything durable. Look paths up; don't copy them into prose.
6. **Obey the island's law.** HALO and client code never leave their repos and sites; nothing foreign is edited or run;
   anything adopted keeps its `upstream` remote.
7. **Write back where the fact belongs**: HANDOFF and `owners.log` in the building or gatehouse, memory in the owner's
   `.agents/memory/`. Put every present-tense claim either in a generated block or as a checked citation.
8. **Pebbles you see:** fix one only if a by-law covers it. Otherwise run `estate report "<what, where>"`.
9. **Runtime** goes to `_data/runtime/<postcode>/` or tmp, never into `.agents/`.
10. **Find before you build:** check the register, the banks (`ui-pick`, the component and repo banks) and the Library
    first.

## 13. How the last two days fit

The work of 23–25 Sep laid the land administration this model sits on:

| ADR / tool | In the model |
|---|---|
| 0001 one map for every machine | the plan on every site |
| 0002 GitHub is the sync plane | the deed registry; the main shipping lane |
| 0003 one place per repo | one plot and one permanent postcode per building |
| 0004 nothing deleted without proof · 0011 retire flow | the vault; the archived and retired states |
| 0005 no compat links | refer by postcode, never by path |
| 0006 worktrees in `_data` | rooms in the utility yard |
| 0007 HALO never copied · 0008 secrets never leave | law follows land; customs on the water |
| 0009 other agents' work is theirs | keepers own their compounds |
| 0010 houses and blocks | compounds (a client block is a compound with a gatehouse) |
| 0012 measure inside the buildings · 0016 building code + operations centre | the estate measures itself; one view |
| 0013 the Estate Manager | the harbour master and land registry seat |
| 0014 big client files | data planes: deep storage off the plots |
| 0015 the hidden estate | the building's interior: one `.agents/`, tool state never tracked |
| 0017 law and land titles | owners (I3) and by-laws (§10) |
| `estate where`, `refresh`, `doors.py` | the chart room, first version; generated doors |
| `census`, `code`, `births`, `dots` | measurement (principle 11) |
| `fix.py` | by-laws, still run by the wrong crew |
| `backup`, `data-backup`, `restore`, `houses.py` | deeds, deep storage, ferries |
| the guard, `estate-bootstrap`, the nightly | walls at the road; building a site from the plan; the floor trigger |

Built on 25 Sep for stage 1:
- `tools/register.py` writes `machines/register.json`: 298 buildings in 116 compounds, with island, postcode,
  provenance, keeper, sites, lifecycle and 14-day activity. It has a doctor; run `estate register`.
- `estate where <postcode>` resolves in one step.
- `tools/docs_check.py` (`estate docs`) finds 69 broken door paths, 6 of them repairable from move records.
- `tools/packet.py` (`estate packet <building>`) gives 1.7k–3.6k tokens per building.
- The nightly now runs `projects.py` (which also missed HALO until today), the register and the docs check.
- `tools/new.py` (`estate new <island>/[<district>/]<compound>[/<building>]`) is the road: it places, names, cards and
  titles a new building, and it refuses second homes, and HALO without the lead's go. It is a dry run by default.
- `door-paths`, the first by-law swept by code: it repoints door citations broken by a recorded move.

What the model still needs: `estate new` for each level and kind; the landing
packet; `estate docs check`; crews on the by-laws; the post; the radio; `estate open`/`run`; consulate records; the
register-driven views.

## 14. The plan, in the order the model needs it

1. **The register (the keystone; first version built 25 Sep: `estate register`).** Generate one register from the disk, GitHub, placements, owners and cards. Give it
   compounds, islands, provenance (from `upstream` remotes), sites and lifecycle. Generate the five project lists from
   it (A0's `projects.json` becomes a view) and wire every generator to a trigger. Everything below reads it.
2. **Postcodes and the landing packet.** `estate where` resolves postcodes from the register; an MCP tool in every
   harness; a packet generator. Measure: calls and tokens before the first edit.
3. **Documents that keep themselves.** `estate docs check` (Oracle's pattern, generalised), path repair from recorded
   moves by crews, and generated blocks for every door's facts.
4. **Births through the road.** `estate new` exists for buildings and compounds (25 Sep). Still to do: island and
   seat templates, and the guard pointing wrong-place births at it. Adopt `_reference`
   code that runs or is changed (buzz, orca, tokentracker on siso-vps).
5. **Crews on the beach.** By-laws run by DeepSeek workers with receipts, audited one in ten. New by-laws: strays,
   empty folders, broken paths from moves, template copies, runtime in `.agents/`.
6. **Engine Island first.** Match each Agent Stack part to one building. Put the overlapping OS attempts (siso-os and
   older Agent Zero homes) to the vault through their owners. Every multi-building compound there gets a gatehouse.
7. **The water.** The post, the radio, ferries, siso-vps as the always-on chart room, the Mini back, consulate records.
   Once Oracle is on halo-vps, HALO Island is fully mapped.
8. **Views from the register.** The operations centre, the Work page, the Library, and the island render.

These stages are the route's reforms (`plan/goal-2050.json` R1–R6) laid on the model. Stage 1 is what R3 lacked.

## 15. Open questions and known conflicts

- **Oracle** spans two islands: its gate is in HALO_Agency, its code lane in SISO_Agency/apps/oracle-streaming. The
  Oracle lane owns this.
- **Engine Island overlap:** siso-os (dormant 159 days, with 6 foreign repos inside it), siso-agent-base (retired),
  older Agent Zero homes, and the foreign kunchenguid/firstmate beside siso-firstmate. The agent stack and A0 own these.
- The **Library's Agent Stack v2** describes parts that have gone quiet. Either reconcile it with what runs, or version
  it (the Library and the agent stack).
- **Bykonz** has its own site and a seat holding 13 buildings. It may graduate from a compound to its own island
  (Shaan).
- **hellzinger** (77.42.66.40) is unknown (Shaan).
- **Skill projections** drift: 47 in the hub vs 53 and 56 projected (the agent stack).
- **Foreign code runs in production** on siso-vps (buzz, orca, tokentracker) and HALO works on a buzz fork. Adopt them
  (ESTATE with the HALO lead).
