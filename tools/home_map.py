#!/usr/bin/env python3
"""Local, read-only view of the private whole-home preservation inventory."""
import argparse
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DEFAULT_DB = Path.home() / 'SISO_Workspace/_data/estate-home-backup-20261016/inventory.sqlite'


def default_inventory():
    complete = DEFAULT_DB.with_name('inventory-all.sqlite')
    if complete.is_file():
        try:
            with connect(complete) as db:
                if db.execute("SELECT 1 FROM scan_info WHERE key='completed_at'").fetchone():
                    return complete
        except sqlite3.Error:
            pass
    return DEFAULT_DB


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def search(path, query='', limit=100, offset=0, kind=''):
    terms = query.split()[:8]
    clauses, params = [], []
    for term in terms:
        clauses.append("path LIKE ? ESCAPE '\\'")
        params.append('%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    if kind:
        clauses.append('kind = ?')
        params.append(kind)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with connect(path) as db:
        rows = [dict(row) for row in db.execute('SELECT path,size,mtime_ns,kind,backup_status,github_location,what_it_is,destination_guess,reason,error FROM entries' + where + ' ORDER BY path LIMIT ? OFFSET ?', params + [min(max(limit, 1), 200), max(offset, 0)])]
    coverage_path = Path(path).with_name('coverage.sqlite')
    if coverage_path.exists() and rows:
        with connect(coverage_path) as db:
            placeholders = ','.join('?' for _ in rows)
            verified = {r['path']: r for r in db.execute(f'SELECT path,status,snapshot_url FROM coverage WHERE path IN ({placeholders})', [r['path'] for r in rows])}
        for row in rows:
            if row['path'] in verified:
                row['backup_status'] = verified[row['path']]['status']
                row['github_location'] = verified[row['path']]['snapshot_url']
    return rows


PAGE = r'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Whole-home preservation · Estate</title>
<style>body{margin:0;background:#10151e;color:#edf1f8;font:15px system-ui}main{max-width:1500px;margin:auto;padding:32px}h1{font-size:30px;margin-bottom:8px}p{color:#b4c0d2}input,select,button{background:#1e293a;color:inherit;border:1px solid #52647d;border-radius:8px;padding:11px}input{width:min(55vw,650px)}button{cursor:pointer}a{color:#8dcaff}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid #29364b;padding:12px;vertical-align:top}th{color:#9fb5d3}td:first-child{overflow-wrap:anywhere;max-width:550px}.wrap{overflow:auto}.stats{display:flex;gap:12px;flex-wrap:wrap;margin:25px 0}.stat{background:#1b2636;padding:18px;border-radius:10px;min-width:150px}.stat b{display:block;font-size:25px}small{color:#a4b4cb}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}#message{min-height:24px}</style>
<!-- uihub:dashboard/storage -->
<main><small>ESTATE / LAPTOP / PRIVATE LOCAL VIEW</small><h1>Whole-home preservation</h1><p>Inventory and verified backup coverage. Destination labels are proposals; no files have been moved.</p>
<div id="stats" class="stats"></div><p id="cutoff"></p>
<form id="form" class="toolbar"><input id="query" placeholder="Search any path…" aria-label="Search paths"><select id="kind" aria-label="Entry kind"><option value="">All entries</option><option value="git_root">Git roots</option><option value="file">Files</option><option value="directory">Folders</option><option value="error">Read errors</option><option value="excluded_directory">Excluded folders</option></select><button>Search</button></form>
<p id="message"></p><div class="wrap"><table><thead><tr><th>Path / what it is</th><th>Bytes</th><th>Last touched</th><th>Backup / GitHub</th><th>Proposed destination / limits</th></tr></thead><tbody id="rows"></tbody></table></div><div class="toolbar"><button id="prev">Previous</button><button id="next">Next 100</button></div></main>
<script>
let offset=0;const $=id=>document.getElementById(id);function el(tag,text){let n=document.createElement(tag);n.textContent=text??'';return n}
async function load(){ $('message').textContent='Loading…';try{const r=await fetch('/api/search?'+new URLSearchParams({q:$('query').value,kind:$('kind').value,offset}));if(!r.ok)throw Error('Query failed');const rows=await r.json();$('rows').replaceChildren();for(const row of rows){let tr=el('tr');let p=el('td',row.path);p.append(el('br'),el('small',row.what_it_is));tr.append(p,el('td',row.size==null?'—':row.size.toLocaleString()),el('td',row.mtime_ns?new Date(row.mtime_ns/1e6).toLocaleString():'—'));let b=el('td',row.backup_status);if(row.github_location&&row.github_location.startsWith('https://github.com/')){let a=el('a','View on GitHub');a.href=row.github_location;a.rel='noreferrer';a.target='_blank';b.append(el('br'),a)}tr.append(b,el('td',[row.destination_guess,row.error||row.reason].filter(Boolean).join(' · ')));$('rows').append(tr)}$('message').textContent=`Showing ${rows.length} entries from ${offset+1}. Unassessed means no verified backup claim.`;$('prev').disabled=offset===0;$('next').disabled=rows.length<100}catch(e){$('message').textContent=e.message}}
$('form').onsubmit=e=>{e.preventDefault();offset=0;load()};$('next').onclick=()=>{offset+=100;load()};$('prev').onclick=()=>{offset=Math.max(0,offset-100);load()};fetch('/api/status').then(r=>r.json()).then(s=>{for(const [label,value]of Object.entries(s.counts)){let d=el('div');d.className='stat';d.append(el('b',value.toLocaleString()),el('span',label));$('stats').append(d)}$('cutoff').textContent='Inventory completed: '+(s.scan.completed_at||'still running')+'. Read errors are gaps, not exclusions.'});load();</script>'''


def status(path):
    with connect(path) as db:
        counts = dict(db.execute('SELECT kind,count(*) FROM entries GROUP BY kind'))
        return {'counts': counts, 'scan': dict(db.execute('SELECT key,value FROM scan_info'))}


def coverage_status(path):
    run = Path(path).parent
    coverage = run / 'coverage.sqlite'
    result = {'verified_files': 0, 'verified_source_bytes': 0}
    if coverage.exists():
        with connect(coverage) as db:
            row = db.execute("SELECT count(*),coalesce(sum(source_bytes),0) FROM coverage WHERE status IN ('verified-bytes','verified-fragments','verified-refetch')").fetchone()
            result = dict(zip(result, row))
    return result


def serve(path, port):
    summary = status(path)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Queries can contain private filenames.

        def do_GET(self):
            if self.headers.get('Host') not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
                self.send_error(403)
                return
            url = urlsplit(self.path)
            args = parse_qs(url.query)
            if url.path == '/':
                body, mime = PAGE.encode(), 'text/html; charset=utf-8'
            elif url.path == '/api/status':
                body, mime = json.dumps(dict(summary, counts=dict(summary['counts'], **coverage_status(path)))).encode(), 'application/json'
            elif url.path == '/api/search':
                try:
                    rows = search(path, args.get('q', [''])[0], offset=int(args.get('offset', ['0'])[0]), kind=args.get('kind', [''])[0])
                except (ValueError, sqlite3.Error):
                    self.send_error(400)
                    return
                body, mime = json.dumps(rows).encode(), 'application/json'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(default_inventory()))
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=8896)
    parser.add_argument('words', nargs='*')
    args = parser.parse_args()
    if args.serve:
        serve(args.db, args.port)
    else:
        print(json.dumps(search(args.db, ' '.join(args.words), limit=20), indent=2))
