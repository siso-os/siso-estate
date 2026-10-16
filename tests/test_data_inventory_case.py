import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("backup", Path(__file__).resolve().parents[1] / "tools/data-backup.py")
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)

class InventoryIdentity(unittest.TestCase):
    def inventory(self, names):
        def api(endpoint, **kwargs):
            if endpoint.startswith("orgs/"):
                return [{"full_name": name, "size": 2} for name in names]
            if endpoint.startswith("user/"):
                return {"state": "active", "role": "admin"}
            return []
        with patch.object(db, "api_json", side_effect=api), patch.object(db, "planes", return_value=[{"backend": "github:siso-data/example-01"}]):
            return db.github_inventory()

    def test_provider_casing_matches_declared_destination(self):
        result = self.inventory(["SISO-DATA/Example-01"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["repositories"][0]["repo"], "siso-data/example-01")
        self.assertEqual(result["total_stored_bytes"], 2048)

    def test_case_alias_cannot_double_count_repository(self):
        result = self.inventory(["SISO-DATA/Example-01", "siso-data/example-01"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["total_stored_bytes"])

    def test_wrong_organization_still_rejected(self):
        result = self.inventory(["sisodias/example-01"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["total_stored_bytes"])

if __name__ == "__main__":
    unittest.main()
