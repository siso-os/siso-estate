# SISO monorepo: the plan (read this after a compact)

Owner: ESTATE (this seat). Shaan, 17 Oct: "you are the owner for all of this stuff apart from Halo, literally anything,
you can touch anything but Halo"; "their owners aren't working right now, push their work". The design is
`docs/MONOREPO-DESIGN.md` (and its page https://siso-monorepo-map.siso-show.pages.dev). The cutover log is
`plan/monorepo-cutover.md`. All the raw research (10 web slices, 286 repo reads, our own history) is in
`plan/monorepo-research/2026-10-17/`; this file is the distillation.

## 1. What Shaan wants (his words, 16-17 Oct)
- "redesign it from first principles as if we didn't have any repos to start with ... I don't care about old history"
- "built to scale ... not just size, information, layout, everything. Shit might get moved around, reorganized, rebuilt
  ... as agents get more intelligence"
- "designed beautifully so that agents can interact with it, self-improve it, self-iterate it and improve the ecosystem.
  This is an agent first ecosystem ... maybe each agent have their own repo as well, like persistent seats"
- In: agent system, agency core, library (knowledge as a DATA MODULE), factory, SISO products, the OS (life and
  lifelock roll into the OS). Out: clients (mostly), HALO, data blocks as separate encrypted repos. No R2. >2 GB always
  on the laptop is fine. "Halo gets its own repo, SISO gets its own repo." Merge many repos into few; rewrite READMEs
  to match contents; a good data architecture. Stop talking about safety floors; everything is backed up.

## 2. The fundamentals learned (each line has a source in plan/monorepo-research/2026-10-17/)
1. Agents are now the main clients of a repo. Anthropic moved off git to its own VCS, Scion (13 Oct 2026, 30k
   merges/day, per-directory permissions); Stripe merges 1,300 agent PRs a week. We are ~4 orders of magnitude below
   that scale: stock git + GitHub stays right for years (anthropic-notes.md, r10).
2. One repo per access boundary, never per product (Google, Meta, Anthropic; our 16 Oct decision). Partition inside by
   owner and job, not by language.
3. The working copy must be small and fetched on demand: blobless clone + cone sparse-checkout + `git maintenance`
   (Microsoft's Scalar is now stock git; VFS for Git lost). Meta commits sparse profiles INTO the repo so the filter
   moves with the code; we generate them per seat (r3, r4).
4. Identity is a declared id, never the path: Backstage/Cortex key on ids; Port keyed on file paths and was shut down
   (15 Oct 2026). Path coupling was our own worst repeated failure (r6, retired.txt).
5. Prose does not enforce: Claude's docs say CLAUDE.md is "context, not enforced configuration". Hooks, lints and
   structural tests enforce, and the best error text names the fix (OpenAI harness engineering). Our LEDGER: 1,641
   lessons, ~10 repeating mistake kinds, most stoppable by a check (r1, r7, words-lessons.txt).
6. Root AGENTS.md is a ~100-line map; each module has its own short AGENTS.md (its contract); Codex loads root->cwd,
   closest wins, 32 KiB cap; Gemini CLI and Copilot read nested AGENTS.md too. Vercel: a compressed 8 KB docs index in
   AGENTS.md passed 100% where on-demand skills passed 53-79%. Stripe scopes almost all rules to subdirectories (r1, r2,
   r4, r10).
7. Boundaries are default-private and checked (Bazel visibility, Nx tags: untagged depends on nothing). moon's
   moon.yml (owners + tags + constraints) is the best manifest model; Nx owners sync is paid; Bazel too heavy (r5).
8. Large changes are generated, sharded by owner, approved once (Google Rosie; 74% of 595 migration changes
   LLM-written). Airbnb moved 3,500 test files in 6 weeks with a sample-tune-sweep loop (r2, r10).
9. Lessons ladder: correction -> fact -> rule -> check -> hook; replay before promote (the check must fail on the
   incident and pass on the fix); files are truth, indexes are caches; forget by proposal into _archive (r7).
10. Persistent agent = a checked-in identity file + a memory folder (Claude Code `.claude/agents/<n>.md` +
    `memory: project`); tools allowlisted; runtime (queue, mailbox, transcripts) outside git; no OSS tool gives a
    file-based seat home that re-spawns, so seats/ is ours to build (r8).
11. Data: GitHub repo <~1-5 GB, files <100 MiB, LFS metered (10 GiB on Pro); GitHub Release assets have no total cap
    (2 GiB/asset, 1,000/release) -> later home for bytes nobody diffs, as age-encrypted 64 MiB chunks (Xet design) with
    only a manifest in git. Not git-crypt (leaks names/sizes, no revoke). Not submodules for pins (accidental pulls) (r9).
12. Codex Cloud splits setup (network, secrets, sparse checkout, data fetch) from the agent phase (offline, no
    secrets); ExecPlan = a restartable plan file (Progress, Decision Log, Validation) = our HANDOFF (r1).
13. Our history (evolution.txt): five eras (firstmate crew -> split repos -> herdr seats -> SDK host -> T3 seats ->
    monorepo). Survived every era: AGENTS.md door, HANDOFF + one-file-per-fact memory, Shaan's verbatim words,
    worktrees in _data, the estate map + ADRs. Rebuilt every era: seat host, routing, messaging. So: durable state in
    git files, runtime replaceable, route by role not model name.
14. Usage (usage-data.txt): only 16 of 269 laptop checkouts had an agent session in 8 days; hot set = HALO, harness,
    estate, siso-code, siso-firstmate, ui-hub, people-graph. Most of the estate is cold.
15. Failure modes to design out (retired.txt): path coupling; duplicate homes; routing by model name; prose rules;
    seats tied to terminal panes; absolute gates (a red main blocked 6 seats overnight); too many repos and
    sibling-path imports; counting dead records as live.

## 3. Decisions taken (all live in sisodias/siso decisions/)
- 0021 folders by job; 0022 one pnpm+turbo workspace, products -> packages -> nothing, hubs are views.
- 0023 (17 Oct): every unit is a module with a permanent id (`module.json`: id, kind, summary, owner seat, one
  `layer:` tag, status, depends; `uses` derived from pnpm workspace deps); `bin/siso` where/owner/move/new/index/
  affected/work; `.siso/moves.jsonl` keeps every old path/repo resolvable; generated index in root AGENTS.md;
  seats/<seat>/ homes (seat.json + AGENTS.md with generated owns block); one generated registry; cone worktrees per
  seat; `.siso/baseline.json` ratchet; `bin/check --affected` for quick loops (land stays full: Actions is off).
- 0024 (17 Oct): data in blocks `siso-data-<family>-<set>-NN` (~1 GB, files <=64 MiB, sealed), families library
  (plain-private) / people / personal / client-<x> / runtime / credentials / archive (age, one key per family),
  pinned in `data/blocks.json` (not submodules), fetched by `bin/get`; Release-asset chunks later.
- Seats are folders, not repos (a repo per agent recreates the sprawl and splits a seat from the code it changes).
- HALO never enters sisodias/siso; Cam's repo is never copied (ADR 0007).

## 4. State (17 Oct, end of this session)
- sisodias/siso main: db11fc04 (spine), afd36a19 (seats), eabef70a (gates). 67 modules, 19 seats, 0 errors.
  Lane worktree `_data/worktrees/siso/spine` (branch lane/spine). A full `bin/check` needs `pnpm install` first.
- siso-os-release #70 merged (54bb92b0): `source.subdir`, 10 public repos export from monorepo folders.
- Archived (drift SAME): siso-foundry, siso-unsolveable-mathematics, unfuck-the-project, siso-agent-integrations,
  siso-agent-stack, siso-project-os. Earlier: siso-agency-frameworks, siso-client-pack, siso-stargate-library,
  siso-internal-labs-server, cafe-89, knowledge.
- Next in hand: cut over siso-people-graph, siso-ui-base, siso-shell, siso-guest-gateway (Shaan: push their work).

## 5. The build, in order (each step leaves main green; land with `bin/land` from a lane worktree)
1. DONE spine. 2. DONE seats. 3. DONE gates.
4. Cutover, repo by repo (recipe below): the 4 above, then wave B (actionmodel, kikas, restaurant-app, 5-star-hire,
   freeflow, siso-browser, siso-music, siso-project-base, sisonotes-backend, siso-agency-lp,
   siso-agency-software-factory, siso-agency, siso-evidence-engines, siso-project-team, jev-agent-skills,
   bike-rental-template, trader-platform), then wave C live seats one at a time (siso-estate LAST, harness-lab,
   skills-hub, playbook, siso-agent-zero, labs-agent-base, siso-internal, SISOCRM, siso-ui-hub, laptop-health,
   siso-os-release, great-library-of-siso, siso-agent-hooks). Then the merges in MONOREPO-DESIGN.md §8 (agents,
   UI banks, OS = apps/os, agency, library) and archive the superseded repos (blackbox4, agent-zero-protocol,
   memories_v2, project-team-2, social-outreach x2, codex-remote after a security read, siso-ui-library, ui-shoot,
   ui-comps, actionist-components, SISOCRM dup, siso-monorepo, siso-core-clean, bdbt, demo-tool, proof x6).
5. Seats move in: each seat's HANDOFF.md, memory/, source/, LOOP.md into seats/<seat>/; t3-seats.json and
   registry.json generated from `.siso/generated/seats.json`; seats launch in their cone worktree.
6. Data: encrypt siso-data-vps-services (WhatsApp DB plain today); declare whatsapp-media, home-keepers,
   held-backups planes; write data/blocks.json; build bin/get; library transcripts -> 3 blocks, banks -> 4 blocks.
7. Laptop: one monorepo checkout every consumer can point at (decide: ~/SISO_Workspace/siso via layout.json, or
   promote `_data/worktrees/siso/main` to track origin/main); retire the old checkouts with houses.py.
8. Gardener (nightly, ESTATE loop): regenerate, grade every module (runs, tests, documented commands, owner live,
   lessons enforced, no secrets), ratchet grades up; lessons inbox -> replayed checks; README heads generated.

## 6. Cutover recipe for one repo (what was proven on 17 Oct)
1. Push its work: `python3 tools/houses.py sync <checkout> --why "..."` (all branches, stashes, dirty -> GitHub).
2. Drift: standalone HEAD vs `source_sha` in sisodias/siso `.agents/migration-20261016.json`. MOVED -> bring the
   new commits in (`git subtree pull --prefix=<path> <remote> <branch>` in a lane, or apply the diff with
   `git apply --directory=<path>`), land.
3. Uncommitted work: apply it into the monorepo path as a commit (it is already safe on GitHub from step 1).
4. Repoint consumers: siso-os-release repos.json (`source.subdir`), skills/launchd/registry paths, seat cwd,
   siso-ci repos.json, `.estate/map.json` + `plan/github-placements.json`.
5. Archive: `gh repo edit sisodias/<r> --description "moved to sisodias/siso:<path> (date)"` then
   `gh repo archive sisodias/<r> --yes` (reversible: `gh repo unarchive`).
6. Retire the checkout: `python3 tools/houses.py retire <checkout> --why "..." --run`.
7. Log it in `plan/monorepo-cutover.md`.

## 7. Traps hit this session
- Another session rewrote MONOREPO-DESIGN.md between my write and commit; `git diff` the exact file before committing.
- `library/.gitignore` ignores foundry/, knowledge/, banks/, works/ (old nested repos): new files there need `git add -f`.
- Three packages are named `@siso/tokens`; only pnpm workspace members count when deriving `uses`.
- The local `_data/worktrees/siso/main` is on stale branch lane/pilot-import; read main with `gh api` or `origin/main`.
- Main itself fails `library/check.sh` without `pnpm install` (siso-shell from codeload).
- Background agents die when the T3 server restarts; write rows incrementally and rerun missing slices.
