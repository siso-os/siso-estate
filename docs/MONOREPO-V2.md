# SISO monorepo v2: one platform, many branches (19 Oct 2026)

Shaan, 19 Oct: "classify, deduplicate and then use Opus reasoning to design the most amazing monorepo for our whole
ecosystem and agency ... scales for the whole agent base ... leverage different github storages ... UI hub and all of our
different sites, OS stuff ... a gaming branch of SISO agency and all these different branches of SISO labs".
Builds on `docs/MONOREPO-DESIGN.md` (17 Oct, v1) and `docs/MONOREPO-PLAN.md`. Evidence: 8 scout reads on 19 Oct in
`plan/monorepo-research/2026-10-19/` (s1 modules, s2 duplicates, s3 every repo, s4 business words, s5 surfaces,
s6 machines and storage, s7 agents, s8 prior research).

## 0. Where v1 got us, and what it missed
- Kept from v1 (proven 17-18 Oct): one private code repo `sisodias/siso`; permanent module ids (`module.json`,
  `.siso/moves.jsonl`), so any re-lay is cheap; seats as folders with sparse cones; checks over prose; data outside git.
- State today: 117 modules, 109,462 files, 1.79 GB tracked, 1.57 GiB pack; 19 seats; 131 repos archived.
- What v1 missed:
  1. **The business.** The tree is laid out by kind of code (agents/, packages/, products/, agency/hq/, agency/industries/,
     library/). Shaan thinks in branches: SISO Agency, SISO Labs and its ventures (gaming, music, video, research),
     SISO OS, the Great Library and Agent Base (s4). No folder says "this is Labs", and no slot exists for a new branch.
  2. **The duplicates came in with the imports.** 5,932 duplicate blob groups (130.9 MB); 4 to 5 overlapping design
     systems; lifelock and siso-internal share 2,785 identical files (s2, s5).
  3. **Storage is one org.** 646 of 752 live repos are data (134 GiB). sisodias mixes code, data, archives and
     clients; a SISO-DATA org already exists, with 499 home-backup shards (s3).
  4. **Agent scale.** Hand-kept `t3-seats.json` (24) has drifted from the generated registry (19). One seat (estate)
     owns 40% of modules. Every land runs the full check on the laptop, and lanes race (s6, s7).
  5. **The laptop is the bottleneck.** 8.8 GiB free, about a day to full; load 28 on 8 cores; 24 lanes at up to
     1.9 GB each (s6).

## 1. The idea in one line
**One platform, many branches.** Shared code lives once in `platform/`. Every business unit is a folder in
`branches/` with the same shape, so a new branch (gaming) is one command. Code moves only one way:
**branch -> platform, never branch -> branch.** If two branches need something, it is promoted into the platform.
This is the Backstage domain -> system -> component model (s8 §4) laid onto folders, and it is what keeps 50
branches as easy to understand as 5.

## 2. The tree
```
siso/
  AGENTS.md            router <= 100 lines; generated index of branches, modules, owners, checks
  decisions/           ADRs (0021-0024 kept; 0025 branches + platform; 0026 storage tiers; 0027 seat scale)
  platform/            used by every branch; nothing here imports a branch
    ui/                ONE design system: @siso/tokens, @siso/ui, blocks, viewer (absorbs ui-suite,
                       design-system, ui-base legacy, siso-shell bank, @agent-base/tokens)
    site/              site-kit, router worker, cms, booking, edge-auth, analytics
    os-kernel/         business-os kernel, auth, data model, notes backend (one copy)
    agents/            zero runtime, harness, hooks, skills, work (claims), team, adapters
    tooling/           bin/ libraries, checks, oss export, the gardener
  branches/
    agency/            SISO Agency: hq (brand, site, landing), factory, agency/industries/<x> packs,
                       clients/<brand> (brand + data manifests only), partners/<x> (pointers only)
    os/                SISO OS: siso-internal (with lifelock as a module), agent-base app, voice, browser,
                       music, siso-code pointer (our T3 Code fork, the agent app), client-platform
    retired/           read-only: modules nobody runs (Orca first, Shaan 19 Oct); kept in git, excluded from
                       checks, cones and the index
    hubs/              the public hubs: ui-hub, software-hub, industries-hub, video-hub, bench, lawbook
    library/           Great Library: knowledge, banks (component, repo, ui), works, foundry, people-graph
    labs/              SISO Labs: incubator. One folder per venture: gaming/, music/, research/, video/...
                       A venture graduates to branches/<venture>/ with `siso move` (ids unchanged)
  estate/              city hall: estate tool, laptop-health, os-release (exports), siso-ci, guest-gateway
  seats/<seat>/        agent homes (seat.json gains "branch"); one generated registry
  data/                manifests only: families, blocks.json, bin/get
  .siso/               tags, moves.jsonl, baseline, budgets.json, generated/
```
Every branch has the same five doors, so any agent can find its way around any branch cold:
```
branches/<b>/
  BRANCH.json          id, name, parent, lead seat, domain (gaming.sisolabs.space), data family,
                       public org (or none), nav group in Agent Base, status (idea|incubating|live|retired)
  AGENTS.md            what the branch is, in Shaan's words; generated list of its modules and seats
  products/ packs/ site/   its own code (depends on platform/ only)
  research/            notes and small text; bulk goes to its data family
```
Rules, all as checks (`bin/check` + `bin/siso index --check`):
- **Direction:** branches -> platform -> nothing. A branch-to-branch import fails, with the fix in the error message
  ("promote X into platform/<area>").
- **Promotion on second use:** the gardener flags any file that appears identically in 2+ branches (the s2 script
  becomes a check: no new duplicate blob over 256 KB across modules).
- **One design system:** a second `tokens` or `ui` package name fails the check.
- **Hubs are views:** a hub may read every branch's manifests (generated catalog), never import their code (ADR 0022).

## 3. A new branch in one command
`bin/siso branch new gaming --parent labs --lead gaming` creates:
1. `branches/labs/gaming/` with BRANCH.json, AGENTS.md (Shaan's words from the 18 Oct note), products/, research/.
2. `seats/gaming/` (seat.json with branch, route, cone), so the Agent Base nav shows it under Labs.
3. The data family `gaming` (manifest only; blocks are made when bytes exist).
4. A site slot `gaming.sisolabs.space` in the hq router (deployed only when it has a page).
5. An optional public export target (`siso-os/<name>`) when the branch publishes anything.
Status starts at `idea`: Shaan, 18 Oct, "Ideas to keep, not to start". No seat is launched until status is
`incubating`. Gaming, music, video and religion-research get their idea slots in the first change.

## 4. Storage: each GitHub org is a tier with one job
| Tier | Where | Holds | Budget and rule |
|---|---|---|---|
| Code | `sisodias/siso` (private) | all SISO-own code, seats, rules, small text | < 2 GB packed; no tracked file > 1 MB without an allowlist line in `.siso/budgets.json` |
| Public | `siso-os/*` | one-way exports of folders (bin/oss) | CLEAN dry run before any push; never edited there |
| Data | `SISO-DATA/*` (org exists) | every data block: `<family>-<set>-NN` | ~1 GB per repo, files <= 64 MiB, sealed; private families age-encrypted |
| Bulk bytes | GitHub Release assets on the data repos | media, corpora, backups nobody diffs | 64 MiB encrypted chunks, manifest in git; one test upload proves it first |
| Clients | client-owned repos and orgs; `HALO-AGENCY` | their code | never copied (ADR 0007); pointer modules only |
| Map | `sisodias/siso-city` | which repo sits where on each machine | generated by estate |
| History | archived sisodias repos | everything merged | `gh repo unarchive` brings any back |
Moves this implies, all still on GitHub (Shaan, 18 Oct: "just keep them on GitHub"):
- The 147 live data repos in sisodias move to SISO-DATA by GitHub transfer (no re-push, no new copies), grouped
  into families: library, personal, runtime, archive, people, credentials, client-<x>.
- The 499 `siso-data-home-laptop-NNNN` shards (~24 GiB) are packed into about 25 sealed 1 GB blocks on the Mac mini's
  external drive (never a VPS), verified by restore, and the shards are archived.
- sisodias ends up holding code only: `siso`, `siso-city`, real forks, and archives.
- A branch gets its own GitHub org only when it gets its own access boundary (outside contributors, a spin-out or a
  sale). Then it is exported Copybara-style, exactly like siso-os. Branch is not the same as org.

## 5. Classify and deduplicate (the cleanup this design implies)
Every repo (752 live, s3):
| Class | Repos | Action |
|---|---|---|
| imported, not archived | 16 | archive after a drift check |
| should merge | 13 | import into its branch (siso-notes -> os, autosaas -> agency/factory, event-ticketing -> agency/industries, book-library + declassified-records -> library, librarian -> platform/agents, workspace/DevSpace -> platform/agents, operator-walk -> os, listmonk -> agency, component-bank index -> library/banks, dispo -> labs, codex-remote after a security read, agent-base warehouse: only what is still needed) |
| real forks | 6 + siso-code | stay forks; a `kind: fork` pointer module in their branch |
| data | 646 | SISO-DATA families (section 4) |
| client / partner / HALO | 25 | untouched |
| seat repo | siso-firstmate | moves last, with Agent Zero |
| dead | 3 | archive (read first) |
Duplicates inside the monorepo (s2, ranked by bytes):
1. One OS app: lifelock rolls into siso-internal as a module (2,785 identical files). Shaan: life and lifelock roll
   into the OS.
2. Notes backend kept once in platform/os-kernel; library/knowledge/app keeps the knowledge app only (1,377 files).
3. One design system in platform/ui (ui-base legacy copy 398 files, @agent-base/tokens, ui-suite, design-system).
4. Shared assets once (8 copies of one 2.6 MB gif; 13.5 MB of 3D sprites in 3 places).
5. One landing (agency/hq/landing, landing-mini and agency-landing folded into agency/hq/site).
6. app-factory's copy of video-hub removed (49 files, 11.4 MB).
7. repo-bank: tooling in library/works, data notes in library/banks; the archived copy goes to `_archive/`.
8. Generated preview output stops being tracked (rebuilt on demand).
9. 30 industry packs generated from one pack template, so each pack holds only what differs.
Nothing is deleted: every fold leaves the losing copy's path in moves.jsonl and its bytes in git history.

## 6. Agents at scale (hundreds of seats)
- **Hierarchy follows the tree.** Agent Zero sits on the whole repo. Each branch's lead seat is that branch's
  project Agent Zero. Seats own modules inside one branch. Arms (Codex, Haiku) work in a lane.
- **One registry.** `seats/*/seat.json` is the only hand-written source. `.siso/generated/seats.json` is built from
  it, and `t3-seats.json` is generated too: runtime fields (thread, pane, history) stay in `_data/`, identity in git.
  The 24-vs-19 drift ends.
- **Ownership rebalanced.** estate keeps estate/ and the tooling (about 10 modules, not 46). Each branch's modules
  go to its lead seat. A module with no live owner rolls up to its branch lead, never to nobody.
- **Claims, not collisions.** `bin/siso claim <module>` writes a claim through platform/agents/work (who, what,
  until when). A second seat sees the claim and queues behind it.
- **Land through a queue, not a race.** The Mac mini's siso-ci becomes the merge queue: a lane pushes its branch,
  the mini rebases, runs affected checks and fast-forwards main in order. No laptop CPU, and no silent SHA replace.
- **No checkout per seat (Shaan, 19 Oct: "worktrees are shitty").** 19 Oct evidence: 24 lanes held 16.7 GB, and
  each "cone" was really a full 1.9 GB copy of source. Replaced by section 9.
- **Routing by role.** Seats name a route (lead, builder, reader); `routing.json` maps roles to models weekly.

## 7. How it keeps itself good (the gardener, nightly, ESTATE loop)
- Regenerates the indexes, branch pages, CODEOWNERS, cones and the t3 registry.
- Grades every module (owner live, check exists and passes, tests, AGENTS.md size, budget) on the map page. A grade
  only goes up (ratchet). 87 of 117 modules have no check today: each gets one before its grade can rise.
- Runs the duplicate scan and the size budgets; opens small fix lanes for Codex builders.
- Promotes lessons into checks only after replay (the check must fail on the incident and pass on the fix).

## 8. Order of work (each step leaves main green; builders are Codex, at most 2, reads are Haiku)
1. **Breathing room (today).** Remove clean, pushed, idle lanes (24 lanes, ~16.7 GB); laptop to 25+ GB free.
2. **Shared checkout + private-index commits + mini queue** (section 9). This comes first because it removes lanes for
   good. Then seat.json → t3-seats, skills and routing are generated.
3. **Finish v1, with liveness.** Read every module and mark the ones nobody runs (Orca first) retired. Archive the 16
   imported repos. Import the should-merge repos that are still live. The seats' old repos are archived as each
   seat restarts on root.
4. **ADR 0025-0027 + the re-lay.** One generated change: `platform/` + `branches/` via `siso move` (ids unchanged;
   every old path still resolves). Branch-direction check on. Idea slots for gaming, music, video and research.
5. **Dedupe** in the order of section 5 (one claim per item, biggest bytes first).
6. **Storage tiers.** Transfer data repos to SISO-DATA; pack the home shards on the mini's drive; one Release-asset
   test upload before any bulk move; verify every block by restore.
7. **Gardener nightly** with grades on the map page.

## 9. Run by Agent Zero's fleet in SISO Code (Shaan, 19 Oct: "very, very important")
The monorepo is the fleet's home, not a second system next to it. Every seat is a SISO Code (T3) thread, started by
Agent Zero with his own tools.
**One checkout, many seats.**
- Every seat thread launches with workspace `root`, on the one monorepo checkout; its cwd is its seat folder.
- No seat gets a worktree. Seats edit only files inside the modules they have claimed.
- Commits never touch the shared index. `bin/siso commit <paths>` builds the commit in a private index
  (`GIT_INDEX_FILE`), from origin/main plus only those paths, and pushes it to `refs/lanes/<seat>/<n>`. Another seat's
  dirty files are never staged or reset (ADR 0009 by construction).
- The Mac mini's queue rebases each lane commit, runs the affected checks, and fast-forwards main. The laptop runs no
  full check and no install.
- The shared checkout then fast-forwards. That refuses only if an incoming file is dirty locally, which a claim makes
  impossible.
- When real isolation is needed (a dependency upgrade, a risky refactor), a lane is an APFS clone of the checkout
  (`cp -c`: near-zero bytes until edited), not a worktree. It is removed when it lands.
- Cost: one checkout on the laptop, whatever the number of seats.
- Proved 19 Oct on the laptop:
  - A private-index commit off origin/main contained exactly its one file and left the shared index unchanged.
  - `cp -c` of `library/` (351 MB) cost 18 MB of disk.
  - Removing 5 clean, idle 1.9 GB lanes freed 9 GB (19 GB free afterwards). The 2 lanes with uncommitted work were kept.
**One identity per seat.**
- `seats/<seat>/seat.json` holds title, branch, route (lead, builder or reader), owns, skills and brief. It is the
  only hand-written source.
- `bin/siso index` generates `.siso/generated/seats.json`. `seat-to-t3` and `seat-fresh-t3` read it.
- `~/.local/state/agent-base/t3-seats.json` keeps runtime only (thread, pane, at, previous_thread). The 24-vs-19 drift
  ends.
**Skills and routing follow the seat.**
- seat.json names its skills. The skills hub registry (siso-skills-hub `registry/skills_registry.json`) installs them
  into `~/.claude/skills` and Codex. The installer gets a check that the installed set matches the registry.
- `route` resolves through Agent Zero's `routing.json` (seats on Sonnet 5.5, Opus only for Agent Zero and leads with a
  written reason, Haiku for reads, Codex for builds), so models change in one file weekly.
**Agent Zero sees the whole fleet from the repo.**
- `bin/siso fleet` joins seat.json, claims, open lanes, the queue state and t3-seats runtime into one table.
- Agent Zero's `fleet-check`, `seat-launch` and `seat-fresh` skills read that table instead of hand lists.
- A branch made by `bin/siso branch new` appears in SISO Code as a project and a seat thread with no other step.

## 10. What stays the same
HALO never enters (ADR 0007). Nothing is deleted. No R2. No data on any VPS. Every repo keeps exactly one home.

HALO gets a monorepo of its own later, built with the same tools (`bin/siso commit`, `sync`, `stats`, the mini queue) once
this one has proven them; it never shares this repo (Shaan, 19 Oct).

Speed is measured, not guessed: `bin/siso commit` and `bin/land` append per-phase milliseconds (fetch, build, gitleaks,
push, sync, retries) to `~/.local/state/siso/land.jsonl`; `bin/siso stats` reports p50/p90 per tool and machine (landed
19 Oct, d4c18b0c). The mini's merge queue writes the same receipts so its time per commit is tracked from day one.

What exists today, live or not, with a verdict per module and repo: `plan/catalog-20261019.json`
(https://siso-catalog.siso-show.pages.dev).
