#!/usr/bin/env python3
"""HARVEST-only safe rescue commits. Does not alter owner refs, index or files."""
import os,sys,json,re,hashlib,subprocess,datetime,stat,shutil
from pathlib import Path
plan=Path(sys.argv[1]); machine=sys.argv[2]; run=Path(sys.argv[3]); scratch=Path(sys.argv[4]); push='--push' in sys.argv
run.mkdir(parents=True,exist_ok=True)
env={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_AUTHOR_NAME':'SISO HARVEST','GIT_AUTHOR_EMAIL':'harvest@siso.local','GIT_COMMITTER_NAME':'SISO HARVEST','GIT_COMMITTER_EMAIL':'harvest@siso.local'}
def cmd(args,cwd=None,data=None,e=None,timeout=300):
    r=subprocess.run(args,cwd=cwd,input=data,capture_output=True,env=e or env,timeout=timeout)
    return r.returncode,r.stdout,r.stderr
def git(p,*a,**kw):return cmd(['git','-C',str(p),*a],**kw)
def gout(p,*a,**kw):
    c,o,e=git(p,*a,**kw)
    if c:raise RuntimeError('git '+a[0]+' failed '+str(c))
    return o.decode().strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
def excluded(name):
    parts=Path(name).parts; low=name.lower(); leaf=parts[-1].lower()
    if set(parts)&{'node_modules','target','.cargo-target','dist','build','vendor','.next','.turbo','.git','.wrangler','.serena','.cache','__pycache__','.venv','.oracle-builds','.playwright-cli','test-results','playwright-report','coverage'}:return 'generated/dependency/runtime'
    if set(parts)&{'sessions','transcripts','chats','chat-logs','.credentials','.secrets','private','personal','whatsapp','Life'}:return 'private/session data'
    if any(t in low for t in ('transcript','chat-log','session-log','storage-state','storage_state','cookie-jar','cookies.json','session.json','runtime-state')):return 'private/session data'
    if leaf.startswith('.env') and leaf not in ('.env.example','.env.sample','.env.template'):return 'credential file'
    if leaf in ('.npmrc','.netrc','auth.json','mcp.json','.mcp.json') or re.search(r'\.(pem|key|p12|pfx|keystore|db|sqlite|sqlite3|jsonl|out|log|err)$',leaf):return 'credentials/runtime/log'
    if re.search(r'\.(png|jpe?g|gif|webp|mp[34]|mov|mkv|wav|aac|dmg|zip|tar|gz|woff2?|ttf|pdf|ico|icns|bin|o|a|dylib|so)$',leaf):return 'binary/media; retained locally'
    return None
def private(data,name):
    if b'\0' in data:return 'binary; retained locally'
    try:s=data.decode('utf8')
    except UnicodeDecodeError:return 'non-text; retained locally'
    # Reports must not carry real account identifiers or private conversations.
    isreport=name.endswith(('.md','.json','.html','.txt')) and any(t in name.lower() for t in ('.agents/','evidence/','runs/','proof','report','research/'))
    if isreport:
        if re.search(r'https?://(?:www\.)?(?:chaturbate|camsoda|stripchat)\.com/(?![^\s]*\b(?:example|test|fixture|synthetic|demo)\b)[A-Za-z0-9_-]{3,}',s,re.I):return 'report has real-platform account URL'
        if re.search(r'\b[A-Za-z0-9._%+-]+@(?!example\.|siso\.local|localhost)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',s):return 'report has personal email; manual safe summary required'
        if re.search(r'(?i)(?:password|accessToken|refreshToken|apiKey|oracleLaunchToken|streamKey)\s*["\x27]?\s*[:=]\s*["\x27][^"\x27\n]{12,}["\x27]',s):return 'report has credential-like field; manual safe summary required'
    return None
def persist(rec):
    (run/(rec['lane']+'.json')).write_text(json.dumps(rec,indent=2)+'\n')
    print(json.dumps({k:rec.get(k) for k in ('lane','status','included','excluded_count','commit','remote_verified','error')}),flush=True)
remote_cache={}
for row in json.loads(plan.read_text()):
    p=Path(row['path']); lane=machine+'-'+re.sub('[^a-zA-Z0-9._-]+','-',p.name)[:130]
    if p.name in ('core','oracle','operator-app'):lane=machine+'-canonical-'+p.name
    rec={'lane':lane,'path':str(p),'observed':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'preparing','excluded':[]}
    try:
        # Raw status saved locally only; no working-tree/index writer is called.
        before=git(p,'status','--porcelain=v1','-z','--untracked-files=all')[1]
        head=gout(p,'rev-parse','HEAD'); rec['head']=head;rec['branch']=gout(p,'branch','--show-current')
        remote=gout(p,'remote','get-url','origin');rec['remote']=remote
        if not re.fullmatch(r'https://github.com/(?:HALO-AGENCY|sisodias)/[A-Za-z0-9_.-]+(?:\.git)?',remote):raise RuntimeError('unexpected remote; held')
        if '/halocrm' in str(p) or '/partners/halo/crm' in str(p):raise RuntimeError('Cam CRM boundary; held')
        idx=Path(gout(p,'rev-parse','--git-path','index'));idx=idx if idx.is_absolute() else p/idx;rec['index_before']=sha(idx)
        if remote not in remote_cache:
            c,o,e=git(p,'ls-remote','--heads','origin',timeout=120)
            if c:raise RuntimeError('GitHub readback unavailable')
            remote_cache[remote]={line.split()[1]:line.split()[0] for line in o.decode().splitlines()}
        refs=remote_cache[remote];rec['github_head_exact']=head in refs.values()
        localtips=[]
        for tip in set(refs.values()):
            if git(p,'cat-file','-e',tip+'^{commit}')[0]==0:localtips.append(tip)
        if not localtips:raise RuntimeError('No observed remote tip available locally')
        unpub=gout(p,'rev-list',head,'--not',*localtips).splitlines()
        rec['unpublished_commits']=len(unpub)
        # Capture staged-only content via an index tree if it differs from disk later.
        staged=git(p,'diff','--cached','--name-only','-z')[1].decode().split('\0');rec['staged_paths']=[s for s in staged if s]
        owner_index_copy=scratch/(lane+'.owner-index')
        if idx.is_file():shutil.copyfile(idx,owner_index_copy)
        ix=scratch/(lane+'.index');ie={**env,'GIT_INDEX_FILE':str(ix)}
        gout(p,'read-tree',head,e=ie)
        names=set(n for n in git(p,'diff',head,'--name-only','-z')[1].decode().split('\0') if n)
        names.update(n for n in git(p,'ls-files','--others','--exclude-standard','-z')[1].decode().split('\0') if n)
        payload=[]; captured=[]; changes=[]
        for name in sorted(names):
            f=p/name;reason=excluded(name)
            if reason:rec['excluded'].append({'path':name,'reason':reason});continue
            if f.is_symlink():rec['excluded'].append({'path':name,'reason':'symlink; preserved in original only'});continue
            if not f.exists():payload.append(('0 '+'0'*40+'\t'+name+'\0').encode());changes.append({'path':name,'action':'deleted'});continue
            if not f.is_file():rec['excluded'].append({'path':name,'reason':'directory or submodule; owned separately'});continue
            st=f.stat()
            if st.st_size>10_000_000:rec['excluded'].append({'path':name,'reason':'over 10 MB'});continue
            b=f.read_bytes();after=f.stat()
            if (st.st_mtime_ns,st.st_size)!=(after.st_mtime_ns,after.st_size):rec['excluded'].append({'path':name,'reason':'changed during read; retry needed'});continue
            reason=private(b,name)
            if reason:rec['excluded'].append({'path':name,'reason':reason});continue
            oid=gout(p,'hash-object','-w','--stdin',data=b);mode='100755' if st.st_mode&stat.S_IXUSR else '100644'
            payload.append((mode+' '+oid+'\t'+name+'\0').encode());captured.append((name,hashlib.sha256(b).hexdigest()));changes.append({'path':name,'action':'file','blob':oid,'sha256':captured[-1][1]})
        if payload:
            c,o,e=git(p,'update-index','-z','--index-info',data=b''.join(payload),e=ie)
            if c:raise RuntimeError('snapshot index update failed')
        rec['included']=len(changes);rec['changes']=changes;rec['excluded_count']=len(rec['excluded'])
        tree=gout(p,'write-tree',e=ie)
        if not changes and not unpub:
            rec['status']='already-on-github' if not rec['excluded'] else 'head-safe-exclusions-local';rec['commit']=head;rec['remote_verified']=True;rec['index_after']=sha(idx);persist(rec);continue
        msg=f'harvest: rescue {lane} on 2026-10-06\n\nPreservation only; not integration approval. Original HEAD: {head}.\nExcluded runtime, private and binary material is recorded in the local harvest ledger.\n\n[skip ci]'
        commit=gout(p,'commit-tree',tree,'-p',head,'-m',msg)
        # Scan every newly introduced commit, including unpushed parent history.
        report=run/(lane+'.gitleaks.json');log=run/(lane+'.scan.log')
        opts=commit+' --not '+' '.join(localtips)
        scan=cmd(['gitleaks','git',str(p),'--log-opts',opts,'--redact','--no-banner','--report-format','json','--report-path',str(report)],timeout=900)
        log.write_bytes(scan[1]+scan[2]);rec['secret_scan_exit']=scan[0]
        if scan[0]:
            rec['status']='held-secret-scan';rec['commit']=commit;rec['remote_verified']=False
            if report.is_file():
                findings=json.loads(report.read_text());rec['scan_findings']=[{'path':f.get('File'),'rule':f.get('RuleID'),'commit':f.get('Commit')} for f in findings]
            persist(rec);continue
        # Ensure new blobs do not carry oversized files inherited from unpublished commits.
        objs=gout(p,'rev-list','--objects',commit,'--not',*localtips).splitlines()
        check=git(p,'cat-file','--batch-check=%(objectname) %(objecttype) %(objectsize)',data=('\n'.join(x.split(' ')[0] for x in objs)+'\n').encode())[1].decode().splitlines()
        big=[x for x in check if len(x.split())==3 and x.split()[1]=='blob' and int(x.split()[2])>10_000_000]
        if big:rec['status']='held-oversized-history';rec['oversized_objects']=big;rec['commit']=commit;persist(rec);continue
        rec['source_moved']=gout(p,'rev-parse','HEAD')!=head
        rec['files_moved']=[n for n,h in captured if not (p/n).is_file() or sha(p/n)!=h]
        rec['index_after']=sha(idx);rec['owner_index_unchanged']=rec['index_before']==rec['index_after']
        if rec['source_moved'] or rec['files_moved'] or not rec['owner_index_unchanged']:
            rec['status']='held-source-changed';rec['commit']=commit;persist(rec);continue
        ref='refs/heads/harvest/2026-10-06/'+lane
        existing=git(p,'ls-remote','origin',ref)[1].decode().split()
        if existing and existing[0]!=commit:ref+='-'+commit[:8]
        rec['ref']=ref;rec['commit']=commit;rec['tree']=tree
        if push:
            c,o,e=git(p,'push','origin',commit+':'+ref,timeout=900)
            rec['push_exit']=c
            (run/(lane+'.push.log')).write_bytes(o+e)
            remotecheck=git(p,'ls-remote','origin',ref)[1].decode().split()
            rec['remote_verified']=bool(remotecheck and remotecheck[0]==commit)
            rec['status']='pushed' if c==0 and rec['remote_verified'] else 'push-failed'
        else:rec['status']='scanned-ready'
        persist(rec)
    except Exception as e:
        rec['status']='error';rec['error']=str(e);persist(rec)
