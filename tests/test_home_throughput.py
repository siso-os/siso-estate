import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('home_throughput', Path(__file__).parents[1] / 'tools/home_throughput.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ThroughputTests(unittest.TestCase):
    def test_rate_uses_verified_bytes_and_includes_idle_wall_time(self):
        rows = [(0, 1_000_000_000), (1800, 3_000_000_000)]
        self.assertEqual(mod.rate(rows)['gb_per_hour'], 4)
        self.assertEqual(mod.rate(rows, now=3600)['gb_per_hour'], 2)

    def test_refetch_and_sparse_holes_are_not_upload_throughput(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'progress.log'
            row = {'at': '2026-10-17T01:00:00+07:00', 'coverage': [
                {'status': 'verified-bytes', 'bytes': 20},
                {'status': 'verified-refetch', 'bytes': 1000},
                {'status': 'verified-fragments', 'bytes': 10_000_000}]}
            path.write_text(json.dumps(row) + '\npartial-write')
            self.assertEqual(mod.observations(path)[0][1], 20)

    def test_no_rate_claim_without_two_observations(self):
        self.assertIsNone(mod.rate([(0, 100)]))
        self.assertIsNone(mod.rate([(0, 100), (10, 50)]))
