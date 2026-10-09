#!/usr/bin/env python3
"""backup.py never makes a GitHub repo for a repo inside a building (docs/MERGES.md: the mirror root cause).

  python3 -m unittest tests/test_backup_nested.py -v
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import backup

WS = backup.WS
BY = {f"{WS}/SISO_Agency": {"kind": "repo"}, f"{WS}/SISO_Agency/partners/fahmy": {"kind": "repo"},
      f"{WS}/SISO_Agents/siso-os": {"kind": "repo"}, f"{WS}/_data/worktrees/x/lane": {"kind": "worktree"}}


class Nested(unittest.TestCase):
    def test_a_new_building_in_a_district_is_its_own(self):
        self.assertIsNone(backup.building_of(f"{WS}/SISO_Agents/laptop-health", BY))
        self.assertIsNone(backup.building_of(f"{WS}/SISO_Agency/clients/newbrand/code", BY))

    def test_a_room_of_a_building_is_not(self):
        self.assertEqual(backup.building_of(f"{WS}/SISO_Agency/partners/fahmy/docs/reviews/x", BY), f"{WS}/SISO_Agency/partners/fahmy")
        self.assertEqual(backup.building_of(f"{WS}/SISO_Agents/siso-os/research/y", BY), f"{WS}/SISO_Agents/siso-os")

    def test_outside_the_city(self):
        self.assertIsNone(backup.building_of(f"{WS}/_archive/2026-09-25-x/a/b", BY))
        self.assertIsNone(backup.building_of(os.path.expanduser("~/elsewhere/repo"), BY))


if __name__ == "__main__":
    unittest.main()
