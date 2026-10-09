#!/usr/bin/env python3
"""fleet.py reads a probe right, names why a box failed, and places headless work off the laptop first.

  python3 -m unittest tests/test_fleet.py -v
"""
import os, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import fleet

LINUX = """## ok
os=Linux
host=vmi3567061
cpus=4
load=0.30 1.35 1.35
mem_total_mb=7941
mem_avail_mb=3800
disk_total_gb=95
disk_free_gb=26
boot=1790371935
containers=6
## procs
 200  4368 ps
 118 202828 codex
 44.4  1804 sh
## agents
codex=2
omp=7
"""

PLAN = {"job_kinds": {"headless-agent": {"cores": 0.5, "ram_gb": 0.6}, "streaming-service": {}},
        "machines": {
            "laptop": {"fleet": {"probe": "local", "fallback": ["headless-agent"], "reserve": {"cores": 4, "ram_gb": 2}}},
            "vps": {"fleet": {"probe": "vps", "for": ["headless-agent"]}},
            "pool": {"fleet": {"probe": "pool", "fallback": ["headless-agent"]}},
            "mini": {"fleet": {"probe": "mini", "for": ["headless-agent"]}},
            "client": {"fleet": {"probe": "client", "client": True, "for": ["streaming-service"],
                                 "never": ["headless-agent"]}}}}


def ok(cpus, load, avail):
    return {"state": "ok", "cpus": cpus, "load": [load], "free_cores": max(0, cpus - load), "mem_avail_gb": avail}


RECORD = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "machines": {
    "laptop": ok(8, 2, 6), "vps": ok(4, 2, 1.3), "pool": ok(2, 0, 3),
    "mini": {"state": "exec-refused", "detail": "takes our key but refuses to run any command"},
    "client": ok(6, 7, 6)}}


class Probe(unittest.TestCase):
    def test_reads_a_linux_probe(self):
        r = fleet.parse(LINUX)
        self.assertEqual((r["cpus"], r["load"][0], r["free_cores"]), (4, 0.3, 3.7))
        self.assertEqual((r["mem_avail_gb"], r["disk_free_gb"], r["containers"]), (3.7, 26, 6))
        self.assertEqual(r["agents"], {"codex": 2, "omp": 7})
        self.assertEqual([p["name"] for p in r["top"]], ["codex"])      # ps and sh are the probe itself

    def test_names_why_a_box_failed(self):
        self.assertEqual(fleet.why_failed("exec request failed on channel 0", 255)[0], "exec-refused")
        self.assertEqual(fleet.why_failed("root@1.2.3.4: Permission denied (publickey,password).", 255)[0], "key-refused")
        self.assertEqual(fleet.why_failed("@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@", 255)[0], "host-key")
        self.assertEqual(fleet.why_failed("No ED25519 host key is known for x and you have requested strict checking.", 255)[0],
                         "host-key")
        self.assertEqual(fleet.why_failed("ssh: connect to host 23.88.117.71 port 22: Operation timed out", 255)[0],
                         "unreachable")


class Filling(unittest.TestCase):
    def rec(self, n):
        return {"machine": "mini", "history": [["2000-01-01T00:00", 1, 1, []]],
                "procs": {"user": "shaansisodia", "user_count": n, "user_limit": 2666, "total": n + 100,
                          "user_top": [["herdr", 1900], ["zsh", 200]]}}

    def test_warns_before_the_limit_and_names_the_culprit(self):
        msg = fleet.filling(self.rec(2300))
        self.assertIn("2300 of the 2666", msg)
        self.assertIn("herdr 1900", msg)

    def test_quiet_when_there_is_room(self):
        self.assertIsNone(fleet.filling(self.rec(400)))
        self.assertIsNone(fleet.filling({"machine": "vps", "procs": {"user_count": 190, "user_limit": None}}))

    def test_reads_process_counts(self):
        r = fleet.parse(LINUX.replace("## procs", "user=root\nprocs_total=266\nprocs_user=194\nprocs_limit=31000\n"
                                                  "## user_top\n     13 node <- node\n     10 codex <- node\n      2 ps <- sh\n## procs", 1))
        self.assertEqual((r["procs"]["user_count"], r["procs"]["user_limit"], r["procs"]["total"]), (194, 31000, 266))
        self.assertEqual(r["procs"]["user_top"], [["node <- node", 13], ["codex <- node", 10]])


HEALTH = {"probe": "mini", "health": {"disk_free_gb": {"clean_below": 25, "alert_below": 12},
                                     "mem_avail_gb": {"alert_below": 1.5}, "cleanup": ["npm cache clean --force"],
                                     "need_after_min": 30, "need": "Restart the mini"}}


class Health(unittest.TestCase):
    def run_health(self, r, after_disk=None):
        calls = {"cleanup": 0, "need": [], "post": []}
        with mock.patch.object(fleet, "cleanup", side_effect=lambda *a: calls.__setitem__("cleanup", calls["cleanup"] + 1)), \
             mock.patch.object(fleet, "need", side_effect=lambda w, *a: calls["need"].append(w) or True), \
             mock.patch.object(fleet, "post", side_effect=lambda t, b: calls["post"].append(t) or True), \
             mock.patch.object(fleet, "plan", return_value={"machines": {"mini": {"fleet": HEALTH}}}), \
             mock.patch.object(fleet, "probe_one", return_value={"disk_free_gb": after_disk}):
            changed = fleet.health("mini", r, HEALTH)
        return changed, calls

    def test_low_disk_runs_the_safe_cleanups_and_asks_only_if_still_low(self):
        _, c = self.run_health({"state": "ok", "disk_free_gb": 20, "mem_avail_gb": 6}, after_disk=22)
        self.assertEqual((c["cleanup"], c["need"]), (1, []))
        _, c = self.run_health({"state": "ok", "disk_free_gb": 9, "mem_avail_gb": 6}, after_disk=10)
        self.assertEqual(c["cleanup"], 1)
        self.assertEqual(len(c["need"]), 1)

    def test_ram_card_only_after_two_low_probes(self):
        r = {"state": "ok", "disk_free_gb": 40, "mem_avail_gb": 1.0, "top": []}
        self.assertEqual(self.run_health(r)[1]["post"], [])
        self.assertEqual(self.run_health(r)[1]["post"], ["mini: RAM low"])

    def test_down_past_the_window_asks_shaan_once_a_day(self):
        r = {"state": "exec-refused", "down_since": "2026-09-26T05:30:00+0700"}
        changed, c = self.run_health(r)
        self.assertTrue(changed)
        self.assertEqual(c["need"], ["Restart the mini"])
        self.assertEqual(self.run_health(r)[1]["need"], [])           # need_at is recent now

    def test_a_short_blip_asks_nobody(self):
        r = {"state": "timeout", "down_since": fleet.stamp()}
        self.assertEqual(self.run_health(r)[1]["need"], [])

    def test_healthy_box_does_nothing(self):
        changed, c = self.run_health({"state": "ok", "disk_free_gb": 40, "mem_avail_gb": 6})
        self.assertEqual((c["cleanup"], c["need"], c["post"]), (0, [], []))


class Pick(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(fleet, "plan", return_value=PLAN),
                  mock.patch.object(fleet, "load", return_value=RECORD)]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_headless_goes_to_its_box_then_off_laptop_then_laptop(self):
        out = fleet.pick("headless-agent", n=12)
        self.assertEqual(out["picked"], "vps")
        self.assertEqual([c["machine"] for c in out["candidates"]], ["vps", "pool", "laptop"])
        self.assertEqual(out["spread"], [{"machine": "vps", "jobs": 2}, {"machine": "pool", "jobs": 4},
                                         {"machine": "laptop", "jobs": 4}])
        self.assertEqual(out["unplaced"], 2)

    def test_the_laptop_keeps_its_reserve(self):
        # 6 free cores - 4 kept = 2 -> 4 jobs; 6 GB - 2 kept = 4 GB -> 6 jobs; the smaller wins
        self.assertEqual(fleet.capacity(RECORD["machines"]["laptop"], PLAN["job_kinds"]["headless-agent"],
                                        PLAN["machines"]["laptop"]["fleet"]["reserve"]), 4)

    def test_a_down_box_is_refused_with_its_reason(self):
        out = fleet.pick("headless-agent")
        self.assertTrue(any(r.startswith("mini: exec-refused") for r in out["refused"]))

    def test_a_client_box_takes_only_its_own_kind(self):
        self.assertNotIn("client", [c["machine"] for c in fleet.pick("headless-agent")["candidates"]])
        self.assertEqual(fleet.pick("streaming-service")["picked"], "client")


if __name__ == "__main__":
    unittest.main()
