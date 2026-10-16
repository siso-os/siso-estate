#!/usr/bin/env python3
"""Render a privacy-redacted depth-three disk report from measured du and read evidence."""
import argparse
import html
import json
import sqlite3
import importlib.util
from pathlib import Path


def build(run):
    run = Path(run)
    home = str(Path.home())
    evidence = json.loads((run / 'size-evidence.json').read_text())
    measured = []
    for line in (run / 'depth3-du.tsv').read_text().splitlines():
        size, path = line.split('\t', 1)
        measured.append({'path': path, 'bytes': int(size) * 1024})
    measured.sort(key=lambda row: row['path'])
    labels = {r['path']: r['safe_public_label'] for r in evidence['top30_non_root_rows']}
    labels.update({r['path']: r['safe_public_label'] for r in evidence.get('additional_large_cache_package_evidence', [])})
    labels.update({home: 'Home', '/Applications': 'Applications', '/Library': 'System Library'})
    ids = {row['path']: f'B{i:05}' for i, row in enumerate(measured, 1)}
    by_path = {row['path']: row for row in measured}
    rows = []
    for row in measured:
        path = row['path']
        root = home if path == home or path.startswith(home + '/') else '/Applications' if path == '/Applications' or path.startswith('/Applications/') else '/Library'
        parent = str(Path(path).parent)
        rows.append({'id': ids[path], 'parent': ids.get(parent), 'label': labels.get(path, 'Private item ' + ids[path]),
                     'bytes': row['bytes'], 'root': labels[root], 'depth': len(Path(path).relative_to(root).parts)})
    top = []
    for entry in evidence['top30_non_root_rows']:
        # Only author-reviewed descriptions and generic labels go into the public artifact.
        fact = entry['content_fact']
        if '/personal' in entry['path']:
            fact = 'Private user-owned files and structured records. Format and metadata evidence is retained locally; payloads require encrypted byte preservation.'
        top.append({'id': ids[entry['path']], 'label': entry['safe_public_label'], 'bytes': entry['size_bytes'],
                    'producer': entry['producer'], 'rebuildability': entry['rebuildability'],
                    'evidence': fact, 'overlap': entry.get('overlap', False)})
    scan = json.loads((run / 'depth3-du-receipt.json').read_text())
    errors = len((run / 'depth3-du-errors.log').read_text().splitlines())
    totals = {labels[p]: by_path[p]['bytes'] for p in [home, '/Applications', '/Library']}
    verified = 0
    result = run / 'personal-legal-result.json'
    if result.exists() and json.loads(result.read_text()).get('status') == 'pushed':
        verified = sum(f['size'] for f in json.loads((run / 'personal-legal-files.json').read_text()))
    refetch, verified_files = 0, 0
    coverage = run / 'coverage.sqlite'
    if coverage.exists():
        with sqlite3.connect(coverage.resolve().as_uri() + '?mode=ro', uri=True) as db:
            statuses = db.execute('SELECT status,count(*),coalesce(sum(source_bytes),0) FROM coverage GROUP BY status').fetchall()
        verified = sum(size for status, count, size in statuses if status in ('verified-bytes', 'verified-fragments'))
        refetch = sum(size for status, count, size in statuses if status == 'verified-refetch')
        verified_files = sum(count for status, count, size in statuses if status.startswith('verified-'))
    denominator_path = run / 'coverage-denominator.json'
    denominator = json.loads(denominator_path.read_text()) if denominator_path.exists() else {}
    restore_path = run / 'three-restore-samples.json'
    restore = json.loads(restore_path.read_text()) if restore_path.exists() else {}
    restore_count = sum(sample.get('match') is True for sample in restore.get('samples', [])) if restore.get('status') == 'verified' else 0
    spec = importlib.util.spec_from_file_location('home_throughput', Path(__file__).with_name('home_throughput.py'))
    metrics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics)
    throughput = metrics.measure(run)
    (run / 'throughput.json').write_text(json.dumps(throughput, indent=2))
    # Only aggregate checkpoint facts may leave the private receipt directory.
    git_checkpoints = {}
    for receipt_path in (run / 'git-preservation').glob('*-own-remote.json'):
        receipt = json.loads(receipt_path.read_text())
        if receipt.get('status') == 'verified':
            git_checkpoints[receipt.get('root')] = int(receipt.get('verified_count', 0))
    metadata_path = run / 'metadata/metadata-upload.json'
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    metadata_result = metadata.get('result', {})
    checkpoints = {'git_roots_with_frozen_refs': len(git_checkpoints),
                   'git_frozen_refs_verified': sum(git_checkpoints.values()),
                   'metadata_remote_verified': metadata_result.get('status') == 'pushed'
                       and metadata_result.get('restore_verified') is True,
                   'complete_home': False}
    lanes_path = run / 'parallel-lanes.json'
    worker_count = len(json.loads(lanes_path.read_text()).get('workers', [])) if lanes_path.exists() else 0
    worker_states = {'running': 0, 'completed': 0, 'failed': 0, 'unknown': 0}
    for number in range(worker_count):
        state_path = run / f'worker-{number}-exit.json'
        state = json.loads(state_path.read_text()).get('status') if state_path.exists() else 'unknown'
        worker_states[state if state in worker_states else 'unknown'] += 1
    checkpoints['worker_states'] = worker_states
    data = {'totals': totals, 'rows': rows, 'top': top, 'scan': scan['completed_at'], 'errors': errors,
            'verified_logical_bytes': verified, 'refetch_logical_bytes': refetch, 'verified_files': verified_files,
            'coverage_denominator': denominator, 'restored_samples': restore_count, 'throughput': throughput, 'checkpoints': checkpoints}
    # Keep the reversible ID-to-path map private, beside the original inventory.
    (run / 'breakdown-private-paths.json').write_text(json.dumps({ids[r['path']]: r['path'] for r in measured}))
    payload = json.dumps(data).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = TEMPLATE.replace('__DATA__', payload)
    (run / 'whats-on-the-laptop.html').write_text(page)
    (run / 'whats-on-the-laptop-summary.json').write_text(json.dumps({k: v for k, v in data.items() if k not in ('rows', 'top')}, indent=2))
    return run / 'whats-on-the-laptop.html'


TEMPLATE = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>What's on the laptop · SISO Estate</title>
<style>*{box-sizing:border-box}body{margin:0;background:#0b1220;color:#e8edf5;font:15px/1.5 system-ui}main{max-width:1440px;margin:auto;padding:40px 28px}small,.muted{color:#a9b9cf}h1{font-size:clamp(32px,5vw,52px);line-height:1.1;letter-spacing:-1.5px;margin:14px 0}h2{margin-top:40px;font-size:23px}p{max-width:980px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:28px 0}.card{padding:24px;border:1px solid #2f3f56;border-radius:16px;background:#142034}.card b{display:block;font-size:36px}.warning{border-left:4px solid #e9b458;padding:16px 20px;background:#27251f;color:#eddbb9}.bar{height:9px;background:#3e9fd8;border-radius:8px;margin-top:14px}.toolbar{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}button,input,select{color:inherit;background:#16243a;border:1px solid #52647b;border-radius:8px;padding:12px;font:inherit}button{cursor:pointer}button.active{background:#285a83}input{flex:1;min-width:200px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;min-width:750px}th,td{text-align:left;padding:14px 12px;border-bottom:1px solid #2a3a50;vertical-align:top}th{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#9eafc7}td:nth-child(2){white-space:nowrap}td small{display:block}.pill{display:inline-block;border:1px solid #52647b;border-radius:20px;padding:2px 9px;font-size:12px}.mono{font-variant-numeric:tabular-nums}footer{margin-top:32px;color:#a9b9cf}@media(max-width:650px){main{padding:25px 16px}.cards{grid-template-columns:1fr}.card b{font-size:30px}}</style></head><body><main>
<!-- uihub:dashboard/storage -->
<small>SISO ESTATE · MEASURED STORAGE</small><h1>What's on the laptop</h1><p>The home folder, plus installed applications and the system Library. Nothing is dismissed as “just cache”: re-fetch manifests need exact versions and sources; our own files stay byte-preserved.</p>
<div id="cards" class="cards"></div><div id="warning" class="warning"></div>
<h2>Where the space is going</h2><p>Repository worktrees account for <b>75.8 GB</b>. The pnpm package store accounts for <b>7.1 GB</b>, but it includes local package references: calling the entire store disposable would be wrong. WhatsApp app data accounts for <b>11.7 GB</b> and is treated as private data, not a re-downloadable cache.</p>
<p id="backup"></p><div class="toolbar"><button id="top" class="active">Largest 30 items</button><button id="all">Full depth-3 breakdown</button><input id="search" aria-label="Filter report" placeholder="Filter by tool, label or bucket ID"><select id="root" aria-label="Root"><option value="">All locations</option><option>Home</option><option>Applications</option><option>System Library</option></select></div>
<p class="muted">Directory rows include their children, so overlapping rows must not be added together. Private names are replaced with stable bucket IDs; the exact path mapping stays in the local Estate inventory.</p><p id="count" class="muted"></p><div class="scroll"><table><thead id="head"></thead><tbody id="body"></tbody></table></div><div class="toolbar"><button id="prev">Previous</button><button id="next">Next</button></div><footer id="footer"></footer></main>
<script>const data=__DATA__;const $=id=>document.getElementById(id);const el=(tag,text)=>{const n=document.createElement(tag);n.textContent=text??'';return n};const gb=n=>(n/1e9).toFixed(2)+' GB';let mode='top',page=0;const roots=new Map(data.rows.map(r=>[r.id,r.root]));
for(const [label,bytes] of Object.entries(data.totals)){const d=el('div');d.className='card';d.append(el('small',label),el('b',gb(bytes)),el('small','Observed allocated space'));$('cards').append(d)}
$('warning').textContent=`At least ${gb(data.totals.Home)} is allocated under home. The scan reported ${data.errors} inaccessible paths or other read errors; their missing bytes are not counted. Sparse logical file sizes can be much larger. This is a measured lower bound, not a complete backup claim.`;
$('backup').textContent=`Verified coverage: ${gb(data.verified_logical_bytes+data.refetch_logical_bytes)} of ${gb(data.coverage_denominator.eligible_observed_logical_bytes||0)} observed eligible logical bytes (${data.verified_files.toLocaleString()} files). Stored bytes: ${gb(data.verified_logical_bytes)}; verified re-fetch manifest: ${gb(data.refetch_logical_bytes)}. Restore samples: ${data.restored_samples} of 3 verified. This denominator excludes protected paths and unknown unreadable bytes, and includes sparse logical lengths; it is not physical disk use or a complete-home claim.`;
const speed=data.throughput;const rateText=speed.current_rate?speed.current_rate.gb_per_hour.toFixed(2)+' GB/hour':'measuring';const etaText=speed.eta_hours===null?'measuring':speed.eta_hours.toFixed(1)+' hours estimated remaining'+(speed.eta_uses_serial_baseline?' (serial baseline)':'');const rateBox=document.createElement('p');rateBox.textContent=`Verified rate: ${rateText} (${speed.mode}; serial baseline ${speed.serial_baseline?speed.serial_baseline.gb_per_hour.toFixed(2):'—'} GB/hour) · ${etaText} · ${gb(speed.remaining_allocated_bytes)} observed allocated bytes remaining · load ${speed.load_1m.toFixed(1)} / 30. Measured ${speed.measured_at}.`;document.getElementById('backup').after(rateBox);const rateNote=document.createElement('small');rateNote.textContent=speed.eta_basis;rateBox.after(rateNote);
const checkpoints=data.checkpoints;const checkpointBox=document.createElement('p');checkpointBox.textContent=`Recovery checkpoints: ${checkpoints.git_frozen_refs_verified} frozen Git refs verified across ${checkpoints.git_roots_with_frozen_refs} repository group(s). Metadata/map checkpoint: ${checkpoints.metadata_remote_verified?'encrypted and remotely verified':'pending remote verification'}. These partial checkpoints do not establish every Git root, WIP, filesystem attribute or denied path as preserved. Worker receipts: ${checkpoints.worker_states.running} running, ${checkpoints.worker_states.failed} failed, ${checkpoints.worker_states.completed} completed, ${checkpoints.worker_states.unknown} unknown.`;rateNote.after(checkpointBox);
function draw(){const q=$('search').value.toLowerCase(),root=$('root').value;const filtered=data[mode==='top'?'top':'rows'].filter(r=>(!root||r.root===root||roots.get(r.id)===root)&&JSON.stringify(r).toLowerCase().includes(q));const names=mode==='top'?['Item','Allocated','Producer / evidence','Preservation decision']:['Bucket / parent','Allocated','Location / depth','Label'];$('head').replaceChildren();let h=el('tr');names.forEach(n=>h.append(el('th',n)));$('head').append(h);$('body').replaceChildren();for(const r of filtered.slice(page*100,(page+1)*100)){const tr=el('tr');if(mode==='top'){const item=el('td',r.label);item.append(el('small',r.id));const evidence=el('td',r.producer);evidence.append(el('small',r.evidence));const decision=el('td',r.rebuildability==='refetch-proven'?'Re-fetch manifest':'Preserve bytes until proven otherwise');decision.append(el('small',r.rebuildability));tr.append(item,el('td',gb(r.bytes)),evidence,decision)}else{tr.append(el('td',r.id+(r.parent?' / '+r.parent:'')),el('td',gb(r.bytes)),el('td',r.root+' / depth '+r.depth),el('td',r.label))}$('body').append(tr)}$('count').textContent=`${filtered.length.toLocaleString()} matching rows · page ${page+1}`;$('prev').disabled=page===0;$('next').disabled=(page+1)*100>=filtered.length;$('top').className=mode==='top'?'active':'';$('all').className=mode==='all'?'active':''}
for(const m of ['top','all'])$(m).onclick=()=>{mode=m;page=0;draw()};$('search').oninput=$('root').onchange=()=>{page=0;draw()};$('prev').onclick=()=>{page--;draw()};$('next').onclick=()=>{page++;draw()};$('footer').textContent='Measured '+data.scan+' · decimal GB · No source files deleted or moved. HALO ownership boundaries remain protected.';draw();</script></body></html>'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run')
    args = parser.parse_args()
    print(build(args.run))
