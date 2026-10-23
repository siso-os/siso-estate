"""Optional deep sizing cannot prevent the live floor status from completing."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('bounded_disk_floor', TOOLS / 'disk-floor.py')
floor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(floor)


class FloorBudget(unittest.TestCase):
    def test_sizing_bounds_each_path_and_the_whole_attempt(self):
        with patch.object(floor, 'paths', return_value=['one', 'two']), \
             patch.object(floor.time, 'monotonic', side_effect=[0, 0, 31]), \
             patch.object(floor.subprocess, 'run', return_value=SimpleNamespace(stdout='1 one')) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                floor.size_all()
            self.assertEqual(run.call_count, 1)
            self.assertLessEqual(run.call_args.kwargs['timeout'], 5)

    def test_timeout_preserves_measurements_and_publishes_current_floor(self):
        with tempfile.TemporaryDirectory(prefix='siso-floor-budget-') as tmp:
            root = Path(tmp)
            sizes = root / 'sizes.json'
            original = json.dumps({'runs': [{'t': 1, 'free': 1, 'sizes': {'saved': 123}}]}).encode()
            sizes.write_bytes(original)
            patches = {'STATE': str(root), 'STATUS': str(root / 'status.json'), 'SIZES': str(sizes),
                       'MACHINE': 'mini', 'FLOOR_GIB': 60, 'ALERTS': str(root / 'alerts.log')}
            with patch.multiple(floor, **patches), patch.object(floor.time, 'time', return_value=30000), \
                 patch.object(floor.shutil, 'disk_usage', return_value=SimpleNamespace(free=33 * 2**30)), \
                 patch.object(floor, 'size_all', side_effect=subprocess.TimeoutExpired('du', 5)), \
                 patch.object(floor, 'idle_dev_servers', return_value=[]), patch.object(floor, 'swap_gib', return_value=0), \
                 patch.object(floor.storage_trend, 'load_lines', return_value=[]), \
                 patch.object(floor.storage_trend, 'load', return_value={}), \
                 patch.object(floor, 'owner_card'), patch.object(floor, 'card'):
                self.assertEqual(floor.main(), 0)
            self.assertEqual(sizes.read_bytes(), original)
            status = json.loads((root / 'status.json').read_text())
            self.assertEqual(status['sizing_status'], 'unavailable_timeout')
            self.assertEqual(status['sizes_observed_at'], 1)
            self.assertEqual(status['machine'], 'mini')
            self.assertFalse(status['ok'])  # The real low-space warning remains intact.
            self.assertEqual(status['floor_gib'], 60)


if __name__ == '__main__':
    unittest.main()
