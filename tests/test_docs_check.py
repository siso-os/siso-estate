#!/usr/bin/env python3
"""docs_check.py against a thrown-together estate: the four verdicts a door's citation can get, and nothing else.

  python3 -m unittest tests/test_docs_check.py -v

Everything is a temp directory: the workspace, the main checkout's register, the moves and the retired-path list.
"""
import json, os, shutil, tempfile, unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import docs_check


class DocsCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="docs-check-")
        self.ws = os.path.join(self.tmp, "ws")
        self.main = os.path.join(self.ws, "SISO_Agents", "siso-estate")
        self.machine = "laptop"
        for d in ("machines/" + self.machine, "machines/github", "plan/link-retire"):
            os.makedirs(os.path.join(self.main, d), exist_ok=True)
        self.building = os.path.join(self.ws, "SISO_Agency", "apps", "thing")
        os.makedirs(os.path.join(self.building, "kept"), exist_ok=True)
        self.wrote(os.path.join(self.building, "kept", "file.md"), "x")
        os.makedirs(os.path.join(self.ws, "SISO_Agency", "apps", "moved-to", "deep"), exist_ok=True)
        self.wrote(os.path.join(self.ws, "SISO_Agency", "apps", "moved-to", "deep", "file.md"), "x")  # a repair must land on something real
        self.wrote(os.path.join(self.main, "machines", self.machine, "code.json"), json.dumps(
            {"results": [{"path": "SISO_Agency/apps/thing", "owner": "own"},
                         {"path": "SISO_Agency/apps/other", "owner": "own"},
                         {"path": "_reference/someone", "owner": "foreign"}]}))
        self.wrote(os.path.join(self.main, "machines", self.machine, "moves.jsonl"), "")
        self.wrote(os.path.join(self.main, "machines", "github", "sisodias.json"),
                   json.dumps([{"nameWithOwner": "sisodias/siso-city", "name": "siso-city"}]))
        self.wrote(os.path.join(self.main, "plan", "link-retire", "RETIRED.txt"),
                   "SISO_Agency/apps/old-thing -> SISO_Agency/apps/moved-to\n")
        # the door: a real path, a moved one, a missing one, a postcode, a git ref, a slash command, a command line
        self.wrote(os.path.join(self.building, "AGENTS.md"),
            "# door\n\n"
            "Keep `kept/file.md`; `" + os.path.join(self.ws, "SISO_Agency/apps/old-thing/deep/file.md") + "` moved.\n"
            "`zz-nothing/here.md` is gone. See `sisodias/oracle-streaming` and `origin/main`.\n"
            "Run `/compact`, then `bin/estate brief`.\n")

    @staticmethod
    def wrote(path, text):
        with open(path, "w") as f:
            f.write(text)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def record(self):
        return docs_check.record(self.machine, self.main, self.ws, self.tmp)

    def test_verdicts(self):
        d = self.record()
        self.assertEqual(d["doors"], 1)                    # the second building has no door
        self.assertEqual(d["cited"], 3)                    # kept/file.md, the moved path, the missing path
        self.assertEqual((d["resolved"], d["moved"], d["missing"]), (1, 1, 1))
        door = d["by_door"][0]
        self.assertEqual(door["path"], "SISO_Agency/apps/thing")
        self.assertEqual(door["moved"][0]["token"], os.path.join(self.ws, "SISO_Agency/apps/old-thing/deep/file.md"))
        self.assertEqual(door["moved"][0]["suggest"], os.path.join(self.ws, "SISO_Agency/apps/moved-to/deep/file.md"))
        self.assertEqual(door["missing"], ["zz-nothing/here.md"])

    def test_paths_relative_to_where_the_door_stands(self):
        # `kept/file.md` only exists under the building: a door-relative citation is not read from the workspace root
        self.assertEqual(docs_check.resolves("kept/file.md", self.building, self.ws), True)
        self.assertEqual(docs_check.resolves("zz-nothing/here.md", self.building, self.ws), False)

    def test_filter(self):
        with open(os.path.join(self.building, "AGENTS.md")) as f:
            cx = docs_check.citations(f.read(), {"sisodias"})
        self.assertEqual(cx, ["kept/file.md", os.path.join(self.ws, "SISO_Agency/apps/old-thing/deep/file.md"), "zz-nothing/here.md"])

    def test_not_this_disks_claim(self):
        # a package name, a name template and a VPS or runtime path are not citations of this disk
        text = "`@scope/pkg` `_archive/YYYY-MM-DD-subject/` `/opt/siso-x/AGENTS.md` `/tmp/x.lock` `kept/file.md`"
        self.assertEqual(docs_check.citations(text, {"sisodias"}), ["kept/file.md"])

    def test_ignored_runtime_state_is_not_missing(self):
        # a path the building's own .gitignore names is made by running it (inbox/, dist/): not a broken claim
        import subprocess
        subprocess.run(["git", "init", "-q", self.building], check=True)
        with open(os.path.join(self.building, ".gitignore"), "w") as f:
            f.write("inbox/\n")
        v = docs_check.door_verdict("`inbox/PAUSE` `zz-nothing/here.md`", self.building, {"sisodias"}, (), self.ws, self.tmp)
        self.assertEqual(v["missing"], ["zz-nothing/here.md"])
        self.assertEqual(v["runtime"], 1)

    def test_an_adopted_fork_is_checked_only_for_our_claims(self):
        text = "`src/components/ui` `~/nowhere-at-all/x.md`"
        v = docs_check.door_verdict(text, self.building, {"sisodias"}, (), self.ws, self.tmp, adopted=True)
        self.assertEqual(v["missing"], ["~/nowhere-at-all/x.md"])

    def test_names_and_caches_are_not_paths(self):
        text = "`engine/laptop-health` `~/.cache/siso-heavy/x.pids` `zz-nothing/here.md`"
        v = docs_check.door_verdict(text, self.building, {"sisodias"}, (), self.ws, self.tmp, names={"engine/laptop-health"})
        self.assertEqual(v["missing"], ["zz-nothing/here.md"])

    def test_a_moved_ground_is_not_a_move_of_something_else(self):
        v = docs_check.door_verdict("`SISO_Agency/apps/old-thingish/file.md`", self.building, {"sisodias"},
                                    docs_check.mappings(os.path.join(self.main, "machines", self.machine, "moves.jsonl"),
                                                        os.path.join(self.main, "plan", "link-retire", "RETIRED.txt"),
                                                        self.ws, self.tmp), self.ws, self.tmp)
        self.assertEqual((v["moved"], v["missing"]), ([], ["SISO_Agency/apps/old-thingish/file.md"]))


if __name__ == "__main__":
    unittest.main()
