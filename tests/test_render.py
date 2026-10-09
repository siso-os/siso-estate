#!/usr/bin/env python3
"""Tests for tools/render.py: the page it writes carries the importmap and the register's own counts.

  python3 -m unittest tests/test_render.py -v

The page is generated into a temp file from the repository's real register, so these tests read the same input the
command does and write nothing into plan/.
"""
import json, os, re, sys, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import render  # noqa: E402

REGISTER = json.load(open(os.path.join(REPO, "machines", "register.json")))


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.out, cls.size = render.build(render.REGISTER, os.path.join(cls.dir.name, "index.html"))
        with open(cls.out) as fh:
            cls.html = fh.read()

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def estate(self):
        m = re.search(r"window\.ESTATE = (\{.*\});\n", self.html)
        self.assertIsNotNone(m, "the page must inline window.ESTATE")
        return json.loads(m.group(1))

    def test_page_is_self_contained_and_imports_three(self):
        for needle in ('<script type="importmap">', "three@0.160.0/build/three.module.js",
                       "three@0.160.0/examples/jsm/controls/OrbitControls.js", "window.ESTATE"):
            self.assertIn(needle, self.html)
        self.assertNotIn("__ESTATE__", self.html, "the data placeholder must be substituted")
        self.assertGreater(self.size, 50000)

    def test_counts_match_the_register(self):
        estate = self.estate()
        self.assertEqual(len(estate["buildings"]), len(REGISTER["buildings"]))
        self.assertEqual(len(estate["compounds"]), len(REGISTER["compounds"]))
        self.assertEqual(estate["counts"], {"buildings": len(REGISTER["buildings"]),
                                            "compounds": len(REGISTER["compounds"])})
        self.assertEqual(estate["generated_at"], REGISTER["generated_at"])

    def test_every_building_carries_the_fields_the_scene_draws(self):
        fields = {"postcode", "path", "island", "compound", "provenance", "seat", "lifecycle", "built_on", "runs",
                  "size_kb", "commits_14d", "code_score", "up_to_code"}
        estate = self.estate()
        for b in estate["buildings"]:
            self.assertEqual(set(b), fields, b.get("postcode"))
        self.assertEqual({b["postcode"] for b in estate["buildings"]},
                         {b["postcode"] for b in REGISTER["buildings"]})

    def test_islands_carry_a_kind_and_the_land_islands_are_the_five(self):
        estate = self.estate()
        kinds = {i["id"]: i["kind"] for i in estate["islands"]}
        land = {i for i, k in kinds.items() if k == "land"}
        self.assertEqual(land, {"agency", "engine", "library", "halo", "home"})
        self.assertEqual(kinds["territory"], "rock")

    def test_every_compound_lists_buildings_the_scene_can_draw(self):
        # the scene places buildings from their compound, so every building must name a compound the scene lays out
        estate = self.estate()
        islands = {i["id"] for i in estate["islands"]}
        compounds = {c["id"]: c for c in estate["compounds"]}
        for c in estate["compounds"]:
            self.assertIn(c["island"], islands, c["id"])
        for b in estate["buildings"]:
            self.assertIn(b["compound"], compounds, b["postcode"])
            self.assertEqual(compounds[b["compound"]]["island"], b["island"], b["postcode"])


if __name__ == "__main__":
    unittest.main()
