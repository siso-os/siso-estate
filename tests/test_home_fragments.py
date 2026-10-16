"""Synthetic sparse-file coverage for whole-home fragment backups."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("home_fragments", TOOLS / "home_fragments.py")
hf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hf)


class Guard:
    def __init__(self):
        self.processes = []

    def register(self, process):
        self.processes.append(process)
        return process

    def check(self):
        pass


class HomeFragments(unittest.TestCase):
    def test_sparse_round_trip_preserves_logical_hash_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sparse.raw"
            with source.open("wb") as output:
                output.truncate(64 * 1024 * 1024)
                output.seek(4096)
                output.write(b"first extent" * 300)
                output.seek(40 * 1024 * 1024 + 8192)
                output.write(b"second extent" * 200)
            os.chmod(source, 0o640)
            os.utime(source, ns=(1_700_000_000_123_456_789, 1_700_000_000_123_456_789))
            descriptor = hf.plan_file(source, fragment_bytes=128 * 1024)
            self.assertEqual(descriptor["logical_size"], 64 * 1024 * 1024)
            self.assertGreaterEqual(len(descriptor["fragments"]), 2)
            self.assertTrue(all(item["size"] <= 256 * 1024 * 1024 for item in descriptor["fragments"]))
            self.assertLess(sum(item["size"] for item in descriptor["fragments"]), descriptor["logical_size"])
            self.assertLess(hf.archive_upper_bytes(descriptor), descriptor["logical_size"] // 4)

            expected_plan = dict(descriptor, fragments=list(descriptor["fragments"]))
            paths, hashes = {}, {}
            with source.open("rb") as data:
                for item in descriptor["fragments"]:
                    data.seek(item["offset"])
                    payload = data.read(item["size"])
                    fragment_path = root / (Path(item["path"]).name)
                    fragment_path.write_bytes(payload)
                    paths[item["path"]] = fragment_path
                    hashes[item["path"]] = hashlib.sha256(payload).hexdigest()
            descriptor["fragments"] = [dict(item, sha256=hashes[item["path"]]) for item in descriptor["fragments"]]
            groups = hf.fragment_groups(descriptor, max_group_bytes=256 * 1024)
            descriptor = hf.merge_descriptors(groups, expected_plan)
            restored = hf.reconstruct_file(descriptor, paths, root / "restored.raw")
            self.assertEqual(restored.stat().st_size, source.stat().st_size)
            self.assertEqual(stat_mode(restored), 0o640)
            self.assertEqual(restored.stat().st_mtime_ns, source.stat().st_mtime_ns)
            self.assertEqual(file_hash(restored), file_hash(source))
            self.assertLess(restored.stat().st_blocks * 512, restored.stat().st_size // 4)

    def test_large_ordinary_extent_is_split_without_staging_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "large.bin"
            with source.open("wb") as output:
                output.truncate(5 * 1024 * 1024 * 1024 + 3)
            with patch.object(hf, "_data_extents", return_value=[(0, source.stat().st_size)]):
                descriptor = hf.plan_file(source, fragment_bytes=200 * 1024 * 1024)
            fragments = descriptor["fragments"]
            self.assertGreater(len(fragments), 20)
            self.assertEqual(sum(item["size"] for item in fragments), source.stat().st_size)
            self.assertTrue(all(item["size"] <= 200 * 1024 * 1024 for item in fragments))
            self.assertFalse(any(Path(temporary).glob("*.part")))

    def test_planner_falls_back_to_full_span_when_sparse_seek_is_unsupported(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "fallback.bin"
            source.write_bytes(b"small fixture")
            error = OSError(22, "operation unsupported")
            with patch.object(hf.os, "lseek", side_effect=error):
                descriptor = hf.plan_file(source, fragment_bytes=4)
            self.assertEqual([(item["offset"], item["size"]) for item in descriptor["fragments"]],
                             [(0, 4), (4, 4), (8, 4), (12, 1)])

    def test_source_open_rejects_symlinked_parent_and_leaf(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            source = real / "payload.bin"
            source.write_bytes(b"private")
            parent_link = root / "linked-parent"
            leaf_link = root / "linked-leaf.bin"
            try:
                parent_link.symlink_to(real, target_is_directory=True)
                leaf_link.symlink_to(source)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlinks unavailable: {error}")
            for linked_path in (parent_link / source.name, leaf_link):
                with self.subTest(path=linked_path), self.assertRaises(OSError):
                    hf._open_source(linked_path)

    def test_reconstruction_rejects_overlap_unsafe_path_and_bad_hash(self):
        base = {"version": 1, "logical_size": 8, "mode": 0o600, "mtime_ns": 1,
                "source_identity": {"device": 1, "inode": 2},
                "fragments": [{"path": "fragments/a.bin", "offset": 0, "size": 5,
                               "logical_size": 8, "mode": 0o600, "mtime_ns": 1, "sha256": "0" * 64},
                              {"path": "fragments/b.bin", "offset": 4, "size": 4,
                               "logical_size": 8, "mode": 0o600, "mtime_ns": 1, "sha256": "1" * 64}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "overlapping"):
                hf.reconstruct_file(base, {}, root / "overlap")
            unsafe = dict(base, fragments=[dict(base["fragments"][0], path="../escape")])
            with self.assertRaisesRegex(ValueError, "unsafe"):
                hf.reconstruct_file(unsafe, {}, root / "unsafe")
            good_shape = dict(base, fragments=[base["fragments"][0]])
            fragment = root / "part"
            fragment.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "hash"):
                hf.reconstruct_file(good_shape, {"fragments/a.bin": fragment}, root / "bad-hash")
            self.assertFalse((root / "bad-hash").exists())

    def test_group_merge_rejects_missing_planned_fragments(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "ordinary.bin"
            source.write_bytes(b"0123456789")
            plan = hf.plan_file(source, fragment_bytes=4)
            expected = dict(plan, fragments=list(plan["fragments"]))
            groups = hf.fragment_groups(plan, max_group_bytes=4)
            verified = []
            for group in groups:
                verified.append(dict(group, fragments=[dict(item, sha256="0" * 64)
                                                      for item in group["fragments"]]))
            with self.assertRaisesRegex(ValueError, "complete planned file"):
                hf.merge_descriptors(verified[:-1], expected)
            self.assertEqual(len(hf.merge_descriptors(verified, expected)["fragments"]), 3)

    def test_archive_producer_refuses_source_mutated_after_planning(self):
        if not shutil.which("zstd"):
            self.skipTest("zstd is required by the production transport")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "mutable.bin"
            source.write_bytes(b"original payload")
            descriptor = hf.plan_file(source, fragment_bytes=64)
            source.write_bytes(b"changed payload!")
            factory = hf.archive_factory(descriptor, source, "private/mutable.bin", encrypt=False)
            output, producer, state, processes = factory({}, [str(source)], root, Guard(), 0)
            try:
                while output.read(1024 * 1024):
                    pass
                producer.join(timeout=10)
                self.assertFalse(producer.is_alive())
                self.assertIsNotNone(state["error"])
            finally:
                output.close()
                for process in processes:
                    process.wait(timeout=10)


def stat_mode(path):
    return path.stat().st_mode & 0o7777


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
