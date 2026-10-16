"""Safety checks for the private package re-fetch manifest."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location(
    "refetch_manifest", Path(__file__).resolve().parents[1] / "tools/refetch_manifest.py")
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)


class RefetchManifest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "npm-cache"
        (self.cache / "index-v5" / "aa").mkdir(parents=True)
        (self.cache / "content-v2" / "sha512").mkdir(parents=True)
        self.store = self.root / "pnpm-store"
        (self.store / "file+local-package.tgz").mkdir(parents=True)
        self.store.joinpath("index.db").write_bytes(b"not sqlite")

    def add_record(self, key, payload, *, sri_payload=None):
        digest = hashlib.sha512(payload).digest()
        hex_digest = digest.hex()
        content = (self.cache / "content-v2" / "sha512" / hex_digest[:2]
                   / hex_digest[2:4] / hex_digest[4:])
        content.parent.mkdir(parents=True, exist_ok=True)
        content.write_bytes(payload)
        integrity = base64.b64encode(sri_payload if sri_payload is not None else digest).decode()
        record = {"key": key, "integrity": "sha512-" + integrity, "size": len(payload)}
        index = self.cache / "index-v5" / "aa" / "index"
        with index.open("ab") as stream:
            stream.write(b"cacache-prefix\n")
            stream.write(json.dumps(record).encode() + b"\n")
        return content

    def test_only_matching_public_registry_tarballs_are_covered(self):
        plain = b"verified public package archive"
        plain_content = self.add_record(
            "make-fetch-happen:request-cache:https://registry.npmjs.org/react/-/react-18.3.1.tgz",
            plain)
        scoped = self.add_record(
            "make-fetch-happen:request-cache:https://registry.npmjs.org/@types/node/-/node-22.0.0.tgz",
            b"scoped archive")
        self.add_record(
            "make-fetch-happen:request-cache:https://other.example/react/-/react-18.3.1.tgz",
            b"foreign host")
        self.add_record(
            "make-fetch-happen:request-cache:https://registry.npmjs.org/react/-/react-18.3.1.tgz?token=secret",
            b"credential-bearing query")

        manifest = rm.build_manifest(self.cache, self.store)
        entries = {(e["name"], e["version"]): e for e in manifest["entries"]}
        self.assertEqual(set(entries), {("react", "18.3.1"), ("@types/node", "22.0.0")})
        react = entries[("react", "18.3.1")]
        self.assertEqual(react["source"], "https://registry.npmjs.org/react/-/react-18.3.1.tgz")
        self.assertEqual(react["covered_file"]["path"], plain_content.relative_to(self.cache).as_posix())
        self.assertEqual(react["covered_file"]["size"], len(plain))
        self.assertEqual(react["covered_file"]["sha512"], hashlib.sha512(plain).hexdigest())
        self.assertEqual(entries[("@types/node", "22.0.0")]["covered_file"]["path"],
                         scoped.relative_to(self.cache).as_posix())
        self.assertEqual(manifest["pnpm_store"]["status"], "raw_required")
        self.assertEqual(manifest["pnpm_store"]["local_tarball_directories"], 1)
        self.assertEqual(manifest["coverage_status"], "provisional_not_uploaded")

    def test_integrity_mismatch_symlink_and_non_tarball_are_not_covered(self):
        content = self.add_record(
            "make-fetch-happen:request-cache:https://registry.npmjs.org/react/-/react-18.3.1.tgz",
            b"archive", sri_payload=b"x" * 64)
        self.add_record(
            "make-fetch-happen:request-cache:https://registry.npmjs.org/react",
            b"metadata only")
        manifest = rm.build_manifest(self.cache, self.store)
        self.assertEqual(manifest["entries"], [])
        self.assertGreaterEqual(manifest["summary"]["index_records_rejected_or_unverified"], 2)
        self.assertEqual(content.read_bytes(), b"archive")

    def test_output_is_private_and_cannot_mutate_cache(self):
        payload = b"archive"
        content = self.add_record(
            "make-fetch-happen:request-cache:https://registry.npmjs.org/react/-/react-18.3.1.tgz",
            payload)
        cache_before = content.stat().st_mtime_ns
        manifest = rm.build_manifest(self.cache, self.store)
        output = self.root / "proof" / "manifest.json"
        rm.write_manifest(manifest, output, [self.cache, self.store])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(content.stat().st_mtime_ns, cache_before)
        self.assertEqual(json.loads(output.read_text())["entries"], manifest["entries"])
        with self.assertRaisesRegex(ValueError, "outside package caches"):
            rm.write_manifest(manifest, self.cache / "manifest.json", [self.cache])

    def test_registry_parser_rejects_ambiguous_or_non_semver_tarball(self):
        self.assertIsNone(rm._parse_registry_tarball(
            "https://registry.npmjs.org/react/-/react-latest.tgz"))
        self.assertIsNone(rm._parse_registry_tarball(
            "https://registry.npmjs.org/react/-/react-18.3.1-01.tgz"))
        self.assertIsNone(rm._parse_registry_tarball(
            "https://user:secret@registry.npmjs.org/react/-/react-18.3.1.tgz"))
        self.assertIsNone(rm._parse_registry_tarball(
            "http://registry.npmjs.org/react/-/react-18.3.1.tgz"))

    def test_conflicting_valid_payloads_for_same_registry_url_are_raw_required(self):
        url = "make-fetch-happen:request-cache:https://registry.npmjs.org/react/-/react-18.3.1.tgz"
        self.add_record(url, b"first archive")
        self.add_record(url, b"different but separately valid archive")
        manifest = rm.build_manifest(self.cache, self.store)
        self.assertEqual(manifest["entries"], [])
        self.assertEqual(manifest["summary"]["index_records_rejected_or_unverified"], 1)

    def test_anonymous_streaming_source_verification_and_raw_required_failures(self):
        payload = b"downloaded from anonymous registry"
        self.add_record(
            "make-fetch-happen:request-cache:https://registry.npmjs.org/react/-/react-18.3.1.tgz",
            payload)
        entry = rm.build_manifest(self.cache, self.store)["entries"][0]

        class Response:
            status = 200
            headers = {"Content-Length": str(len(payload))}

            def __init__(self):
                self.offset = 0

            def geturl(self):
                return entry["source"]

            def getcode(self):
                return 200

            def read(self, size):
                block = payload[self.offset:self.offset + size]
                self.offset += len(block)
                return block

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()

        opener = Opener()
        public, rejected = rm.verify_sources([entry], opener_factory=lambda *handlers: opener)
        self.assertEqual(len(public), 1)
        self.assertEqual(rejected, [])
        self.assertEqual(public[0]["source_verification_status"], "verified_public")
        self.assertIsNotNone(public[0]["source_verified_at"])
        self.assertIsNone(opener.request.get_header("Authorization"))
        self.assertEqual(opener.request.get_method(), "GET")

        class PrivateOpener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

        public, rejected = rm.verify_sources([entry], opener_factory=lambda *handlers: PrivateOpener())
        self.assertEqual(public, [])
        self.assertEqual(rejected[0]["source_verification_status"], "not_public")
        self.assertIsNotNone(rejected[0]["source_verified_at"])

    def test_source_verification_caps_bytes_and_redirect_host(self):
        entry = {"covered_file": {"size": rm.MAX_SOURCE_BYTES + 1}}
        with self.assertRaisesRegex(ValueError, "1 GB"):
            rm.verify_sources([entry], opener_factory=lambda *args: self.fail("must not open"))
        handler = rm._RegistryRedirects()
        import urllib.request
        request = urllib.request.Request("https://registry.npmjs.org/react/-/react-18.3.1.tgz")
        with self.assertRaisesRegex(ValueError, "left public npm registry"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://example.com/private.tgz")


if __name__ == "__main__":
    unittest.main()
