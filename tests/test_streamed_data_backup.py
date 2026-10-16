"""Real local Git + zstd/age transport with scaled bounded staging, no network."""
import copy
import hashlib
import io
import tarfile
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("streamed_backup_tests", TOOLS / "data-backup.py")
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)
real_admit_upload = db.admit_upload
real_release_reservation = db.release_reservation


class StreamedBackup(unittest.TestCase):
    def test_budget_tolerates_concurrently_removed_owned_directory(self):
        with __import__('tempfile').TemporaryDirectory() as temporary:
            stage = Path(temporary)
            guard = db.StreamingBudget(stage)
            with patch.object(db.os, 'walk', return_value=[(str(stage / 'already-removed'), [], [])]):
                self.assertEqual(guard.usage(), 0)

    def test_parallel_and_serial_stream_locks_exclude_each_other_but_parallel_lanes_coexist(self):
        with tempfile.TemporaryDirectory() as temporary:
            capacity = Path(temporary) / "capacity.json"
            with patch.object(db, "reservation_path", return_value=str(capacity)):
                with db.streaming_lock(2) as parallel_a:
                    self.assertTrue(parallel_a)
                    with db.streaming_lock(2) as parallel_b:
                        self.assertTrue(parallel_b)
                    with db.streaming_lock(1) as serial:
                        self.assertFalse(serial)
                with db.streaming_lock(1) as serial:
                    self.assertTrue(serial)
                    with db.streaming_lock(2) as parallel:
                        self.assertFalse(parallel)

    def test_two_disjoint_streams_complete_together_through_mocked_remote(self):
        first = self.source / "first"
        second = self.source / "second"
        first.write_bytes(b"first lane payload" * 1500)
        second.write_bytes(b"second lane payload" * 1500)
        rendezvous = threading.Barrier(2)
        real_archive_stream = db.archive_stream
        results, failures = {}, []

        def synchronized_archive(*args, **kwargs):
            rendezvous.wait(timeout=15)
            return real_archive_stream(*args, **kwargs)

        def run(name, path):
            try:
                results[name] = db.run_streamed(self.plane, "laptop", [str(path)], name, name,
                                                db.STREAM_PROFILE, max_streams=2)
            except Exception as error:
                failures.append(type(error).__name__)

        with patch.object(db, "archive_stream", side_effect=synchronized_archive), \
             patch.object(db.os, "getloadavg", return_value=(1.0, 1.0, 1.0)):
            workers = [threading.Thread(target=run, args=("parallel-a", first)),
                       threading.Thread(target=run, args=("parallel-b", second))]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=45)

        self.assertFalse(any(worker.is_alive() for worker in workers), "parallel stream workers did not finish")
        self.assertEqual(failures, [])
        self.assertEqual(set(results), {"parallel-a", "parallel-b"})
        self.assertTrue(all(result["status"] == "pushed" for result in results.values()), results)
        self.assertTrue(all(result["restore_verified"] for result in results.values()), results)
        self.assertEqual(self.refs().count("refs/tags/snapshots/laptop/parallel-a"), 1)
        self.assertEqual(self.refs().count("refs/tags/snapshots/laptop/parallel-b"), 1)
        self.assert_clean()

    def test_load_ceiling_is_returned_as_a_retryable_hold_reason(self):
        with patch.object(db.os, "getloadavg", return_value=(30.0, 0.0, 0.0)):
            result = self.run_stream()
        self.assertEqual(result["status"], "held", result)
        self.assertEqual(result["held_reason"], "streaming resource monitor failed: load ceiling")

    def test_parallel_lane_reservations_admit_two_and_enforce_aggregate_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capacity = root / "capacity.json"
            plane = {"backend": "github:siso-data/fixture", "access_boundary": "siso",
                     "github_access_teams": ["backup-readers"]}
            inventory = {"complete": True, "repositories": [{"repo": "siso-data/fixture",
                         "git_objects_bytes": 0, "release_assets_bytes": 0}],
                         "observed_bytes": 0}
            def api(endpoint, **kwargs):
                if endpoint.startswith("repos/") and "/teams?" not in endpoint and "/collaborators?" not in endpoint:
                    return {"private": True, "permissions": {"push": True}}
                if endpoint.startswith("orgs/"):
                    return {"default_repository_permission": "none"}
                if "/teams?" in endpoint:
                    return [{"slug": "backup-readers"}]
                return []
            peak = 100_000
            with patch.object(db, "reservation_path", return_value=str(capacity)), \
                 patch.object(db, "coordinator_admission", return_value=True), \
                 patch.object(db, "github_inventory", return_value=inventory), \
                 patch.object(db, "api_json", side_effect=api), \
                 patch.object(db.dm, "github", return_value=("siso-data", "fixture", None)), \
                 patch.object(db.shutil, "disk_usage", return_value=type("Disk", (), {"free": 20_000_000_000})()):
                admitted = [real_admit_upload(plane, 10_000, peak, "laptop", db.STREAM_PROFILE, 3)
                            for _ in range(2)]
                self.assertTrue(all(result[0] for result in admitted), admitted)
                self.assertTrue(capacity.exists(), admitted)
                state = json.loads(capacity.read_text())
                self.assertEqual(sum(row["local_peak_bytes"] for row in state.values()), peak * 2)
                self.assertFalse(real_admit_upload(plane, 10_000, 250_001, "laptop", db.STREAM_PROFILE, 3)[0])
                # Parallel mode bypasses the legacy full-run flock; each run still owns its reservation.
                with db.streaming_lock(3) as first, db.streaming_lock(3) as second:
                    self.assertTrue(first and second)
                for _, token in admitted:
                    real_release_reservation(token)

    def test_parallel_budget_counts_all_managed_lanes_and_holds_at_load_ceiling(self):
        with tempfile.TemporaryDirectory() as temporary:
            stage_root = Path(temporary) / "stage"
            first, second = stage_root / "run-a", stage_root / "run-b"
            first.mkdir(parents=True)
            second.mkdir()
            (first / "part").write_bytes(b"a" * 100)
            (second / "part").write_bytes(b"b" * 200)
            guard = db.StreamingBudget(first, stage_root)
            self.assertGreaterEqual(guard.usage(), 300)
            with patch.object(db.os, "getloadavg", return_value=(30.0, 0.0, 0.0)):
                with self.assertRaisesRegex(RuntimeError, "load ceiling"):
                    guard.check()

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.ws = self.root / "workspace"
        self.ws.mkdir()
        self.source = self.ws / "input"
        self.source.mkdir()
        self.payload = os.urandom(600_000)
        (self.source / "payload").write_bytes(self.payload)
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.plane = {"name": "fixture", "root": str(self.ws), "paths": ["input"], "encrypt": False,
                      "backend": "github:siso-data/siso-data-stream-fixture-01", "access_boundary": "siso"}
        self.enterContext(patch.object(db, "WS", str(self.ws)))
        self.enterContext(patch.object(db, "REPO", str(self.ws)))
        self.enterContext(patch.object(db, "WORK", str(self.ws / "repos")))
        self.enterContext(patch.object(db, "CHUNK", 32_000))
        self.enterContext(patch.object(db, "STREAM_CAP", 450_000))
        self.enterContext(patch.object(db, "STREAM_METADATA", 8_000))
        self.enterContext(patch.object(db, "STREAM_FLOOR", 15_000_000_000))
        self.enterContext(patch.object(db.shutil, "disk_usage", side_effect=lambda p: type("Disk", (), {"free": self.free})()))
        self.enterContext(patch.object(db, "admit_upload", return_value=(True, "fixture")))
        self.release = self.enterContext(patch.object(db, "release_reservation"))
        self.enterContext(patch.object(db, "secrets_in", return_value=[]))
        self.free = 20_000_000_000
        self.calls = []
        real_shell = db.StreamingBudget.shell
        def local_shell(guard, *args, **kwargs):
            self.calls.append(args)
            if args[:4] == ("git", "remote", "add", "origin"):
                args = (*args[:4], str(self.remote))
            if args[:2] == ("git", "commit"):
                args = ("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args[1:])
            return real_shell(guard, *args, **kwargs)
        self.enterContext(patch.object(db.StreamingBudget, "shell", local_shell))

    def run_stream(self):
        return db.run_plane(self.plane, "laptop", stream_profile=db.STREAM_PROFILE)

    def refs(self):
        return subprocess.check_output(["git", "-C", str(self.remote), "for-each-ref", "--format=%(refname)"], text=True).splitlines()

    def assert_clean(self):
        root = self.ws / "_data/estate-stream-stage"
        self.assertFalse(root.exists() and any(root.iterdir()))
        self.assertEqual((self.source / "payload").read_bytes(), self.payload)

    def test_multiple_chunks_larger_than_cap_never_accumulate_and_restore_hashes_pass(self):
        result = self.run_stream()
        self.assertEqual(result["status"], "pushed", result)
        self.assertGreater(result["manifest"]["size"], db.STREAM_CAP)
        self.assertGreater(len(result["manifest"]["parts"]), 10)
        self.assertLessEqual(result["staging_peak_bytes"], db.STREAM_CAP)
        self.assertEqual(result["stream_restore"], {"files": 1, "bytes": len(self.payload)})
        pin = next(iter(result["manifest"]["pins"].values()))
        self.assertEqual(len(set(pin["commits"].values())), result["chunks"])
        for part in result["manifest"]["parts"]:
            commit = pin["commits"][part["name"]]
            names = subprocess.check_output(["git", "-C", str(self.remote), "ls-tree", "--name-only", commit], text=True).splitlines()
            self.assertEqual(names, [part["name"]])
            parents = subprocess.check_output(["git", "-C", str(self.remote), "rev-list", "--parents", "-n", "1", commit], text=True).split()
            self.assertEqual(parents, [commit])
        self.assertTrue(any(ref.startswith("refs/tags/snapshots/") for ref in self.refs()))
        self.assert_clean()
        self.release.assert_called_once_with("fixture", retain_remote=True)

    def test_remote_corruption_never_completes_and_cleans_staging(self):
        real_download = db.dm.binary_command
        def corrupt(args, destination, *rest, **kwargs):
            real_download(args, destination, *rest, **kwargs)
            with open(destination, "r+b") as output:
                first = output.read(1)
                output.seek(0)
                output.write(bytes([first[0] ^ 1]))
        with patch.object(db.dm, "binary_command", side_effect=corrupt):
            result = self.run_stream()
        self.assertEqual(result["status"], "error", result)
        self.assertFalse(any(ref.startswith("refs/tags/snapshots/") for ref in self.refs()))
        self.assert_clean()

    def test_low_free_midstream_stops_without_completed_snapshot(self):
        real_fetch = db.dm.fetch_part
        def lower(*args, **kwargs):
            path = real_fetch(*args, **kwargs)
            self.free = db.STREAM_FLOOR - 1
            return path
        with patch.object(db.dm, "fetch_part", side_effect=lower):
            result = self.run_stream()
        self.assertEqual(result["status"], "error", result)
        self.assertFalse(any(ref.startswith("refs/tags/snapshots/") for ref in self.refs()))
        self.assert_clean()

    def test_encrypted_round_trip_remains_bounded(self):
        key = self.root / "identity"
        subprocess.run(["age-keygen", "-o", str(key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        recipient = subprocess.check_output(["age-keygen", "-y", str(key)], text=True).strip()
        self.plane["encrypt"] = True
        with patch.object(db, "KEY", str(key)), patch.object(db, "recipient", return_value=recipient):
            result = self.run_stream()
        self.assertEqual(result["status"], "pushed", result)
        self.assertTrue(result["manifest"]["encrypted"])
        self.assertEqual(result["stream_restore"]["bytes"], len(self.payload))
        self.assertLessEqual(result["staging_peak_bytes"], db.STREAM_CAP)
        self.assert_clean()

    def test_wrong_decryption_key_never_completes(self):
        self.payload = self.payload[:20_000]
        (self.source / "payload").write_bytes(self.payload)
        keys = [self.root / "sender-key", self.root / "wrong-key"]
        for key in keys:
            subprocess.run(["age-keygen", "-o", str(key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        recipient = subprocess.check_output(["age-keygen", "-y", str(keys[0])], text=True).strip()
        self.plane["encrypt"] = True
        receipts = []
        with patch.object(db, "KEY", str(keys[1])), patch.object(db, "recipient", return_value=recipient):
            with self.assertRaises(ValueError):
                db.run_streamed(self.plane, "laptop", [str(self.source / "payload")], "wrong-key", "fixture",
                                db.STREAM_PROFILE, on_verified_files=receipts.extend)
        self.assertEqual(receipts, [])
        self.assertFalse(any(ref.startswith("refs/tags/snapshots/") for ref in self.refs()))
        self.assert_clean()

    def test_oversized_sqlite_copy_holds_before_allocating_or_uploading(self):
        database = self.source / "state.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute("create table fixture(data blob)")
            connection.execute("insert into fixture values (?)", (os.urandom(400_000),))
        self.plane["sqlite_backup"] = True
        result = self.run_stream()
        self.assertEqual(result["status"], "held", result)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.refs(), [])
        self.release.assert_not_called()
        self.assert_clean()

    def test_small_sqlite_holds_until_a_descriptor_safe_snapshot_path_exists(self):
        self.payload = self.payload[:20_000]
        (self.source / "payload").write_bytes(self.payload)
        with sqlite3.connect(self.source / "state.sqlite") as connection:
            connection.execute("create table fixture(data text)")
            connection.execute("insert into fixture values ('synthetic')")
        self.plane["sqlite_backup"] = True
        result = self.run_stream()
        self.assertEqual(result["status"], "held", result)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.refs(), [])
        self.release.assert_not_called()
        self.assert_clean()

    def test_historical_staging_is_preserved_and_not_charged_as_new_allocation(self):
        other = self.ws / "_data/estate-stage/other-owner"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"z" * (db.STREAM_CAP + 1))
        self.payload = self.payload[:20_000]
        (self.source / "payload").write_bytes(self.payload)
        result = self.run_stream()
        self.assertEqual(result["status"], "pushed", result)
        self.assertEqual(other.stat().st_size, db.STREAM_CAP + 1)
        self.assertLessEqual(result["staging_peak_bytes"], db.STREAM_CAP)
        self.assert_clean()
        self.release.assert_called_once_with("fixture", retain_remote=True)

    def test_mixed_access_hold_ignores_streaming_exception(self):
        self.plane["upload_hold"] = "mixed access"
        self.assertEqual(self.run_stream()["status"], "held")
        self.assertEqual(self.calls, [])

    def test_explicit_file_outside_declared_roots_is_held(self):
        outside = self.ws / "private-chat-export.json"
        outside.write_text('{"fixture": true}')
        result = db.run_streamed(self.plane, "laptop", [str(outside)], "outside", "fixture", db.STREAM_PROFILE)
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.calls, [])

    def test_halo_path_is_held_even_when_encrypted_and_named_siso(self):
        halo = self.ws / "SISO_Agency/partners/halo/fixture.age"
        halo.parent.mkdir(parents=True)
        halo.write_bytes(b"already encrypted fixture")
        self.plane.update({"paths": ["input", "SISO_Agency/partners/halo"], "encrypt": True,
                           "access_boundary": "siso", "pre_encrypted": True})
        result = db.run_streamed(self.plane, "laptop", [str(halo)], "halo", "fixture", db.STREAM_PROFILE)
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.calls, [])

    def test_whole_home_stream_requires_explicit_root_and_exclude_lists(self):
        self.plane["whole_home"] = True
        self.plane.pop("exclude", None)
        result = db.run_streamed(self.plane, "laptop", [str(self.source / "payload")], "whole-home", "fixture", db.STREAM_PROFILE)
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.calls, [])

    def test_leaf_and_parent_symlink_swaps_cannot_open_protected_bytes(self):
        protected = self.ws / "outside-protected" / "payload"
        protected.parent.mkdir()
        protected.write_bytes(b"synthetic protected payload")
        source = self.source / "payload"
        saved_file = self.source / "payload.saved"
        saved_dir = self.ws / "input.saved"
        real_open = db.safe_source.open_source
        for attack in ("leaf", "parent"):
            with self.subTest(attack=attack):
                calls = {"open": 0, "returned": 0}
                def swap_then_open(root, relative, *args, **kwargs):
                    if relative == "input/payload":
                        calls["open"] += 1
                        if calls["open"] == 2:
                            if attack == "leaf":
                                source.rename(saved_file)
                                source.symlink_to(protected)
                            else:
                                self.source.rename(saved_dir)
                                self.source.symlink_to(protected.parent, target_is_directory=True)
                    result = real_open(root, relative, *args, **kwargs)
                    if relative == "input/payload" and calls["open"] == 2:
                        calls["returned"] += 1
                    return result
                try:
                    with patch.object(db.safe_source, "open_source", side_effect=swap_then_open):
                        result = self.run_stream()
                    self.assertEqual(result["status"], "error", result)
                    self.assertEqual(calls["returned"], 0)
                    self.assertEqual(protected.read_bytes(), b"synthetic protected payload")
                    self.assertFalse(any(ref.startswith("refs/tags/snapshots/") for ref in self.refs()))
                finally:
                    if source.is_symlink():
                        source.unlink()
                    if saved_file.exists():
                        saved_file.rename(source)
                    if self.source.is_symlink():
                        self.source.unlink()
                    if saved_dir.exists():
                        saved_dir.rename(self.source)
                self.assert_clean()

    def test_private_file_receipts_arrive_only_after_remote_restore_verifies(self):
        self.payload = self.payload[:20_000]
        source = self.source / "payload"
        source.write_bytes(self.payload)
        receipts = []
        result = db.run_streamed(self.plane, "laptop", [str(source)], "callback", "fixture", db.STREAM_PROFILE,
                                 on_verified_files=receipts.extend)
        self.assertEqual(result["status"], "pushed", result)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["path"], "input/payload")
        self.assertEqual(receipts[0]["size"], len(self.payload))
        self.assertEqual(receipts[0]["sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(receipts[0]["source_stat"]["file"]["size"], len(self.payload))
        self.assertNotIn(str(source), json.dumps(result))


class RemoteStreamValidation(unittest.TestCase):
    def _archive(self, path, payload, declared_sha=None):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tar:
            for name, data in payload.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            entries = [{"path": name, "size": len(data), "sha256": declared_sha if name == "payload" and declared_sha else hashlib.sha256(data).hexdigest()}
                       for name, data in payload.items()]
            manifest = json.dumps({"files": entries}).encode()
            info = tarfile.TarInfo("MANIFEST.json")
            info.size = len(manifest)
            tar.addfile(info, io.BytesIO(manifest))
        with path.open("wb") as destination:
            subprocess.run(["zstd", "-q", "-c"], input=raw.getvalue(), stdout=destination, check=True)

    def test_wrong_file_hash_and_unsafe_tar_are_rejected_without_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename, declared_sha in [("payload", "0" * 64), ("../outside", hashlib.sha256(b"fixture").hexdigest())]:
                raw = io.BytesIO()
                with tarfile.open(fileobj=raw, mode="w") as tar:
                    info = tarfile.TarInfo(filename)
                    info.size = 7
                    tar.addfile(info, io.BytesIO(b"fixture"))
                    expected = json.dumps({"files": [{"path": filename, "size": 7, "sha256": declared_sha}]}).encode()
                    info = tarfile.TarInfo("MANIFEST.json")
                    info.size = len(expected)
                    tar.addfile(info, io.BytesIO(expected))
                archive = root / "synthetic.zst"
                with archive.open("wb") as destination:
                    subprocess.run(["zstd", "-q", "-c"], input=raw.getvalue(), stdout=destination, check=True)
                validator = db.dm.StreamValidator()
                try:
                    with self.assertRaises((ValueError, OSError)):
                        validator.write_from(archive, hashlib.sha256())
                        validator.finish()
                finally:
                    validator.close()
                self.assertFalse((root / "payload").exists())
                self.assertFalse((root.parent / "outside").exists())

    def test_selected_sample_is_private_until_full_validation_then_matches_source_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "synthetic.zst"
            target = root / "restored-sample.bin"
            payload = {"payload": b"restored exact bytes", "other": b"also fully hashed"}
            self._archive(archive, payload)
            validator = db.dm.StreamValidator(sample_paths={"payload": target}, sample_max_bytes=64)
            try:
                validator.write_from(archive, hashlib.sha256())
                self.assertFalse(target.exists())
                result = validator.finish()
                self.assertEqual(target.read_bytes(), payload["payload"])
                self.assertEqual(result["samples"]["payload"]["sha256"], hashlib.sha256(payload["payload"]).hexdigest())
                self.assertEqual(result["samples"]["payload"]["size"], len(payload["payload"]))
            finally:
                validator.close()

    def test_failed_full_hash_or_sample_limit_leaves_no_sample_or_provisional_file(self):
        for declared_sha, maximum in [("0" * 64, 64), (None, 3)]:
            with self.subTest(maximum=maximum), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "synthetic.zst"
                target = root / "restored-sample.bin"
                self._archive(archive, {"payload": b"restored exact bytes"}, declared_sha)
                validator = db.dm.StreamValidator(sample_paths={"payload": target}, sample_max_bytes=maximum)
                try:
                    with self.assertRaises(ValueError):
                        validator.write_from(archive, hashlib.sha256())
                        validator.finish()
                finally:
                    validator.close()
                self.assertFalse(target.exists())
                self.assertEqual(list(root.glob(".estate-sample-*")), [])

    def test_sample_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "existing.bin"
            target.write_bytes(b"keep")
            with self.assertRaises(ValueError):
                db.dm.StreamValidator(sample_paths={"payload": target})
            self.assertEqual(target.read_bytes(), b"keep")

    def test_restore_rejects_stream_profile_instead_of_falling_through(self):
        with patch.object(db.sys, "argv", ["data-backup.py", "restore", "fixture", "/tmp/stream-restore",
                                             "--stream-profile", db.STREAM_PROFILE]):
            with self.assertRaises(SystemExit) as result:
                db.main()
        self.assertNotEqual(result.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
