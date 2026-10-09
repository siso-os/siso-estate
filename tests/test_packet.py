#!/usr/bin/env python3
"""Tests for tools/packet.py: a temp building, a fake code.json, and the packet it produces.

  python3 -m unittest tests/test_packet.py -v

Everything outside tools/packet.py itself is injected through ESTATE_WS / ESTATE_CODE / ESTATE_INBOX /
ESTATE_MODEL, so these tests read nothing from the real estate.
"""
import contextlib, io, json, os, sys, tempfile, unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import packet  # noqa: E402

DOOR = "".join("Door line %d: what this building is and how it is worked.\n" % i for i in range(1, 121))
RULES = ("## 12. How an agent works in the estate\n\n"
         + "".join("%d. Rule %d, with a continuation that belongs to it.\n   and its second line.\n" % (i, i)
                   for i in range(1, 11))
         + "\n## 13. How the last two days fit\n\nnot part of the rules\n")
STATE = ("# Handoff\n\n## State (first, old)\n- an older state that must not be in the packet\n\n"
         "## State (second, now)\n- the last state, which is the one that counts\n- and its second line\n")
ROW = {"path": "SISO_Agents/fake-building", "district": "SISO_Agents",
       "origin": "https://github.com/sisodias/fake-building.git", "owner": "own", "seat": "FAKE",
       "state_land": False, "lifecycle": "active", "idle_days": 1.5, "score": 0.75, "up_to_code": False,
       "failed": ["pushed", "named"]}


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class World:
    """A throwaway workspace: one building with a door, a handoff, memory and tasks, plus the code and inbox."""

    def __init__(self, tmp):
        self.ws = os.path.join(tmp, "ws")
        b = os.path.join(self.ws, "SISO_Agents", "fake-building")
        put(os.path.join(b, "AGENTS.md"), DOOR)
        put(os.path.join(b, ".agents", "HANDOFF.md"), STATE)
        put(os.path.join(b, ".agents", "memory", "MEMORY.md"), "# Memory — fake\n\n- [One fact](one.md) — the hook\n")
        put(os.path.join(b, ".agents", "tasks", "in_progress", "TASK-9001", "task.json"),
            json.dumps({"id": "TASK-9001", "title": "Ship the thing", "status": "in_progress"}))
        put(os.path.join(b, ".agents", "tasks", "completed", "TASK-9002", "task.json"),
            json.dumps({"id": "TASK-9002", "title": "Already done", "status": "completed"}))
        self.code = os.path.join(tmp, "code.json")
        os.makedirs(os.path.join(self.ws, "HALO_Agency", "halo-building"))  # a plot on GitHub only
        rows = [dict(ROW), dict(ROW, path="SISO_Agents/twin"), dict(ROW, path="HALO_Agency/twin"),
                dict(ROW, path="HALO_Agency/halo-building", seat="HALO")]
        put(self.code, json.dumps({"results": rows}))
        self.inbox = os.path.join(tmp, "INBOX.md")
        put(self.inbox, "prose that mentions no building at all\n"
                        "- [ ] 2026-09-25 someone: fake-building: a stray copy (SISO_Agents/fake-building/x)\n")
        self.model = os.path.join(tmp, "MODEL.md")
        put(self.model, RULES)

    def run(self, *argv):
        env = {"ESTATE_WS": self.ws, "ESTATE_CODE": self.code,
               "ESTATE_INBOX": self.inbox, "ESTATE_MODEL": self.model}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = packet.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class PacketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = World(self.tmp.name)

    def section(self, out, name):
        body = out.split("## %s — source: " % name, 1)[1]
        return body.split("\n", 1)[1].split("\n\n## ", 1)[0]

    def names(self, out):
        return [l.split(" — source: ")[0][3:] for l in out.split("\n")
                if l.startswith("## ") and " — source: " in l]

    def tokens(self, out):
        line = [l for l in out.strip().split("\n") if l.startswith("packet: ")][-1]
        return int(line.split()[1])

    def test_sections_present_and_in_model_order(self):
        code, out, _ = self.w.run("SISO_Agents/fake-building")
        self.assertEqual(code, 0)
        self.assertEqual(self.names(out),
                         ["Card", "Door", "Now", "Memory", "Tasks", "Letters", "Rules"])
        card = self.section(out, "Card")
        self.assertIn("island: engine", card)
        self.assertIn("law: SISO's", card)
        self.assertIn("failed rules: pushed, named", card)
        self.assertIn("Door line 120", out)
        self.assertEqual(self.section(out, "Door"), DOOR.strip())
        now = self.section(out, "Now")
        self.assertIn("second, now", now)
        self.assertNotIn("first, old", now)
        tasks = self.section(out, "Tasks")
        self.assertIn("TASK-9001 — Ship the thing", tasks)
        self.assertNotIn("TASK-9002", tasks)
        self.assertIn("fake-building: a stray copy", self.section(out, "Letters"))
        self.assertNotIn("prose that mentions no building", out)
        rules = self.section(out, "Rules")
        self.assertEqual(len([l for l in rules.split("\n") if l[:2].rstrip(".").isdigit() and ". " in l
                              and l.split(". ", 1)[0].isdigit()]), 10)
        self.assertIn("10. Rule 10", rules)
        self.assertNotIn("not part of the rules", rules)

    def test_budget_respected_and_longest_cut_first(self):
        code, out, _ = self.w.run("SISO_Agents/fake-building", "--budget", "700")
        self.assertEqual(code, 0)
        self.assertLessEqual(self.tokens(out), 700)
        self.assertIn("packet: %d tokens (budget 700)" % self.tokens(out), out)
        # the door is the longest section, so it is the one cut, and its marker names its full file
        self.assertIn("[... truncated, full file: SISO_Agents/fake-building/AGENTS.md]", out)
        self.assertNotIn("Door line 120", out)
        # the card and the ten rules are never cut
        self.assertIn("failed rules: pushed, named", out)
        self.assertIn("10. Rule 10", out)

    def test_json_output(self):
        code, out, _ = self.w.run("SISO_Agents/fake-building", "--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["building"], "SISO_Agents/fake-building")
        self.assertEqual(d["island"], "engine")
        self.assertEqual(d["tokens"], sum(s["tokens"] for s in d["sections"]))
        self.assertTrue(d["sections"][0]["text"].startswith("path: SISO_Agents/fake-building"))

    def test_name_ambiguous_exits_2(self):
        code, out, err = self.w.run("twin")
        self.assertEqual(code, 2)
        self.assertIn("ambiguous", err)
        self.assertIn("SISO_Agents/twin", err)
        self.assertIn("HALO_Agency/twin", err)

    def test_unknown_target_exits_1(self):
        code, _, err = self.w.run("nothing-here")
        self.assertEqual(code, 1)
        self.assertIn("no building", err)

    def test_path_resolution_and_halo_law(self):
        for arg in (os.path.join(self.w.ws, "HALO_Agency", "halo-building"), "HALO_Agency/halo-building"):
            code, out, _ = self.w.run(arg)
            self.assertEqual((code, self.names(out)), (0, ["Card", "Rules"]))
            card = self.section(out, "Card")
            self.assertIn("island: halo", card)
            self.assertIn("law: Cam's code stays in camronkellman/halocrm; never copy it off", card)


if __name__ == "__main__":
    unittest.main()
