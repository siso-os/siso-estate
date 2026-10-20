"""Worktree deletion must be backed by verified remote content, even across reruns."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'

def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

wip = load('worktree-wip')
shed = load('worktree-shed')
retire = load('worktree-retire')

class Preservation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.main, self.lane, self.remote = [self.root / n for n in ('source', 'repo/lane', 'remote.git')]
        self.run_git('init', '--bare', str(self.remote))
        self.run_git('init', str(self.main))
        self.run_git('-C', str(self.main), 'config', 'user.name', 'Fixture')
        self.run_git('-C', str(self.main), 'config', 'user.email', 'fixture@example.invalid')
        (self.main / 'source.txt').write_text('base\n')
        (self.main / '.gitignore').write_text('node_modules/\nnotes/\n.env\n')
        self.run_git('-C', str(self.main), 'add', '.')
        self.run_git('-C', str(self.main), 'commit', '-qm', 'base')
        self.run_git('-C', str(self.main), 'remote', 'add', 'origin', str(self.remote))
        self.run_git('-C', str(self.main), 'push', '-q', 'origin', 'HEAD:main')
        self.run_git('-C', str(self.main), 'worktree', 'add', '-qb', 'fixture', str(self.lane))
        real_git = wip.git
        def git(p, *args, **kwargs):
            if args == ('remote', 'get-url', 'origin'):
                return subprocess.CompletedProcess([], 0, 'https://github.com/sisodias/fixture.git\n', '')
            return real_git(p, *args, **kwargs)
        for ctx in (patch.object(wip, 'git', side_effect=git),
                    patch.object(wip, 'open_paths', return_value=set()),
                    patch.object(wip, 'seat_cwds', return_value=[]),
                    patch.object(wip, 'HELD', str(self.root / 'held')),
                    patch.object(wip, 'REMOVED', str(self.root / 'state/removed.jsonl')),
                    patch.dict(os.environ, {'GIT_ALLOW_PROTOCOL': 'file'})):
            ctx.start()
            self.addCleanup(ctx.stop)

    def run_git(self, *args):
        r = subprocess.run(['git', *args], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[:200])
        return r.stdout.strip()

    def process(self, remove=True):
        return wip.process(str(self.lane), True, remove, set(), [])

    def test_preserves_local_commits_dirty_files_and_excluded_data(self):
        (self.lane / 'local.txt').write_text('local commit\n')
        self.run_git('-C', str(self.lane), 'add', 'local.txt')
        self.run_git('-C', str(self.lane), 'commit', '-qm', 'local only')
        local_head = self.run_git('-C', str(self.lane), 'rev-parse', 'HEAD')
        (self.lane / 'source.txt').write_text('dirty\n')
        (self.lane / 'new.txt').write_text('new\n')
        (self.lane / '.env').write_text('EXAMPLE=held\n')
        (self.lane / 'notes').mkdir()
        (self.lane / 'notes/proof.txt').write_text('unique evidence\n')
        result = self.process()
        self.assertEqual(result['result'], 'removed', result)
        self.assertFalse(self.lane.exists())
        refs = self.run_git('--git-dir', str(self.remote), 'for-each-ref', '--format=%(objectname)', 'refs/heads/estate/').splitlines()
        self.assertEqual(len(refs), 1)
        sha = refs[0]
        self.run_git('--git-dir', str(self.remote), 'merge-base', '--is-ancestor', local_head, sha)
        self.assertEqual(self.run_git('--git-dir', str(self.remote), 'show', sha + ':source.txt'), 'dirty')
        self.assertEqual(self.run_git('--git-dir', str(self.remote), 'show', sha + ':new.txt'), 'new')
        self.assertEqual(len(list((self.root / 'held').rglob('.env'))), 1)
        self.assertEqual(len(list((self.root / 'held').rglob('proof.txt'))), 1)
        tree = self.run_git('--git-dir', str(self.remote), 'ls-tree', '-r', '--name-only', sha)
        self.assertNotIn('.env', tree)

    def test_existing_wip_never_discards_newer_edits(self):
        (self.lane / 'source.txt').write_text('first\n')
        self.assertEqual(self.process(remove=False)['result'], 'committed')
        (self.lane / 'source.txt').write_text('second\n')
        self.assertEqual(self.process()['result'], 'removed')
        refs = self.run_git('--git-dir', str(self.remote), 'for-each-ref', '--format=%(objectname)', 'refs/heads/estate/').splitlines()
        contents = [self.run_git('--git-dir', str(self.remote), 'show', sha + ':source.txt') for sha in refs]
        self.assertEqual(set(contents), {'first', 'second'})

    def test_clean_remote_tree_needs_no_push_to_archived_repository(self):
        with patch.object(wip, 'push_wip', side_effect=AssertionError('no push needed')):
            result = self.process()
        self.assertEqual(result['result'], 'removed', result)
        self.assertEqual(result['unpushed'], 0)

    def test_primary_clone_with_outside_worktree_stays(self):
        result = wip.process(str(self.main), True, True, set(), [])
        self.assertIn('outside worktree', result['result'])
        self.assertTrue(self.main.exists())

    def test_primary_snapshot_preserves_dirty_work_without_removing_checkout(self):
        (self.main / 'source.txt').write_text('new primary work')
        result = wip.process(str(self.main), True, False, set(), [])
        self.assertEqual(result['result'], 'committed', result)
        self.assertTrue(self.main.exists())
        self.assertEqual(self.run_git('--git-dir', str(self.remote), 'show', result['sha'] + ':source.txt'), 'new primary work')

    def test_clean_standalone_clone_retires_only_without_unique_ignored_data(self):
        clone = self.root / 'clone'
        self.run_git('clone', '-q', '-b', 'main', str(self.remote), str(clone))
        (clone / '.env').write_text('fixture=unique')
        result = wip.process(str(clone), True, True, set(), [])
        self.assertIn('unique data', result['result'])
        self.assertTrue(clone.exists())
        (clone / '.env').unlink()
        result = wip.process(str(clone), True, True, set(), [])
        self.assertEqual(result['result'], 'removed', result)
        self.assertFalse(clone.exists())

    def test_primary_nested_repository_inside_ignored_dependencies_is_preserved(self):
        clone = self.root / 'clone'
        self.run_git('clone', '-q', '-b', 'main', str(self.remote), str(clone))
        nested = clone / 'node_modules/author'; nested.mkdir(parents=True)
        self.run_git('init', '-q', str(nested))
        (nested / 'unique.txt').write_text('unpublished work')
        result = wip.process(str(clone), True, True, set(), [])
        self.assertIn('nested repo', result['result'])
        self.assertTrue((nested / 'unique.txt').exists())

    def test_standing_release_lanes_cannot_be_retired_as_cold_clones(self):
        for lane in retire.KEEP_NAMES:
            self.assertTrue(wip.NO_REMOVE.search('/workspace/repo/' + lane), lane)

    def test_stale_tracking_tip_cannot_prove_remote_preservation(self):
        self.run_git('--git-dir', str(self.remote), 'update-ref', '-d', 'refs/heads/main')
        self.assertFalse(wip.already_remote(str(self.lane)))

    def test_single_branch_fetch_does_not_hide_a_freshly_verified_wip_tip(self):
        self.run_git('-C', str(self.main), 'config', 'remote.origin.fetch', '+refs/heads/main:refs/remotes/origin/main')
        (self.lane / 'source.txt').write_text('preserved new work')
        self.assertEqual(self.process(remove=False)['result'], 'committed')
        self.assertTrue(wip.already_remote(str(self.lane), clean=False))

    def test_unpushed_and_stashed_work_are_not_clean_remote_trees(self):
        (self.lane / 'source.txt').write_text('local only\n')
        self.assertFalse(wip.already_remote(str(self.lane)))
        self.run_git('-C', str(self.lane), 'add', 'source.txt')
        self.run_git('-C', str(self.lane), 'commit', '-qm', 'local only')
        self.assertFalse(wip.already_remote(str(self.lane)))

    def test_archived_repo_is_rearchived_even_when_second_push_fails(self):
        fail = subprocess.CompletedProcess([], 1, '', 'archived')
        def api(*args, **kwargs):
            return subprocess.CompletedProcess([], 0, 'true\n' if '--jq' in args else '', '')
        with patch.object(wip, 'git', return_value=fail), patch.object(wip, 'sh', side_effect=api) as calls:
            self.assertFalse(wip.push_wip('fixture', 'estate/wip-fixture', 'sisodias', 'fixture'))
        self.assertTrue(any('archived=true' in call.args for call in calls.call_args_list))

    def test_secret_scanner_error_keeps_tree(self):
        (self.lane / 'source.txt').write_text('dirty\n')
        with patch.object(wip, 'leaks', return_value=['*']):
            result = self.process()
        self.assertEqual(result['result'], 'gitleaks-unreadable')
        self.assertTrue(self.lane.exists())

    def test_generic_halo_lane_uses_shared_repository_boundary(self):
        original = wip.git
        def halo_git(p, *args, **kwargs):
            if args == ('remote', 'get-url', 'origin'):
                return subprocess.CompletedProcess([], 0, 'https://github.com/HALO-AGENCY/halo.git\n', '')
            if args == ('rev-parse', '--path-format=absolute', '--git-common-dir'):
                return subprocess.CompletedProcess([], 0, '/partners/halo/oracle/core/.git\n', '')
            return original(p, *args, **kwargs)
        (self.lane / 'source.txt').write_text('generic lane work\n')
        with patch.object(wip, 'git', side_effect=halo_git):
            result = self.process(remove=False)
        self.assertEqual(result['result'], 'committed', result)
        self.assertTrue(result['pushed'])
        self.assertTrue(self.lane.exists())

    def test_halo_store_cannot_publish_to_siso_remote(self):
        original = wip.git
        def wrong_owner(p, *args, **kwargs):
            if args == ('rev-parse', '--path-format=absolute', '--git-common-dir'):
                return subprocess.CompletedProcess([], 0, '/partners/halo/oracle/core/.git\n', '')
            return original(p, *args, **kwargs)
        with patch.object(wip, 'git', side_effect=wrong_owner):
            result = self.process()
        self.assertEqual(result['result'], 'kept: repository boundary mismatch')
        self.assertTrue(self.lane.exists())

    def test_unobserved_shared_repository_boundary_keeps_tree(self):
        original = wip.git
        def unavailable(p, *args, **kwargs):
            if args == ('rev-parse', '--path-format=absolute', '--git-common-dir'):
                return subprocess.CompletedProcess([], 1, '', 'unavailable')
            return original(p, *args, **kwargs)
        with patch.object(wip, 'git', side_effect=unavailable):
            result = self.process()
        self.assertEqual(result['result'], 'kept: cannot establish repository boundary')
        self.assertTrue(self.lane.exists())

    def sparse_lane(self):
        (self.lane / 'active').mkdir()
        (self.lane / 'outside').mkdir()
        (self.lane / 'active/keep.txt').write_text('selected\n')
        (self.lane / 'outside/source.txt').write_text('off cone\n')
        self.run_git('-C', str(self.lane), 'add', 'active', 'outside')
        self.run_git('-C', str(self.lane), 'commit', '-qm', 'sparse sources')
        self.run_git('-C', str(self.lane), 'sparse-checkout', 'init', '--cone')
        self.run_git('-C', str(self.lane), 'sparse-checkout', 'set', 'active')

    def test_sparse_absences_do_not_block_a_preserved_worktree(self):
        self.sparse_lane()
        (self.lane / 'source.txt').write_text('saved work\n')
        result = self.process()
        self.assertEqual(result['result'], 'removed', result)
        refs = self.run_git('--git-dir', str(self.remote), 'for-each-ref', '--format=%(objectname)', 'refs/heads/estate/').splitlines()
        self.assertEqual(len(refs), 1)
        self.assertEqual(self.run_git('--git-dir', str(self.remote), 'show', refs[0] + ':outside/source.txt'), 'off cone')
        self.assertEqual(self.run_git('--git-dir', str(self.remote), 'show', refs[0] + ':source.txt'), 'saved work')

    def test_recreated_sparse_file_after_snapshot_keeps_worktree(self):
        self.sparse_lane()
        original = wip.finish
        def race(*args, **kwargs):
            if not kwargs.get('quarantined'):
                (self.lane / 'outside').mkdir(exist_ok=True)
                (self.lane / 'outside/source.txt').write_text('new writer\n')
            return original(*args, **kwargs)
        with patch.object(wip, 'finish', side_effect=race):
            result = self.process()
        self.assertIn('changed after snapshot', result['result'])
        self.assertEqual((self.lane / 'outside/source.txt').read_text(), 'new writer\n')

    def unpopulated_submodule(self):
        head = self.run_git('-C', str(self.lane), 'rev-parse', 'HEAD')
        (self.lane / '.gitmodules').write_text('[submodule "vendor"]\n\tpath = vendor\n\turl = https://example.invalid/vendor.git\n')
        self.run_git('-C', str(self.lane), 'add', '.gitmodules')
        self.run_git('-C', str(self.lane), 'update-index', '--add', '--cacheinfo', '160000,' + head + ',vendor')
        self.run_git('-C', str(self.lane), 'commit', '-qm', 'unpopulated source pin')
        (self.lane / 'vendor').mkdir()
        # Model the Git refusal observed on the umbrella's retained module metadata.
        original = wip.git
        def pinned_move(p, *args, **kwargs):
            if args[:2] == ('worktree', 'move'):
                return subprocess.CompletedProcess([], 128, '', 'fatal: working trees containing submodules cannot be moved or removed')
            return original(p, *args, **kwargs)
        ctx = patch.object(wip, 'git', side_effect=pinned_move)
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_unpopulated_submodule_pin_can_retire_after_preservation(self):
        self.unpopulated_submodule()
        result = self.process()
        self.assertEqual(result['result'], 'removed', result)
        self.assertFalse(self.lane.exists())

    def test_nonempty_submodule_directory_stays_intact(self):
        self.unpopulated_submodule()
        (self.lane / 'vendor').mkdir(exist_ok=True)
        (self.lane / 'vendor/local.txt').write_text('held source\n')
        result = self.process()
        self.assertEqual(result['result'], 'kept: cannot quarantine linked worktree')
        self.assertEqual((self.lane / 'vendor/local.txt').read_text(), 'held source\n')

    def test_failed_submodule_metadata_repair_restores_original_path(self):
        self.unpopulated_submodule()
        original = wip.sh
        def fail_repair(*args, **kwargs):
            if 'repair' in args:
                return subprocess.CompletedProcess([], 1, '', 'repair unavailable')
            return original(*args, **kwargs)
        with patch.object(wip, 'sh', side_effect=fail_repair):
            result = self.process()
        self.assertEqual(result['result'], 'kept: cannot quarantine linked worktree')
        self.assertTrue(self.lane.exists())
        self.assertEqual(self.run_git('-C', str(self.lane), 'status', '--porcelain'), '')

    def test_locked_submodule_worktree_stays_intact(self):
        self.unpopulated_submodule()
        self.run_git('-C', str(self.main), 'worktree', 'lock', str(self.lane))
        result = self.process()
        self.assertEqual(result['result'], 'kept: cannot quarantine linked worktree')
        self.assertTrue(self.lane.exists())

    def test_nested_git_metadata_is_not_discarded_with_empty_pins(self):
        self.unpopulated_submodule()
        admin = Path(self.run_git('-C', str(self.lane), 'rev-parse', '--absolute-git-dir'))
        nested = admin / 'modules/vendor/local-refs'
        nested.parent.mkdir(parents=True)
        nested.write_text('unpublished metadata\n')
        result = self.process()
        self.assertEqual(result['result'], 'kept: cannot quarantine linked worktree')
        self.assertEqual(nested.read_text(), 'unpublished metadata\n')

    def test_newer_untracked_content_blocks_removal(self):
        (self.lane / 'new.txt').write_text('snapshot\n')
        original = wip.finish
        def race(*args, **kwargs):
            if not kwargs.get('quarantined'):
                (self.lane / 'new.txt').write_text('changed while pushing\n')
            return original(*args, **kwargs)
        with patch.object(wip, 'finish', side_effect=race):
            result = self.process()
        self.assertIn('changed after snapshot', result['result'])
        self.assertTrue(self.lane.exists())

    def test_writer_recreates_original_lane_after_quarantine_is_preserved(self):
        (self.lane / 'source.txt').write_text('saved change\n')
        original = wip.finish
        def writer(*args, **kwargs):
            if kwargs.get('quarantined'):
                self.lane.mkdir()
                (self.lane / 'newer.txt').write_text('new session\n')
            return original(*args, **kwargs)
        with patch.object(wip, 'finish', side_effect=writer):
            result = self.process()
        self.assertEqual(result['result'], 'removed')
        self.assertEqual((self.lane / 'newer.txt').read_text(), 'new session\n')

    def test_private_extensions_stay_out_even_under_docs_and_tests(self):
        for name in ('docs/.env', 'tests/credentials.json', 'docs/backup.sqlite', 'tests/fixture.test.jsonl', 'apps/os/life/notes/today.md', 'apps/whatsapp-link/chats/today.json'):
            self.assertTrue(wip.keep_out(name, 12))

    def test_scanner_nonzero_is_fail_closed(self):
        with patch.object(wip, 'sh', return_value=subprocess.CompletedProcess([], 2, '', '')):
            self.assertEqual(wip.leaks(str(self.lane), 'abc'), ['*'])

    def test_shed_never_deletes_tracked_descendants_of_ignored_directory(self):
        modules = self.lane / 'node_modules'
        modules.mkdir()
        (modules / 'authored.txt').write_text('must stay\n')
        self.run_git('-C', str(self.lane), 'add', '-f', 'node_modules/authored.txt')
        self.assertEqual(shed.ignored(str(self.lane), [str(modules)]), [])

    def test_shed_never_uses_parent_ignore_rules_inside_a_nested_repository(self):
        nested = self.lane / 'nested'; nested.mkdir()
        self.run_git('init', '-q', str(nested))
        modules = nested / 'node_modules'; modules.mkdir()
        (modules / 'authored.txt').write_text('nested authored data')
        self.assertNotIn(str(modules), shed.candidates(str(self.lane)))

    def test_ignored_generated_parent_with_nested_authored_repository_stays(self):
        modules = self.lane / 'node_modules'; modules.mkdir()
        nested = modules / 'authored'; nested.mkdir()
        self.run_git('init', '-q', str(nested))
        (nested / 'unique.txt').write_text('unpublished work')
        self.assertEqual(shed.ignored(str(self.lane), [str(modules)]), [])

    def test_registered_clone_shedding_excludes_archive_private_and_reference_roots(self):
        workspace = str(self.root)
        paths = ['SISO_Agents/repo', '_archive/frozen', 'personal/private', '_reference/upstream',
                 'SISO_Agency/apps/project/_archive/frozen']
        census = {'repos': [{'kind': 'repo', 'github': 'sisodias/fixture', 'path': str(self.root / p)} for p in paths]}
        self.assertEqual(shed.registered_clones(census, workspace), [str(self.root / paths[0])])

    def test_finder_marker_at_worktree_root_does_not_abort_retirement(self):
        (self.root / '.metadata_never_index').write_text('')
        with patch.object(retire, 'WT', str(self.root)), patch.object(retire, 'held', return_value=[]), patch.object(retire.sys, 'argv', ['worktree-retire.py']), patch('sys.stdout', new_callable=io.StringIO):
            retire.main()
        self.assertTrue(self.lane.exists())

    def test_relative_gitdir_resolves_against_worktree(self):
        marker = self.lane / '.git'
        actual = shed.gitdir(str(self.lane))
        marker.write_text('gitdir: ' + os.path.relpath(actual, self.lane) + '\n')
        self.assertEqual(shed.gitdir(str(self.lane)), actual)
        self.assertGreater(retire.touched(str(self.lane)), 0)

if __name__ == '__main__':
    unittest.main()
