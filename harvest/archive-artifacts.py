#!/usr/bin/env python3
"""Archive explicit approved task artifacts in their owning repository via objects."""
import os,sys,json,subprocess,hashlib,re,datetime,stat
from pathlib import Path
config=Path(sys.argv[1]);scratch=Path(sys.argv[2]);out=Path(sys.argv[3]);out.mkdir(parents=True,exist_ok=True)
env={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_AUTHOR_NAME':'SISO HARVEST','GIT_AUTHOR_EMAIL':'harvest@siso.local','GIT_COMMITTER_NAME':'SISO HARVEST','GIT_COMMITTER_EMAIL':'harvest@siso.local'}
def call(a,data=None,e=None):return subprocess.run(a,input=data,env=e or env,capture_output=True,timeout=900)
def git(p,*a,data=None,e=None):
 r=call(['git','-C',str(p),*a],data,e)
 if r.returncode:raise RuntimeError('git '+a[0]+' failed')
 return r.stdout
def safe_digest_finding(f,contents):
 name=f['File'];raw=contents.get(name)
 if not name.endswith('.json') or not raw:return False
 try:d=json.loads(raw);line=raw.decode().splitlines()[f['StartLine']-1]
 except Exception:return False
 candidates=re.findall(r':\s*"([0-9a-f]{40}|[0-9a-f]{64})"',line)
 if len(candidates)!=1:return False
 v=candidates[0];paths=[]
 def visit(x,path=''):
  if isinstance(x,dict):
   for k,y in x.items():
    if y==v:paths.append(path+'/'+k)
    elif isinstance(y,(dict,list)):visit(y,path+'/'+k)
  elif isinstance(x,list):
   for i,y in enumerate(x):visit(y,path+'/'+str(i))
 visit(d)
 return bool(paths) and all(re.search(r'(sha(?:256)?|digest|hash|sourceCommit|baseCommit|sourcePreparation|preservedTopLevelFunctions|runtimeFiles)',p,re.I) for p in paths)
for pack in json.loads(config.read_text()):
 name=pack['name'];p=Path(pack['repo']);parent=pack['parent'];ref='refs/heads/harvest/2026-10-06/'+name;ie={**env,'GIT_INDEX_FILE':str(scratch/(name+'.index'))}
 rec={'name':name,'repo':str(p),'parent':parent,'ref':ref,'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':[],'excluded':[]}
 try:
  git(p,'status','--porcelain=v1','-z','--untracked-files=no')
  idx=Path(git(p,'rev-parse','--git-path','index').decode().strip());idx=idx if idx.is_absolute() else p/idx;index_before=hashlib.sha256(idx.read_bytes()).hexdigest()
  git(p,'read-tree',parent,e=ie);contents={};records=[]
  for item in pack['files']:
   source=Path(item['source']);target=item['target']
   if source.is_symlink() or not source.is_file():rec['excluded'].append({'path':str(source),'reason':'not a regular file'});continue
   if source.stat().st_size>10_000_000:rec['excluded'].append({'path':str(source),'reason':'over 10 MB'});continue
   if re.search(r'(^|/)(?:sessions|transcripts|chats|\.credentials)(/|$)',str(source)) or source.suffix in ('.jsonl','.out','.log','.err'):rec['excluded'].append({'path':str(source),'reason':'prohibited log/private path'});continue
   data=source.read_bytes()
   if b'\0' in data:rec['excluded'].append({'path':str(source),'reason':'binary/media retained locally'});continue
   try:s=data.decode('utf8')
   except UnicodeDecodeError:rec['excluded'].append({'path':str(source),'reason':'non-text retained locally'});continue
   if item.get('report',True):
    if re.search(r'\b[A-Za-z0-9._%+-]+@(?!example\.|siso\.local|localhost)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',s) or re.search(r'https?://(?:www\.)?(?:chaturbate|camsoda|stripchat)\.com/(?![^\s]*\b(?:example|test|fixture|synthetic|demo)\b)[A-Za-z0-9_-]{3,}',s,re.I):rec['excluded'].append({'path':str(source),'reason':'private account locator; safe ledger summary only'});continue
   oid=git(p,'hash-object','-w','--stdin',data=data).decode().strip();mode='100755' if source.stat().st_mode&stat.S_IXUSR else '100644'
   records.append((mode+' '+oid+'\t'+target+'\0').encode());contents[target]=data;rec['files'].append({'source':str(source),'target':target,'sha256':hashlib.sha256(data).hexdigest(),'blob':oid,'bytes':len(data)})
  if records:git(p,'update-index','-z','--index-info',data=b''.join(records),e=ie)
  # First candidate is never pushed. Exclude any unvalidated scanner hit and recreate.
  for attempt in range(3):
   tree=git(p,'write-tree',e=ie).decode().strip();commit=git(p,'commit-tree',tree,'-p',parent,'-m',f'harvest: preserve {name} evidence on 2026-10-06\n\nTask artifacts and source preservation; not integration approval.\n\n[skip ci]').decode().strip()
   report=out/(name+f'.scan-{attempt}.json');args=['gitleaks','git',str(p),'--log-opts',parent+'..'+commit,'--redact','--no-banner','--report-format','json','--report-path',str(report)]
   scan=call(args);(out/(name+f'.scan-{attempt}.log')).write_bytes(scan.stdout+scan.stderr)
   if scan.returncode==0:break
   findings=json.loads(report.read_text()) if report.is_file() else []
   if not findings:raise RuntimeError('secret scanner did not return usable findings')
   safe=[f for f in findings if pack.get('allow_digest_findings') and safe_digest_finding(f,contents)]
   unsafe=[f for f in findings if f not in safe]
   if not unsafe:
    ignore=scratch/(name+'.gitleaksignore');ignore.write_text('\n'.join(f['Fingerprint'] for f in safe)+'\n')
    scan=call(args+['--gitleaks-ignore-path',str(ignore)]);rec['verified_digest_false_positives']=[{'file':f['File'],'line':f['StartLine'],'reason':'JSON source/function digest or Git commit identity'} for f in safe]
    if scan.returncode:raise RuntimeError('digest-filtered scan failed')
    break
   for target in sorted(set(f['File'] for f in unsafe)):
    # Restore the published parent entry rather than deleting previously preserved code.
    prior=git(p,'ls-tree','-z',parent,'--',target);record=('0 '+'0'*40+'\t'+target+'\0').encode()
    if prior:
     left,path=prior.rstrip(b'\0').split(b'\t',1);mode,typ,oid=left.split();record=mode+b' '+oid+b'\t'+path+b'\0'
    git(p,'update-index','-z','--index-info',data=record,e=ie)
    rec['excluded'].append({'path':target,'reason':'unvalidated secret-scan match; retained locally'})
    rec['files']=[f for f in rec['files'] if f['target']!=target];contents.pop(target,None)
  if scan.returncode:raise RuntimeError('secret scan remains held')
  rec['index_unchanged']=index_before==hashlib.sha256(idx.read_bytes()).hexdigest()
  if not rec['index_unchanged']:raise RuntimeError('owner index moved; preserve candidate but pause push')
  rec['commit']=commit;rec['secret_scan_exit']=scan.returncode
  existing=git(p,'ls-remote','origin',ref).decode().split()
  if existing and existing[0]!=commit:ref+='-'+commit[:8];rec['ref']=ref
  push=call(['git','-C',str(p),'push','origin',commit+':'+ref]);(out/(name+'.push.log')).write_bytes(push.stdout+push.stderr)
  check=git(p,'ls-remote','origin',ref).decode().split();rec['remote_verified']=bool(check and check[0]==commit);rec['status']='pushed' if push.returncode==0 and rec['remote_verified'] else 'push-failed'
 except Exception as e:rec['status']='error';rec['error']=str(e)
 (out/(name+'.json')).write_text(json.dumps(rec,indent=2)+'\n');print(json.dumps({'name':name,'status':rec['status'],'files':len(rec['files']),'excluded':len(rec['excluded']),'commit':rec.get('commit'),'error':rec.get('error')}),flush=True)
