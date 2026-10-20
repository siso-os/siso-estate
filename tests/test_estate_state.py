"""A live-tree record write must not modify code or its tracked snapshots."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
from estate_state import record_path


class EstateState(unittest.TestCase):
    def test_main_and_live_share_records_without_writing_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            roots = [home / f"SISO_Workspace/_data/worktrees/siso/{lane}/estate/core"
                     for lane in ("main", "live")]
            for root in roots:
                root.mkdir(parents=True)
                (root / "module.json").write_text(json.dumps({"id": "siso.estate.core"}))
            with patch("estate_state.Path.home", return_value=home):
                targets = [record_path(root, "machines", "laptop", "repos.json") for root in roots]
                self.assertEqual(targets[0], targets[1])
                Path(targets[0]).parent.mkdir(parents=True)
                Path(targets[0]).write_text("synthetic runtime record")
                self.assertIn("_data/estate/runtime", targets[0])
                self.assertTrue(all(not (root / "machines").exists() for root in roots))

    def test_standalone_and_fixture_roots_keep_their_existing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(record_path(tmp, "machines", "repos.json"),
                             str(Path(tmp) / "machines/repos.json"))
