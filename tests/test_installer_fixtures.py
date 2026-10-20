import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('installer_fixtures', Path(__file__).resolve().parents[1] / 'tools/installer-fixtures.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class InstallerFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='siso-stack-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        (self.path / '.siso/agent-stack').mkdir(parents=True)
        (self.path / '.codex').mkdir()
        (self.path / '.codex/AGENTS.md').write_text('SISO AGENT STACK')
        self.receipt = self.path / '.siso/agent-stack/install-receipt.json'
        self.receipt.write_text(json.dumps({'schema_version': 1, 'home': str(self.path), 'stack_version': 'fixture'}))

    def test_name_alone_never_certifies_a_fixture(self):
        self.receipt.unlink()
        self.assertFalse(fixtures.certified(self.path))

    def test_receipt_for_another_home_never_certifies(self):
        self.receipt.write_text(json.dumps({'schema_version': 1, 'home': '/', 'stack_version': 'fixture'}))
        self.assertFalse(fixtures.certified(self.path))

    def test_receipt_and_generated_marker_certify_exact_home(self):
        self.assertTrue(fixtures.certified(self.path))

    def test_rename_keeps_fingerprint_but_new_authored_work_changes_it(self):
        before = fixtures.fingerprint(self.path)
        renamed = self.path.with_name(self.path.name + '.estate-retiring-fixture')
        self.path.rename(renamed)
        try:
            self.assertEqual(before, fixtures.fingerprint(renamed))
            (renamed / 'new.txt').write_text('must stay')
            self.assertNotEqual(before, fixtures.fingerprint(renamed))
        finally:
            renamed.rename(self.path)

    def test_open_descendant_protects_home(self):
        self.assertTrue(fixtures.busy(self.path, {str(self.path / 'source.txt')}))
        self.assertFalse(fixtures.busy(self.path, {str(self.path) + '-different/source.txt'}))


if __name__ == '__main__':
    unittest.main()
