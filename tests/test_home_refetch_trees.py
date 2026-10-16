import importlib.util
import hashlib
import json
import base64
from contextlib import closing
import io
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "tools/home_refetch_trees.py"
SPEC = importlib.util.spec_from_file_location("home_refetch_trees", SCRIPT)
hr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hr)


class HomeRefetchTreesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.db = self.base / "inventory-all.sqlite"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript("""
                CREATE TABLE scan_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE directory_summary (
                    root TEXT NOT NULL, directory TEXT NOT NULL, depth INTEGER NOT NULL,
                    logical_bytes INTEGER NOT NULL, allocated_bytes INTEGER NOT NULL,
                    file_count INTEGER NOT NULL, error_count INTEGER NOT NULL,
                    PRIMARY KEY(root,directory));
                CREATE TABLE entries (
                    path TEXT PRIMARY KEY, size INTEGER, allocated_bytes INTEGER,
                    mtime_ns INTEGER, is_directory INTEGER, kind TEXT, error TEXT);
            """)
            conn.execute("INSERT INTO scan_info VALUES ('completed_at','2026-10-16T23:42:25+0700')")

    def tearDown(self):
        self.temp.cleanup()

    def add_dir(self, relative, logical=1000, physical=2000, files=10, errors=0):
        path = self.home / relative
        path.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO directory_summary VALUES (?,?,?,?,?,?,?)",
                         (str(self.home), str(path), len(path.parts) - len(self.home.parts), logical, physical, files, errors))
        return path

    def test_selects_rebuildable_candidates_records_lock_evidence_but_keeps_tree_raw(self):
        repo = self.home / "project"
        repo.mkdir()
        (repo / "package.json").write_text('{"name":"demo","version":"2.1.0","packageManager":"npm@10.0.0"}')
        (repo / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{}}')
        tree = self.add_dir("project/node_modules", logical=4000, physical=5000)
        (tree / ".package-lock.json").write_text('{"lockfileVersion":3,"packages":{}}')
        unknown = self.add_dir("unknown/venv", logical=6000, physical=7000)
        halo = self.add_dir("SISO_Agency/partners/halo/crm/node_modules", physical=9000)
        (halo.parent / "package.json").write_text('{"name":"private"}')
        (halo.parent / "package-lock.json").write_text('{"packages":{}}')

        manifest = hr.build_manifest(self.db, self.home, min_bytes=0)
        by_path = {row["absolute_root"]: row for row in manifest["rows"]}
        self.assertEqual(set(by_path), {str(tree), str(unknown)})
        proven = by_path[str(tree)]
        self.assertEqual(proven["classification"], "raw-required")
        self.assertFalse(proven["recipe_backed"])
        self.assertEqual(proven["source"]["version"], "2.1.0")
        self.assertEqual(proven["reconstruction"]["command"], "npm ci")
        self.assertFalse(proven["reconstruction"]["executed"])
        self.assertEqual([item["path"] for item in proven["evidence"]],
                         [str((repo / "package.json").resolve()), str((repo / "package-lock.json").resolve())])
        self.assertTrue(all(len(item["sha256"]) == 64 for item in proven["evidence"]))
        self.assertIn("installed_tree_contents_and_local_edits_not_verified", proven["raw_required_exceptions"])
        self.assertEqual(manifest["summary"]["safe_to_exclude_bytes"], 0)
        self.assertEqual(manifest["summary"]["candidate_byte_reduction"], 0)

    def test_nested_candidates_do_not_double_count_and_inventory_errors_stay_raw(self):
        parent = self.add_dir(".cache", logical=10000, physical=12000, files=20)
        self.add_dir(".cache/huggingface/xet", logical=5000, physical=6000, files=10)
        problematic = self.add_dir("repo/.venv", logical=100, physical=200, errors=2)
        manifest = hr.build_manifest(self.db, self.home, min_bytes=0)
        paths = [row["absolute_root"] for row in manifest["rows"]]
        self.assertEqual(paths, [str(parent), str(problematic)])
        self.assertEqual(manifest["summary"]["physical_bytes"], 12200)
        p = next(row for row in manifest["rows"] if row["absolute_root"] == str(problematic))
        self.assertIn("inventory_subtree_has_read_errors", p["raw_required_exceptions"])
        self.assertEqual(p["safe_to_exclude_bytes"], 0)

    def test_incomplete_inventory_and_unhashable_evidence_fail_closed(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("DELETE FROM scan_info")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            hr.build_manifest(self.db, self.home, min_bytes=0)

    def test_writer_sets_private_modes_and_rejects_outside_path(self):
        manifest = {"schema": "test"}
        private = self.base / "private" / "refetch-trees"
        output = private / "manifest.json"
        hr.write_private(manifest, output, private)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(private.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads(output.read_text()), manifest)
        with self.assertRaisesRegex(ValueError, "under the private"):
            hr.write_private(manifest, self.base / "public.json", private)

    def test_candidate_link_resolving_outside_home_is_not_selected(self):
        outside = self.base / "outside"
        outside.mkdir()
        link = self.home / "link"
        link.symlink_to(outside)
        target = link / "node_modules"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO directory_summary VALUES (?,?,?,?,?,?,?)",
                         (str(self.home), str(target), 1, 1, 2, 1, 0))
        self.assertEqual(hr.build_manifest(self.db, self.home, min_bytes=0)["rows"], [])

    def test_exact_public_npm_blob_becomes_a_covered_file_only_after_live_hash_match(self):
        cache = self.home / ".npm/_cacache"
        content = cache / "content-v2/sha512/aa/bb/blob"
        content.parent.mkdir(parents=True)
        payload = b"source-verified package bytes"
        content.write_bytes(payload)
        digest = hashlib.sha512(payload).hexdigest()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO directory_summary VALUES (?,?,?,?,?,?,?)",
                         (str(self.home), str(cache), 2, len(payload), 80, 1, 0))
            conn.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?)",
                         (str(content.resolve()), len(payload), 80, 1, 0, "file", None))
        source_manifest = self.base / "npm-manifest.json"
        source_manifest.write_text(json.dumps({
            "schema": "siso.estate.refetch-manifest.v1",
            "source_root": str(cache.resolve()),
            "entries": [{
                "name": "demo", "version": "1.2.3",
                "source": "https://registry.npmjs.org/demo/-/demo-1.2.3.tgz",
                "source_verification_status": "verified_public",
                "source_verified_at": "2026-10-16T16:32:18Z",
                "covered_file": {"path": content.relative_to(cache).as_posix(), "size": len(payload), "sha512": digest},
            }],
        }))
        manifest = hr.build_manifest(self.db, self.home, min_bytes=0, npm_manifest=source_manifest)
        row = next(item for item in manifest["rows"] if item["absolute_root"] == str(cache))
        self.assertEqual(row["classification"], "exact-refetch-verified")
        self.assertEqual(row["safe_to_exclude_bytes"], 80)
        self.assertEqual(row["raw_required_physical_bytes"], 0)
        self.assertEqual(row["covered_files"][0]["sha512"], digest)
        self.assertEqual(manifest["summary"]["exact_refetch_bytes"], 80)

        content.write_bytes(b"locally changed")
        changed = hr.build_manifest(self.db, self.home, min_bytes=0, npm_manifest=source_manifest)
        row = next(item for item in changed["rows"] if item["absolute_root"] == str(cache))
        self.assertEqual(row["classification"], "raw-required")
        self.assertEqual(row["safe_to_exclude_bytes"], 0)

    def test_installed_files_require_tar_member_hash_and_safe_source_identity(self):
        home = self.home
        install_root = home / "project/node_modules"
        package_root = install_root / "demo"
        package_root.mkdir(parents=True)
        package_json = b'{"name":"demo","version":"1.2.3"}'
        asset = b"known package file contents"
        (package_root / "package.json").write_bytes(package_json)
        (package_root / "index.js").write_bytes(asset)

        archive_buffer = io.BytesIO()
        with tarfile.open(fileobj=archive_buffer, mode="w:gz") as tar:
            for name, payload in (("package/package.json", package_json), ("package/index.js", asset)):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(payload))
        archive = archive_buffer.getvalue()
        cache_root = home / ".npm/_cacache"
        blob = cache_root / "content-v2/sha512/11/22/blob.tgz"
        blob.parent.mkdir(parents=True)
        blob.write_bytes(archive)
        archive_sha512 = hashlib.sha512(archive).digest()
        integrity = "sha512-" + base64.b64encode(archive_sha512).decode()

        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO directory_summary VALUES (?,?,?,?,?,?,?)",
                         (str(home), str(install_root), 2, 100, 200, 2, 0))
            for path in (package_root / "package.json", package_root / "index.js"):
                st = path.stat()
                conn.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?)",
                             (str(path.resolve()), st.st_size, 4096, st.st_mtime_ns, 0, "file", None))
        source_manifest = self.base / "npm-source.json"
        source_manifest.write_text(json.dumps({
            "schema": "siso.estate.refetch-manifest.v1", "source_root": str(cache_root.resolve()),
            "entries": [{"name": "demo", "version": "1.2.3",
                         "source": "https://registry.npmjs.org/demo/-/demo-1.2.3.tgz",
                         "integrity": integrity, "source_verification_status": "verified_public",
                         "covered_file": {"path": blob.relative_to(cache_root).as_posix(),
                                          "size": len(archive), "sha512": hashlib.sha512(archive).hexdigest()}}],
        }))
        protected = self.base / "git-protected-prefixes.json"
        protected.write_text(json.dumps({"schema": "home-git-protected-prefixes.v1", "protected_prefixes": []}))
        output = self.base / "private" / "exact-members.json"
        result = hr.build_installed_manifest(self.db, source_manifest, protected, output, home, 2_000_000)
        self.assertTrue(result["done"])
        self.assertEqual(result["summary"]["matched_installed_package_roots"], 1, result["summary"])
        self.assertEqual(result["summary"]["archives_processed"], 1, result["summary"])
        rows = {row["absolute_path"]: row for row in result["covered_files"]}
        package_json_path, asset_path = (package_root / "package.json").resolve(), (package_root / "index.js").resolve()
        self.assertEqual(set(rows), {str(package_json_path), str(asset_path)})
        self.assertEqual(rows[str(asset_path)]["archive_member"], "package/index.js")
        self.assertEqual(rows[str(asset_path)]["source_integrity"], integrity)
        self.assertIn("device", rows[str(asset_path)]["source_stat"])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

        (package_root / "index.js").write_bytes(b"local edit")
        with closing(sqlite3.connect(self.db)) as conn, conn:
            st = (package_root / "index.js").stat()
            conn.execute("UPDATE entries SET size=?,mtime_ns=? WHERE path=?",
                         (st.st_size, st.st_mtime_ns, str((package_root / "index.js").resolve())))
        changed = hr.build_installed_manifest(self.db, source_manifest, protected,
                                              self.base / "private2" / "exact-members.json", home, 2_000_000)
        self.assertNotIn(str(asset_path), {row["absolute_path"] for row in changed["covered_files"]})


if __name__ == "__main__":
    unittest.main()
