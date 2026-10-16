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

    def test_recovery_leaves_hold_to_expire_and_dry_run_never_writes(self):
        with tempfile.TemporaryDirectory() as home, patch.object(guard, 'HOME', home), patch.object(guard, 'free_kib', return_value=70 * 1024**2), patch.object(guard.subprocess, 'run') as run:
            guard.check_floor(60, False)
            self.assertEqual(list(Path(home).iterdir()), [])
            hold = Path(home) / '.cache/siso-heavy/hold'
            hold.parent.mkdir(parents=True)
            hold.write_text('9999999999\tESTATE-MINI: disk low')
            run.return_value.returncode = 0
            guard.check_floor(60, True)
            run.assert_not_called()

if __name__ == '__main__':
    unittest.main()
