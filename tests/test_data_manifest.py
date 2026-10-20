"""Synthetic transport, integrity, extraction and storage admission regressions."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("data_backup_manifest_tests", TOOLS / "data-backup.py")
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)
dm = db.dm


class Manifests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "data.tar.zst.aaa").write_bytes(b"first")
        (self.source / "data.tar.zst.aab").write_bytes(b"second")
        self.location = "github:siso-data/siso-data-fixture-01"
        self.manifest = dm.build(self.source, ["data.tar.zst.aaa", "data.tar.zst.aab"], self.location, False)
        self.manifest["pins"] = {self.location: {"commit": "a" * 40}}

    def test_round_trip_parts_and_whole_are_both_verified(self):
        self.assertTrue(dm.verify_parts(self.manifest, self.source))
        bad = copy.deepcopy(self.manifest)
        bad["sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            dm.verify_parts(bad, self.source)
        (self.source / "data.tar.zst.aab").write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            dm.verify_parts(self.manifest, self.source)

    def test_corrupt_and_unavailable_locations_fall_back_in_order(self):
        locations = ["disk:HOLD:/missing", "disk:mini:/corrupt", "disk:HOLD:/valid"]
        self.manifest["locations"] = locations
        calls = []
        def fetch(location, manifest, target, sh, machine):
            calls.append(location)
            if location == locations[0]:
                raise OSError("unavailable")
            for part in manifest["parts"]:
                (Path(target) / part["name"]).write_bytes((self.source / part["name"]).read_bytes())
            if location == locations[1]:
                (Path(target) / manifest["parts"][0]["name"]).write_bytes(b"corrupt")
        destination = self.root / "archive"
        self.assertEqual(dm.verified_archive(self.manifest, destination, None, fetch=fetch), locations[-1])
        self.assertEqual(calls, locations)
        self.assertEqual(destination.read_bytes(), b"firstsecond")

    def test_all_failed_locations_never_publish_partial_or_overwrite_destination(self):
        destination = self.root / "archive"
        destination.write_bytes(b"previous verified archive")
        def fetch(location, manifest, target, sh, machine):
            (Path(target) / manifest["parts"][0]["name"]).write_bytes(b"first")
        with self.assertRaises(RuntimeError):
            dm.verified_archive(self.manifest, destination, None, fetch=fetch)
        self.assertEqual(destination.read_bytes(), b"previous verified archive")

    def test_pin_and_name_validation(self):
        for mutation in (lambda m: m["parts"][0].update(name="../outside"),
                         lambda m: m["parts"][0].update(size=0),
                         lambda m: m.update(pins={}),
                         lambda m: m.update(locations=["https://example.invalid/file"]),
                         lambda m: m["parts"].append(m["parts"][0])):
            bad = copy.deepcopy(self.manifest)
            mutation(bad)
            with self.assertRaises((ValueError, KeyError)):
                dm.validate(bad)
        with self.assertRaises(ValueError):
            dm.github("github:sisodias/siso-data-fixture", write=True)
        with self.assertRaises(ValueError):
            dm.github("github:HALO-AGENCY/halo-data-fixture", write=True)

    def test_release_requires_asset_ids_and_downloads_exact_identifiers(self):
        location = self.location + "#snapshot"
        self.manifest["locations"] = [location]
        self.manifest["pins"] = {location: {"release_id": 7, "assets": {p["name"]: i + 10 for i, p in enumerate(self.manifest["parts"])}}}
        dm.validate(self.manifest)
        def download(args, target, expected_size=None):
            asset_id = int(args[2].rsplit("/", 1)[1])
            Path(target).write_bytes((self.source / self.manifest["parts"][asset_id - 10]["name"]).read_bytes())
        with patch.object(dm, "binary_command", side_effect=download):
            dm.verified_archive(self.manifest, self.root / "release-archive", None)
        self.assertEqual((self.root / "release-archive").read_bytes(), b"firstsecond")

    def test_download_size_limit_kills_oversized_stream(self):
        with self.assertRaises(ValueError):
            dm.binary_command([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 10000)"], self.root / "oversized", 8)
        self.assertEqual((self.root / "oversized").stat().st_size, 0)

    def test_disk_machine_gate(self):
        location = "disk:mini:" + str(self.source)
        self.manifest["locations"] = [location]
        with self.assertRaises(RuntimeError):
            dm.verified_archive(self.manifest, self.root / "disk", None, disk_machine="HOLD")
        dm.verified_archive(self.manifest, self.root / "disk", None, disk_machine="mini")

    def archive(self, name="safe/file", type=None):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as archive:
            entry = tarfile.TarInfo(name)
            if type:
                entry.type = type
                entry.linkname = "../../outside"
                archive.addfile(entry)
            else:
                entry.size = 7
                archive.addfile(entry, io.BytesIO(b"payload"))
        path = self.root / "archive.zst"
        with path.open("wb") as output:
            subprocess.run(["zstd", "-q", "-c"], input=raw.getvalue(), stdout=output, check=True)
        return path

    def test_safe_extraction_and_existing_destination_guard(self):
        archive = self.archive()
        destination = self.root / "restore"
        dm.extract_archive(archive, destination)
        self.assertEqual((destination / "safe/file").read_bytes(), b"payload")
        with self.assertRaises(ValueError):
            dm.extract_archive(archive, destination)

    def test_invalid_tar_never_publishes_restore(self):
        compressed = self.root / "not-tar.zst"
        with compressed.open("wb") as output:
            subprocess.run(["zstd", "-q", "-c"], input=b"this is not a tar archive", stdout=output, check=True)
        with self.assertRaises(ValueError):
            dm.extract_archive(compressed, self.root / "restore")
        self.assertFalse((self.root / "restore").exists())

    def test_traversal_links_and_devices_leave_no_partial_restore(self):
        for name, kind in [("../outside", None), ("/absolute", None), ("link", tarfile.SYMTYPE),
                           ("hard", tarfile.LNKTYPE), ("device", tarfile.CHRTYPE)]:
            with self.subTest(name=name):
                destination = self.root / "restore"
                with self.assertRaises(ValueError):
                    dm.extract_archive(self.archive(name, kind), destination)
                self.assertFalse(destination.exists())
                self.assertFalse((self.root / "outside").exists())


class Admission(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(db, "WORK", str(self.root / "work")))
        self.enterContext(patch.object(db, "coordinator_admission", return_value=True))
        self.enterContext(patch.object(db.shutil, "disk_usage", return_value=type("Disk", (), {"free": 100 * 1024 ** 4})()))
        self.plane = {"name": "fixture", "backend": "github:siso-data/siso-data-fixture-01",
                      "access_boundary": "siso", "github_access_teams": ["siso-readers"]}
        self.inventory = {"complete": True, "observed_bytes": 0,
                          "repositories": [{"repo": "siso-data/siso-data-fixture-01", "git_objects_bytes": 0, "release_assets_bytes": 0}]}
        self.enterContext(patch.object(db, "github_inventory", side_effect=lambda: self.inventory))
        self.enterContext(patch.object(db, "api_json", side_effect=self.api))
        self.private, self.default_permission, self.actual_teams = True, "none", ["siso-readers"]

    def api(self, endpoint, paginate=False):
        if endpoint == "orgs/siso-data":
            return {"default_repository_permission": self.default_permission}
        if "/teams?" in endpoint:
            return [{"slug": team} for team in self.actual_teams]
        if "/collaborators?" in endpoint:
            return []
        return {"private": self.private, "permissions": {"push": True}}

    def test_capacity_reservation_is_counted_and_released(self):
        ok, token = db.admit_upload(self.plane, 3_000_000_000)
        self.assertTrue(ok)
        self.assertFalse(db.admit_upload(self.plane, 2_000_000_000)[0])
        db.release_reservation(token, retain_remote=True)
        # Attempted remote writes stay charged even across a REST refresh.
        # Only explicit reconciliation may release that retained upper bound.
        self.assertFalse(db.admit_upload(self.plane, 2_000_000_000)[0])
        Path(db._rest_cache_path()).unlink()
        self.assertFalse(db.admit_upload(self.plane, 2_000_000_000)[0])
        db.release_reservation(token)
        self.assertTrue(db.admit_upload(self.plane, 2_000_000_000)[0])

    def test_org_five_tb_and_incomplete_inventory_deny(self):
        self.inventory["observed_bytes"] = 4_999_999_000_000
        self.assertFalse(db.admit_upload(self.plane, 1_000_000)[0])
        self.inventory.update(observed_bytes=0, complete=False)
        self.assertFalse(db.admit_upload(self.plane, 1)[0])

    def test_coordinator_requires_configured_exact_machine_and_hostname(self):
        (self.root / "plan").mkdir()
        with patch.object(db, "REPO", str(self.root)), patch.object(db.socket, "gethostname", return_value="coordinator-host"):
            # Call the real function: the admission fixture otherwise mocks this check.
            original_spec = importlib.util.spec_from_file_location("coordinator_fixture", TOOLS / "data-backup.py")
            original = importlib.util.module_from_spec(original_spec)
            original_spec.loader.exec_module(original)
            with patch.object(original, "REPO", str(self.root)), patch.object(original.socket, "gethostname", return_value="coordinator-host"):
                self.assertFalse(original.coordinator_admission("laptop"))
                config = self.root / "plan/github-data-storage.json"
                config.write_text(json.dumps({"upload_coordinator": {"machine": "laptop", "hostname": "other-host"}}))
                self.assertFalse(original.coordinator_admission("laptop"))
                config.write_text(json.dumps({"upload_coordinator": {"machine": "laptop", "hostname": "coordinator-host"}}))
                self.assertFalse(original.coordinator_admission("mini"))
                self.assertTrue(original.coordinator_admission("laptop"))

    def test_unknown_coordinator_and_local_floor_deny_before_reservation(self):
        with patch.object(db, "coordinator_admission", return_value=False):
            self.assertFalse(db.admit_upload(self.plane, 1)[0])
        with patch.object(db.shutil, "disk_usage", return_value=type("Disk", (), {"free": 1})()):
            self.assertFalse(db.admit_upload(self.plane, 1)[0])

    def test_attempted_remote_bytes_remain_charged_for_inventory_lag(self):
        ok, token = db.admit_upload(self.plane, 3_000_000_000)
        self.assertTrue(ok)
        db.release_reservation(token, retain_remote=True)
        state = json.loads(Path(db.reservation_path()).read_text())
        self.assertEqual(state[token]["local_peak_bytes"], 0)
        self.assertFalse(db.admit_upload(self.plane, 2_000_000_000)[0])

    def test_run_scoped_streaming_floor_does_not_lower_full_stage_admission(self):
        disk = type("Disk", (), {"free": 20_000_000_000})()
        with patch.object(db.shutil, "disk_usage", return_value=disk):
            self.assertFalse(db.admit_upload(self.plane, 1000, local_peak_bytes=100_000_000)[0])
            ok, token = db.admit_upload(self.plane, 1000, local_peak_bytes=100_000_000, stream_profile=db.STREAM_PROFILE)
            self.assertTrue(ok)
            db.release_reservation(token)
            self.assertFalse(db.admit_upload(self.plane, 1000, local_peak_bytes=db.STREAM_CAP + 1, stream_profile=db.STREAM_PROFILE)[0])
            self.assertFalse(db.admit_upload(self.plane, 1000, local_peak_bytes=1, stream_profile="unknown")[0])

    def test_privacy_reader_and_hold_gates(self):
        self.private = False
        self.assertFalse(db.admit_upload(self.plane, 1)[0])
        self.private = True
        self.default_permission = "read"
        self.assertFalse(db.admit_upload(self.plane, 1)[0])
        self.default_permission = "none"
        self.actual_teams += ["wrong-boundary"]
        self.assertFalse(db.admit_upload(self.plane, 1)[0])
        self.plane["upload_hold"] = "mixed-access"
        self.assertEqual(db.run_plane(self.plane, "laptop")["status"], "held")


class Inventory(unittest.TestCase):
    def test_org_wide_repository_and_release_inventory_and_failure_coverage(self):
        replies = {"orgs/siso-data/repos?type=all&per_page=100": [{"full_name": "siso-data/undeclared", "size": 100}],
                   "repos/siso-data/undeclared/releases?per_page=100": [{"id": 7}],
                   "repos/siso-data/undeclared/releases/7/assets?per_page=100": [{"id": 1, "size": 1234}],
                   "user/memberships/orgs/siso-data": {"state": "active", "role": "admin"}}
        with patch.object(db, "planes", return_value=[]), patch.object(db, "api_json", side_effect=lambda endpoint, **kw: replies[endpoint]):
            result = db.github_inventory()
            self.assertTrue(result["complete"])
            self.assertEqual(result["total_stored_bytes"], 100 * 1024 + 1234)
            self.assertEqual(result["release_assets_bytes"], 1234)
        with patch.object(db, "planes", return_value=[{"backend": "github:siso-data/missing"}]), patch.object(db, "api_json", side_effect=lambda endpoint, **kw: replies[endpoint]):
            result = db.github_inventory()
            self.assertFalse(result["complete"])
            self.assertIsNone(result["total_stored_bytes"])
            self.assertEqual(result["missing"], ["siso-data/missing"])


if __name__ == "__main__":
    unittest.main()
