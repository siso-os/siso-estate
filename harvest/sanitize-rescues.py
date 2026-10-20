#!/usr/bin/env python3
"""Remove held artifacts from task-owned rescue objects; never edits owner files."""
import sys,json,subprocess,os,hashlib,datetime
from pathlib import Path
run=Path(sys.argv[1]); scratch=Path(sys.argv[2]); requests=json.loads(Path(sys.argv[3]).read_text())
env={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_AUTHOR_NAME':'SISO HARVEST','GIT_AUTHOR_EMAIL':'harvest@siso.local','GIT_COMMITTER_NAME':'SISO HARVEST','GIT_COMMITTER_EMAIL':'harvest@siso.local'}
def call(a,data=None,e=None):return subprocess.run(a,input=data,env=e or env,capture_output=True)
def git(p,*a,data=None,e=None):
 r=call(['git','-C',str(p),*a],data,e)
 if r.returncode:raise RuntimeError('git '+a[0]+' failed')
 return r.stdout
for req in requests:
 f=run/(req['lane']+'.json');d=json.loads(f.read_text());p=Path(d['path']);old=d['commit'];head=d['head'];ie={**env,'GIT_INDEX_FILE':str(scratch/(req['lane']+'.index'))}
 try:
  git(p,'read-tree',old,e=ie)
  drop=set(req.get('paths',[]))
  if req.get('drop_findings'):drop.update(x['path'] for x in d.get('scan_findings',[]) if x['commit']==old)
  for prefix in req.get('prefixes',[]):drop.update(x['path'] for x in d['changes'] if x['path'].startswith(prefix))
  # Clear all requested copied paths; restore original tracked state at exact roots after.
  records=[]
  for name in sorted(drop):
   records.append(('0 '+'0'*40+'\t'+name+'\0').encode())
  if records:git(p,'update-index','-z','--index-info',data=b''.join(records),e=ie)
  restore=set(drop)|set(req.get('restore_roots',[]))
  records=[]
  for name in sorted(restore):
   out=git(p,'ls-tree','-z',head,'--',name)
   for entry in out.split(b'\0'):
    if not entry:continue
    left,path=entry.split(b'\t',1);mode,typ,oid=left.split()
    if typ==b'blob':records.append(mode+b' '+oid+b'\t'+path+b'\0')
  if records:git(p,'update-index','-z','--index-info',data=b''.join(records),e=ie)
  tree=git(p,'write-tree',e=ie).decode().strip()
  commit=old
  if drop:
   commit=git(p,'commit-tree',tree,'-p',head,'-m',f'harvest: filtered rescue {d["lane"]} on 2026-10-06\n\nPreservation only. Original HEAD: {head}. Raw sensitive artifacts and redundant dependency copies excluded.\n\n[skip ci]').decode().strip()
  refs=git(p,'ls-remote','--heads','origin').decode().splitlines();tips=[]
  for tip in set(x.split()[0] for x in refs):
   if call(['git','-C',str(p),'cat-file','-e',tip+'^{commit}']).returncode==0:tips.append(tip)
  ignore=scratch/(req['lane']+'.gitleaksignore')
  args=['gitleaks','git',str(p),'--log-opts',commit+' --not '+' '.join(tips),'--redact','--no-banner','--report-format','json','--report-path',str(run/(req['lane']+'.sanitized-gitleaks.json'))]
  if req.get('allow_verified_digest_findings'):
   raise RuntimeError('historical Oracle digest exception is unsupported here; retain held findings for owner validation')
  scan=call(args);(run/(req['lane']+'.sanitized-scan.log')).write_bytes(scan.stdout+scan.stderr)
  d['secret_scan_exit']=scan.returncode;d['prior_rescue_commit']=old;d['commit']=commit;d['tree']=tree;d['sanitized_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
  d['excluded'] += [{'path':x,'reason':req.get('reason','secret-scan hold; safe summary retained')} for x in sorted(drop)]
  d['changes']=[x for x in d['changes'] if x['path'] not in drop];d['included']=len(d['changes']);d['excluded_count']=len(d['excluded'])
  d['status']='scanned-ready' if scan.returncode==0 else 'held-secret-scan'
  d['ref']='refs/heads/harvest/2026-10-06/'+d['lane']
  index=Path(git(p,'rev-parse','--git-path','index').decode().strip());index=index if index.is_absolute() else p/index
  d['index_after']=hashlib.sha256(index.read_bytes()).hexdigest();d['owner_index_unchanged']=d['index_before']==d['index_after']
  f.write_text(json.dumps(d,indent=2)+'\n');print(d['lane'],d['status'],d['included'],d['excluded_count'],flush=True)
 except Exception as e:print(req['lane'],'ERROR',str(e),flush=True)
