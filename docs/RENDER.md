# The estate as one living world (spec, third draft)

Spec only; nothing is built. Written 25 Sep 2026 from the estate's own records, each number measured that day.

Shaan asked for a 3D render of the estate: islands and water, compounds and buildings, agents with houses who walk
when they work and sleep when they don't, scaling to thousands. His corrections to earlier drafts:
- "it's everything on my laptop, the whole goddamn estate, personal included";
- "personal in the same world, I want to see the same world for everything";
- the world should visibly change "as we improve the estate";
- "look at the data and how the estate works and how it maps".

This draft answers them. It starts from what the estate is (§1–2), the vocabulary it already uses (§3), and the three
records it keeps (§4). It then derives the world from those, part by part, with the reasoning for each choice and what
would make it wrong.

---

## 1. What the picture is for

**Who looks.** Shaan, daily, to see how SISO is running without asking an agent. Agents never need the 3D picture:
they read the same facts as JSON (`city.json`, `estate where`). So the picture is built for one human's eyes, and
every design choice favours his reading speed over completeness.

**The questions it must answer at a glance**, in the order he asks them:
1. What is being worked on right now, and by whom?
2. What is waiting on me?
3. What is broken or at risk (unbacked, keys loose, below code, a machine gone quiet)?
4. How is the estate getting better: which era are we in, and what moved today?
5. Where does a thing live, what feeds it, and what does it feed?

**Tests of the design.** Each part below has to pass three tests, or it is decoration:
- it answers one of the five questions;
- it is drawn from a record, never invented;
- it stays readable when the estate is a thousand times bigger.

---

## 2. What the estate is: the whole thing, measured

| Part | Measured 25 Sep |
|---|---|
| **The map** (`sisodias/siso-city`, same paths on every machine) | 314 entries; the register holds 300 buildings in 116 compounds |
| **Checkouts on the laptop** | 378: 133 live, 113 ephemeral (temporary git folders), 77 archive, 20 dependency, 16 reference, 11 harness home, 6 data; plus 65 worktrees |
| **Building sizes** (`size_kb`) | median 3.4 MB, top tenth above 254 MB, largest 4.3 GB: a spread of about 1,300 to 1 |
| **Lifecycle** | 34 active, 62 warm, 25 dormant, 65 archived, 78 dark (on GitHub, on no machine), 36 unscored |
| **Provenance** | 215 ours, 71 foreign, 12 adopted, 2 client |
| **Disk** (460 GB) | SISO_Agency 57.7 GB (apps 32, partners 20), `_data` 42.3, personal 27.6 (data 11, private 8.8, math-bounties 2.4, coursework 1.9, trading 1.5, legal 0.9), `_archive` 26.7, `~/.codex` 23.9, `~/Library` 23.1, HALO 17.3, caches ~22 |
| **The home folder** (`plan/home-zones.json`) | 8 zones: the estate, macOS, shell, agent homes, SISO tool state, toolchains and caches, keys, loose keys |
| **Agent homes** | 20: 6 live, 14 variants or idle |
| **Hidden folders in the workspace** | 775 in 10 classes (tool state 203, harness config 202, project tooling 167, retired conventions 71, unknown 52, ...) |
| **Services** | 27 launchd jobs `com.siso.*` (26 loaded); 21 listening servers, of which 6 are tied to a repo and 7 are registered in `plan/ports.json` |
| **Machines** | laptop and siso-vps on the map (siso-vps: 12 checkouts, 36 services, 6 containers, 5 `/opt` folders outside git); halo-vps recorded; the Mac Mini blocked since 23 Sep; hetzner unreachable; bykonz-contabo reachable and unmapped; Cam's Mac recorded and never mapped |
| **GitHub** | 217 repos after today's vault work; `siso-vault` holds finished repos as bundles; 21 encrypted data planes |
| **Keys** | the store `.credentials/projects/`; 2 loose key files in `~`; 6 repos keep key files outside the store |
| **Agents** | 7 running seats in herdr right now; 4,402 session files since 27 Jul (1,762 Claude, 2,640 Codex); workers on DeepSeek, Haiku and Qwen |
| **Births** (where agents made things, since 27 Jul) | 733: 65 right, 394 wrong, 171 unknown, 56 other, 47 ephemeral. Of the wrong ones, 267 were worktrees made inside a repo's `.worktrees`; 237 of all births were a bare `mkdir` |
| **Flows found so far** | 1,092 name links between 101 live buildings (`plan/planner/edges.json`); median building is named by 9 others |
| **Health now** (the operations centre) | 19 of 138 repos up to code, mean score 0.64; last nightly: 10 errors, 10 missing, 1 held by the secret scan |

---

## 3. The vocabulary already exists; the world uses it

The estate model (`docs/MODEL.md` §5) already names every level, and the legend (`docs/LEGEND.md`) already says which
part of the business each thing serves. The world does not invent new nouns. It gives each existing one a shape:

| Level (MODEL.md) | What it is | In the world |
|---|---|---|
| Territory | the whole estate | the world: land, sea, sky and bedrock |
| Island (legend: a side) | SISO Agency, SISO Agents, the Library, personal, the utilities | a region of land |
| District | a part of a side (`hq`, `apps`, `factory`, `clients`, `partners/<agency>`) | a neighbourhood with its own street grid |
| Compound | a product or engagement with several repos, behind a gatehouse | a walled plot with a gate building |
| Building | one repo | a building whose shape comes from its kind |
| Room | a worktree (`_data/worktrees/<repo>/<lane>`) | an annex joined to its building by a bridge |
| Seat | a persistent agent role (A0, ESTATE, CRM-LEAD, BYK, ...) | a citizen with a house |
| Site | a machine | a power grid (§6) |
| Pebble / rock | small mess swept by code or a crew / a problem for a keeper | litter / a boulder on a road |
| Law, governor, keeper | ADRs; who governs an island; who owns a building | a flag on the island; the keeper's colour on the door |

Using the model's own words means the world, the docs, `estate where` and the agents describe the same thing with
the same names. A renamed concept in one place becomes a bug in another.

---

## 4. First principle: three records, and the truth is where they disagree

The estate keeps three separate records, refreshed at different speeds:

1. **The map: what should be.** Land titles: `siso-city` and its postcodes, the legend's placement, owners, the
   building code. It changes when a decision is made (nightly, `estate refresh`).
2. **The ground: what is physically there,** per machine. Checkouts, dirty and unpushed work, sizes, hidden folders,
   worktrees, loose files (`inventory.py`, `census.py`, `dots.py`; hourly).
3. **The life: what is happening.** Agents, processes, ports, launchd jobs, containers, commits, backups (seconds to
   minutes; the operations centre rebuilds every 2 minutes).

When all three agree, a building is simply healthy and quiet. Every problem the estate has is a disagreement between
two of them, so the world draws each kind of disagreement as a physical thing you can point at:

| Disagreement | Records | Measured now | In the world |
|---|---|---|---|
| on the map, on no machine | map vs ground | 78 dark | foundations with no building: a vacant plot |
| on a machine, not on the map | ground vs map | 0 live (the census found none); 113 ephemeral | a squatter shack (red); a tent for a temporary folder |
| made in the wrong place | life vs map | 394 wrong births | litter where it landed, with a thread back to the agent that made it |
| work not pushed or backed up | ground vs life | 10 backup errors, 10 missing | scaffolding with a warning light |
| a service outside git | life vs map | 5 `/opt` folders on siso-vps | a building with no foundations |
| a server on an unregistered port | life vs map | 14 of 21 | an unlicensed stall at the street edge |
| a key outside the store | ground vs map | 2 in `~`, 6 repos | a coin glowing on the street |
| a door naming a missing path | ground vs map | 69 broken paths | a boarded door |

This is the heart of the design. A picture of what exists is a map. A picture of where the records disagree is a
dashboard, and it stays useful at any scale, because the disagreements are what need attention.

---

## 5. The geography: the business is the land

### 5.1 Why the business decides the land, and not the disk

Three candidates for what land means:
- **Folders:** land = the directory tree. The first render did this. It fails the moment a repo moves: the city is
  redrawn by a `mv`, and HALO appears beside SISO Agency instead of inside it.
- **Machines:** land = the laptop, the VPSs. It fails because one repo lives on several machines, and a deploy would
  move a building across the sea.
- **The business:** land = the part of the business a thing serves (the legend's first principle). A move to its right
  place is a move on the map too, and a deploy changes nothing but lights.

The business wins because it is the only one that stays still while the estate is being fixed. That is why the legend
was written first: the world is the legend drawn.

### 5.2 One world for everything

Shaan: "the same world for everything". So the world holds the whole estate, arranged so that nearness means
something. Adjacency encodes the flows, so a glance at the coastline already says what feeds what:

```text
                    beyond the horizon: trade ports (Anthropic, OpenCode, Kaggle, Groq, Cloudflare, Plane, Convex)
                                      and foreign ships (client boxes, Cam's Mac: seen, never mapped)

          THE LIGHTHOUSE (Great Library)               the embassy quarter
          banks · works · people graph · foundry        (other people's code, flagged)
                     |  code and knowledge roads
   PERSONAL  ~strait~  THE MAINLAND (SISO Agency)  ==harbour/customs==  THE PORT (SISO Agents)
   (own island,        hq at the centre              (_inbox, Downloads,   agent houses, city hall,
    fogged until       apps · factory · clients       Desktop)              the power station (agent stack),
    opened; legal &    partner cities on the coast:                         compute, the memory hall
    private = a        HALO (largest, gold road to hq)                      (harness homes, sessions)
    sealed keep)       Fahmy (Bykonz, MelanoTresses, myGUMM)
                                 |
                  the treasury (.credentials, the 21 data planes) under hq
          ~~~~~~~~~~~~~~~ the sea: GitHub (every repo's title lives here) ~~~~~~~~~~~~~~~
                  the vault on the sea floor (_archive, siso-vault): sunken outlines with plaques
          ================ the bedrock: the machine itself ================
          system rock (macOS) · the toolchain quarry · caches as silt · the shell's roots
```

The reasoning for each placement:
- **The mainland is at the centre** because the agency is the whole business. Everything else serves it or is
  served by it.
- **Partners sit on the mainland's coast inside walls.** They are part of SISO Agency (Shaan, 25 Sep), and each keeps
  its own law behind its gate (HALO's code never leaves Cam's repo, ADR 0007). HALO is the largest city, and a gold
  road runs from it to hq because HALO funds SISO.
- **The Library sits beside the factory** because the heaviest code flow runs Library banks → factory → products.
  Its lighthouse faces outwards because much of it is public.
- **The port faces the mainland across the harbour** because agents work everywhere, and the harbour between them is
  where loose cargo waits to be filed.
- **Personal is its own island across a strait:** in the same world, as he asked, but separate, because nothing of the
  business should flow into it by accident. It is fogged until opened. `legal/` and `private/` are a sealed keep,
  drawn as sealed and never opened, named or read by any tool.
- **The treasury is under hq:** one strong room for keys and the encrypted data planes.
- **GitHub is the sea** because it holds every repo's title and floats every building; a vacant plot is a title with
  nothing built on land.
- **The vault is the sea floor:** finished buildings sink there and stay visible as outlines with a plaque (what, when,
  where its history went), so the vault is findable, not forgotten.
- **The machine itself is bedrock:** macOS, `~/Library`, toolchains and caches are not buildings. They are strata
  under the land, sized by disk, and the caches are silt that the laptop-health agent dredges.
- **The rest of the home folder:** each of its 8 zones takes the place of its meaning. Agent homes become the port's
  memory hall; SISO tool state becomes the utility yards behind its tool's building; shell and keys sit in bedrock and
  treasury. An entry in `~` with no zone is a squatter on the beach.

### 5.3 Machines are power grids, not places

A building checked out on a machine is lit in that machine's colour. One that runs a service there carries a chimney
on that grid. A lens per machine dims everything not on it: "show me siso-vps" lights its 12 checkouts and its 36
services wherever they sit in the business. Something that exists only on a VPS (a container, an `/opt` service) still
gets a building at its business place, flagged "no foundations" until it is in git. A machine that has not been probed
recently is fog on its colour: the Mac Mini's grid has been dark and fogged since 23 Sep.

This choice would be wrong if Shaan thinks of the VPSs as places he visits. The machine lens is the answer to that:
the same world, lit by one grid.

---

## 6. A building: shape from its kind, size from three measures

### 6.1 Shape comes from kind

MODEL.md already gives each building a kind. The kind decides the architecture, so a glance tells an app from a
dataset:

| Kind | Architecture | Why |
|---|---|---|
| app | a tower with lit windows | people use it; it has floors of features |
| service | a works with a chimney on its machine's grid | it runs; smoke means running |
| package | a warehouse | other buildings take from it |
| site | a shopfront facing a street | it faces the public |
| data | a silo or reservoir | it holds, it does not act |
| research | an observatory | it looks outward |
| docs | a reading room | it is read |
| tool | a workshop | it makes things for others |
| gatehouse | the gate of its compound | it holds the compound's front door |

### 6.2 Size is three different things

One number would lie. oracle-streaming is 4.2 GB, most of it artefacts and not source. So:
- **Footprint = what it is:** tracked source, on a log scale.
- **Basement = what it holds:** data, builds, artefacts, drawn below ground and seen only in the X-ray lens. The skyline
  shows the business, not the storage.
- **Lit floors = what it does:** work commits in 14 days, the estate's own tidying excluded (`tools/register.py`, fixed
  25 Sep).

Log scales are required, not a style choice: with a 1,300-to-1 spread, a linear scale makes 90% of buildings
invisible beside the largest.

**Importance is not height.** How much flows through a building (`used_by` in the edge scan) becomes the width of its
plaza and the roads into it. Otherwise the busiest hub would be a skyscraper whether or not anyone works on it.

### 6.3 What the rest of the record draws

| Record | Look |
|---|---|
| lifecycle | active: lit glass; warm: warm light; dormant: stone; archived: sinking; dark: a vacant plot |
| building code score | roof colour; a red roof is below code |
| public / private | an open ground floor / walled |
| provenance: fork or adopted | the upstream's flag on the roof; foreign buildings sit in the embassy quarter |
| keeper (`plan/owners.json`) | the keeper's colour on the door |
| rooms (worktrees) | annexes on bridges; a worktree inside the repo itself (5 today) is an illegal extension |
| hidden folders | the plumbing: counts per class until the X-ray lens opens one building |
| pebbles / rocks | litter a crew can sweep / a boulder for the keeper |

---

## 7. The citizens

### 7.1 Three kinds, because the records have three kinds

1. **Seats** (persistent roles: A0, ESTATE, CRM-LEAD, BYK, STREAMING, LAPTOP, ...). Each has a house on the port in its
   harness's style (Claude, Codex, omp), and a power line to the account it draws on (the claude-siso login, or plain
   `claude`, Fahmy's). Account usage (`az-usage`) is the water level of each reservoir.
2. **Sessions** (4,402 in two months). At a thousandfold, one figure per session is noise. Sessions are **heat**: a
   glow over the buildings where work happened, fading over a week. Their births are drawn where they landed: right
   ones become buildings, wrong ones become litter traced back to their session.
3. **Workers** (DeepSeek, Haiku, Qwen): labourers who walk out of a seat's house, work, and vanish when their run ends.

### 7.2 What a seat is doing

| State | Seen |
|---|---|
| working in a building | walks the roads to it; scaffolding and a build animation there |
| working on another machine | stands at that grid's substation |
| talking to another agent, or posting to the console | an arc from the agent to whoever it talks to |
| waiting on Shaan (an open decision card) | a flare above it, seen from any zoom |
| idle | at home, lights on |
| done | at home, lights off, asleep |

### 7.3 The observation problem, and how to close it

Today the records cannot say where most agents are. The operations centre sees 7 running agents and places 1 in a repo:
herdr reports every pane's folder as `~`, and most agents have no name ("unnamed"; the known gap "recipient lacks stable
session identity"). An honest world would show six agents standing at home while they are working. Three signals close
it, in order of trust:
1. **Stable seat names** in herdr (the agent stack's fix), so a citizen is someone.
2. **The building an agent last wrote to**, read from its session's recent tool calls: file paths and working folders
   only, never its reasoning. Map the path to a building with the register.
3. **Check-ins on the Work page** (`siso-work`), when the agent declares its task.

Until those exist, the world shows an agent's location as unknown, fogged, rather than guessing.

---

## 8. Roads and pipes: everything feeds something

Shaan: "every single thing here exists to either provide training data for another or used as a part of another or a
set part of the process ... they all flow like water".

**Roads** carry what one building gives another. Four kinds, from the flows in `docs/PLANNER.md` §3:
- **money** (gold): partners → hq;
- **code** (stone): Library banks → factory → products and client apps; an industry's pack → its vertical → its
  partners (legend §3b);
- **knowledge** (blue): client work → frameworks → the Library;
- **training** (light): sessions → lessons, memory and skills → the port's power station.

**Pipes** carry running things: each launchd job, container and server is a pipe from its building to its machine's
grid. A job whose program is missing is a broken pipe; one with no manifest line is unlicensed (goal A11).

**Evidence decides what is drawn.** A road exists only with evidence: a name found in another building's file, with
file and line (`plan/planner/edges.json`), or a flow confirmed in `docs/FLOWS.md`. Proven roads are paved; guessed ones
are dirt tracks, drawn fainter. The scan's generic names ("monorepo", "design-system") make noise today, so a hub with
39 incoming links is checked before it is drawn as a crossroads.

**Traffic** moves on a road when the flow actually happens: a commit in one building that names another, a pipeline
run, a session writing a lesson. An empty road for months is a finding.

**A building with no road in or out feeds nothing.** Under Shaan's principle, that is the strongest vault candidate the
world can show, and it shows it without any agent writing a report.

---

## 9. Growth: the world gets better as the estate does

Shaan wants to see the estate improve. So the points system (`docs/SCORE.md`) is not a number beside the world; it is
the world's condition.

**The era sets the whole look.** At South Sudan 1980s: dirt roads, dark windows, litter everywhere. By Shenzhen: paved
districts, walls round the partners. By Shanghai 2020: every road paved and lit, traffic flowing, the port's power
station humming. By 2050: the city repairs itself while you watch.

**Each unfuck check changes one visible thing**, so every upgrade has a picture:

| Check | Before | After |
|---|---|---|
| A3 doctor green | twin buildings chained together, buildings inside buildings | each building alone on its plot |
| A5 names | signs that do not match their buildings; partners outside their walls | signs match; partners inside their cities |
| A6 one of each | three skills warehouses, several Agent Zero houses | one of each |
| A8 generated doors | boarded doors (69) | every door open and signed |
| A11 services from one manifest | unlicensed and broken pipes | every pipe licensed and whole |
| A12 harness homes | 14 empty houses on the port | one house per harness |
| A14 one key store | coins on the street | the treasury holds them all |
| A15 every machine on the map | fogged and dark grids | every grid lit |
| A16 everything backed up | buildings without foundations, scaffolding | foundations under everything |

**The timeline replays the growth.** A scrubber runs from 27 Jul, when the birth records start, using
`score-history.jsonl`, `moves.jsonl`, `removed.jsonl`, `vault.jsonl` and `births.json`. Buildings appear, move to their
right place, sink into the vault; litter piles up and gets swept; the era label changes. Played end to end, it is the
story of the unfuck in a minute.

---

## 10. Lenses: one world, several views

Like the info views in a city builder, a lens recolours the same world. The world never moves:

| Lens | Shows | Answers |
|---|---|---|
| the business (default) | lifecycle, agents, flares | what is happening, what waits on me |
| health | code score, disagreements, the weather | what is broken |
| machines | one grid at a time | what runs where |
| flows | roads by kind and traffic | what feeds what |
| history | the timeline | how we got here |
| X-ray | basements, plumbing (hidden folders), rooms | what a building holds |
| privacy | personal unfogged (local only) | Shaan's own island |

---

## 11. Scale: from 300 buildings to 300,000

Today: 300 buildings, 116 compounds, 7 seats, 21 servers, 4,402 sessions. A thousandfold is 300,000 buildings, hundreds
of seats and millions of sessions. What keeps the world usable:

1. **Places never move.** A building's coordinates come from its postcode, hashed to a slot on its district's hex grid.
   Districts fill outward in rings, keeping empty slots at the edge; a new district claims land at its island's coast.
   A treemap is ruled out: adding one building reshuffles the whole city, and Shaan would lose his bearings on every
   visit. Research on "software cities" (CodeCity; later work on stable layouts for evolving code) found the same
   problem: layout stability matters more than packing.
2. **Tiles, like a map.** The world is cut into one tile per district, loaded as the camera approaches, the way map
   apps load streets. Nothing ever loads the whole estate at once.
3. **Semantic zoom.** At the world level, islands are masses. At an island, districts are blocks whose height is their
   total. At a district, buildings. At a building, floors, rooms, agents and the X-ray.
4. **Aggregates.** Sessions are heat, not people. Hidden folders are counts until opened. Far districts are one mesh.
5. **Instancing.** One instanced mesh per building shape and material: tens of thousands of buildings in a handful of
   draw calls at 60 frames a second in a browser. Agents are animated sprites, hundreds at most.
6. **Log scales** everywhere, so a 3-file repo and terabytes of data share one skyline.

---

## 12. Look and feel

**Readable toy-city, not photoreal.** Low-poly, soft light, clear colours, in the family of Townscaper and Islanders.
Three reasons: it stays legible when there are thousands of buildings; it is cheap to draw on a laptop that already
struggles for RAM; and an era's style (dirt to paved, dark to lit) reads instantly in that style.

**Precedents it borrows from:**
- CodeCity (software as a city: districts as packages, buildings as classes);
- Gource (a repo's history replayed, with each contributor walking to the files they touch). That is our agents
  walking to the buildings they work in;
- Anno (islands joined by trade routes);
- Factorio (flows you can watch);
- Cities: Skylines (info-view lenses);
- RimWorld (citizens whose jobs you can read at a glance).

**One colour language, used everywhere,** including the operations centre and the console: each machine's colour,
each flow's colour, lifecycle tones and code-score roofs. It is defined once in the city service.

---

## 13. The data: what the world reads

The world never scans a disk. It reads what the estate already computes:

| Layer | Source today | Cadence |
|---|---|---|
| map | `.estate/map.json`, `machines/register.json`, `plan/legend.json`, `plan/owners.json`, `plan/home-zones.json` | nightly, `estate refresh` |
| ground | `machines/<m>/repos.json`, `census.json`, `dots.json`, `backup.json` | hourly |
| life | the operations centre's `city.json` (agents, servers, activity, alerts, backup, machines), herdr, launchctl, the vps records | every 2 minutes; agents every few seconds |
| history | `score-history.jsonl`, `moves.jsonl`, `removed.jsonl`, `vault.jsonl`, `births.json` | as it happens |
| roads | `plan/planner/edges.json`, later `docs/FLOWS.md` | nightly |

**Output:** `world/` beside the operations centre:
- `world.json`: regions, districts, grids, horizon ports, era, colour language;
- one tile per district: buildings, rooms, roads, pipes;
- a live feed from the city service already running at `127.0.0.1:8895`: agents, flares, traffic, fog.

**Privacy.** This is the most sensitive picture SISO has: every client, every service, every machine.
- It stays private: SISO Internal Labs behind its login, or local. It is never published.
- Personal is drawn by district only. `legal/` and `private/` exist only as a sealed shape.
- Keys are counts, never names or values.
- Client boxes are never mapped.
- Agent locations come from tool-call paths, never from what an agent thought.

---

## 14. What has to be true before it is built

These are findings from writing this spec, and they are estate work in their own right:
1. **The register still speaks the old model.** Its islands are `agency`, `engine`, `library`, `halo`, `home`, `vault`,
   `foreign`, `territory`, and 150 of 300 buildings have no district. The regions must come from `plan/legend.json`
   first, or the world draws the old mess beautifully.
2. **Personal, the home zones and the other machines are not in the register.** Today it knows the workspace and the
   siso-vps checkouts. Each zone of `~`, each personal area, and each VPS service needs a record.
3. **Agents need identity and location** (§7.3): stable seat names in herdr, and a building per session from its tool
   calls.
4. **The flows need confirming** (`docs/FLOWS.md`), so roads are evidence and not name-matching noise.
5. **The moves land:** HALO into `partners/halo`, the merges in `docs/MERGES.md`.

---

## 15. The order to build it, each step with its gate

1. **The register speaks the legend, and holds the whole estate** (personal, home zones, machines, VPS services).
   Gate: every entry in `~` and every personal area has a region; `estate where` agrees with the world.
2. **`world.json` and the tiles** from the records. Gate: a stable-place test (add 100 fake buildings; no existing
   coordinate changes) and a privacy test (no name under `legal/` or `private/`, no key value, in any output).
3. **The static world:** regions, districts, buildings by kind, the three sizes, semantic zoom, instancing. Gate:
   60 fps with a synthetic 30,000-building estate on this laptop.
4. **The disagreements and the weather** (§4, §9). Gate: every alert in the operations centre has a visible place.
5. **Citizens:** seats and houses, states, flares, workers, session heat. Gate: each running seat stands in the right
   building, or in fog when unknown, never in a guessed place.
6. **Roads and pipes** from evidence, paved or dirt. Gate: every road opens its file and line.
7. **The timeline and the eras.** Gate: a replay from 27 Jul matches `HISTORY.md` day by day.
8. **Into SISO Internal Labs** as the window (score tiers W1 and W5). Gate: Shaan opens it instead of asking where
   things stand.

## 16. Open questions for Shaan

1. **What is the first thing you want to see each morning?** The default lens and camera should open there.
2. **Should agents talk in the world** (speech arcs only) or show a line of what they are doing on hover?
3. **Sound:** none, or a quiet city hum that rises with traffic?
