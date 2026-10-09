# The world: build spec for one Opus

A handoff brief, 25 Sep 2026. One Opus takes this and builds the estate world as a beautiful, static, cheap-to-run 3D
page. Read this whole file first, then `docs/RENDER.md` (what the world means), `docs/WORLD-IDEAS.md` (the idea list;
the big five are §2) and `docs/LEGEND.md` (where things belong).

Shaan's words, 25 Sep: "i want a cool 3d world type thing of my thing with islands ... it doesn't need to necessarily
be auto-updated but i just want to be fucking cool", and today: "i don't want it to be auto syncing and super compute
heavy but i want it to look really beautiful".

## 1. What you are building

A diorama of the whole estate: islands on a sea, one per region of the legend (mainland = SISO Agency, port, library,
personal, embassy), districts on them, one building per repo, roads between repos that use each other, agents as
figures, the vault as sunken ruins. It reads a snapshot (`plan/world/world.json`) and draws it. It does not sync, poll
or call any server. A new snapshot comes from rerunning `python3 tools/world.py` and rebuilding.

The bar is "Townscaper at golden hour", not "a data chart in 3D". Someone who has never heard of the estate should want
to screenshot it.

## 2. What exists today (start here, do not start blank)

| Thing | Where | State |
|---|---|---|
| Snapshot generator | `tools/world.py` (262 lines) | works; writes `plan/world/world.json` and inlines it into the page |
| The data | `plan/world/world.json` | 5 regions, 22 districts, 378 hex tiles, 223 buildings, 140 roads, 7 agents, 75 vaulted repos, 8 personal landmarks, machines, weather |
| Current page | `plan/world/template.html` (423 lines) | vanilla three.js 0.160: sky shader, water, instanced hexes, kinds as merged primitives, bloom, lenses, hover cards. Works, but looks like primitives. This is what you replace. |
| Live copy | console card `http://127.0.0.1:8891/card/9324fc44-08e-mugyvw5b/html` | the current page |

Building fields: `id, path, name, kind, life, size, c14 (commits 14d), d30, score (building code), ok, prov, upstream,
keeper, public, vps, laptop, lit, unpushed`. Placement (which hex each building stands on) is already decided in
`world.py` by a stable hex spiral per district. Keep that: a building must not move between snapshots unless it moved on
disk.

## 3. The stack (decided)

**Language: TypeScript, vanilla three.js (r18x, latest), Vite.** Not React Three Fiber, not Babylon, not Godot or Bevy.

- three.js has the reusable prior art below (the best hex diorama on the web is plain three.js), the asset packs load
  as glTF, and the result must be one static HTML file that opens in the console, a browser and the Internal Labs Mac
  app.
- R3F adds React for a page that has almost no UI state; its helpers (drei) exist as `@pmndrs/vanilla` and the
  post-processing library works without React.
- Godot and Bevy web exports are 10 to 40 MB of engine and do not reuse any of the code below.
- **Renderer:** `WebGPURenderer` with TSL shaders, which falls back to WebGL2 by itself. Test in Safari as well as
  Chrome, because the Mac app is WebKit.

**Output:** a single self-contained HTML file (`vite-plugin-singlefile`), with models compressed (meshopt or Draco,
textures KTX2 or small PNG atlases) and inlined, so it posts as one console card. Budget: 12 MB or less. It must never
be published to a public URL: it shows personal and client names.

## 4. What to reuse (checked 25 Sep 2026)

| Repo | Licence | Take |
|---|---|---|
| [felixturner/hex-map-wfc](https://github.com/felixturner/hex-map-wfc) | MIT, 439★ | **The look.** Medieval hex islands in three.js r183, WebGPU + TSL; 4,100 hexes at 2 draw calls per grid (BatchedMesh). Take its `Lighting.js` and `PostFX.js` wholesale: GTAO at half resolution, tilt-shift depth of field scaled to zoom, vignette and grain, shadow frustum fitted per frame, water from scrolling caustics plus sine-wave shoreline bands. Its article (`docs/article/blog-post.md`) explains each. Do not take the wave-function-collapse solver: our layout is data, not random. |
| [KayKit Medieval Hexagon Pack](https://github.com/KayKit-Game-Assets/KayKit-Medieval-Hexagon-Pack-1.0) | CC0 (per KayKit; confirm `LICENSE.txt`) | 200+ hex tiles, buildings and props, glTF. The tiles hex-map-wfc uses. |
| Kenney City Kit, Hexagon Kit ([kenney.nl](https://kenney.nl), [poly.pizza](https://poly.pizza/bundle/City-Kit-0CkvGrBJ0u)) | CC0 | Modern buildings, towers, roads, for the higher eras. |
| [Grizaceo/repociv](https://github.com/Grizaceo/repociv) | MIT, created this month | The closest idea to ours: a Civ-V hex map of a local repo workspace, repos as cities, AI agents as units, three.js r175 low-poly pass. Read it for city tiers, fog of war and agent units. Young (0★), so read it for ideas and take only small pieces. |
| [brunosimon/folio-2025](https://github.com/brunosimon/folio-2025) | MIT (code) | Best in class for charm: toon lighting, wind in foliage, camera feel, sound. Read before tuning the camera and materials. |
| [thaslle/stylized-water](https://github.com/thaslle/stylized-water) | MIT | Cartoon water with shore foam, cheap. Use it if hex-map-wfc's water does not read well at our scale. |
| [pmndrs/postprocessing](https://github.com/pmndrs/postprocessing) | Zlib | Only if you stay on WebGL: SMAA, N8AO, tone mapping, LUT. On WebGPU use three's own TSL passes as hex-map-wfc does. |
| [gkjohnson/three-gpu-pathtracer](https://github.com/gkjohnson/three-gpu-pathtracer) | MIT | Optional "photo mode": a path-traced still of the current view, rendered only when asked. The beauty-per-compute trick: heavy compute once, on demand, never per frame. |
| [MaibornWolff/codecharta](https://github.com/maibornwolff/codecharta) and [liltrendi/gitlantis](https://github.com/liltrendi/gitlantis) | BSD-3 / unclear | Prior art for code-as-city and repo-as-ocean. Read for which metrics people map to height and colour; take no code (gitlantis has no clear licence). |

Put each cloned reference in `~/SISO_Workspace/_reference/` (`estate where` first; never edit it there). Record what
you took, from where and under which licence in `plan/world/app/THIRD-PARTY.md`.

## 5. The look

Aim: a tilt-shift diorama at golden hour. Warm low sun, long soft shadows, ambient occlusion in every crevice, a
turquoise shallow shelf around each island fading to deep blue, foam on every shore, a faint haze towards the horizon.
Saturated but not candy.

1. **Islands are land, not tiles.** Hexes stay as the grid, but the coastline is a smooth cliff and beach skirt, shown
   as terrain, not a honeycomb. Inland hexes vary in height a little by district, so districts read as hills and
   plateaus.
2. **Buildings come from the kind, not a primitive.** Map each `kind` to a small set of assets (KayKit for the early
   eras, Kenney city pieces for the later ones). Pick the variant by a hash of the repo id, so each repo keeps its look
   across snapshots. Height follows `size`; the roof colour is the building-code `score` (the only data colour in the
   default view).
3. **Life is movement, rationed.** Windows glow where `lit`; smoke where `c14 > 0`; scaffolding where `unpushed`;
   a flag on partner walls; boats on the sea lanes; agents as small figures at their building, asleep (a z-bubble)
   when idle. All of this runs from one clock uniform in shaders, not per-object JavaScript.
4. **The sea carries history.** The 75 vaulted repos are ruins under the water beside the island they came from,
   visible through the shallows.
5. **Personal is sealed.** The personal island is misted: a landmark silhouette per area, no names, until a key is held
   down (the current `sealed` flag).
6. **Era sets the architecture.** `world.era` (from `tools/score.py`) chooses the asset set and palette: today's era
   ("Xiaogang, 1978") is villages and dirt roads; Shenzhen is concrete and cranes; Shanghai 2020 is glass and neon.
   Build two eras now (today's, and Shanghai 2020 as the "proposed" look) and make adding one a data table.

Before building the full scene, render **three look stills** of the same camera (for example: warm Townscaper, cool
Monument Valley, night neon) as a small page, post it to the console and let Shaan pick by looking. Build the one he
picks. If he does not answer within the session, build the first.

## 6. Keep it cheap (this is a requirement, not a nicety)

1. **Render on demand.** No continuous `requestAnimationFrame` while nothing changes: render when the camera moves, on
   hover, and at 30 fps for ambient motion for 20 seconds after the last input, then stop and hold a still. A tab left
   open must use about 0% GPU.
2. **Batch.** One `BatchedMesh` or `InstancedMesh` per material, one shared texture atlas. Target 50 draw calls or
   fewer for the whole scene.
3. **Bake what does not move.** Terrain ambient occlusion and island shadows can be baked once at load into vertex
   colours or a lightmap; only buildings and water need live shading.
4. **Quality steps.** On battery or a slow first frame, drop GTAO and depth of field and halve the pixel ratio.
5. **No network at runtime.** Everything is inlined; the page works offline.

## 7. What it must do (interaction)

1. Orbit, pan and zoom, eased, with limits (never under the sea, never lost in the sky). Double-click a building flies
   to it.
2. Hover shows a card (name, path, kind, keeper, commits in 14 days, building-code score, upstream). Keep the current
   card fields.
3. Lenses from the current page: business (default), health (roof colour to red/amber/green), flows (roads as glowing
   arcs), machines (colour by where it runs).
4. Labels: region names always; district names at mid zoom; building names only near. Never overlapping.
5. **Two of the big five, if time allows after the look is right:** blast radius (click a building, light everything
   connected by `roads` downstream and upstream) and the proposed world (a toggle that plays merges and folds from a
   `proposed.json` you generate from `docs/MERGES.md`). Both are pure snapshot data; neither needs sync.

## 8. Where it lives and what you may touch

- New app: `SISO_Agents/siso-estate/plan/world/app/` (Vite project, `src/`, `public/models/`, `THIRD-PARTY.md`).
  Build output: `plan/world/app/dist/world.html`.
- You may edit `tools/world.py` only to **add** fields the renderer needs (for example a variant seed or `proposed`).
  Do not change placement or remove fields; `bin/estate-nightly` runs it.
- Keep `plan/world/template.html` until the new page is accepted, then retire it to `_archive/` with a MANIFEST line.
- Do not touch other repos. Do not publish anywhere public. Run type-checks and Playwright through `heavy` / `shot`.
- Commit your own files in siso-estate; check `git status` first (other agents write here: `machines/laptop/*` is the
  nightly's). Never stash or reset other work.

## 9. Done means (gates, in order)

1. **Look picked:** the three stills posted, Shaan's pick or the default recorded in `.agents/HANDOFF.md`.
2. **It renders:** Playwright at 2000×1250 (installed Chrome) and WebKit, 0 console errors, all 223 buildings, 5
   regions and 140 roads present (count them from the scene, not the JSON).
3. **It is beautiful:** screenshots from 4 fixed cameras (the whole estate, the mainland, the library, HALO's
   partner compound) posted side by side with the current page's same views. Shaan's reaction is the gate.
4. **It is cheap:** measured, not estimated: draw calls ≤ 50; 0 frames rendered in a 10-second idle window after the
   20-second ambient window; 60 fps while orbiting on the laptop on mains power; `world.html` ≤ 12 MB.
5. **It is honest:** a building's card matches `world.json` for 5 random ids; placement is unchanged from the current
   snapshot for every id.

Report back in the `shaan-report` shape with the console URL of the new page and the before/after page.

## 10. Out of scope

Live sync, websockets, real agent locations (needs herdr names first; see WORLD-IDEAS §2.4), films and generated
facades (rungs 3 and 4), sound, VR, and any server.
