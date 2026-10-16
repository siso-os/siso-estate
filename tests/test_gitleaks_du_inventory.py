"""Keep the du false-positive exception narrower than real Sourcegraph credentials."""
import hashlib, pathlib, shutil, subprocess, tempfile, unittest
CONFIG = pathlib.Path(__file__).resolve().parents[1] / ".gitleaks.toml"
ARTIFACT = ".agents/runs/storage-20261005/mini/du-before.tsv"
IDENTIFIER = hashlib.sha1(b"public fixture cache identifier").hexdigest()

@unittest.skipUnless(shutil.which("gitleaks"), "gitleaks required")
class DuInventoryAllowlistTest(unittest.TestCase):
    def scan(self, content, relative=ARTIFACT):
        with tempfile.TemporaryDirectory(prefix="estate-security-case-") as root:
            target = pathlib.Path(root) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("sourcegraph\n" + content + "\n")
            result = subprocess.run(["gitleaks", "dir", "--no-banner", "--redact", "-l", "error", "--config", str(CONFIG), root], capture_output=True)
            self.assertIn(result.returncode, (0, 1), "scanner infrastructure error")
            return result.returncode

    def test_known_cache_hash_rows_are_not_credentials(self):
        for cache in (".local/share/opencode/snapshot", "Library/Application Support/Code/CachedData", "Library/Caches/CloudKit/com.apple.appleaccountd", "Library/Caches/flutter_engine", "Library/Caches/io.sentry"):
            with self.subTest(cache=cache):
                self.assertEqual(self.scan("104\t/Users/example/" + cache + "/" + IDENTIFIER), 0)

    def test_real_token_still_fails_inside_artifact(self):
        token = "sgp_" + IDENTIFIER
        self.assertEqual(self.scan("sourcegraph_access_token = '" + token + "'"), 1)
        self.assertEqual(self.scan("104\t/Users/example/Library/Caches/io.sentry/" + token), 1)

    def test_token_nested_under_cache_hash_still_fails(self):
        self.assertEqual(self.scan("104\t/Users/example/Library/Caches/io.sentry/" + IDENTIFIER + "/sgp_" + IDENTIFIER), 1)

    def test_adjacent_credential_is_not_hidden_by_cache_row(self):
        row = "104\t/Users/example/Library/Caches/io.sentry/" + IDENTIFIER
        secret = "sourcegraph_access_token = 'sgp_" + IDENTIFIER + "'"
        self.assertEqual(self.scan(row + "\n" + secret), 1)
        self.assertEqual(self.scan(secret + "\n" + row), 1)

    def test_bare_token_assignment_still_fails(self):
        self.assertEqual(self.scan("sourcegraph_access_token = '" + IDENTIFIER + "'"), 1)

    def test_same_hash_outside_exact_artifact_still_fails(self):
        self.assertEqual(self.scan("104\t/Users/example/Library/Caches/io.sentry/" + IDENTIFIER, "other.tsv"), 1)

if __name__ == "__main__": unittest.main()
