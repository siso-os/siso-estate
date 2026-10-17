import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('home_backup_quota', Path(__file__).parents[1] / 'tools/home_backup.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
backup = mod.backup


class RestQuotaTests(unittest.TestCase):
    def test_rate_limit_waits_for_reset_then_succeeds(self):
        calls = []

        def fake_sh(*cmd, **kw):
            calls.append(cmd)
            if cmd[1:3] == ('api', 'rate_limit'):
                return 0, json.dumps({'resources': {'core': {'reset': time.time() + 1}}}), ''
            return (1, '', 'gh: API rate limit exceeded (HTTP 403)') if len(calls) == 1 else (0, '{"ok": 1}', '')
        with patch.object(backup, 'sh', fake_sh), patch.object(backup.time, 'sleep') as sleep:
            code, out, _ = backup.gh_rest('api', 'repos/x/y')
        self.assertEqual((code, out), (0, '{"ok": 1}'))
        sleep.assert_called_once()
        self.assertGreaterEqual(sleep.call_args[0][0], 30)

    def test_rmtree_retry_survives_enotempty(self):
        real = backup.shutil.rmtree
        calls = []

        def flaky(path, *a, **k):
            calls.append(path)
            if len(calls) < 3:
                raise OSError(66, 'Directory not empty')
            return real(path, *a, **k)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'part'
            (target / '.git').mkdir(parents=True)
            with patch.object(backup.shutil, 'rmtree', flaky), patch.object(backup.time, 'sleep'):
                backup.rmtree_retry(target)
            self.assertFalse(target.exists())
        self.assertEqual(len(calls), 3)

    def test_new_shard_missing_from_cached_inventory_is_refreshed(self):
        stale = {'complete': True, 'repositories': []}
        fresh = {'complete': True, 'repositories': [{'repo': 'o/new', 'git_objects_bytes': 0, 'release_assets_bytes': 0}]}
        self.assertIsNone(backup.stored_bytes(stale, 'o', 'new'))
        with patch.object(backup, 'github_inventory', return_value=fresh), \
                patch.object(backup, '_load_rest_cache', return_value={}), patch.object(backup, '_save_rest_cache') as save:
            value = backup.refresh_inventory()
        self.assertEqual(backup.stored_bytes(value, 'o', 'new'), 0)
        self.assertEqual(save.call_args[0][0]['inventory']['value'], fresh)

    def test_plain_failure_is_bounded(self):
        with patch.object(backup, 'sh', lambda *a, **k: (1, '', 'HTTP 404')) as _, patch.object(backup.time, 'sleep'):
            self.assertEqual(backup.gh_rest('api', 'repos/x/y', tries=2)[0], 1)

    def test_ensure_plane_caches_shard_and_survives_already_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            seen = []

            def fake_rest(*args, **kw):
                seen.append(args)
                if args[1].startswith('repos/') and seen.count(args) == 1 and args[0] == 'api' and len(args) == 2 and sum(1 for a in seen if a == args) == 1:
                    return 1, '', 'HTTP 404'
                if args[1] == 'orgs/siso-data/repos':
                    return 1, '', 'HTTP 422 name already exists'
                if args[1].startswith('repos/'):
                    return 0, json.dumps({'private': True, 'full_name': 'siso-data/siso-data-home-laptop-004'}), ''
                return 0, '', ''
            plane = {'name': 'home-laptop-004', 'backend': 'github:siso-data/siso-data-home-laptop-004'}
            with patch.object(mod, 'make_plane', lambda r, s: plane), patch.object(backup, 'gh_rest', fake_rest), \
                    patch.object(backup, 'REPO', tmp):
                (Path(tmp) / 'plan').mkdir()
                (Path(tmp) / 'plan/data-planes.json').write_text(json.dumps({'planes': []}))
                mod.ensure_plane(run, 4)
                before = len(seen)
                mod.ensure_plane(run, 4)
            self.assertEqual(len(seen), before)


if __name__ == '__main__':
    unittest.main()
