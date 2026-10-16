"""Access boundaries are deployment contracts, not inferred backup proof."""
import json
from pathlib import Path
import re
import unittest

PLAN = Path(__file__).resolve().parents[1] / "plan" / "data-planes.json"


class PlaneDestinations(unittest.TestCase):
    def test_every_plane_has_isolated_storage_destination(self):
        planes = json.loads(PLAN.read_text())["planes"]
        self.assertEqual(len(planes), 39)
        self.assertEqual(len({p["name"] for p in planes}), 39)
        for plane in planes:
            with self.subTest(plane=plane["name"]):
                match = re.fullmatch(r"github:([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+)(?:#[^/\s]+)?", plane["backend"])
                self.assertIsNotNone(match)
                self.assertNotIn(match[1].lower(), {"sisodias", "halo-agency"})
                self.assertIn("access_boundary", plane)
                if plane["access_boundary"] == "mixed-unresolved":
                    self.assertTrue(plane.get("upload_hold"))

    def test_protected_scopes_cannot_silently_enter_siso_series(self):
        planes = {p["name"]: p for p in json.loads(PLAN.read_text())["planes"]}
        for name, plane in planes.items():
            if name.startswith("halo-") or name in {"storage-oracle-builds-20261003", "astra-review-captures-pending", "client-alj-ofm"}:
                self.assertEqual(plane["access_boundary"], "halo")
                self.assertIn("/halo-data-", plane["backend"])
        for name in ("agency-client-files", "vault-2026-09-24-estate", "workspace-loose", "credentials", "fleet-results"):
            self.assertEqual(planes[name]["access_boundary"], "mixed-unresolved")
            self.assertTrue(planes[name]["upload_hold"])
        self.assertTrue(planes["astra-review-captures-pending"]["upload_hold"])


if __name__ == "__main__":
    unittest.main()
