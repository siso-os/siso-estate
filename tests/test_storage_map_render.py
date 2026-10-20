"""Render an explicitly supplied synthetic tree without a machine scan."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("storage_map", TOOLS / "storage-map.py")
storage_map = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage_map)


class OfflineRender(unittest.TestCase):
    def test_explicit_tree_and_doc_never_probe_live_machine(self):
        with tempfile.TemporaryDirectory(prefix=".siso-ephemeral-storage-render-") as root:
            root = Path(root)
            tree, mapping, doc = root / "tree.json", root / "map.json", root / "report.md"
            tree.write_text(json.dumps({"t": 1000, "at": "synthetic", "volumes": [],
                                       "disk_gib": 100, "free_gib": 20, "tree": {"path": "/synthetic", "children": []},
                                       "dots": [], "worktrees": [], "hist": []}))
            mapping.write_text(json.dumps({"places": []}))
            with patch.object(storage_map, "MAP", str(mapping)), \
                    patch.object(storage_map, "TREE", str(root / "absent-live-tree")), \
                    patch.object(storage_map, "DOC", str(root / "absent-live-doc")), \
                    patch.object(storage_map, "history", return_value=[]), \
                    patch.object(storage_map, "walk", side_effect=AssertionError("no scan")), \
                    patch.object(storage_map, "volumes", side_effect=AssertionError("no probe")), \
                    patch.object(storage_map, "worktrees", side_effect=AssertionError("no inventory")), \
                    patch.object(sys, "argv", ["storage-map.py", "--render", "--tree", str(tree), "--doc", str(doc)]):
                storage_map.main()
            self.assertIn("20.0 free", doc.read_text())
            self.assertFalse((root / "absent-live-doc").exists())


if __name__ == "__main__":
    unittest.main()
