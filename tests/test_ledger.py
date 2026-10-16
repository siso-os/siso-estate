#!/usr/bin/env python3
"""The storage ledger: five buckets, derived homes, need from mtime, and pull's lookups.

  python3 -m unittest tests/test_ledger.py -v
"""
import importlib.util, os, sys, unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import ledger as L

spec = importlib.util.spec_from_file_location("pull", os.path.join(TOOLS, "pull.py"))
pull = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pull)

NOW = 1_800_000_000
DAY = 86400
G = 1048576  # KiB per GiB


def rule_for(path, places):
    best = None
    for p in places:
        q = p["path"].rstrip("/")
        if (path == q or path.startswith(q + "/")) and (best is None or len(q) > len(best["path"])):
            best = p
    return best


def node(path, kib, kids=()):
    return {"path": path, "kib": kib, "children": list(kids), "rest": kib - sum(k["kib"] for k in kids)}


class Homes(unittest.TestCase):
    def test_clean_pushed_repo_is_github(self):
        repo = {"github": [{"owner": "sisodias", "repo": "x"}], "unpushed_commits": 0, "dirty": 0, "untracked": 0}
        self.assertEqual(L.home_of({"rule": "keep"}, repo)[:2], ("github:sisodias/x", False))

    def test_dirty_or_unpushed_repo_is_only_here(self):
        repo = {"github": [{"owner": "o", "repo": "r"}], "unpushed_commits": 2, "dirty": 0, "untracked": 0}
        home, pending, why = L.home_of({"rule": "keep", "home": "github:o/r"}, repo)
        self.assertTrue(pending)
        self.assertIn("unpushed", why)
        self.assertEqual(L.bucket_of({"rule": "keep"}, home, pending, "cold"), L.HERE)

    def test_no_remote_is_only_here(self):
        home, pending, _ = L.home_of({"rule": "keep"}, {"remotes": {}, "github": []})
        self.assertEqual(L.bucket_of({}, home, pending, "cold"), L.HERE)

    def test_rule_defaults(self):
        self.assertEqual(L.home_of({"rule": "regenerable"})[0], "rebuild:regenerable")
        self.assertEqual(L.home_of({"rule": "github"})[:2], ("github:pending", True))
        self.assertEqual(L.home_of({"rule": "vault"})[0].split(":")[0], "vault")
        self.assertIsNone(L.home_of({"rule": "keep"})[0])

    def test_declared_home_wins_over_rule(self):
        self.assertEqual(L.home_of({"rule": "github", "home": "plane:personal"})[0], "plane:personal")


class Need(unittest.TestCase):
    def test_auto_follows_mtime(self):
        self.assertEqual(L.need_of({}, NOW - 2 * DAY, NOW), "working")
        self.assertEqual(L.need_of({}, NOW - 9 * DAY, NOW), "cold")
        self.assertEqual(L.need_of({}, None, NOW), "cold")

    def test_repo_commit_counts(self):
        import datetime
        day = datetime.datetime.fromtimestamp(NOW - DAY).isoformat()
        self.assertEqual(L.need_of({}, NOW - 99 * DAY, NOW, {"last_commit": day}), "working")

    def test_declared_need_wins(self):
        self.assertEqual(L.need_of({"need": "cold"}, NOW, NOW), "cold")

    def test_newest_mtime_sees_two_levels(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "a", "b"))
            f = os.path.join(d, "a", "x")
            open(f, "w").close()
            os.utime(f, (NOW, NOW))
            self.assertEqual(L.newest_mtime(d), NOW)
            self.assertIsNone(L.newest_mtime(os.path.join(d, "nope")))


class Buckets(unittest.TestCase):
    def test_bucket_order(self):
        b = L.bucket_of
        self.assertEqual(b({"need": "working"}, None, False, "working"), L.WORKING)
        self.assertEqual(b({"rule": "regenerable"}, "rebuild:regenerable", False, "cold"), L.REBUILD)
        self.assertEqual(b({}, "github:o/r", False, "cold"), L.SAFE)
        self.assertEqual(b({}, "vault:p", False, "cold"), L.SAFE)
        self.assertEqual(b({}, "github:o/r", False, "working"), L.WORKING)
        self.assertEqual(b({}, "here", False, "cold"), L.HERE)
        self.assertEqual(b({}, "github:pending", True, "cold"), L.HERE)
        self.assertEqual(b({}, None, False, "cold"), L.HERE)

    def test_every_byte_lands_once(self):
        tree = node("/D", 100 * G, [node("/D/a", 40 * G, [node("/D/a/n", 10 * G)]), node("/D/b", 30 * G), node("/D/c", 20 * G)])
        places = [{"path": "/D/a", "rule": "keep", "home": "github:o/a", "need": "working"},
                  {"path": "/D/a/n", "rule": "regenerable"},
                  {"path": "/D/b", "rule": "vault"},
                  {"path": "/D/c", "rule": "keep"}]
        rows, un, sums, _ = L.ledger(tree, places, rule_for, {}, [], [], NOW, mtime=lambda p: NOW - 30 * DAY, expand=lambda p: p)
        self.assertEqual(sums[L.WORKING], 30 * G)     # a minus the nested n
        self.assertEqual(sums[L.REBUILD], 10 * G)
        self.assertEqual(sums[L.SAFE], 30 * G)
        self.assertEqual(sums[L.HERE], 20 * G)        # c: no home declared
        self.assertEqual(sums[L.UNMAPPED], 10 * G)    # / D's own residual
        self.assertEqual(sum(sums.values()), 100 * G)
        self.assertEqual(un, [("/D", 10 * G)])

    def test_system_volumes_are_rebuildable(self):
        tree = node("/D", G)
        _, _, sums, _ = L.ledger(tree, [], rule_for, {}, [], [{"role": "System", "bytes": 2 * 1024**3}, {"role": "Data", "bytes": 5}], NOW, expand=lambda p: p)
        self.assertEqual(sums[L.REBUILD], 2 * G)

    def test_worktrees_split_by_state(self):
        tree = node("/H/_data/worktrees", 10 * G)
        places = [{"path": "/H/_data/worktrees", "rule": "owner"}]
        wts = [{"repo": "r", "lane": "a", "kib": 3 * G, "branch": "b", "last_commit": "2000-01-01", "dirty": 0, "pushed": True},
               {"repo": "r", "lane": "d", "kib": 2 * G, "branch": "d", "last_commit": "2000-01-01", "dirty": 4, "pushed": True},
               {"repo": "r", "lane": "w", "kib": G, "branch": "w", "last_commit": "2000-01-01", "dirty": 0, "pushed": True, "busy": True}]
        _, _, sums, hw = L.ledger(tree, places, rule_for, {}, wts, [], NOW, mtime=lambda p: None, expand=lambda p: p)
        self.assertEqual((sums[L.SAFE], sums[L.WORKING]), (3 * G, G))
        self.assertEqual(sums[L.HERE], 2 * G + 4 * G)  # dirty worktree + the place's leftover with no home


class PullLookups(unittest.TestCase):
    places = [{"path": "~/SISO_Workspace/SISO_Agency/apps"}, {"path": "~/SISO_Workspace/SISO_Agency/partners/fahmy"}]

    def test_find_place(self):
        self.assertEqual(len(pull.find_place("fahmy", self.places)), 1)
        self.assertEqual(pull.find_place("~/SISO_Workspace/SISO_Agency/apps", self.places)[0]["path"], self.places[0]["path"])
        self.assertEqual(pull.find_place("nothing-here", self.places), [])

    def test_comes_back_names_the_way(self):
        self.assertIn("estate pull", L.comes_back("github:o/r", "~/x"))
        self.assertIn("estate data restore personal", L.comes_back("plane:personal", "~/x"))
        self.assertEqual(L.comes_back("rebuild:npm ci", "~/x"), "npm ci")


if __name__ == "__main__":
    unittest.main()
