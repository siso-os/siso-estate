# The world: ideas to make it ridiculously good

Ideas for the estate world (`plan/world`, spec `docs/RENDER.md`), 25 Sep 2026. Each idea says what you would see, why
it is more than decoration (which question it answers), what data it needs (exists today or to build), and how much
compute it takes, so the list is also a ladder for spending more compute as it arrives.

## 1. The rule every idea has to pass

A cool effect earns its place only if it shows something true about the estate that is hard to see any other way.
Glitter that means nothing wears off in a week; a storm over a district whose backups failed last night is something you
look at every morning. So each idea below is tied to a record the estate keeps, and the ones that need a record that
does not exist yet say so.

**The compute ladder** the ideas are sorted by:

| Rung | Where it runs | What it unlocks |
|---|---|---|
| 1 | your browser, today (three.js, instancing) | everything drawn from the snapshot: shapes, colours, motion |
| 2 | your browser with WebGPU | 100,000+ buildings, GPU particles, real water, shadows at scale |
| 3 | a render box (a VPS with a GPU, or a cloud job) | cinematic videos, photoreal passes, a daily flyover |
| 4 | image and video models (the Higgsfield skills are already installed) | a unique generated facade per building, a film of each month |
| 5 | agents living inside it | the world becomes the control room: plans appear as ghosts, you approve by voice |

## 2. The big five: what I would build first

These five give the most truth per unit of effort, and each works on rung 1.

1. **The proposed world.** One toggle shows the estate *after* a plan: MERGES.md's merges done, HALO inside
   `partners/halo`, the vault folds finished. Buildings that merge slide together and fuse; repos that go to the vault
   sink; the skyline goes from 223 buildings to about 70. You see a plan before anything is touched, and say yes by
   looking. Data: `docs/MERGES.md` and the legend, turned into a move list (to build: a `proposed.json`).
2. **Blast radius.** Click a building and everything that depends on it lights up downstream along the evidence roads;
   everything it depends on lights up upstream. "If the component bank breaks, these 39 go dark." Data:
   `plan/planner/edges.json` (exists).
3. **The replay.** A scrubber from 27 Jul to today. The great migration of 23 Sep plays as a convoy: 202 moves drive
   across the map at once; 84 removals sink; litter piles up where 394 things were made in the wrong place, then the
   sweepers come. Data: `moves.jsonl`, `removed.jsonl`, `vault.jsonl`, `births.json`, `score-history.jsonl` (all exist).
4. **Agents you can actually follow.** Each seat walks to the building it is really in, not the one its tab title
   suggests. Messages between agents fly as paper planes; a decision waiting on you is a flare over your tower. Data: to
   build. It needs stable seat names in herdr, and each session's last written path (file paths only, never reasoning).
5. **Weather is health.** A storm cloud sits over a district with backup errors; fog lies over a machine nobody has
   reached (the Mini since 23 Sep); a rainbow shows on a day something shipped; lightning strikes when the secret scan
   holds a push. Data: the operations centre's `city.json` (exists).

## 3. The land itself

- **Terrain from the business.** Elevation is what a place earns or carries: HALO's city stands on gold hills because
  it funds SISO; the Library is a plateau of accumulated knowledge. Data: the legend today, revenue later (to build,
  private).
- **Rivers are flows.** The heaviest flows become real water channels: Library banks → factory → products is a river
  whose width is the number of imports. A dried riverbed is a flow that stopped. Data: `edges.json` (exists; volumes to
  build).
- **Buildings carry their history as strata.** One band per month of work, stacked in order: old floors in weathered
  stone, new ones in glass. A repo that went quiet grows vines. You read a repo's life off its side. Data: `git log` per
  repo (exists).
- **Brand skins.** Every client and product building wears its own brand: Bykonz Yard in Bykonz's colours with its logo
  on the roof. Data: each repo's favicon and Tailwind colours (in the repos; to extract).
- **Eras change the architecture.** South Sudan 1980s is mud-brick and dirt; Shenzhen is concrete and cranes; Shanghai
  2020 is glass, neon and a maglev looping the mainland; 2050 is bioluminescent and self-repairing. The whole world
  restyles when the score crosses an era. Data: `docs/SCORE.md` (exists).
- **Day and night are real.** At night only what runs is lit, so the city at 03:30 shows the nightly: backup ships sail
  from every island to the GitHub sea, and the data planes' armoured barges dive to the treasury. Data: `launchctl`,
  `backup.json` (exist).

## 4. Life in the streets

- **Work you can see.** Typing sparks fly when an agent edits files; a commit is a crate carried to the harbour; a push
  is a boat leaving for the GitHub sea; a test run is a crane lifting. Claude and Codex wear different uniforms;
  DeepSeek and Haiku workers are drones that swarm out and back.
- **The three shared cranes.** `heavy` allows 3 builds at once machine-wide. Show them as three cranes in the industrial
  quarter with a queue of trucks: you see why the laptop is slow, and who is holding a slot. Data: `heavy --status`
  (exists).
- **Desire paths.** Routes agents walk often wear into the grass and become paved roads. After a month, the real
  workflows are drawn in the ground, whatever the plan said. Data: session paths over time (to build).
- **Litter and sweepers.** Every wrong-place birth drops litter where it landed, with a thread back to the agent that
  made it. Each routine fix in `fixes.jsonl` is a street sweeper that clears one. Data: `births.json`, `fixes.jsonl`
  (exist).
- **The money road.** HALO's gold road carries pulses of value into the hq treasury. Data: finance records (to build;
  private, off by default).

## 5. Seeing through it

- **The X-ray.** Hold a key and buildings turn to glass: basements hold data (oracle-streaming's 4 GB of artefacts),
  pipes are the hidden folders (775, coloured by class), annexes are worktrees. Data: `dots.json`, `repos.json` (exist).
- **The disagreement radar.** A pulse spreads from the camera and pings every place the records disagree: squatters,
  unpushed scaffolding, unlicensed servers, loose keys. Data: exists.
- **Split time.** A slider down the middle of the screen: 22 Sep on the left, today on the right, the same camera.
  Data: the snapshots (exists from today on; the 22 Sep map from `repos-before-moves-2026-09-23.json`).
- **Ask the city.** Type or say "where does a new client go?" The camera flies there and a ghost building appears on
  the right plot, chosen by the legend's three questions. Data: `tools/legend.py`, `estate where` (exist).
- **Undersea cables.** The machines' connections are cables on the sea floor: the laptop to siso-vps, siso-vps to the
  Macs over the tailnet, deploys to halo-vps. A cut cable is a machine gone quiet. Data: `plan/machines.json` (exists).

## 6. Control from inside the world (rung 5)

- **Plans appear as ghosts.** When an agent proposes a plan, its moves appear as translucent buildings and dotted
  roads before anything happens. You say "go" and they solidify as the agent works; you say "no" and they fade. This is
  your "plan first, build on go" rule, made physical.
- **Every building is a door.** Its card gets actions: open the repo, open its page, spawn its keeper agent, run the
  building code on it. The world stops being a picture of the control room and becomes the control room (score tier W3
  and W4).
- **Voice.** "Show me HALO" flies the camera; "send ESTATE to fix the red roofs" dispatches the seat, and you watch it
  walk there. SISO Voice already exists to take the words.
- **Ride along.** A follow camera on one agent: sit on BYK's shoulder for a day at 60x speed.

## 7. Films and presence (rungs 3 and 4)

- **The morning flyover.** A 30-second film rendered at 07:00 on a render box: the camera sweeps what changed
  yesterday, and captions name each change. It lands on the console and your phone. Data: the day's records.
- **Generated facades.** An image model paints a unique facade per building from its README: an observatory for a
  research work, a neon arcade for a consumer app. Regenerated when a repo changes purpose. Rung 4.
- **The monthly film.** A generated video of the city's month, cut to the score: what rose, what sank, which era it
  reached.
- **A tabletop.** The estate on your desk in a headset, at arm's length; lean in to enter a district.
- **Sound, off by default.** Each district plays an instrument; activity sets the tempo; silence means idle. At night
  you would hear the nightly run.

## 8. At a thousand times the size

- **Archipelago to continents.** Partners become countries with borders; the mainland becomes a continent. You zoom
  from satellite view (countries and their capitals) down to a single street, with tiles streamed like a map app.
- **Many estates.** Each partner agency's estate is a neighbouring world you can sail to. The world itself becomes
  something SISO can offer: an estate you can see, sold as part of working with SISO.

## 9. What exists and what to build

| Needed | State |
|---|---|
| a snapshot of the estate as a world (`tools/world.py`) | built 25 Sep |
| moves, removals, vault, births, score history | exist |
| evidence roads (`edges.json`) | exist, noisy; FLOWS.md to confirm |
| operations centre facts (`city.json`) | exist |
| the proposed world (`proposed.json` from MERGES.md) | to build |
| agent identity and real location | to build (agent stack: herdr names; estate: path from tool calls) |
| per-repo history strata, brand colours | to extract from the repos |
| a render box for films | to provision |
