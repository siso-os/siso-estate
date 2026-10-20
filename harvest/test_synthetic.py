"""Offline preservation tests: fake remote/scanner, real temporary Git objects only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
GIT = shutil.which('git')

class Preservation(unittest.TestCase):
    def test_holds_and_exclusions(self):
        with tempfile.TemporaryDirectory(prefix='harvest-synthetic-') as tmp:
            t = Path(tmp); repo = t/'owner'; repo.mkdir()
            env = os.environ.copy()
            for name in subprocess.check_output([GIT, 'rev-parse', '--local-env-vars'], text=True).split():
                env.pop(name, None)
            def git(*args):
                return subprocess.check_output([GIT, '-C', str(repo), *args], env=env).strip()
            git('init', '-q'); git('config', 'user.email', 'fixture@example.com'); git('config', 'user.name', 'Fixture')
            (repo/'keep.py').write_text('original = True\n')
            git('add', 'keep.py'); git('commit', '-qm', 'fixture')
            git('remote', 'add', 'origin', 'https://github.com/sisodias/synthetic-fixture')
            head = git('rev-parse', 'HEAD').decode()
            (repo/'keep.py').write_text('staged = True\n'); git('add', 'keep.py')
            (repo/'keep.py').write_text('working = True\n')
            (repo/'.env').write_text('synthetic excluded credential fixture\n')
            (repo/'media.png').write_bytes(b'fixture binary\x00')
            fake = t/'bin'; fake.mkdir(); pushes = t/'pushes'
            (fake/'git').write_text('#!'+sys.executable+'\nimport os,sys\na=sys.argv[1:]\nif "ls-remote" in a: print('+repr(head+' refs/heads/main')+')\nelif "push" in a: open('+repr(str(pushes))+',"a").write("PUSH\\n"); sys.exit(99)\nelse: os.execv('+repr(GIT)+',['+repr(GIT)+']+a)\n')
            scanner = fake/'gitleaks'
            scanner.write_text('#!'+sys.executable+'\nimport os,sys\nif os.environ.get("MUTATE"): open('+repr(str(repo/'keep.py'))+',"a").write("changed = True\\n")\nsys.exit(int(os.environ.get("SCAN_EXIT","2")))\n')
            for p in fake.iterdir(): p.chmod(0o755)
            env['PATH'] = str(fake)+os.pathsep+env['PATH']
            def state():
                return (git('rev-parse', 'HEAD'), hashlib.sha256((repo/'.git/index').read_bytes()).hexdigest(), git('status', '--porcelain=v1'), (repo/'keep.py').read_bytes())
            def run(script, *args):
                return subprocess.run([sys.executable, str(HERE/script), *map(str,args)], env=env, capture_output=True, check=True)
            plan=t/'plan.json'; plan.write_text(json.dumps([{'path':str(repo)}]))
            scratch=t/'scratch';scratch.mkdir();out=t/'out'
            before=state()
            run('snapshot.py', plan, 'fixture', out, scratch, '--push')
            receipt=out/'fixture-owner.json';d=json.loads(receipt.read_text())
            self.assertEqual(d['status'], 'held-secret-scan');self.assertEqual(d['secret_scan_exit'],2)
            self.assertEqual(state(),before);self.assertFalse(pushes.exists())
            tree=git('ls-tree','--name-only',d['commit']).decode().splitlines()
            self.assertEqual(tree,['keep.py']);self.assertEqual(d['excluded_count'],2)
            # A forged ready label cannot bypass a failed scan receipt.
            d['status']='scanned-ready';receipt.write_text(json.dumps(d))
            run('push-rescues.py',out);self.assertFalse(pushes.exists());self.assertEqual(state(),before)
            requests=t/'requests.json';requests.write_text(json.dumps([{'lane':d['lane'],'paths':['keep.py']}]))
            run('sanitize-rescues.py',out,scratch,requests)
            self.assertEqual(json.loads(receipt.read_text())['status'],'held-secret-scan')
            self.assertEqual(state(),before);self.assertFalse(pushes.exists())
            artifact=t/'artifact.py';artifact.write_text('synthetic = True\n')
            config=t/'archive.json';config.write_text(json.dumps([{'name':'fixture','repo':str(repo),'parent':head,'files':[{'source':str(artifact),'target':'saved.py','report':False}]}]))
            archive=t/'archive';run('archive-artifacts.py',config,scratch,archive)
            self.assertEqual(json.loads((archive/'fixture.json').read_text())['status'],'error')
            self.assertEqual(state(),before);self.assertFalse(pushes.exists())
            # Successful scanner with --push omitted produces a candidate only.
            env['SCAN_EXIT']='0';run('snapshot.py',plan,'fixture',out,scratch)
            d=json.loads(receipt.read_text());self.assertEqual(d['status'],'scanned-ready')
            self.assertEqual(state(),before);self.assertFalse(pushes.exists())
            d['index_before']='0'*64;receipt.write_text(json.dumps(d))
            run('push-rescues.py',out);self.assertFalse(pushes.exists());self.assertEqual(state(),before)
            # A source changed during scanning must never be published.
            env['MUTATE']='1';run('snapshot.py',plan,'fixture',out,scratch,'--push')
            self.assertEqual(json.loads(receipt.read_text())['status'],'held-source-changed')
            self.assertFalse(pushes.exists());self.assertEqual(git('rev-parse','HEAD'),before[0])
            self.assertEqual(hashlib.sha256((repo/'.git/index').read_bytes()).hexdigest(),before[1])

if __name__ == '__main__': unittest.main()
