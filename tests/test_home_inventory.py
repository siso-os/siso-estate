"""Metadata-only whole-home inventory contract."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("home_inventory", Path(__file__).resolve().parents[1] / "tools/home_inventory.py")
hi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hi)


class HomeInventory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "home"
        self.root.mkdir()
        self.db = self.root / "inventory.sqlite"

    def rows(self):
        with sqlite3.connect(self.db) as conn:
            return {row[0]: row[1:] for row in conn.execute(
                "SELECT path,kind,nearest_git_root,exclusion,reason,metadata_location,git_kind,link_target,error FROM entries")}

    def run_scan(self, **kwargs):
        hi.scan([str(self.root)], str(self.db), **kwargs)
        return self.rows()

    def test_deep_nested_repositories_worktree_and_bare_root(self):
        deep = self.root
        for i in range(18):
            deep = deep / f"level-{i}"
        deep.mkdir(parents=True)
        file = deep / "payload.json"
        file.write_text("metadata only")
        parent_repo = self.root / "repo"
        (parent_repo / ".git" / "objects").mkdir(parents=True)
        (parent_repo / "nested").mkdir()
        (parent_repo / ".git" / "HEAD").write_text("ref: refs/heads/main")
        (parent_repo / "nested" / ".git").mkdir()
        (parent_repo / "nested" / "tracked.txt").write_text("x")
        wt = self.root / "worktree"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: ../repo/.git/worktrees/worktree")
        bare = self.root / "bare.git"
        for name in ("objects", "refs"):
            (bare / name).mkdir(parents=True)
        for name in ("HEAD", "config"):
            (bare / name).write_text("git")
        (bare / "objects" / "pack").mkdir()
        (bare / "objects" / "pack" / "pack-test.pack").write_bytes(b"git data")

        rows = self.run_scan()
        self.assertEqual(rows[str(file)][0], "file")
        self.assertEqual(rows[str(file)][1], None)
        self.assertEqual(rows[str(parent_repo)][0], "git_root")
        self.assertEqual(rows[str(parent_repo / "nested")][0], "git_root")
        self.assertEqual(rows[str(wt)][0], "git_root")
        self.assertEqual(rows[str(wt)][5], "gitfile")
        self.assertEqual(rows[str(bare)][0], "bare_git_root")
        self.assertEqual(rows[str(bare / "objects")][0], "git_metadata")
        self.assertNotIn(str(bare / "objects" / "pack" / "pack-test.pack"), rows)

    def test_user_data_retained_cache_and_halo_excluded(self):
        library = self.root / "Library" / "Application Support" / "app" / "data.db"
        library.parent.mkdir(parents=True)
        library.write_text("opaque")
        cached = self.root / "project" / "node_modules" / "pkg" / "index.js"
        cached.parent.mkdir(parents=True)
        cached.write_text("opaque")
        trash_file = self.root / ".Trash" / "keep-recorded"
        trash_file.parent.mkdir()
        trash_file.write_text("opaque")
        app_file = self.root / "Applications" / "custom-data.txt"
        app_file.parent.mkdir()
        app_file.write_text("opaque")
        bundle = self.root / "Applications" / "Example.app"
        (bundle / "Contents").mkdir(parents=True)
        (bundle / "Contents" / "binary").write_text("opaque")
        halo = self.root / hi.HALO_CANONICAL
        halo.mkdir(parents=True)
        (halo / "private.txt").write_text("opaque")

        rows = self.run_scan(halo_prefixes=[str(halo)])
        self.assertIn(str(library), rows)
        self.assertIn(str(trash_file), rows)
        self.assertIn(str(app_file), rows)
        self.assertEqual(rows[str(bundle)][3], "installed application bundle; rebuildable")
        self.assertNotIn(str(bundle / "Contents" / "binary"), rows)
        self.assertNotIn(str(cached), rows)
        self.assertEqual(rows[str(cached.parent.parent)][3], "rebuildable dependency or tool cache")
        self.assertIn(str(halo), rows)
        self.assertEqual(rows[str(halo)][3], "operator-supplied HALO exclusion")
        self.assertNotIn(str(halo / "private.txt"), rows)

    def test_symlink_empty_dir_output_and_permission_errors_are_visible(self):
        empty = self.root / "empty"
        empty.mkdir()
        link = self.root / "outside-link"
        link.symlink_to("relative/missing-target")
        blocked = self.root / "blocked"
        blocked.mkdir()
        real_scandir = os.scandir

        def denied(path):
            if os.fspath(path) == str(blocked):
                raise PermissionError("fixture denied")
            return real_scandir(path)

        with patch.object(hi.os, "scandir", side_effect=denied):
            rows = self.run_scan()
        self.assertEqual(rows[str(empty)][0], "directory")
        self.assertEqual(rows[str(link)][0], "symlink")
        self.assertIn("without following", rows[str(link)][3])
        self.assertEqual(rows[str(link)][6], "relative/missing-target")
        self.assertEqual(rows[str(blocked)][0], "error")
        self.assertIn("PermissionError", rows[str(blocked)][7])
        self.assertIn(str(self.db), rows)
        self.assertEqual(rows[str(self.db)][2], "inventory_output")

    def test_exclusion_prefix_covers_a_scan_root_inside_it(self):
        prefix = self.root / "excluded-tree"
        scanroot = prefix / "nested"
        scanroot.mkdir(parents=True)
        (scanroot / "file").write_text("opaque")
        hi.scan([str(scanroot)], str(self.db), exclusions=[str(prefix)])
        rows = self.rows()
        self.assertEqual(rows[str(scanroot)][0], "excluded_directory")
        self.assertEqual(rows[str(scanroot)][2], str(prefix))
        self.assertNotIn(str(scanroot / "file"), rows)

    def test_all_content_includes_caches_apps_library_and_git_metadata(self):
        cache_file = self.root / ".cache" / "app" / "cache.db"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_bytes(b"cache")
        bundle_file = self.root / "Applications" / "Tool.app" / "Contents" / "data"
        bundle_file.parent.mkdir(parents=True)
        bundle_file.write_bytes(b"app")
        library_cache = self.root / "Library" / "Caches" / "tool" / "cache"
        library_cache.parent.mkdir(parents=True)
        library_cache.write_bytes(b"cache")
        worktree_git = self.root / "repo" / ".git"
        (worktree_git / "objects" / "pack").mkdir(parents=True)
        (worktree_git / "HEAD").write_text("ref: refs/heads/main")
        (worktree_git / "config").write_text("[core]")
        (worktree_git / "objects" / "pack" / "pack-1").write_bytes(b"object")
        bare = self.root / "bare.git"
        for name in ("objects/pack", "refs/heads"):
            (bare / name).mkdir(parents=True)
        for name in ("HEAD", "config", "objects/pack/object"):
            path = bare / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"git")
        halo = self.root / hi.HALO_CANONICAL
        halo.mkdir(parents=True)
        (halo / "data").write_bytes(b"never include")
        explicit = self.root / "explicit-excluded"
        explicit.mkdir()
        (explicit / "data").write_bytes(b"excluded by caller")

        rows = self.run_scan(all_content=True, halo_prefixes=[str(halo)], exclusions=[str(explicit)])
        self.assertIn(str(cache_file), rows)
        self.assertIn(str(bundle_file), rows)
        self.assertIn(str(library_cache), rows)
        self.assertEqual(rows[str(worktree_git / "objects" / "pack" / "pack-1")][0], "file")
        self.assertEqual(rows[str(bare / "objects" / "pack" / "object")][0], "file")
        self.assertNotIn(str(halo / "data"), rows)
        self.assertNotIn(str(explicit / "data"), rows)

    def test_allocated_size_and_depth_three_summary_count_files_without_directory_bytes(self):
        deep = self.root / "Library" / "A" / "B" / "C"
        deep.mkdir(parents=True)
        depth_three = self.root / "Library" / "A" / "B"
        root_level_file = self.root / "root.txt"
        root_level_file.write_text("root")
        depth_two_file = self.root / "one" / "depth-two.txt"
        depth_two_file.parent.mkdir()
        depth_two_file.write_text("depth two")
        sparse = deep / "sparse.bin"
        with sparse.open("wb") as output:
            output.truncate(16 * 1024 * 1024)
            output.seek(16 * 1024 * 1024 - 1)
            output.write(b"x")
        (deep / "empty-dir").mkdir()

        self.run_scan(all_content=True)
        with sqlite3.connect(self.db) as conn:
            size, allocated = conn.execute(
                "SELECT size,allocated_bytes FROM entries WHERE path=?", (str(sparse),)).fetchone()
            aggregate = conn.execute(
                "SELECT depth,logical_bytes,allocated_bytes,file_count,error_count FROM directory_summary WHERE root=? AND directory=?",
                (str(self.root), str(depth_three))).fetchone()
            root_total = conn.execute(
                "SELECT file_count FROM directory_summary WHERE root=? AND directory=?",
                (str(self.root), str(self.root))).fetchone()[0]
            files = conn.execute(
                "SELECT count(*) FROM entries WHERE kind IN ('file','git_metadata_file') AND exclusion IS NULL").fetchone()[0]
            summary_paths = {row[0] for row in conn.execute("SELECT directory FROM directory_summary")}
        self.assertEqual(size, 16 * 1024 * 1024)
        self.assertEqual(allocated, sparse.stat().st_blocks * 512)
        self.assertLessEqual(allocated, size)
        self.assertEqual(aggregate, (3, size, allocated, 1, 0))
        self.assertEqual(root_total, files)
        self.assertNotIn(str(sparse), summary_paths)
        self.assertNotIn(str(deep / "empty-dir"), summary_paths)
        self.assertNotIn(str(root_level_file), summary_paths)
        self.assertNotIn(str(depth_two_file), summary_paths)

    def test_file_scan_root_has_no_fake_directory_summary(self):
        root_file = Path(self.temp.name) / "single-file"
        root_file.write_text("contents")
        inventory = Path(self.temp.name) / "single-file-inventory.sqlite"
        hi.scan([str(root_file)], str(inventory), all_content=True)
        with sqlite3.connect(inventory) as conn:
            summary = conn.execute("SELECT count(*) FROM directory_summary").fetchone()[0]
        self.assertEqual(summary, 0)

    def test_unreadable_file_error_is_attributed_to_parent_only(self):
        blocked = self.root / "unreadable-file"
        blocked.write_text("contents")
        real_scandir = os.scandir

        class EntryProxy:
            def __init__(self, entry):
                self.entry = entry
                self.name = entry.name

            def stat(self, follow_symlinks=False):
                if self.name == "unreadable-file":
                    raise PermissionError("fixture denied")
                return self.entry.stat(follow_symlinks=follow_symlinks)

        class IteratorProxy:
            def __init__(self, entries):
                self.source = entries
                self.entries = iter(EntryProxy(entry) for entry in entries)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.source.close()
                return False

            def __iter__(self):
                return self

            def __next__(self):
                return next(self.entries)

        def denied_entry(path):
            return IteratorProxy(real_scandir(path))

        with patch.object(hi.os, "scandir", side_effect=denied_entry):
            hi.scan([str(self.root)], str(self.db), all_content=True)
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT kind,is_directory,error FROM entries WHERE path=?", (str(blocked),)).fetchone()
            summaries = {item[0] for item in conn.execute("SELECT directory FROM directory_summary")}
            errors = conn.execute("SELECT error_count FROM directory_summary WHERE directory=?", (str(self.root),)).fetchone()[0]
        self.assertEqual(row[0], "error")
        self.assertIsNone(row[1])
        self.assertIn("PermissionError", row[2])
        self.assertIn(str(self.root), summaries)
        self.assertNotIn(str(blocked), summaries)
        self.assertEqual(errors, 1)


if __name__ == "__main__":
    unittest.main()
