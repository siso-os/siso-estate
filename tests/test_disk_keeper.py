import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
spec = importlib.util.spec_from_file_location('disk_keeper', TOOLS / 'disk-keeper.py')
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)
POLICY = json.loads(k.POLICY.read_text())


class DiskKeeper(unittest.TestCase):
    def test_policy_references_existing_estate_authorities(self):
        for name in POLICY['references']:
            path=(k.POLICY.parent/name).resolve()
            self.assertTrue(path.is_file(), str(path))
            self.assertIsInstance(json.loads(path.read_text()), dict)
        for rule in POLICY['rules']:
            self.assertTrue(rule['owner'])
            self.assertTrue(rule['restore'])

    def test_native_sessions_and_personal_win_over_runtime_and_dependencies(self):
        for path in ('.claude/projects/a/session.jsonl', '.codex/sessions/2026/a', '.codex/auth.json',
                     '.codex/config.toml', '.claude.json', 'Desktop/a/node_modules',
                     'SISO_Workspace/personal/a/node_modules', 'SISO_Workspace/fahmy/node_modules'):
            with self.subTest(path=path):
                rule = k.classify(path, POLICY)
                self.assertTrue(rule['protected'])
                self.assertEqual(k.select(rule, 99*k.GB, 0, time.time()), 'hold')

    def test_source_dependencies_and_unknown_are_distinct(self):
        for path, expected in [('SISO_Workspace/_data/worktrees/siso/baseline-abc','source'),
                               ('SISO_Workspace/_data/worktrees/siso/lane/node_modules','rebuildable'),
                               ('SISO_Workspace/Great_Library_of_SISO/banks/tool/node_modules','rebuildable'),
                               ('SISO_Workspace/_data/siso-ci-work/workers/mini-0/clones/sisodias/siso/node_modules','rebuildable'),
                               ('.local/state/codex-run','runtime'),
                               ('SISO_Workspace/_data/backup-repos','unique'),
                               ('SISO_Workspace/_data/brand-new-pile','unclassified')]:
            self.assertEqual(k.classify(path, POLICY)['class'], expected)

    def test_receipted_large_holds_are_classified_without_expiry(self):
        for path, expected in [('.local/share/agent-base/releases', 'runtime'),
                               ('Library/Python', 'runtime'), ('.hermes', 'runtime'),
                               ('SISO_Workspace/_data/ui-hub', 'unique'),
                               ('SISO_Workspace/_data/image-bank', 'unique'),
                               ('SISO_Workspace/_data/people-watch', 'unique'),
                               ('foundry-data', 'unique'),
                               ('SISO_Workspace/.git', 'source'),
                               ('SISO_Workspace/SISO_Agency', 'source'),
                               ('SISO_Workspace/Great_Library_of_SISO', 'unique')]:
            with self.subTest(path=path):
                rule=k.classify(path,POLICY)
                self.assertEqual(rule['class'],expected)
                self.assertEqual(k.select(rule,99*k.GB,0,time.time()),'hold')

    def test_private_apps_are_protected_even_when_they_contain_dependencies(self):
        for path in ('Library/Application Support/Google/node_modules', 'Library/Group Containers/WhatsApp',
                     'Library/Containers/app/cache', 'Library/Accounts', 'Library/WebKit', '.local/state/siso-code/userdata/statev2.sqlite',
                     '.omp/browser-profiles', 'SISO_Workspace/_data/siso-music'):
            self.assertTrue(k.classify(path,POLICY)['protected'],path)

    def test_growth_alarm_stats_database_without_opening_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();path=home/'.local/state/siso-code/userdata/statev2.sqlite'
            path.parent.mkdir(parents=True);path.touch()
            policy=dict(growth_watches=[dict(id='db',path=str(path.relative_to(home)),owner='siso-code',cap_gb=-1)])
            with patch.object(Path,'open',side_effect=AssertionError('must not read database')):
                alarms=k.growth_alarms(home,policy)
            self.assertEqual(alarms[0]['owner'],'siso-code')

    def test_scratch_requires_producer_marker_and_retains_failed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();path=home/'SISO_Workspace/_data/siso-ci-work/tmp/siso-ci-job-old'
            path.mkdir(parents=True);old=time.time()-200*3600
            sizes={str(path.relative_to(home)): k.GB}
            os.utime(path,(old,old))
            self.assertEqual(k.candidates(home,sizes,POLICY,{},set()),[])
            marker=path/'.siso-ci-generated';marker.touch();os.utime(marker,(old,old));os.utime(path,(old,old))
            self.assertEqual(k.candidates(home,sizes,POLICY,{},set())[0]['action'],'expire')
            failed=path/'.siso-ci-failed';failed.touch();os.utime(failed,(old,old));os.utime(path,(old,old))
            self.assertEqual(k.candidates(home,sizes,POLICY,{},set()),[])

    def test_ci_cleanup_rechecks_quarantine_and_counts_only_removed_units(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();root=home/'SISO_Workspace/_data/siso-ci-work'
            clone=root/'workers/mini-0/clones/sisodias/siso';clone.mkdir(parents=True)
            rel=str(clone.relative_to(home));calls=[]
            def prune(root, **kwargs):
                calls.append(kwargs)
                self.assertEqual(kwargs['days'],2)
                self.assertFalse(kwargs['pnpm'])
                if kwargs['dry_run']:
                    kwargs['size_fn'](clone)
                else:
                    self.assertTrue(kwargs['safe_fn'](str(clone)+'.estate-retiring-'+str(os.getpid())))
                    self.assertFalse(kwargs['safe_fn'](str(clone)+'-unselected'))
                return [str(clone)]
            retention=SimpleNamespace(GIB=1024**3,prune=prune,preserved_clone=lambda p: True,
                                      tree_bytes=lambda p: self.fail('reuse measured size'))
            preservation=SimpleNamespace(process=lambda *a: dict(result='would retire verified standalone clone'),seat_cwds=lambda:[])
            with patch.object(k,'module',side_effect=lambda name:retention if name=='ci-retention' else preservation):
                rows=k.ci_cleanup(home,POLICY,'mini',{rel:123},set(),False)
            self.assertEqual(len(calls),2)
            self.assertEqual(rows[0]['bytes'],123)

    def test_ci_retention_cannot_bypass_stash_or_outside_worktree_hold(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();clone=home/'SISO_Workspace/_data/siso-ci-work/workers/mini-0/clones/sisodias/siso'
            clone.mkdir(parents=True)
            def prune(root, **kwargs):
                self.assertFalse(kwargs['safe_fn'](clone))
                return []
            retention=SimpleNamespace(GIB=1024**3,prune=prune,preserved_clone=lambda p:True)
            preservation=SimpleNamespace(process=Mock(return_value=dict(result='kept: stash or outside worktree')),seat_cwds=lambda:[])
            with patch.object(k,'module',side_effect=lambda name:retention if name=='ci-retention' else preservation):
                self.assertEqual(k.ci_cleanup(home,POLICY,'mini',{},set(),False),[])
            self.assertEqual(preservation.process.call_args.args[1:3],(False,True))

    def test_glob_star_does_not_hide_unknown_subdirectory(self):
        self.assertFalse(k.match('a/b/c/d', 'a/*/d'))
        self.assertTrue(k.match('a/b/c/d', 'a/**/d'))
        self.assertTrue(k.match('a/b/c', 'a/*'))

    def test_preserved_worktree_gets_full_48_hours_after_push(self):
        rule = k.classify('SISO_Workspace/_data/worktrees/siso/lane', POLICY)
        now = 1000000
        self.assertEqual(k.select(rule, k.GB, 0, now), 'preserve')
        self.assertEqual(k.select(rule, k.GB, 0, now, now-48*3600+1), 'preserve')
        self.assertEqual(k.select(rule, k.GB, 0, now, now-48*3600), 'retire')
        self.assertEqual(k.select(rule, k.GB, now-1, now, 0), 'hold')
        self.assertEqual(k.select(rule, k.GB, None, now, 0), 'hold')

    def test_rebuildable_cap_requires_one_day_idle_and_ttl_expires_small_cache(self):
        rule = k.classify('.npm/_cacache', POLICY)
        now = 1000000
        self.assertEqual(k.select(rule, 4*k.GB, now-86400+1, now), 'hold')
        self.assertEqual(k.select(rule, 4*k.GB, now-86400, now), 'expire')
        self.assertEqual(k.select(rule, k.GB, now-168*3600, now), 'expire')
        self.assertEqual(k.select(rule, k.GB, now-24*3600, now), 'hold')

    def test_nested_inventory_does_not_double_count_dependencies(self):
        sizes = {'SISO_Workspace/_data/worktrees/siso/lane': 9*k.GB,
                 'SISO_Workspace/_data/worktrees/siso/lane/node_modules': 7*k.GB}
        totals = k.attributed(sizes, POLICY)
        self.assertEqual(totals['source'], 2*k.GB)
        self.assertEqual(totals['rebuildable'], 7*k.GB)
        self.assertEqual(sum(totals.values()), 9*k.GB)
        roots=[dict(path=p,bytes=v) for p,v in sizes.items()]
        freed=k.release_totals(roots,sizes,POLICY)
        self.assertEqual(freed['source'],2*k.GB)
        self.assertEqual(freed['rebuildable'],7*k.GB)
        self.assertEqual(sum(freed.values()),9*k.GB)

    def test_fixture_selection_observes_nested_write_and_does_not_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            cache = home/'.npm/_cacache'
            cache.mkdir(parents=True)
            item = cache/'item'
            item.write_text('rebuildable')
            old = time.time()-200*3600
            os.utime(item, (old,old)); os.utime(cache, (old,old))
            rows = k.candidates(home, {'.npm/_cacache': 2*k.GB}, POLICY, {}, set())
            self.assertEqual(rows[0]['action'], 'expire')
            item.write_text('recently used')
            self.assertEqual(k.candidates(home, {'.npm/_cacache': 2*k.GB}, POLICY, {}, set()), [])
            self.assertTrue(cache.exists())

    def test_fixture_discovery_keeps_protected_paths_and_ignores_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve()
            (home/'.codex/sessions').mkdir(parents=True)
            (home/'SISO_Workspace/_data/mystery').mkdir(parents=True)
            # Mock a symlink rather than creating one: production forbids new links.
            with patch.object(Path, 'is_symlink', side_effect=lambda: False):
                paths, errors=k.discover(home,POLICY)
            self.assertIn(home/'.codex/sessions', paths)
            self.assertIn(home/'SISO_Workspace/_data/mystery', paths)
            self.assertNotIn(home,paths)
            self.assertNotIn(home/'SISO_Workspace',paths)
            self.assertEqual(errors,[])

    def test_known_large_dependencies_are_found_even_when_git_budget_is_exhausted(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();lane=home/'SISO_Workspace/_data/worktrees/siso/main'
            dep=lane/'apps/siso-code/node_modules';dep.mkdir(parents=True);(lane/'.git').mkdir()
            policy=dict(POLICY,discovery_budget_seconds=0)
            with patch.object(k,'call',side_effect=AssertionError('Git inventory must not be needed')):
                paths,_=k.discover(home,policy)
            self.assertIn(dep,paths)

    def test_unclassified_unique_runtime_have_no_expiry(self):
        for path in ('mystery', 'SISO_Workspace/_data/clean-slate-2026', '.local/state/anything'):
            self.assertEqual(k.select(k.classify(path, POLICY), 99*k.GB, 0, time.time()), 'hold')

    def test_unknown_size_inventory_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve()
            path=home/'unknown'; path.mkdir()
            with patch.object(k,'call',side_effect=k.subprocess.TimeoutExpired('du',1)):
                sizes, errors=k.measure([path],home,POLICY)
            self.assertEqual(sizes,{})
            self.assertIn('timed out',errors[0])

    def test_budget_exhausted_after_n_measured_keeps_those_n_and_skips_unknown(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve()
            cached=['Library/Caches/Homebrew','Library/Caches/pip']
            unknown=['unknown-tree-1','unknown-tree-2']
            paths=[home/p for p in unknown+cached]
            for p in paths: p.mkdir(parents=True)
            ticks=iter([0.0,0.0,0.0])  # deadline, then the two cached paths; the clock is spent after that
            clock=lambda: next(ticks, 10000.0)
            with patch.object(k,'call',return_value=CompletedProcess([],0,'1 fixture\n')) as run, \
                    patch.object(k.time,'monotonic',side_effect=clock):
                sizes, errors = k.measure(paths,home,POLICY)
            self.assertEqual(set(sizes),set(cached))
            self.assertEqual(run.call_count,2)
            self.assertIn('measurement budget exhausted; 2 paths unknown',errors)

    def test_private_probes_are_short_and_follow_source_measurement(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();private=home/'Library/Group Containers';source=home/'SISO_Workspace/SISO_Agency'
            private.mkdir(parents=True);source.mkdir(parents=True)
            with patch.object(k,'call',return_value=CompletedProcess([],0,'1 fixture\n')) as run:
                _,errors=k.measure([private,source],home,POLICY)
            self.assertEqual(errors,[])
            self.assertEqual(run.call_args_list[0].args[0][-1],str(source))
            self.assertLessEqual(run.call_args_list[1].kwargs['timeout'],5)

    def test_stalled_du_costs_one_entry_and_mount_is_skipped(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve()
            stalled,ok,vault=home/'stalled',home/'ok',home/'vault'
            for p in (stalled,ok,vault): p.mkdir()
            def fake(cmd,timeout):
                if cmd[-1]==str(stalled): raise k.subprocess.TimeoutExpired(cmd,timeout)
                return CompletedProcess(cmd,0,'1 fixture\n')
            with patch.object(k,'call',side_effect=fake) as run, patch.object(k.os.path,'ismount',side_effect=lambda p: p==str(vault)):
                sizes,errors=k.measure([stalled,ok,vault],home,POLICY)
            self.assertEqual(set(sizes),{'ok'})
            self.assertIn('stalled: du unavailable or timed out',errors)
            self.assertIn('vault: mount skipped (external volume or vault)',errors)
            self.assertEqual(run.call_count,2)

    def test_git_ledger_has_one_line_per_machine_date_and_verified_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            remote = root / 'remote.git'
            k.git(root, 'init', '--bare', '-q', str(remote))
            directory = root / 'ledger'
            row = dict(date='2026-10-21', machine='laptop', bytes_by_class={'source':1}, free_gb=61)
            sha = k.ledger(row, directory, str(remote), 'laptop')
            self.assertEqual(k.git(directory, 'ls-remote', '--heads', 'origin', 'refs/heads/estate/disk-ledger-laptop').split()[0], sha)
            row['free_gb'] = 62
            k.ledger(row, directory, str(remote), 'laptop')
            rows = [json.loads(line) for line in (directory/'ledger.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['free_gb'], 62)
            self.assertEqual(k.git(directory, 'status', '--porcelain'), '')

    def test_busy_cache_is_not_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve()
            cache=home/'.npm/_cacache';cache.mkdir(parents=True)
            old=time.time()-200*3600;os.utime(cache,(old,old))
            rows=k.candidates(home,{'.npm/_cacache':k.GB},POLICY,{}, {str(cache/'active')})
            self.assertEqual(rows,[])

    def test_abandoned_package_temp_pack_expires_but_busy_and_recent_packs_stay(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();root=home/'.npm/_cacache/tmp';root.mkdir(parents=True)
            old=time.time()-72*3600;sizes={}
            for name in ('old-pack','busy-pack','recent-pack'):
                p=root/name;p.write_bytes(b'package fixture');sizes[str(p.relative_to(home))]=p.stat().st_size
                if name!='recent-pack':os.utime(p,(old,old))
            busy={str(root/'busy-pack')}
            rows=k.candidates(home,sizes,POLICY,{},busy)
            self.assertEqual([r['path'] for r in rows],['.npm/_cacache/tmp/old-pack'])
            wip=SimpleNamespace(open_paths=lambda:busy,seat_cwds=lambda:[],inside=lambda paths,p:p in paths)
            released,_=k.enforce(rows,home,POLICY,{},wip)
            self.assertEqual(released['rebuildable'],len(b'package fixture'))
            self.assertFalse((root/'old-pack').exists())
            self.assertTrue((root/'busy-pack').exists());self.assertTrue((root/'recent-pack').exists())

    def test_changed_worktree_is_held_before_preservation(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve()
            lane=home/'SISO_Workspace/_data/worktrees/siso/lane';lane.mkdir(parents=True)
            wip=Mock()
            row=dict(path=str(lane.relative_to(home)),action='retire',fingerprint='before',bytes=1)
            with patch.object(k,'fingerprint',return_value='after'):
                _,results=k.enforce([row],home,POLICY,{},wip)
            wip.process.assert_not_called()
            self.assertIn('changed since plan',results[0]['result'])

    def test_runtime_selection_is_only_closed_old_codex_run_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve()
            runs=home/'.local/state/codex-run/runs';runs.mkdir(parents=True)
            old=time.time()-721*3600
            for name in ('old.jsonl','busy.jsonl','auth.json','recent.jsonl'):
                file=runs/name;file.write_text('fixture')
                if name!='recent.jsonl':os.utime(file,(old,old))
            protected=home/'.codex/sessions/session.jsonl';protected.parent.mkdir(parents=True)
            protected.write_text('never archive');os.utime(protected,(old,old))
            rows=k.runtime_plan(home,{str(runs/'busy.jsonl')},time.time())
            self.assertEqual([Path(r['path']).name for r in rows],['old.jsonl'])
            self.assertTrue(protected.exists())

    def test_runtime_ttl_comes_from_the_classification_file(self):
        import copy
        policy=copy.deepcopy(POLICY)
        next(rule for rule in policy['rules'] if rule['id']=='codex-runs')['ttl_hours']=1
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp).resolve();runs=home/'.local/state/codex-run/runs';runs.mkdir(parents=True)
            file=runs/'old.jsonl';file.write_text('fixture')
            old=time.time()-2*3600;os.utime(file,(old,old))
            self.assertEqual(k.runtime_plan(home,set(),time.time()),[])
            self.assertEqual(k.runtime_plan(home,set(),time.time(),policy)[0]['ttl_hours'],1)


if __name__ == '__main__':
    unittest.main()
