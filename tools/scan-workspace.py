#!/usr/bin/env python3
"""Read-only full-tree inventory of SISO_Workspace. Writes dirs.jsonl, gitroots.jsonl, pruned.jsonl, symlinks.jsonl."""
import os, json, sys, time, configparser, re
ROOT = "/Users/shaansisodia/SISO_Workspace"
OUT = os.environ.get("SCAN_OUT") or os.path.dirname(os.path.abspath(__file__))
PRUNE = {"node_modules",".venv","venv","__pycache__",".next",".nuxt",".turbo",".cache","target",
         ".pnpm-store",".gradle","Pods",".mypy_cache",".pytest_cache",".ruff_cache","site-packages",
         ".tox",".parcel-cache",".svelte-kit",".expo",".vercel",".wrangler","bower_components",
         ".yarn",".npm","DerivedData",".build",".dart_tool",".pub-cache","vendor_bundle"}
MARKERS = {"AGENTS.md","CLAUDE.md","README.md","package.json","pyproject.toml","Cargo.toml","go.mod",
           "DOMAIN-MANIFEST.json","SKILL.md","HANDOFF.md","CURRENT_STATE.md","requirements.txt",
           "docker-compose.yml","Dockerfile","wrangler.toml","vercel.json","module-manifest.json",
           "pnpm-workspace.yaml","turbo.json","setup.py","manage.py","index.html",".gitmodules"}
MARKER_DIRS = {".agents",".claude",".tasks",".codex",".worktrees","skills",".git","graphify-out","archive","_archive"}
def remote_of(gitpath):
    try:
        if os.path.isfile(gitpath):
            with open(gitpath) as f: return {"kind":"file","gitdir":f.read().strip()[:300]}
        cfg = os.path.join(gitpath,"config")
        remotes = {}
        cur=None
        with open(cfg, errors="replace") as f:
            for line in f:
                m = re.match(r'\s*\[remote "(.+)"\]', line)
                if m: cur=m.group(1); continue
                if line.strip().startswith("["): cur=None; continue
                if cur and line.strip().startswith("url"):
                    remotes[cur]=line.split("=",1)[1].strip()
        head=""
        try:
            with open(os.path.join(gitpath,"HEAD")) as f: head=f.read().strip()
        except Exception: pass
        return {"kind":"dir","remotes":remotes,"head":head[:120]}
    except Exception as e:
        return {"kind":"err","err":str(e)[:100]}
dirs=open(os.path.join(OUT,"dirs.jsonl"),"w"); gits=open(os.path.join(OUT,"gitroots.jsonl"),"w")
pr=open(os.path.join(OUT,"pruned.jsonl"),"w"); sl=open(os.path.join(OUT,"symlinks.jsonl"),"w")
t0=time.time(); n=0; maxd=0
stack=[(ROOT,0)]
while stack:
    path,depth=stack.pop()
    rel=os.path.relpath(path,ROOT)
    nf=0; nd=0; by=0; marks=[]; exts={}; newest=0
    try:
        it=list(os.scandir(path))
    except Exception as e:
        dirs.write(json.dumps({"p":rel,"d":depth,"err":str(e)[:80]})+"\n"); continue
    for e in it:
        try:
            if e.is_symlink():
                try: tgt=os.readlink(e.path)
                except Exception: tgt="?"
                sl.write(json.dumps({"p":os.path.relpath(e.path,ROOT),"t":tgt,"ok":os.path.exists(e.path)})+"\n")
                continue
            if e.is_dir(follow_symlinks=False):
                nd+=1
                if e.name==".git":
                    gits.write(json.dumps({"p":rel,"d":depth,**remote_of(e.path)})+"\n"); marks.append(".git"); continue
                if e.name in MARKER_DIRS: marks.append(e.name+"/")
                if e.name in PRUNE:
                    pr.write(json.dumps({"p":os.path.relpath(e.path,ROOT),"n":e.name})+"\n"); continue
                stack.append((e.path,depth+1))
            else:
                if e.name==".git":
                    gits.write(json.dumps({"p":rel,"d":depth,**remote_of(e.path)})+"\n"); marks.append(".git(file)"); continue
                nf+=1
                st=e.stat(follow_symlinks=False); by+=st.st_size
                if st.st_mtime>newest: newest=st.st_mtime
                if e.name in MARKERS: marks.append(e.name)
                x=os.path.splitext(e.name)[1].lower()[:8]
                exts[x]=exts.get(x,0)+1
        except Exception: pass
    top=sorted(exts.items(),key=lambda kv:-kv[1])[:4]
    dirs.write(json.dumps({"p":rel,"d":depth,"f":nf,"nd":nd,"b":by,"m":marks,"x":top,"t":int(newest)})+"\n")
    n+=1; maxd=max(maxd,depth)
    if n%50000==0: print(n,"dirs",round(time.time()-t0),"s",file=sys.stderr,flush=True)
print(json.dumps({"dirs":n,"maxdepth":maxd,"secs":round(time.time()-t0)}))
