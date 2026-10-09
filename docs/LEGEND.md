# The SISO estate legend: how everything fits together

Status: agreed in shape by Shaan, 2026-09-25 ~17:40 ("I like the way you've decided the shape"); the moves wait on live work (§8). Second draft of ~17:15, extended with the shape of every side. The first draft (~16:30) used whose code a thing is as the first
question and put HALO beside SISO Agency. Shaan rejected both: "it's not about whose code it is ... all of it's our
code that we're working on", "halo should be in siso agency", and Fahmy's agency "is technically a partner like halo".
His words are verbatim in `.agents/source/2026-09-23-shaan-verbatim.md`. This file replaces `docs/MODEL.md` §1 and §4.
The machine-readable form is `plan/legend.json`. Nothing has moved yet.

## 1. The first principle: the estate mirrors the business
A thing's place is the part of the business its work serves. It is not decided by:

- **Whose code it is.** SISO's job is working with code, so all of it is SISO's work. Where the code came from (Cam's
  GitHub, an open-source upstream, a client's repo) is recorded on the building as its origin. Its rules travel with
  it: Cam's code stays in his repo, and an upstream keeps its `upstream` remote. The origin never picks the place.
- **What type of thing it is** (app, client, script). Type describes a building. It is not a place.
- **Which machine it runs on.** A machine is a site the map points to. It is not a place either.

Two agents who know the business must put the same new thing in the same place. That is the test of this legend.

## 2. The business, in Shaan's words (25 Sep)
- "SISO Agency is kind of the whole thing."
- SISO works only with agencies: "we only work with agencies ... because it's the highest leverage". One partner
  agency fans out to many clients.
- "HALO Agency is one of the agencies that SISO Agency directly partnered with to help scale, and that's how we fund
  SISO Agency." HALO is partner #1: the most important, and the funder.
- Fahmy's agency "is technically a partner like HALO ... in terms of category it's the same thing". Its clients
  include Bykonz Yard, MelanoTresses and myGUMM ("all the Bykonz work, Melanotresses, all of that's been under Fahmy's
  agency").
- "Great Library of SISO is the whole ... open source information infrastructure side of things."
- "The SISO estate [is] the actual land map, private land map of everything."
- The thesis (5 Sep): SISO = Scalable Intelligent Systems Operator. It retrieves, distils and assembles what is already
  harvested into business operating systems, one industry at a time.

## 3. The shape
```
~/SISO_Workspace                   the SISO estate: SISO Agency, the whole thing; this tree plus its register is the map
├── SISO_Agency/                   the business side
│   ├── hq/                        SISO itself: its sites, roster, verticals (industries/), work hub, machines
│   ├── partners/                  the agencies SISO works through (the leverage)
│   │   ├── halo/                  #1, funds SISO. Its systems: crm/, oracle/, kellman/, inspiration/.
│   │   │   └── clients/           HALO's clients: collegebesties/, ...                       site: halo-vps
│   │   ├── fahmy/                 #2. Its own work at the top (hq/, services, the client platform pilot).
│   │   │   └── clients/           bykonz/, melanotresses/, mygumm/                        site: bykonz-contabo
│   │   └── <old-partner>/         the agency Lumelle and Home Essentials came through (name from Shaan)
│   │       └── clients/           lumelle/, home-essentials/
│   ├── apps/                      products: the business-OS modules built once and used by every partner
│   ├── factory/                   the method: how SISO builds them
│   └── clients/                   work that came without a partner: Action Model (near-subsidiary; feeds the
│                                  frameworks), and older direct work (restaurant, bike rental, five-star hire, ...)
├── SISO_Agents/                   the agent infrastructure side: the workforce every partner's work runs on
├── Great_Library_of_SISO/         the information infrastructure side: open source, everything reads from it
├── personal/                      Shaan's life, outside the business
└── _data/ _archive/ _inbox/       utilities: runs and worktrees, the vault, intake
```

The register carries what folders cannot: `partner_of`, `client_of`, `funds`, `pilots` (the vertical),
`uses` (products and Library banks), `supersedes`, `origin` (where the code came from), and `runs_on` (services per
site).

## 3a. The shape inside each side
**SISO Agency, the business side.** Everything in it answers one question: which part of the business is this?

| Part | Holds | Today |
|---|---|---|
| `hq/` | SISO itself: `website/`, `agents/` (its roster), `industries/` (the verticals), `infra/` (Twenty, its CRM instance), `docs/`, `runs/`, and its own hubs | the hubs `siso-internal` and `siso-internal-labs` sit in `apps/`; `hq/agents/alj_ofm` is HALO's |
| `partners/<agency>/` | the partner's own systems at the top, its clients in `clients/<brand>/`; one site record | does not exist yet; HALO is at the root, Fahmy in `clients/` |
| `apps/` | products only: modules built once and used across partners (client platform, CRM kernel, inbox, mailing, ticketing, the knowledge module) | also holds hubs, Oracle links and Polymarket |
| `factory/` | the method: software factory, app factory, e-commerce factory, the agency frameworks | holds second copies of the Library's repo bank |
| `clients/<brand>/` | clients that came without a partner | also holds Fahmy's and the old partner's clients |

The verticals in `hq/industries/` are where the business's reuse is decided: a vertical names the modules an industry
needs, the partner that pilots it, and the products it has produced. Only `model_management` exists today; events
(Fahmy), e-commerce (the old partner) and the business-OS industries (Action Model supplied 17) belong beside it.
Action Model stays a client, but its reusable output (the frameworks, the industry material) is SISO's: it lives in
`factory/` and `hq/industries/`, and Action Model `uses` it (Shaan: "those frameworks are really important for SISO
Agency").

**SISO Agents, the agent infrastructure side.** Its own rule already fits the principle and stays: flat, one repo per
job, `agent-zero/` the one group, and a new repo only for independent adoption, ownership, security or release
(`SISO_Agents/AGENTS.md`). This side does not grow 1000× in repos. What grows is running agents, and they live as seats
and sessions working inside the business side's buildings. Its repos have two faces, which its manifest records
(`SISO_Agents/DOMAIN-MANIFEST.json`):

- **The live setup** runs SISO today:
  - `agent-zero/`;
  - `siso-harness-lab` (brain, live hooks, measurement);
  - `siso-agent-brain` (the state service);
  - `siso-compute-pool` and `siso-worker-node`;
  - `siso-project-team`;
  - `siso-estate`.
- **The public distribution** packages the stack for open release:
  - `siso-skills-hub`;
  - `siso-agent-hooks`;
  - `siso-agent-playbook` (and `-public`);
  - `siso-agent-runtime`;
  - `siso-agent-stack` (the installer);
  - `siso-agent-integrations`;
  - `siso-session-intelligence`;
  - `siso-project-os`;
  - `jev-agent-skills`.
- **Adopted tools** the agents use: `camofox-browser`, `devspace`/`siso-workspace`, `catgpt-gateway`.

The shape needs no new folders. It needs each repo's face and job recorded, and one release path from the live setup
to the public packages.

**The Great Library, the information infrastructure side.** Its shape stays: the catalog at the root (`registry/`,
`site/`, `bin/gls`), then `banks/`, `knowledge/`, `foundry/`, `people-graph/`, `works/`.
- It gains a code bank: `_reference/code-references/` and the clones kept for study (principle 6).
- It loses nothing to copies: the factory uses its banks and keeps none of its own.

**personal, Shaan's life.** Its shape is his life areas: `legal/`, `private/`, `data/`, `goals/`, study
(`team-entrepreneurship/`), `trading-for-dad/` (all three Polymarket homes become one), `math-bounties/`,
`accounting/`, and `apps/` for his own apps. The folders that copy the estate's shape go:
- `agents/` holds the court agents, so they move into `legal/`;
- `library/` and `work/` are empty;
- `tools/kaneo-worktrees` goes to `_data/worktrees`.

**The machines.** Each site maps to the part of the business it serves:

| Site | Serves | Maps to |
|---|---|---|
| halo-vps | HALO | `partners/halo/` |
| bykonz-contabo | Fahmy's agency | `partners/fahmy/` |
| siso-vps | SISO | `hq/` and the agent side; it also runs partner services, each recorded with its partner |
| laptop | everything | a cache of the whole map |
| the Mac Mini | the vault | storage |

## 3b. Industry verticals: the spine between the Library and the partners (checked 25 Sep)

An industry is not a fourth kind of place. It is one flow that crosses the estate, and today it sits in four places:

1. **The method:** `superapp-forge`, inside `SISO_Agency/factory/siso-agency-frameworks`: how an industry is researched.
2. **The research:** `Great_Library_of_SISO/works/siso-industry-packs` (public): one pack per industry, the method's output.
3. **SISO's verticals:** `SISO_Agency/hq/industries/<vertical>/`: the industries SISO sells into. Only `model_management`
   exists: HALO's industry (creator and OFM agencies), with a site clone of an OFM agency (`upper-echelon-clone`) and its
   capture tools (`_recon`).
4. **A client's view:** Action Model's industry map (`clients/actionmodel/site/system-map/industries`).

The flow runs method → pack → vertical → the partners and clients who work in it. So `hq/industries/<vertical>/` is the
vertical's one index: it names its pack, its playbook and the partners and clients in it (model_management: HALO and
Oracle). Packs stay public in the Library; client maps stay with the client and feed packs back. HALO's
`inspiration/` (OFM material to mine) is the same vertical's raw material and joins `model_management` when HALO moves
to `partners/halo`.

## 4. The reasoning, point by point
1. **Partners go inside the business.** SISO Agency owns the relationship with each partner; the partner is not a
   neighbour of SISO. HALO and Fahmy's agency are the same category, so they get the same place and the same shape.
   Priority (HALO first) is a property of the partner. It is not a different place.
2. **HALO still has "its own main estate"** (Shaan, 24 Sep). A partner territory is a whole estate: its own systems,
   its own clients, its own machine (halo-vps maps to `partners/halo/`). It now sits inside the business it belongs to.
3. **SISO has three sides: the business, the agent infrastructure and the information infrastructure.** All three are
   SISO Agency's. `SISO_Agents/` and `Great_Library_of_SISO/` stay beside `SISO_Agency/` rather than inside it, for
   three reasons:
   - they serve every partner at once;
   - the Library has a public face;
   - folding them in would push about 200 repos one level deeper and tell an agent nothing new.

   What matters is that partners and clients sit inside the business side.
4. **Leverage is the scaling logic.** There is one shared core (products, factory, agents, Library) and N partner
   territories, all the same shape.
   - Partner #3 means copying the partner shape.
   - A new client means one folder under its partner.
   - A new capability is built once in the core and used by every partner.

   That is the business model written into the map, and it holds at 1000×: 50 partners with 20 clients each still has
   the same depth.
5. **A client lives under the partner it came through.** The relationship is the partner's. MelanoTresses belongs
   under Fahmy, not beside him.
6. **Code kept to learn from is knowledge.** It belongs in the Library, like the repo bank and the component bank.
   Code SISO runs or builds on goes where it serves, as SISO's own building with an `upstream` remote. So
   `_reference/` dissolves:
   - `code-references/` and the study clones become a Library bank;
   - buzz, tokentracker and orca, which run on siso-vps, are adopted where they serve.
7. **The Library holds what is known; the agents hold what acts.** The UI base (components plus Shaan's notes) is
   knowledge, so it is the Library's. The `ui-pick` skill that queries it is the workforce's.

## 5. Where a new thing goes: three questions, in order
1. **Which part of the business does its work serve?**

   | It serves | It goes to |
   |---|---|
   | A partner agency's own systems | `SISO_Agency/partners/<agency>/` |
   | One of a partner's clients | `SISO_Agency/partners/<agency>/clients/<brand>/` |
   | A client that came without a partner | `SISO_Agency/clients/<brand>/` |
   | How SISO runs itself | `SISO_Agency/hq/<function>` |
   | Every partner, as a module built once | `SISO_Agency/apps/<product>` |
   | Every partner, as the way SISO builds | `SISO_Agency/factory/` |
   | The agents that do the work | `SISO_Agents/<job>` |
   | Knowledge, including code kept to learn from | `Great_Library_of_SISO/` |
   | Shaan's life | `personal/` |
2. **Does something already do this job?** One job, one owner (`plan/legend.json` lists the owners). Work in the
   existing building, or add a building to its compound. A replacement declares `supersedes`, and the old building
   points forward or goes to the vault.
3. **Is it code, data or a run?**
   - Code: a building (a repo).
   - Data (databases, uploads, client PII): a data plane, never a repo.
   - A run (a worktree, scratch, a test's temp folder): `_data/worktrees/<repo>/<lane>` or scratch, never inside a
     building.

Examples:

| New thing | Where it goes |
|---|---|
| A new site for a HALO client | `SISO_Agency/partners/halo/clients/<brand>/` |
| A booking module from the Bykonz work that three clients will use | `SISO_Agency/apps/<module>`; Bykonz `uses` it |
| Oracle's new relay service | `SISO_Agency/partners/halo/oracle/` (HALO's system) |
| A new skill | `SISO_Agents/siso-skills-hub` (the skills job has an owner) |
| A repo cloned to study | the Library |
| A Polymarket scraper for Dad | `personal/trading-for-dad/` |

## 6. Rules that keep it true at 1000×
1. The top level holds sides, never instances. Partners, clients, products and jobs grow inside them.
2. Every partner has the same shape: its systems at the top, `clients/<brand>/` for its clients, and one site record.
3. Depth is bounded: side, then partner, then client, then compound, then building.
4. One job, one owner. `estate new` refuses a second building for a job that already has an owner.
5. Relationships are edges, not copies. A product used by 50 clients lives once.
6. Replaced generations point forward, or go to the vault.
7. Every machine is on the map as services (units, containers, ports, domains), each tied to a building and to the
   partner or side it serves.
8. Every door's legend line (side, partner, client, job, keeper) is written by code from the register, never by hand.

## 7. What is misfiled today (measured 25 Sep; nothing moved)
1. **HALO sits beside the business instead of inside it.** `HALO_Agency/` should become
   `SISO_Agency/partners/halo/`. Its clients, College Besties first, go under `clients/`. §8 has the cost.
2. **Fahmy's agency is filed as a single client, and the 23 Sep sort cut one of its clients loose.**
   - Now: `SISO_Agency/partners/fahmy` (21.9 GB, 13 repos) sits as one SISO client.
   - On 23 Sep the sort moved the MelanoTresses copy inside Fahmy's block to the vault as a "second clone". It kept
     `SISO_Agency/partners/fahmy/clients/melanotresses` as if it were a direct SISO client. Agent Zero's records
     (`siso-firstmate/siso/estate/ESTATE.md`) still say "same Fahmy client estate as Bykonz". The sort erased a
     business relationship because the estate did not know partners existed.
   - Should be: `SISO_Agency/partners/fahmy/`, with `clients/bykonz/`, `clients/melanotresses/` and
     `clients/mygumm/`.
3. **Two clients came through an old partner that no record names.** Shaan (25 Sep): Lumelle and Home Essentials
   came "through another ... old client that we were working with". They belong under that partner once he names it
   (§9). Action Model stays a direct client, a near-subsidiary. The rest of `clients/` is older direct work.
4. **Oracle is HALO's system, but half of it sits in SISO's products.**
   - `SISO_Agency/apps/oracle-streaming` holds the old monorepo `sisodias/oracle`. It is a migration half done: 9
     unpushed commits, 96 changed files, and 3 worktrees from 22 Sep.
   - The folder names are crossed: the folder called oracle-streaming holds the repo called oracle, and
     `HALO_Agency/oracle` holds `sisodias/oracle-streaming`.
   - `SISO_Agency/apps/` also holds three links and a folder of links into HALO.
   - Should be: everything under `partners/halo/oracle/` once STREAMING finishes the migration.
5. **Done 26 Sep: SISO HQ held HALO's work.** `hq/agents/alj_ofm` (repo `siso-agent-alj_ofm`), HALO's OFM agent, is now
   `partners/halo/agents/alj_ofm`. `hq/industries/model_management` stays: it is SISO's vertical, and HALO pilots it.
6. **The agent side's live setup and its public packages have drifted apart, and a few copies are stale.**
   - The first draft called the packages duplicates by their last-commit dates. Their own manifest says otherwise:
     `siso-agent-hooks`, `-runtime` and `-stack` are the public distribution, and `siso-harness-lab` is the live lab
     that measures and tunes. The packages stopped being fed on 1 and 9 Aug, while the live setup kept moving.
   - Stale copies: `siso-skills` (a 23 Jul snapshot of the skills), `siso-os` (the older agent OS, with 6 foreign
     clones inside), and `/opt/siso-skills` on siso-vps (no git).
   - DevSpace has two histories (`siso-workspace` and `devspace`).
   - Should be: one release path from the live setup to the packages; the stale copies point forward or go to the
     vault. Keeper: the agent stack.
7. **`SISO_Agency/apps` holds things that are not products.** It holds SISO's own hubs (`siso-internal`,
   `siso-internal-labs`), which belong in `hq/`, and Shaan's trading research (`Polymarket-Research`), which belongs
   with `personal/trading-for-dad` (Polymarket has three homes today).
8. **The Library is copied into the factory.** The repo bank and the agency frameworks have second copies inside
   `factory/siso-agency-software-factory/modules/`.
9. **Twelve empty plot folders look like broken projects.** 11 are GitHub-only plots; `apps/dispo` is on no map.

The machines:

10. **What runs is invisible to the register.**

    | Machine | The register says it runs | What actually runs |
    |---|---|---|
    | laptop | 0 | 25 `com.siso` launchd jobs (11 running) and 34 listening ports |
    | siso-vps | 12 | 36 services and 6 containers |
    | halo-vps | 0 | the HALO CRM stack behind app.haloangels.net |

11. **Two live services on siso-vps have no backup.** `siso-tokens` and `siso-sheets-bridge` run code from `/opt`
    folders that have no git. No backup on the box covers them, and no copy has been found elsewhere.
12. **siso-vps serves SISO, Shaan, Fahmy and HALO, with nothing on the box saying which service is whose.**

## 8. What the partner moves cost, and how they ran
**Both partner moves are done.** Fahmy's agency moved to `SISO_Agency/partners/fahmy` on 25 Sep ~17:50. HALO moved to
`SISO_Agency/partners/halo` on 25 Sep 21:12, when Shaan said nothing in HALO was working and no process had its folder
there: one `estate move` (5 worktrees repaired, 15 Claude history links), College Besties to `clients/`, the launchd job
reloaded from its source (`estate services`), 65 consumers repointed, 7 links retargeted, the estate's code and plans
updated; the backup still never copies Cam's code at the new path. The text below is the plan as it stood before.

**Both partner blocks are live (measured 25 Sep ~17:45).**
- Fahmy's block:
  - `bykonzyard` and `bykonz-admin` were committed 14 minutes earlier;
  - `bykonzyard` has 3 lane worktrees in `_data/worktrees/bykonzyard/`;
  - `mygumm` has 62 changed files;
  - the mission-control server (launchd `com.siso.client-platform-mission-control`) runs from inside the block.
- HALO: seven processes have their working folder in `HALO_Agency/oracle` or `HALO_Agency/crm` (the Oracle and CRM
  lanes).

A move under a working agent breaks it mid-task: there are no compatibility links (ADR 0005). So each move runs at its
keeper's safe point:
- Fahmy's agency with BYK;
- HALO with CRM-LEAD, READBACK and STREAMING.

Each move follows `estate-move`:
1. `estate move --no-link`;
2. repair the worktrees;
3. reload the launchd job from the new path;
4. repoint the files that refer to the old path;
5. update `SISO_Agency/.gitignore`;
6. then the estate's own code (register, projects list, siso-vps placements, fix policies, layout).

**Fahmy's cost (measured 25 Sep):** 566 files mention `partners/fahmy` or `clients/melanotresses`. 319 of
them are inside the block itself: its scripts, the mission-control server and its docs, all using absolute paths.
Most of the rest are history (Agent Zero's protocol ledgers 55, HQ run records 30, the estate's records 49), and
records are not consumers. Moving the block is a real migration: repoint the block's own files, then reload the
launchd job.

**HALO's cost (measured 25 Sep):**
- 79 files mention `HALO_Agency`. 57 of them are the estate's own generated records, which regenerate.
- About 22 are written by hand:
  - Agent Zero's records (4) and memory files (3);
  - three skills: `estate-keeper`, `siso-credentials`, `siso-project-front-door`;
  - the harness lab, the skills hub and the district doors.
- One launchd job: `com.siso.halo.cam-laptop-sync`.
- Live work: the READBACK seat is working in an Oracle worktree whose git link points into `HALO_Agency/oracle`, and
  CRM-LEAD keeps HALO. The move runs when both are at a safe point. `estate move` repairs worktrees and `repoint`
  rewrites the files, the same tools that moved Agent Zero (84 files) on 24 Sep.

This is not "a lot of commands": about 22 files and one launchd job, done by the tools.

## 9. What only Shaan can answer
1. The name of the old partner that Lumelle and Home Essentials came through (Shaan, 25 Sep: "through another ...
   old client that we were working with"). No record names it; until he does, they stay in `clients/` with
   `client_of: an old partner`.

Answered 25 Sep ~17:40:
- Fahmy's clients are Bykonz, MelanoTresses and myGUMM.
- Action Model is a near-subsidiary client whose frameworks matter to SISO.
- The shape is agreed.

## 10. How the estate works, and the order to level it up
The loop, each step run by code:

1. **Birth.** `estate new` asks the three questions.
2. **Seeing.** The register scans buildings, services and data on every site each night.
3. **Drift.** The doctor compares all of it with the legend.
4. **Repair.** By-laws fix routine drift with receipts; everything else goes to the keeper by name.
5. **Telling.** Every door gets a generated legend line.

The order:

1. Shaan's answers (§9).
2. The partner moves: Fahmy's agency, then HALO.
3. The register reads `plan/legend.json` and adds the edges.
4. The legend line goes into every door.
5. `estate new` and the guard enforce the three questions.
6. Services go on the map on every site.
7. The rest of §7, one keeper at a time.
8. The laptop becomes a cache: 35 of the 157 buildings on it are active.
