import importlib.util
from pathlib import Path
import os
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('storage_dispatch', Path(__file__).resolve().parents[1] / 'tools/storage-guard.py')
guard = importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)


class Dispatch(unittest.TestCase):
    def test_minimal_launchd_path_finds_homebrew_and_user_tools(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True):
            env = guard.tool_environment()
        self.assertEqual(env['PATH'].split(':')[:2], ['/opt/homebrew/bin', str(Path.home() / '.local/bin')])
        self.assertIn('/usr/bin', env['PATH'].split(':'))

    def test_existing_credentials_and_machine_identity_are_preserved(self):
        with patch.dict(os.environ, {'PATH': '/custom/bin', 'ESTATE_R2_REMOTE': 'fixture-r2:bucket'}, clear=True), patch.object(guard, 'MACHINE', 'mini'):
            env = guard.tool_environment()
        self.assertEqual(env['ESTATE_MACHINE'], 'mini')
        self.assertEqual(env['ESTATE_R2_REMOTE'], 'fixture-r2:bucket')
        self.assertIn('/custom/bin', env['PATH'].split(':'))

    def test_no_path_has_no_empty_or_duplicate_search_component(self):
        with patch.dict(os.environ, {}, clear=True):
            paths = guard.tool_environment()['PATH'].split(':')
        self.assertNotIn('', paths)
        self.assertEqual(len(paths), len(set(paths)))


if __name__ == '__main__':
    unittest.main()
