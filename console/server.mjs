#!/usr/bin/env node
// SISO console — the agent-controlled viewport.
// Agents POST cards; the page renders lanes per agent over SSE; Shaan answers
// decision cards; answers land in inbox/<agent>.jsonl where the agent's Monitor
// (mid-turn) or Stop-hook waiter (idle) picks them up. No deps, Node >= 20.
//
//   POST /post    {agent, kind: note|card|html|decision|status, title?, body, options?, id?, url?}
//   POST /answer  {agent, card, text}
//   POST /resolve {card}
//   GET  /events  SSE (replays last REPLAY events, then live)
//   GET  /state   {open_decisions, unread, agents}
//   GET  /        the page
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { homedir } from 'node:os';
import { fileURLToPath } from 'node:url';

const HOME = process.env.CONSOLE_HOME || path.dirname(fileURLToPath(import.meta.url));
const STATE = process.env.CONSOLE_STATE || path.join(HOME, 'state');
const INBOX = path.join(STATE, 'inbox');
const EVENTS = path.join(STATE, 'events.jsonl');
const STATEJSON = path.join(STATE, 'state.json');
const PORT = Number(process.env.CONSOLE_PORT || 8891);
const REPLAY = 5000;
const PAGE_FILE = process.env.CONSOLE_PAGE || path.join(HOME, 'page.html');  // the page; read per request so it can change without a restart
const STATIC = process.env.CONSOLE_STATIC || path.join(HOME, 'static');
const TRIM = 16000;  // replayed bodies above this are sent empty; the page fetches /card/<id> on demand
const TYPES = { '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.webp': 'image/webp', '.css': 'text/css', '.js': 'text/javascript' };
fs.mkdirSync(INBOX, { recursive: true });

const clients = new Set();
let seq = 0;
const cards = new Map(); // id -> latest card event
const unreadByAgent = new Map();

// Memory: events.jsonl is ~460 MB (post bodies up to 1.9 MB each), so it is streamed, never held. A card whose body is over TRIM
// keeps only its metadata plus the byte range of its line (_off/_len); /card/<id> reads that one line back on demand.
// `replay` holds the last REPLAY lines in the form /events sends them (big ones already trimmed).
const replay = [];
function replayLine(e, raw) {
  if (raw.length <= TRIM) return raw;
  const t = { ...e, body: '', trimmed: true }; delete t._off; delete t._len;
  return JSON.stringify(t);
}
function remember(e, raw, off, len) {
  replay.push(replayLine(e, raw)); if (replay.length > REPLAY) replay.shift();
  if (e.type === 'post' && typeof e.body === 'string' && e.body.length > TRIM) { e = { ...e, body: undefined, _off: off, _len: len }; }
  index(e);
}
function load() {
  if (!fs.existsSync(EVENTS)) return;
  const fd = fs.openSync(EVENTS, 'r');
  const buf = Buffer.allocUnsafe(8 << 20);
  let pos = 0, lineStart = 0, carry = [];
  const handle = (lineBuf, off) => {
    if (!lineBuf.length) return;
    const raw = lineBuf.toString('utf8');
    try { const e = JSON.parse(raw); seq = Math.max(seq, e.seq || 0); remember(e, raw, off, lineBuf.length); } catch {}
  };
  for (;;) {
    const n = fs.readSync(fd, buf, 0, buf.length, pos); if (!n) break;
    let from = 0;
    for (let i = buf.indexOf(10, 0); i !== -1 && i < n; i = buf.indexOf(10, from)) {
      carry.push(Buffer.from(buf.subarray(from, i)));
      const lineBuf = carry.length === 1 ? carry[0] : Buffer.concat(carry); carry = [];
      handle(lineBuf, lineStart); lineStart += lineBuf.length + 1; from = i + 1;
    }
    if (from < n) carry.push(Buffer.from(buf.subarray(from, n)));
    pos += n;
  }
  if (carry.length) handle(Buffer.concat(carry), lineStart);
  fs.closeSync(fd);
}
function readCard(c) {
  if (c._off === undefined) return c;
  const fd = fs.openSync(EVENTS, 'r');
  try {
    const b = Buffer.allocUnsafe(c._len); fs.readSync(fd, b, 0, c._len, c._off);
    const full = JSON.parse(b.toString('utf8')); if (c.resolved) full.resolved = true; return full;
  } finally { fs.closeSync(fd); }
}
function index(e) {
  if (e.type === 'post') { cards.set(e.id, e); unreadByAgent.set(e.agent, (unreadByAgent.get(e.agent) || 0) + (e.kind === 'status' ? 0 : 1)); }
  if (e.type === 'answer' || e.type === 'resolve') { const c = cards.get(e.card); if (c) c.resolved = true; }
  if (e.type === 'seen') unreadByAgent.set(e.agent, 0);
}
function writeState() {
  const open = [...cards.values()].filter(c => c.kind === 'decision' && !c.resolved);
  const s = { open_decisions: open.length, open: open.map(c => ({ id: c.id, agent: c.agent, title: c.title })), unread: [...unreadByAgent.entries()].reduce((a, [, n]) => a + n, 0), agents: [...new Set([...cards.values()].map(c => c.agent))], updated: new Date().toISOString() };
  fs.writeFileSync(STATEJSON, JSON.stringify(s));
  return s;
}
function emit(e) {
  e.seq = ++seq; e.ts = e.ts || new Date().toISOString();
  const raw = JSON.stringify(e);
  const off = fs.existsSync(EVENTS) ? fs.statSync(EVENTS).size : 0;
  fs.appendFileSync(EVENTS, raw + '\n');
  remember(e, raw, off, Buffer.byteLength(raw)); writeState();
  const data = `data: ${JSON.stringify(e)}\n\n`;
  for (const res of clients) { try { res.write(data); } catch {} }
  return e;
}
function notify(title, body, url) {
  const esc = s => String(s).replace(/["\\]/g, ' ').slice(0, 180);
  // Clickable when terminal-notifier is installed: click opens the card's URL (or the console). Falls back to osascript.
  const tn = '/opt/homebrew/bin/terminal-notifier';
  const open = url || `http://127.0.0.1:${PORT}/`;
  execFile(tn, ['-title', esc(title), '-message', esc(body), '-open', open, '-sound', 'Glass', '-group', 'siso-console'], (err) => {
    if (err) execFile('osascript', ['-e', `display notification "${esc(body)}" with title "${esc(title)}" sound name "Glass"`], () => {});
  });
}
function json(res, code, obj) { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); }
function body(req) { return new Promise((r) => { let s = ''; req.on('data', c => { s += c; if (s.length > 2e6) req.destroy(); }); req.on('end', () => { try { r(JSON.parse(s || '{}')); } catch { r(null); } }); }); }
const safeName = s => String(s || 'agent').replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 64);

// Agents that post under a bare session id ("5c76faff-6e6") get a readable name from the
// last aiTitle Claude Code wrote into that session's transcript. Titles change as a session
// moves on, so a hit is cached for ten minutes; a miss is cached for good.
const PROJECTS = path.join(homedir(), '.claude', 'projects');
const titleCache = new Map();
function sessionTitle(agent) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{3}$/.test(agent)) return null;
  const hit = titleCache.get(agent);
  if (hit && (hit.title === null || Date.now() - hit.at < 600e3)) return hit.title;
  let title = null;
  try {
    for (const dir of fs.readdirSync(PROJECTS)) {
      let names; try { names = fs.readdirSync(path.join(PROJECTS, dir)); } catch { continue; }
      const f = names.find(n => n.startsWith(agent) && n.endsWith('.jsonl'));
      if (!f) continue;
      const file = path.join(PROJECTS, dir, f), size = fs.statSync(file).size, len = Math.min(size, 512000);
      const buf = Buffer.alloc(len), fd = fs.openSync(file, 'r');
      fs.readSync(fd, buf, 0, len, size - len); fs.closeSync(fd);
      const m = [...buf.toString('utf8').matchAll(/"aiTitle":"((?:[^"\\]|\\.){1,120})"/g)].pop();
      if (m) try { title = JSON.parse(`"${m[1]}"`); } catch {}
      break;
    }
  } catch {}
  titleCache.set(agent, { title, at: Date.now() });
  return title;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  if (req.method === 'GET' && url.pathname === '/') {
    let page = PAGE;
    try { page = fs.readFileSync(PAGE_FILE, 'utf8'); } catch {}
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }); return res.end(page);
  }
  if (req.method === 'GET' && url.pathname.startsWith('/static/')) {
    const name = path.basename(url.pathname);
    const file = path.join(STATIC, name);
    if (!fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { 'content-type': TYPES[path.extname(name)] || 'application/octet-stream', 'cache-control': 'max-age=3600' });
    return fs.createReadStream(file).pipe(res);
  }
  const cardRoute = url.pathname.match(/^\/card\/([A-Za-z0-9_.-]+)(\/html)?$/);
  if (req.method === 'GET' && cardRoute) {
    const c = cards.get(cardRoute[1]);
    if (!c) { res.writeHead(404); return res.end('no such card'); }
    let full; try { full = readCard(c); } catch { res.writeHead(500); return res.end('card body unreadable'); }
    if (!cardRoute[2]) return json(res, 200, full);
    // Agent-authored HTML: the sandbox CSP gives it an opaque origin even when opened as a
    // top-level tab, so its scripts cannot post answers to this console.
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'content-security-policy': 'sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms' });
    return res.end(full.body);
  }
  if (req.method === 'GET' && url.pathname === '/agents') {
    const titles = {};
    for (const a of new Set([...cards.values()].map(c => c.agent))) { const t = sessionTitle(a); if (t) titles[a] = t; }
    return json(res, 200, { titles });
  }
  // Pages a Claude session posted (html cards, or cards with a url), newest first, without bodies. SISO Code reads this
  // to show each thread's pages (AGENT-BASE, 14 Oct: "see these pages it made for me" without going to the console).
  if (req.method === 'GET' && url.pathname === '/pages') {
    const sessions = new Set((url.searchParams.get('session') || '').split(',').filter(Boolean));
    const pages = [...cards.values()]
      .filter(c => c.session && sessions.has(c.session) && (c.kind === 'html' || c.url))
      .sort((a, b) => b.seq - a.seq).slice(0, 50)
      .map(c => ({ id: c.id, title: c.title || c.id, kind: c.kind, ts: c.ts, session: c.session,
        url: c.kind === 'html' ? `http://127.0.0.1:${PORT}/card/${c.id}/html` : c.url }));
    return json(res, 200, { pages });
  }
  if (req.method === 'GET' && url.pathname === '/state') return json(res, 200, writeState());
  if (req.method === 'GET' && url.pathname === '/events') {
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
    for (const l of replay) res.write(`data: ${l}\n\n`);
    res.write(`data: ${JSON.stringify({ type: 'ready', seq })}\n\n`);
    clients.add(res); req.on('close', () => clients.delete(res));
    return;
  }
  if (req.method === 'POST') {
    const b = await body(req); if (!b) return json(res, 400, { error: 'bad json' });
    if (url.pathname === '/post') {
      const agent = safeName(b.agent); const kind = ['note', 'card', 'html', 'decision', 'status'].includes(b.kind) ? b.kind : 'note';
      const id = b.id ? safeName(b.id) : `${agent}-${Date.now().toString(36)}`;
      const e = emit({ type: 'post', id, agent, kind, title: String(b.title || '').slice(0, 200), body: String(b.body || '').slice(0, 1900000), options: Array.isArray(b.options) ? b.options.slice(0, 8).map(String) : undefined, url: b.url ? String(b.url) : undefined, session: b.session ? String(b.session) : undefined });
      if (kind === 'decision') notify(`${agent} needs a decision`, e.title || e.body, e.url);
      else if (kind === 'card' && e.url) notify(`${agent}: ${e.title || 'open this'}`, e.body || e.url, e.url);
      // pop: open the URL on Shaan's screen. OFF unless he turns it on himself (SHAAN_OPEN=1 in the console server's env):
      // 3 Oct ~04:4x he said "why do pages still auto open i don't like them auto opening", reversing his earlier 23:18 ask.
      if (b.pop && e.url && /^https?:\/\//.test(e.url) && process.env.SHAAN_OPEN === '1') execFile('/usr/bin/open', [e.url], () => {});
      return json(res, 200, { ok: true, id, seq: e.seq });
    }
    if (url.pathname === '/answer') {
      const agent = safeName(b.agent); const text = String(b.text || '').slice(0, 4000); const card = b.card ? safeName(b.card) : undefined;
      const line = { ts: new Date().toISOString(), card, text, from: 'shaan' };
      fs.appendFileSync(path.join(INBOX, `${agent}.jsonl`), JSON.stringify(line) + '\n');
      // Session inbox uses the 12-char prefix: console-wait/console-inbox poll `${SID:0:12}` (D6 finding 1, 2026-09-04).
      const c = card && cards.get(card); if (c && c.session) fs.appendFileSync(path.join(INBOX, `${safeName(String(c.session).slice(0, 12))}.jsonl`), JSON.stringify(line) + '\n');
      emit({ type: 'answer', agent, card, text });
      return json(res, 200, { ok: true });
    }
    if (url.pathname === '/resolve') { emit({ type: 'resolve', card: safeName(b.card) }); return json(res, 200, { ok: true }); }
    if (url.pathname === '/seen') { emit({ type: 'seen', agent: safeName(b.agent) }); return json(res, 200, { ok: true }); }
  }
  res.writeHead(404); res.end();
});

const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SISO console</title>
<style>
:root{--bg:#0b0d10;--panel:#12161b;--line:#222a33;--text:#d9dee5;--dim:#7c8794;--acc:#7cc4ff;--warn:#ffb454;--ok:#5fd68a;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,sans-serif}
header{position:sticky;top:0;z-index:2;display:flex;gap:14px;align-items:center;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--line)}
header b{color:var(--acc)}header .pill{padding:2px 8px;border:1px solid var(--line);border-radius:999px;color:var(--dim);cursor:pointer}header .pill.on{color:var(--text);border-color:var(--acc)}
#open{color:var(--warn)}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px;padding:12px 16px;align-items:start}
.lane{background:var(--panel);border:1px solid var(--line);border-radius:10px;min-width:0}
.lane h2{margin:0;padding:8px 12px;font-size:13px;color:var(--acc);border-bottom:1px solid var(--line);display:flex;justify-content:space-between}
.lane h2 small{color:var(--dim);font-weight:400}
.card{padding:10px 12px;border-bottom:1px solid var(--line)}.card:last-child{border-bottom:0}
.card .t{font-weight:600;margin-bottom:4px}.card .m{color:var(--dim);font-size:11px;margin-top:6px}
.card.status{padding:5px 12px;color:var(--dim);font-size:12px}
.card.decision{border-left:3px solid var(--warn)}.card.decision.done{border-left-color:var(--ok);opacity:.7}
.card pre{white-space:pre-wrap;word-break:break-word;margin:0;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
.card iframe{width:100%;height:360px;border:1px solid var(--line);border-radius:6px;background:#fff}
.opts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.opts button,.ans button{background:#1b2430;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer}.opts button:hover{border-color:var(--acc)}
.ans{display:flex;gap:6px;margin-top:8px}.ans input{flex:1;background:#0f1318;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
.answer{color:var(--ok);font-size:12px;margin-top:6px}
a{color:var(--acc)}
</style></head><body>
<header><b>SISO console</b><span id="open"></span><span id="filters"></span><span style="margin-left:auto;color:var(--dim)" id="conn">connecting…</span></header>
<main id="main"></main>
<script>
const lanes=new Map(),cards=new Map();let filter=null,unseen=0;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const link=s=>esc(s).replace(/(https?:\\/\\/[^\\s<]+)/g,'<a href="$1" target="_blank">$1</a>');
function lane(a){if(lanes.has(a))return lanes.get(a);const el=document.createElement('section');el.className='lane';el.dataset.agent=a;el.innerHTML='<h2><span>'+esc(a)+'</span><small></small></h2><div class="cards"></div>';document.getElementById('main').prepend(el);lanes.set(a,el);renderFilters();return el}
function renderFilters(){const f=document.getElementById('filters');f.innerHTML=[null,...lanes.keys()].map(a=>'<span class="pill'+((filter===a)?' on':'')+'" data-a="'+(a||'')+'">'+(a||'all')+'</span>').join(' ');f.querySelectorAll('.pill').forEach(p=>p.onclick=()=>{filter=p.dataset.a||null;renderFilters();for(const [a,el] of lanes)el.style.display=(!filter||filter===a)?'':'none'})}
function post(url,obj){return fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(obj)})}
function render(e){const L=lane(e.agent);const wrap=L.querySelector('.cards');let el=cards.get(e.id);if(!el){el=document.createElement('div');cards.set(e.id,el);wrap.prepend(el)}
el.className='card '+e.kind+(e.resolved?' done':'');let h='';if(e.title)h+='<div class="t">'+esc(e.title)+'</div>';
if(e.kind==='html')h+='<iframe sandbox="allow-scripts allow-popups" srcdoc="'+esc(e.body)+'"></iframe>';else if(e.kind==='status')h+=link(e.body);else h+='<pre>'+link(e.body)+'</pre>';
if(e.url)h+='<div class="m"><a href="'+esc(e.url)+'" target="_blank">'+esc(e.url)+'</a></div>';
if(e.kind==='decision'&&!e.resolved){h+='<div class="opts">'+(e.options||[]).map(o=>'<button data-o="'+esc(o)+'">'+esc(o)+'</button>').join('')+'</div><div class="ans"><input placeholder="reply to '+esc(e.agent)+'…"><button>send</button></div>'}
if(e.answers)h+=e.answers.map(a=>'<div class="answer">→ '+esc(a)+'</div>').join('');
h+='<div class="m">'+new Date(e.ts).toLocaleTimeString()+' · '+e.kind+'</div>';el.innerHTML=h;
const send=t=>{if(!t)return;post('/answer',{agent:e.agent,card:e.id,text:t})};el.querySelectorAll('.opts button').forEach(b=>b.onclick=()=>send(b.dataset.o));const inp=el.querySelector('.ans input');if(inp){inp.onkeydown=k=>{if(k.key==='Enter')send(inp.value)};el.querySelector('.ans button').onclick=()=>send(inp.value)}
L.querySelector('h2 small').textContent=new Date(e.ts).toLocaleTimeString();if(document.hidden&&e.kind!=='status'){unseen++;document.title='('+unseen+') SISO console'}}
const model=new Map();
function handle(e){if(e.type==='post'){model.set(e.id,{...e,answers:[]});render(model.get(e.id))}else if(e.type==='answer'){const c=model.get(e.card);if(c){c.resolved=true;c.answers.push(e.text);render(c)}}else if(e.type==='resolve'){const c=model.get(e.card);if(c){c.resolved=true;render(c)}}
const open=[...model.values()].filter(c=>c.kind==='decision'&&!c.resolved).length;document.getElementById('open').textContent=open?open+' open decision'+(open>1?'s':''):''}
function connect(){const es=new EventSource('/events');es.onopen=()=>document.getElementById('conn').textContent='live';es.onmessage=m=>{const e=JSON.parse(m.data);if(e.type==='ready'){document.getElementById('conn').textContent='live';return}handle(e)};es.onerror=()=>{document.getElementById('conn').textContent='reconnecting…'}}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){unseen=0;document.title='SISO console'}});connect();
</script></body></html>`;

load(); writeState();
server.listen(PORT, '127.0.0.1', () => { console.log(`console: http://127.0.0.1:${PORT}  state=${STATE}`); });
