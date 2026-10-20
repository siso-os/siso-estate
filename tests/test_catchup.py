import json, os, subprocess, sys, tempfile, unittest

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'catchup.py')


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, check=True).stdout


class Catchup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.origin, self.here, self.there = (os.path.join(t, n) for n in ('origin.git', 'here', 'there'))
        sh(t, 'git', 'init', '-q', '--bare', '-b', 'main', self.origin)
        for d in (self.here, self.there):
            sh(t, 'git', 'clone', '-q', self.origin, d)
            sh(d, 'git', 'config', 'user.email', 't@t'); sh(d, 'git', 'config', 'user.name', 't')
        self.write(self.there, 'log.jsonl', '{"a": 1}\n')
        self.write(self.there, 'plan.json', json.dumps({'x': 1, 'y': 1}, indent=1) + '\n')
        self.write(self.there, 'code.py', 'A = 1\n')
        sh(self.there, 'git', 'add', '.'); sh(self.there, 'git', 'commit', '-qm', 'base'); sh(self.there, 'git', 'push', '-q', 'origin', 'main')
        sh(self.here, 'git', 'pull', '-q', 'origin', 'main')
        # GitHub moves on
        self.write(self.there, 'log.jsonl', '{"a": 1}\n{"there": 2}\n')
        self.write(self.there, 'plan.json', json.dumps({'x': 1, 'y': 2}, indent=1) + '\n')
        self.write(self.there, 'code.py', 'A = 2\n')
        self.write(self.there, 'new.txt', 'new\n')
        sh(self.there, 'git', 'add', '.'); sh(self.there, 'git', 'commit', '-qm', 'remote'); sh(self.there, 'git', 'push', '-q', 'origin', 'main')
        # uncommitted work here
        self.write(self.here, 'log.jsonl', '{"a": 1}\n{"here": 3}\n')
        self.write(self.here, 'plan.json', json.dumps({'x': 1, 'y': 1, 'z': 9}, indent=1) + '\n')

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, d, name, text):
        with open(os.path.join(d, name), 'w') as f:
            f.write(text)

    def read(self, name):
        with open(os.path.join(self.here, name)) as f:
            return f.read()

    def run_tool(self, *a):
        return subprocess.run([sys.executable, TOOL, '--repo', self.here, *a], capture_output=True, text=True,
                              env=dict(os.environ, HOME=self.tmp.name))

    def test_fast_forward_keeps_uncommitted_work(self):
        self.assertEqual(self.run_tool().returncode, 0)
        self.assertNotIn('remote', sh(self.here, 'git', 'log', '--oneline'))   # plan changes nothing
        r = self.run_tool('--run')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sh(self.here, 'git', 'rev-parse', 'HEAD'), sh(self.here, 'git', 'rev-parse', 'origin/main'))
        self.assertEqual(self.read('code.py'), 'A = 2\n')
        self.assertEqual(self.read('new.txt'), 'new\n')
        self.assertEqual(set(self.read('log.jsonl').splitlines()), {'{"a": 1}', '{"there": 2}', '{"here": 3}'})
        self.assertEqual(json.loads(self.read('plan.json')), {'x': 1, 'y': 2, 'z': 9})
        self.assertEqual(sh(self.here, 'git', 'diff', '--cached', '--name-only'), '')
        self.assertEqual(sorted(sh(self.here, 'git', 'diff', '--name-only').split()), ['log.jsonl', 'plan.json'])

    def test_clashing_code_holds_and_changes_nothing(self):
        self.write(self.here, 'code.py', 'A = 3\n')
        head = sh(self.here, 'git', 'rev-parse', 'HEAD')
        r = self.run_tool('--run')
        self.assertEqual(r.returncode, 1)
        self.assertIn('held code.py', r.stdout)
        self.assertEqual(sh(self.here, 'git', 'rev-parse', 'HEAD'), head)
        self.assertEqual(self.read('code.py'), 'A = 3\n')
        self.assertEqual(self.run_tool('--run', '--theirs', 'code.py').returncode, 0)
        self.assertEqual(self.read('code.py'), 'A = 2\n')

    def test_diverged_branch_gets_a_merge_to_push(self):
        self.write(self.here, 'mine.txt', 'mine\n')
        sh(self.here, 'git', 'add', 'mine.txt'); sh(self.here, 'git', 'commit', '-qm', 'local')
        r = self.run_tool('--run')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        parents = sh(self.here, 'git', 'log', '-1', '--format=%P').split()
        self.assertEqual(len(parents), 2)
        self.assertEqual(self.read('mine.txt'), 'mine\n')
        self.assertEqual(self.read('code.py'), 'A = 2\n')
        sh(self.here, 'git', 'push', '-q', 'origin', 'main')   # a fast-forward for GitHub


if __name__ == '__main__':
    unittest.main()
