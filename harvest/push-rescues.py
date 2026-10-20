#!/usr/bin/env python3
import sys,json,subprocess,datetime,os,hashlib
from pathlib import Path
run=Path(sys.argv[1]);env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'}
def git(p,*a):return subprocess.run(['git','-C',str(p),*a],capture_output=True,env=env,timeout=900)
for f in sorted(run.glob('*.json')):
 if 'gitleaks' in f.name:continue
 d=json.loads(f.read_text())
 if d.get('status')!='scanned-ready':continue
 if d.get('secret_scan_exit')!=0 or d.get('owner_index_unchanged') is not True:print(d['lane'],'HELD safety receipt incomplete',flush=True);continue
 if d['included']==0 and d['unpublished_commits']==0:
  d['status']='head-safe-exclusions-local';d['commit']=d['head'];d['remote_verified']=True;f.write_text(json.dumps(d,indent=2)+'\n');print(d['lane'],d['status'],flush=True);continue
 p=Path(d['path'])
 idx=Path(git(p,'rev-parse','--git-path','index').stdout.decode().strip());idx=idx if idx.is_absolute() else p/idx
 if (git(p,'rev-parse','HEAD').stdout.decode().strip()!=d['head'] or not idx.is_file() or hashlib.sha256(idx.read_bytes()).hexdigest()!=d.get('index_before') or d.get('source_moved') or d.get('files_moved')):
  print(d['lane'],'HELD source changed',flush=True);continue
 ref=d.get('ref','refs/heads/harvest/2026-10-06/'+d['lane']);commit=d['commit'];p=d['path']
 if not ref.startswith('refs/heads/harvest/2026-10-06/'):raise SystemExit('invalid push target')
 existing=git(p,'ls-remote','origin',ref).stdout.decode().split()
 if existing and existing[0]!=commit:ref+='-'+commit[:8]
 r=git(p,'push','origin',commit+':'+ref);(run/(d['lane']+'.push.log')).write_bytes(r.stdout+r.stderr)
 check=git(p,'ls-remote','origin',ref);fields=check.stdout.decode().split();verified=check.returncode==0 and fields and fields[0]==commit
 d.update(ref=ref,push_exit=r.returncode,remote_verified=bool(verified),status='pushed' if r.returncode==0 and verified else 'push-failed',pushed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
 f.write_text(json.dumps(d,indent=2)+'\n');print(json.dumps({k:d.get(k) for k in ['lane','status','commit','remote_verified','included']}),flush=True)
