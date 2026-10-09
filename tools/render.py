#!/usr/bin/env python3
"""The island render: the register as one self-contained three.js voxel scene (plan/model.json -> render, MODEL.md 11).

  render.py                 write plan/render/index.html and print its path and size
  render.py --out <path>    write it somewhere else

Reads machines/register.json only (never edits it) and inlines just the fields the scene draws, so the page needs no
server, no build step and no dependency beyond three.js from a CDN. Islands are land masses sized by their buildings,
compounds are patches, buildings are voxel towers (footprint by size, height by 14-day activity), lifecycle is colour,
a red roof is below code, a glowing roof runs on a site, and a dark building is an empty plot outline.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REGISTER = os.path.join(REPO, "machines", "register.json")
OUT = os.path.join(REPO, "plan", "render", "index.html")

BUILDING_FIELDS = ("postcode", "path", "island", "compound", "provenance", "seat", "lifecycle", "built_on", "runs",
                   "size_kb", "commits_14d", "code_score", "up_to_code")
ROCK_NAME = {"territory": "the map"}


def payload(register):
    """Only what the scene draws: islands, compounds and the 13 fields a tower is built from."""
    islands, known = [], set()
    for isl in register["islands"]:
        known.add(isl["id"])
        islands.append({"id": isl["id"], "name": isl.get("name") or isl["id"], "folder": isl.get("folder"),
                        "law": isl.get("law"), "governor": isl.get("governor"),
                        # the law is what separates land from serviced ground: islets carry none (MODEL.md 1)
                        "kind": "land" if isl.get("law") else "islet"})
    for b in register["buildings"]:                               # land under no island: the map's rock
        if b["island"] not in known:
            known.add(b["island"])
            islands.append({"id": b["island"], "name": ROCK_NAME.get(b["island"], b["island"]), "folder": None,
                            "law": None, "governor": None, "kind": "rock"})
    compounds = [{"id": c["id"], "island": c["island"], "district": c.get("district"),
                  "gatehouse": c.get("gatehouse"), "keeper": c.get("keeper"), "lifecycle": c.get("lifecycle"),
                  "provenance": c.get("provenance")}
                 for c in register["compounds"]]
    buildings = [{k: b.get(k) for k in BUILDING_FIELDS} for b in register["buildings"]]
    return {"generated_at": register.get("generated_at"), "model": register.get("model"),
            "counts": {"buildings": len(buildings), "compounds": len(compounds)},
            "islands": islands, "compounds": compounds, "buildings": buildings}


def build(register_path=REGISTER, out_path=OUT):
    with open(register_path) as fh:
        register = json.load(fh)
    blob = json.dumps(payload(register), separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("/*__ESTATE__*/null", blob)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(html)
    return out_path, len(html.encode())


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SISO estate · island render</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; overflow: hidden; background: linear-gradient(#070b16, #0e1f38 55%, #1d4a6b); }
  canvas { display: block; }
  body { font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace; color: #e8eef7; }
  #header { position: fixed; top: 14px; left: 50%; transform: translateX(-50%); padding: 6px 14px; border-radius: 999px;
            background: rgba(8,14,26,.72); border: 1px solid rgba(140,180,240,.22); white-space: nowrap; }
  #header b { color: #ffd37a; font-weight: 600; }
  #legend { position: fixed; top: 14px; left: 14px; width: 272px; padding: 12px 14px; border-radius: 12px;
            background: rgba(8,14,26,.78); border: 1px solid rgba(140,180,240,.22); }
  #legend h1 { margin: 0 0 8px; font-size: 12px; letter-spacing: .14em; text-transform: uppercase; color: #9fb6d8; }
  #legend table { width: 100%; border-collapse: collapse; }
  #legend td { padding: 1px 0; vertical-align: top; }
  #legend td.k { width: 24px; }
  .sw { display: inline-block; width: 14px; height: 10px; border-radius: 2px; border: 1px solid rgba(0,0,0,.4); }
  #legend hr { border: 0; border-top: 1px solid rgba(140,180,240,.2); margin: 8px 0; }
  #legend p { margin: 2px 0; color: #b9c8de; }
  #tip { position: fixed; display: none; max-width: 360px; padding: 8px 10px; border-radius: 10px; pointer-events: none;
         background: rgba(6,11,21,.94); border: 1px solid rgba(150,190,250,.35); box-shadow: 0 8px 26px rgba(0,0,0,.5); }
  #tip .pc { color: #ffd37a; font-weight: 600; }
  #tip .r { color: #b9c8de; }
  #tip .r b { color: #e8eef7; font-weight: 500; }
  #hint { position: fixed; bottom: 12px; left: 50%; transform: translateX(-50%); color: #7f93b0; font-size: 12px; }
</style>
<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
    "three/addons/controls/OrbitControls.js": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js"
  }
}
</script>
</head>
<body>
<div id="header"></div>
<div id="legend">
  <h1>SISO estate · legend</h1>
  <table id="legend-rows"></table>
  <hr>
  <p>Red roof = below code (&lt; 0.70).</p>
  <p>Glowing roof = a service runs on a site.</p>
  <p>Taller = more commits in 14 days. Wide plot = ≥ 200 MB.</p>
  <p>Wireframe plot = dark: built on no site.</p>
  <p>Grey island = the vault · violet = the foreign quarter.</p>
</div>
<div id="tip"></div>
<div id="hint">drag to orbit · scroll to zoom · right-drag to pan · hover a tower</div>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const E = window.ESTATE = /*__ESTATE__*/null;

// a postcode may be built on more than one plot (the same repo kept in the Library and the vault, say), so a building
// is identified by its compound and its place in it, never by its postcode alone
const MEMBERS = new Map();
for (const b of E.buildings) {
  const list = MEMBERS.get(b.compound);
  if (list) list.push(b); else MEMBERS.set(b.compound, [b]);
}
const membersOf = id => MEMBERS.get(id) || [];

const COLOR = {                                                   // lifecycle, provenance, ground
  active: 0xffb340, warm: 0x86c9ff, dormant: 0x8d949e, archived: 0x484e55, unscored: 0xeef2f7,
  red: 0xff3b30, glow: 0x2bffc6, dark: 0x8ab4ff, foreign: 0xa855f7, client: 0x2dd4bf, adopted: 0x4ade80,
  sand: 0xdbc68a, grass: 0x5aa845, stone: 0x8d949e, rock: 0x6f7378, path: 0xb0854f, water: 0x14405f,
  utilities: 0x7f8c99, customs: 0x7f8c99, records: 0x7f8c99,
};
const ISLAND_TOP = { vault: COLOR.stone, foreign: 0x9d7fd0 };     // the vault is grey stone, foreign its own colour
const CAPS = { foreign: 'foreign', client: 'client', adopted: 'adopted' };
const LEGEND = [['active', 'active — committed in the last 14 days'], ['warm', 'warm — alive, quiet'],
                ['dormant', 'dormant'], ['archived', 'archived'], ['unscored', 'unscored'],
                ['dark', 'dark — built nowhere']];
const LEGEND_CAPS = [['foreign', 'foreign cap (purple)'], ['client', 'client cap (teal)'], ['adopted', 'adopted cap (green)']];

const hex = n => n.toString(16).padStart(6, '0');
const box = new THREE.BoxGeometry(1, 1, 1);

// a dark plot is an empty plot: a 2x2 frame on the ground with a short post at each corner, drawn as one line set
const PLOT = { r: 1, h: 0.8 };
const PLOT_EDGES = [];
for (const [x, z] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
  const [nx, nz] = [-z, x];
  PLOT_EDGES.push([x * PLOT.r, 0, z * PLOT.r, nx * PLOT.r, 0, nz * PLOT.r]);
  PLOT_EDGES.push([x * PLOT.r, 0, z * PLOT.r, x * PLOT.r, PLOT.h, z * PLOT.r]);
}

const KPITCH = { land: 3, islet: 2, rock: 2 };                    // 1 voxel = 1 unit; pitch = cell per building
const KMARGIN = { land: 3, islet: 2, rock: 2 };
const KOUTER = { land: 5, islet: 4, rock: 3 };                    // shore margin around the packed patches
const KLINK = { land: 2, islet: 1, rock: 1 };
const SLAB = 2, GRASS = 0.7, PATCH = 0.5;
const GROUND = SLAB + GRASS, TOP = GROUND + PATCH;                // island surface, then patch surface
const BARE_ISLET = 12;                                            // a serviced islet with no buildings yet

// ---------------------------------------------------------------- layout: patches on islands, islands in water
const patchOf = (n, kind) => {
  const inner = Math.max(1, Math.ceil(Math.sqrt(Math.max(1, n))));
  return { inner, pitch: KPITCH[kind], side: inner * KPITCH[kind] + KMARGIN[kind] };
};

function shelfPack(items, width, gap) {
  const out = []; let x = 0, y = 0, rowH = 0, w = 0;
  for (const it of items) {
    if (x > 0 && x + it.side > width) { y += rowH + gap; x = 0; rowH = 0; }
    out.push({ it, x, y }); x += it.side + gap; rowH = Math.max(rowH, it.side); w = Math.max(w, x - gap);
  }
  return { out, w, h: y + rowH };
}

function packPatches(items, kind) {                               // the narrowest packing: island-shaped, not a strip
  const sorted = items.slice().sort((a, b) => b.side - a.side);
  const maxSide = Math.max(...sorted.map(i => i.side));
  const area = sorted.reduce((s, i) => s + i.side * i.side, 0);
  const lo = Math.max(maxSide, Math.ceil(Math.sqrt(area) * 0.85)), step = Math.max(1, Math.round(maxSide / 4));
  let best = null;
  for (let w = lo; w <= lo + maxSide * 3; w += step) {
    const p = shelfPack(sorted, w, KLINK[kind]);
    const score = Math.max(p.w, p.h) * 1000 + (p.w * p.h) / 1000;
    if (!best || score < best.score) best = { ...p, score };
  }
  return best;
}

function layoutIslands() {
  const islands = E.islands.map(i => {
    const compounds = E.compounds.filter(c => c.island === i.id)
      .map(c => ({ c, ...patchOf(membersOf(c.id).length, i.kind) }));
    if (!compounds.length) return { ...i, side: BARE_ISLET, places: [], count: 0 };
    const p = packPatches(compounds, i.kind);
    return { ...i, side: Math.max(p.w, p.h) + 2 * KOUTER[i.kind], places: p.out, count: compounds.length };
  });
  const by = new Map(islands.map(i => [i.id, i]));
  const main = by.get('agency') || islands.find(i => i.kind === 'land') || islands[0];
  const LAND_ANGLE = { engine: 205, library: 335, halo: 92, home: 148 };
  const ISLET_ANGLE = { vault: 270, foreign: 33, customs: 176, utilities: 120, records: 237 };
  for (const i of islands) {
    if (i === main) { i.x = 0; i.z = 0; continue; }
    const angle = (i.kind === 'land' ? LAND_ANGLE[i.id] : ISLET_ANGLE[i.id]) || 0;
    const r = (main.side + i.side) / 2 + (i.kind === 'land' ? 18 : 34), a = angle * Math.PI / 180;
    i.x = Math.cos(a) * r; i.z = Math.sin(a) * r;
  }
  for (let iter = 0; iter < 400; iter++) {                        // islands never overlap: push on the shallower axis
    let moved = false;
    for (let a = 0; a < islands.length; a++) for (let b = a + 1; b < islands.length; b++) {
      const A = islands[a], B = islands[b], gap = 6;
      const dx = B.x - A.x, dz = B.z - A.z;
      const ox = (A.side + B.side) / 2 + gap - Math.abs(dx), oz = (A.side + B.side) / 2 + gap - Math.abs(dz);
      if (ox <= 0 || oz <= 0) continue;
      moved = true;
      if (ox < oz) { const s = (dx >= 0 ? 1 : -1) * ox / 2; A.x -= s; B.x += s; }
      else { const s = (dz >= 0 ? 1 : -1) * oz / 2; A.z -= s; B.z += s; }
    }
    if (!moved) break;
  }
  const xs = islands.flatMap(i => [i.x - i.side / 2, i.x + i.side / 2]);
  const zs = islands.flatMap(i => [i.z - i.side / 2, i.z + i.side / 2]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cz = (Math.min(...zs) + Math.max(...zs)) / 2;
  for (const i of islands) { i.x -= cx; i.z -= cz; }
  return islands;
}

const ISLANDS = layoutIslands();

const PLACED = new Map();                                         // plots drawn per island, for the labels and the check
function placeBuildings() {                                       // one plot per building, inside its compound patch
  const out = [];
  for (const island of ISLANDS) {
    if (!island.places.length) continue;
    const ox = island.x - island.side / 2 + KOUTER[island.kind], oz = island.z - island.side / 2 + KOUTER[island.kind];
    for (const { it, x, y } of island.places) {
      const px = ox + x, pz = oz + y, start = (it.side - it.inner * it.pitch) / 2;
      membersOf(it.c.id).forEach((b, n) => {
        const col = n % it.inner, row = Math.floor(n / it.inner);
        out.push({ b, x: px + start + (col + 0.5) * it.pitch, z: pz + start + (row + 0.5) * it.pitch });
        PLACED.set(island.id, (PLACED.get(island.id) || 0) + 1);
      });
    }
  }
  return out;
}

const BUILDINGS = placeBuildings();
const towerHeight = b => Math.min(32, 1 + Math.round(3 * Math.log2(1 + (b.commits_14d || 0))));
const footprint = b => ((b.size_kb || 0) > 200000 ? 2 : 1);

// ---------------------------------------------------------------- scene
const scene = new THREE.Scene();
scene.background = skyTexture();
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.body.appendChild(renderer.domElement);

const bounds = a => [Math.min(...a), Math.max(...a)];
const [minX, maxX] = bounds(ISLANDS.flatMap(i => [i.x - i.side / 2, i.x + i.side / 2]));
const [minZ, maxZ] = bounds(ISLANDS.flatMap(i => [i.z - i.side / 2, i.z + i.side / 2]));
const RADIUS = 0.5 * Math.hypot(maxX - minX, maxZ - minZ);

const camera = new THREE.PerspectiveCamera(42, innerWidth / innerHeight, 1, RADIUS * 30);
const dist = RADIUS / Math.tan(THREE.MathUtils.degToRad(21)) * 0.9;
camera.position.set(0.62, 0.52, 1).normalize().multiplyScalar(dist);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.maxPolarAngle = Math.PI * 0.49;
controls.minDistance = 20;
controls.maxDistance = RADIUS * 4;
controls.update();

scene.add(new THREE.AmbientLight(0x93a5c8, 0.85));
const sun = new THREE.DirectionalLight(0xfff2dc, 1.15);
sun.position.set(RADIUS * 0.7, RADIUS * 1.1, RADIUS * 0.45);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0006;
const half = RADIUS * 1.3;
Object.assign(sun.shadow.camera, { left: -half, right: half, top: half, bottom: -half, near: 1, far: RADIUS * 5 });
sun.shadow.camera.updateProjectionMatrix();
scene.add(sun, sun.target);

const water = new THREE.Mesh(new THREE.PlaneGeometry(RADIUS * 9, RADIUS * 9),
  new THREE.MeshLambertMaterial({ color: COLOR.water }));
water.rotation.x = -Math.PI / 2;
water.position.y = SLAB * 0.45;
scene.add(water);

const sandMat = new THREE.MeshLambertMaterial({ color: COLOR.sand });   // islands: sand edge, inset top layer
const topMats = new Map();
for (const island of ISLANDS) {
  const topColor = ISLAND_TOP[island.id] || COLOR[island.id] || (island.kind === 'rock' ? COLOR.rock : COLOR.grass);
  if (!topMats.has(topColor)) topMats.set(topColor, new THREE.MeshLambertMaterial({ color: topColor }));
  const base = new THREE.Mesh(new THREE.BoxGeometry(island.side, SLAB, island.side), sandMat);
  base.position.set(island.x, SLAB / 2, island.z);
  base.receiveShadow = true;
  const top = new THREE.Mesh(new THREE.BoxGeometry(island.side - 2, GRASS, island.side - 2), topMats.get(topColor));
  top.position.set(island.x, SLAB + GRASS / 2, island.z);
  top.receiveShadow = true;
  scene.add(base, top);
}

// compound patches: slightly raised, path-coloured, one instance each
const patchCount = ISLANDS.reduce((n, i) => n + i.places.length, 0);
if (patchCount) {
  const patches = new THREE.InstancedMesh(new THREE.BoxGeometry(1, PATCH, 1),
    new THREE.MeshLambertMaterial({ color: COLOR.path }), patchCount);
  patches.receiveShadow = true;
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), pos = new THREE.Vector3(), scl = new THREE.Vector3();
  let n = 0;
  for (const island of ISLANDS) {
    const ox = island.x - island.side / 2 + KOUTER[island.kind], oz = island.z - island.side / 2 + KOUTER[island.kind];
    for (const p of island.places) {
      m.compose(pos.set(ox + p.x + p.it.side / 2, GROUND + PATCH / 2, oz + p.y + p.it.side / 2), q,
                scl.set(p.it.side, 1, p.it.side));
      patches.setMatrixAt(n++, m);
    }
  }
  scene.add(patches);
}

// towers and roof voxels, batched by colour; dark buildings are outlines only
const towerBuckets = new Map(), capBuckets = new Map(), darkEdges = [], pickables = [];
BUILDINGS.forEach(({ b, x, z }, owner) => {
  const f = footprint(b);
  if (b.lifecycle === 'dark') { pushEdges(x, GROUND + 0.06, z); return; }
  const h = towerHeight(b);
  bucket(towerBuckets, b.lifecycle, { x, y: TOP + h / 2, z, sx: f, sy: h, sz: f, owner });
  let roof = TOP + h;
  const cap = key => { bucket(capBuckets, key, { x, y: roof + 0.5, z, sx: f, sy: 1, sz: f, owner }); roof += 1; };
  if (CAPS[b.provenance]) cap(CAPS[b.provenance]);
  if (b.code_score != null && b.code_score < 0.7) cap('red');
  if (b.runs && b.runs.length) cap('glow');
});
for (const [key, list] of towerBuckets) instantiate(list, towerMaterial(key), true);
for (const [key, list] of capBuckets) instantiate(list, capMaterial(key), true);
if (darkEdges.length) {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(darkEdges, 3));
  scene.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: COLOR.dark, transparent: true, opacity: 0.7 })));
}

// island labels: camera-facing sprites, pushed to each island's own outer edge and above its tallest tower
for (const island of ISLANDS) {
  const own = BUILDINGS.filter(p => p.b.island === island.id);
  const tallest = own.reduce((h, p) => Math.max(h, towerHeight(p.b)), 0);
  const len = Math.hypot(island.x, island.z) || 1;
  const out = island.kind === 'land' ? 0.3 : 0.55;
  scene.add(islandLabel(island, GROUND + (island.kind === 'land' ? 6 : 13) + tallest,
                        island.x + (island.x / len) * island.side * out,
                        island.z + (island.z / len) * island.side * out));
}

// ---------------------------------------------------------------- legend, header, tooltip
const header = document.getElementById('header');
const drawn = PLACED.size ? [...PLACED.values()].reduce((a, b) => a + b, 0) : 0;
header.innerHTML = `SISO estate · <b>${E.counts.buildings}</b> buildings in <b>${E.counts.compounds}</b> compounds` +
  ` · generated ${E.generated_at}` + (drawn === E.counts.buildings ? '' : ` · only ${drawn} plots drawn`);
document.getElementById('legend-rows').innerHTML =
  LEGEND.map(([k, text]) => `<tr><td class="k"><span class="sw" style="background:#${hex(COLOR[k])}"></span></td><td>${text}</td></tr>`)
    .join('') +
  LEGEND_CAPS.map(([k, text]) => `<tr><td class="k"><span class="sw" style="background:#${hex(COLOR[k])}"></span></td><td>${text}</td></tr>`)
    .join('') +
  `<tr><td class="k"><span class="sw" style="background:#${hex(COLOR.red)};border-style:dashed"></span></td><td>red roof — below code</td></tr>` +
  `<tr><td class="k"><span class="sw" style="background:#${hex(COLOR.glow)}"></span></td><td>glowing roof — running</td></tr>`;

const tip = document.getElementById('tip');
const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2();
const esc = s => String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const row = (k, v) => `<div class="r">${k} <b>${esc(v)}</b></div>`;
addEventListener('pointermove', ev => {
  pointer.set((ev.clientX / innerWidth) * 2 - 1, -(ev.clientY / innerHeight) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(pickables, false);
  if (!hits.length) { tip.style.display = 'none'; return; }
  const b = BUILDINGS[hits[0].object.userData.owners[hits[0].instanceId]].b;
  tip.innerHTML = `<div class="pc">${esc(b.postcode)}</div>` +
    row('compound', b.compound) + row('keeper (seat)', b.seat) + row('lifecycle', b.lifecycle) +
    row('provenance', b.provenance) + row('commits 14d', b.commits_14d == null ? '—' : b.commits_14d) +
    row('runs on', b.runs && b.runs.length ? b.runs.join(', ') : '—') +
    row('plot', b.path);
  tip.style.display = 'block';
  tip.style.left = Math.min(ev.clientX + 16, innerWidth - 380) + 'px';
  tip.style.top = Math.min(ev.clientY + 16, innerHeight - 170) + 'px';
});
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

// ---------------------------------------------------------------- helpers
function skyTexture() {
  const c = document.createElement('canvas');
  c.width = 2; c.height = 256;
  const g = c.getContext('2d'), grad = g.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, '#060a14'); grad.addColorStop(0.55, '#0d1b33'); grad.addColorStop(1, '#1d4a6b');
  g.fillStyle = grad; g.fillRect(0, 0, 2, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function bucket(map, key, item) {
  const list = map.get(key);
  if (list) list.push(item); else map.set(key, [item]);
}

function pushEdges(x, y, z) {
  for (const e of PLOT_EDGES) darkEdges.push(x + e[0], y + e[1], z + e[2], x + e[3], y + e[4], z + e[5]);
}

function instantiate(list, material, pickable) {
  const mesh = new THREE.InstancedMesh(box, material, list.length);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), v = new THREE.Vector3(), s = new THREE.Vector3();
  list.forEach((o, i) => {
    m.compose(v.set(o.x, o.y, o.z), q, s.set(o.sx, o.sy, o.sz));
    mesh.setMatrixAt(i, m);
  });
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.userData.owners = list.map(o => o.owner);
  if (pickable) pickables.push(mesh);
  scene.add(mesh);
  return mesh;
}

function towerMaterial(key) {
  return new THREE.MeshLambertMaterial({ color: COLOR[key] || COLOR.dormant,
    emissive: key === 'active' ? 0x3a2200 : 0x000000 });
}

function capMaterial(key) {
  if (key === 'glow') return new THREE.MeshLambertMaterial({ color: COLOR.glow, emissive: COLOR.glow });
  if (key === 'red') return new THREE.MeshLambertMaterial({ color: COLOR.red, emissive: 0x4a0000 });
  return new THREE.MeshLambertMaterial({ color: COLOR[key], emissive: COLOR[key], emissiveIntensity: 0.28 });
}

function islandLabel(island, y, x, z) {
  const n = PLACED.get(island.id) || 0;
  const lines = [[island.name, 62, 600, '#ffe6ae']];
  if (n) lines.push([`${n} buildings`, 46, 400, '#dce9fa'], [`${island.count} compound${island.count === 1 ? '' : 's'}`, 44, 400, '#a9bedb']);
  else lines.push(['no buildings yet', 46, 400, '#a9bedb']);
  const c = document.createElement('canvas');
  c.width = 480; c.height = 300;
  const g = c.getContext('2d');
  g.textAlign = 'center'; g.shadowColor = 'rgba(0,0,0,.95)'; g.shadowBlur = 16;
  lines.forEach(([text, size, weight, colour], i) => {
    g.font = `${weight} ${size}px ui-monospace, Menlo, monospace`;
    const w = g.measureText(text).width, max = c.width - 24;
    if (w > max) g.font = `${weight} ${Math.floor(size * max / w)}px ui-monospace, Menlo, monospace`;
    g.fillStyle = colour;
    g.fillText(text, c.width / 2, 62 + i * 62);
  });
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  const w = Math.max(22, Math.min(34, island.side * 0.7));
  sprite.scale.set(w, w * (c.height / c.width), 1);
  sprite.position.set(x, y, z);
  return sprite;
}

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});
</script>
</body>
</html>
"""


def main(argv):
    out = argv[argv.index("--out") + 1] if "--out" in argv else OUT
    path, size = build(REGISTER, out)
    print("%s  (%d bytes, %.1f KB)" % (path, size, size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
