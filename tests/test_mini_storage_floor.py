import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('guard', Path(__file__).resolve().parents[1] / 'tools/storage-guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

class MiniFloor(unittest.TestCase):
    def test_used_warning_threshold_and_no_admission_side_effect(self):
        for kib, expected in ((117187499, False), (117187500, True)):
            df = 'Filesystem 1024-blocks Used Available Capacity Mounted\n/dev/test 239362496 %s 999999 90%% /\n' % kib
            with patch.object(guard, 'sh', return_value=df), patch.object(guard.subprocess, 'run') as run:
                state = guard.check_used(120, False)
                self.assertEqual(state['warning'], expected)
                self.assertEqual(state['warn_bytes'], 120_000_000_000)
                run.assert_not_called()

    def test_used_warning_records_current_status_and_recovers(self):
        with tempfile.TemporaryDirectory() as home, patch.object(guard, 'HOME', home):
            for kib, expected in ((190000000, True), (100000000, False)):
                df = 'Filesystem blocks Used Available Capacity Mounted\n/dev/test 239362496 %s 999999 90%% /\n' % kib
                with patch.object(guard, 'sh', return_value=df):
                    guard.check_used(120, True)
                state = json.loads((Path(home) / '.local/state/estate/storage-used.json').read_text())
                self.assertEqual(state['warning'], expected)

    def test_low_holds_new_admission_and_records_status(self):
        with tempfile.TemporaryDirectory() as home, patch.object(guard, 'HOME', home), patch.object(guard, 'free_kib', return_value=1024), patch.object(guard.subprocess, 'run') as run:
            run.return_value.returncode = 0
            state = guard.check_floor(60, True)
            self.assertTrue(state['low'])
            self.assertEqual(run.call_args.args[0][1:3], ['--hold', '10'])
            self.assertTrue(json.loads((Path(home) / '.local/state/estate/mini-storage-floor.json').read_text())['low'])

    def test_foreign_hold_survives_low_and_recovered_disk(self):
        with tempfile.TemporaryDirectory() as home, patch.object(guard, 'HOME', home), patch.object(guard.subprocess, 'run') as run:
            hold = Path(home) / '.cache/siso-heavy/hold'
            hold.parent.mkdir(parents=True)
            hold.write_text('9999999999\tOwner deploy freeze')
            for free in (1, 70 * 1024**2):
                with patch.object(guard, 'free_kib', return_value=free):
                    guard.check_floor(60, True)
            run.assert_not_called()
            self.assertEqual(hold.read_text(), '9999999999\tOwner deploy freeze')

    def test_recovery_releases_own_hold_and_dry_run_never_writes(self):
        with tempfile.TemporaryDirectory() as home, patch.object(guard, 'HOME', home), patch.object(guard, 'free_kib', return_value=70 * 1024**2), patch.object(guard.subprocess, 'run') as run:
            guard.check_floor(60, False)
            self.assertEqual(list(Path(home).iterdir()), [])
            hold = Path(home) / '.cache/siso-heavy/hold'
            hold.parent.mkdir(parents=True)
            hold.write_text('9999999999\tESTATE-MINI: disk low')
            run.return_value.returncode = 0
            guard.check_floor(60, True)
            # Space is back: the guard releases its own hold at once instead of making work wait for it to expire.
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][1:], ['--release'])

if __name__ == '__main__':
    unittest.main()
