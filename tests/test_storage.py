"""Synthetic in-memory tests only: no scans, state writes, or external commands.

  python3 -B -m unittest discover -s tests -p test_storage.py -v
"""
import contextlib
import copy
import datetime as dt
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import storage

NOW = 1_791_244_800


def at(offset=0):
    return dt.datetime.fromtimestamp(NOW + offset, dt.timezone.utc).isoformat()


class StorageTest(unittest.TestCase):
    def setUp(self):
        # The fixtures below are sized around a 100 GB reserve; they test the decision logic, not the constant.
        self._reserve, storage.RESERVE_BYTES = storage.RESERVE_BYTES, 100_000_000_000
        self.addCleanup(setattr, storage, "RESERVE_BYTES", self._reserve)
        self.fleet = {"at": at(), "machines": {"mini": {
            "machine": "mini", "os": "Darwin", "state": "ok", "at": at(-10),
            "disk_free_gb": 120, "disk_total_gb": 460}}}
        self.register = {"generated_at": at(-60), "buildings": [{
            "postcode": "synthetic-producer", "provenance": "ours", "seat": "FIXTURE",
            "state_land": False, "island": "engine", "lifecycle": "active"}]}
        self.request = {"machine": "mini", "category": "runtime", "postcode": "synthetic-producer",
                        "peak_bytes": 2_000_000_000, "growth_buffer_bytes": 5_000_000_000}

    def check(self, **changes):
        return storage.check(self.fleet, self.register, now=NOW, **dict(self.request, **changes))

    def assert_hold(self, result, reason):
        self.assertEqual((result["decision"], result["action"], result["reason"]),
                         ("HOLD", "defer-future-allocation", reason))

    def test_reserve_is_the_pause_line_not_the_cleaning_target(self):
        # Shaan, 21 Oct: work never waits on a made-up floor; only the 15 GiB danger line holds an allocation.
        self.assertEqual(self._reserve, 15 * storage.GIB)

    def test_all_allowlisted_allocation_roles_use_registered_producer(self):
        for category in storage.CATEGORIES:
            with self.subTest(category=category):
                result = self.check(category=category)
                self.assertEqual(result["decision"], "PASS")
                self.assertEqual(result["category"], category)
                self.assertEqual(result["provenance"]["kind"], "ours")

    def test_exact_capacity_boundary_and_transient_peak(self):
        available = 120 * 2 ** 30 - 100_000_000_000 - 5_000_000_000
        self.assertEqual(self.check(peak_bytes=available)["headroom_bytes"], 0)
        self.assertEqual(self.check(peak_bytes=available)["decision"], "PASS")
        self.assert_hold(self.check(peak_bytes=available + 1), "capacity-reserve")
        self.assertEqual(self.check()["snapshot"]["free_bytes_lower_bound"], 128_849_018_880)

    def test_task_scale_low_capacity_holds_both_machines(self):
        # Existing fleet rounds down: 72.7 decimal GB -> 67 GiB; 10.4 GB -> 9 GiB.
        for machine, gib in (("mini", 67), ("laptop", 9)):
            with self.subTest(machine=machine):
                self.fleet["machines"] = {machine: {"machine": machine, "state": "ok", "os": "Darwin",
                    "at": at(-10), "disk_free_gb": gib, "disk_total_gb": 460}}
                self.assert_hold(self.check(machine=machine), "capacity-reserve")

    def test_machine_sample_freshness_not_global_timestamp(self):
        for offset in (-301, 1):
            with self.subTest(offset=offset):
                self.fleet["machines"]["mini"]["at"] = at(offset)
                self.assert_hold(self.check(), "record-stale-or-future")

    def test_timezone_offsets_and_freshness_boundary(self):
        self.fleet["machines"]["mini"]["at"] = dt.datetime.fromtimestamp(
            NOW - 300, dt.timezone(dt.timedelta(hours=7))).isoformat()
        self.assertEqual(self.check()["decision"], "PASS")

    def test_bad_timestamp_and_unavailable_capacity_fail_closed(self):
        row = self.fleet["machines"]["mini"]
        for value in (None, "bad", at()[:19], 123):
            with self.subTest(value=value):
                row["at"] = value
                self.assert_hold(self.check(), "timestamp-invalid")
        row["at"] = at(-10)
        row["state"] = "timeout"
        row["last_ok"] = at()
        self.assert_hold(self.check(), "capacity-unavailable")
        row["state"], row["machine"] = "ok", "laptop"
        self.assert_hold(self.check(), "capacity-unavailable")

    def test_malformed_sizes_cannot_approve(self):
        row = self.fleet["machines"]["mini"]
        for value in (None, -1, True, 120.0, "120", float("nan"), float("inf"), 461):
            with self.subTest(value=value):
                row["disk_free_gb"] = value
                self.assert_hold(self.check(), "capacity-record-invalid")
        row["disk_free_gb"] = 120
        for key in ("peak_bytes", "growth_buffer_bytes"):
            for value in (0, -1, True, 2.0, "2", 2 ** 63):
                with self.subTest(key=key, value=value):
                    self.assert_hold(self.check(**{key: value}), "allocation-budget-invalid")

    def test_unknown_category_machine_and_producer_hold(self):
        self.assert_hold(self.check(category="cache"), "allocation-category-invalid")
        self.assert_hold(self.check(machine="other"), "allocation-category-invalid")
        self.assert_hold(self.check(postcode="unregistered"), "producer-missing-or-ambiguous")
        self.register["buildings"] *= 2
        self.assert_hold(self.check(), "producer-missing-or-ambiguous")

    def test_provenance_keeper_and_protected_producers_hold(self):
        original = copy.deepcopy(self.register["buildings"][0])
        cases = [("provenance", "foreign", "producer-provenance-held"),
                 ("provenance", "client", "producer-provenance-held"),
                 ("provenance", None, "producer-provenance-held"),
                 ("seat", None, "producer-keeper-missing"),
                 ("seat", " ", "producer-keeper-missing"),
                 ("state_land", True, "producer-keeper-missing"),
                 ("state_land", None, "producer-keeper-missing"),
                 ("island", "home", "producer-protected-or-unclassified"),
                 ("island", None, "producer-protected-or-unclassified"),
                 ("partner", "halo", "producer-protected-or-unclassified"),
                 ("client_of", "fixture", "producer-protected-or-unclassified"),
                 ("lifecycle", None, "producer-lifecycle-unknown")]
        for key, value, reason in cases:
            with self.subTest(key=key, value=value):
                self.register["buildings"] = [dict(original, **{key: value})]
                self.assert_hold(self.check(), reason)

    def test_adopted_needs_upstream_and_archived_needs_archive_role(self):
        building = self.register["buildings"][0]
        building["provenance"] = "adopted"
        self.assert_hold(self.check(), "producer-upstream-missing")
        building["upstream"] = "https://example.invalid/fixture"
        self.assertEqual(self.check()["decision"], "PASS")
        building["lifecycle"] = "archived"
        self.assert_hold(self.check(), "producer-archived")
        self.assertEqual(self.check(category="archive")["decision"], "PASS")

    def test_stale_missing_and_malformed_register_hold(self):
        for value, reason in ((at(-86401), "record-stale-or-future"), (None, "timestamp-invalid")):
            self.register["generated_at"] = value
            self.assert_hold(self.check(), reason)
        self.register["generated_at"] = at()
        for value in (None, {}, [None]):
            self.register["buildings"] = value
            self.assert_hold(self.check(), "register-invalid")

    def previous(self, free=145, offset=-610):
        old = copy.deepcopy(self.fleet)
        old["machines"]["mini"].update(disk_free_gb=free, at=at(offset))
        return storage.check(old, self.register, now=NOW + offset, **self.request)

    def test_regrowth_raises_buffer_and_changes_pass_to_hold(self):
        before = self.check()
        result = self.check(previous=self.previous())
        self.assertEqual(before["decision"], "PASS")
        self.assert_hold(result, "capacity-reserve")
        self.assertEqual(result["free_change_bytes"], -25 * storage.GIB)
        self.assertEqual(result["effective_growth_buffer_bytes"], 26 * storage.GIB - 1)

    def test_reclaimed_space_never_weakens_declared_growth_buffer(self):
        for free in (100, 120):
            with self.subTest(free=free):
                result = self.check(previous=self.previous(free=free))
                self.assertEqual(result["decision"], "PASS")
                self.assertEqual(result["effective_growth_buffer_bytes"], 5_000_000_000)

    def test_incomparable_stale_or_reversed_previous_holds(self):
        prior = self.previous()
        cases = [("machine", "laptop", "previous-not-comparable"),
                 ("os", "Linux", "previous-not-comparable"),
                 ("total_bytes_lower_bound", 900 * storage.GIB, "previous-not-comparable"),
                 ("free_bytes_lower_bound", -1, "previous-invalid"),
                 ("free_bytes_lower_bound", 1, "previous-invalid"),
                 ("at", at(-3601), "record-stale-or-future"),
                 ("at", at(-10), "previous-not-earlier"),
                 ("at", at(-5), "previous-not-earlier")]
        for key, value, reason in cases:
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(prior)
                changed["snapshot"][key] = value
                self.assert_hold(self.check(previous=changed), reason)

    def cli(self, argv=None, previous=None, blobs=None):
        blobs = blobs or {"fleet.json": json.dumps(self.fleet).encode(),
                          "register.json": json.dumps(self.register).encode()}
        reads, output = [], io.StringIO()

        def read_only(path, mode):
            self.assertEqual(mode, "rb")
            self.assertIn(path, [storage.record_path(storage.REPO, "machines", name) for name in blobs])
            reads.append(path)
            return io.BytesIO(blobs[os.path.basename(path)])

        argv = argv if argv is not None else [
            "--machine", "mini", "--category", "runtime", "--postcode", "synthetic-producer",
            "--peak-bytes", "2000000000", "--growth-buffer-bytes", "5000000000"]
        stdin = mock.Mock(buffer=io.BytesIO(previous or b""))
        with mock.patch("builtins.open", side_effect=read_only), mock.patch("subprocess.run", side_effect=AssertionError), \
                mock.patch("os.scandir", side_effect=AssertionError), mock.patch.object(storage.time, "time", return_value=NOW), \
                mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(output):
            code = storage.main(argv + (["--previous-stdin"] if previous is not None else []))
        return code, json.loads(output.getvalue()), reads

    def test_cli_exit_codes_and_only_two_read_only_record_opens(self):
        before = copy.deepcopy((self.fleet, self.register))
        code, result, reads = self.cli()
        self.assertEqual((code, result["decision"], len(reads)), (0, "PASS", 2))
        self.assertEqual((self.fleet, self.register), before)
        self.fleet["machines"]["mini"]["disk_free_gb"] = 9
        code, result, _ = self.cli()
        self.assertEqual((code, result["decision"]), (1, "HOLD"))

    def test_cli_reuses_one_previous_result_without_accumulating_history(self):
        previous = json.dumps(self.previous()).encode()
        code, result, _ = self.cli(previous=previous)
        self.assertEqual((code, result["reason"]), (1, "capacity-reserve"))
        self.assertLess(len(json.dumps(result).encode()), storage.PREVIOUS_LIMIT)
        self.assertNotIn("history", result)
        self.assertNotIn("previous", result)

    def test_private_fields_and_errors_are_not_echoed(self):
        marker = "PRIVATE-FIXTURE-PATH-AND-TOKEN"
        self.fleet["machines"]["mini"].update(top=[{"args": marker}], hostname=marker)
        self.register["buildings"][0].update(path=marker, description=marker, seat=marker)
        _, result, _ = self.cli()
        self.assertNotIn(marker, json.dumps(result))
        for argv in (["--bad", marker], ["--machine", marker]):
            code, result, reads = self.cli(argv=argv)
            self.assertEqual((code, reads), (2, []))
            self.assertNotIn(marker, json.dumps(result))
        with mock.patch("builtins.open", side_effect=OSError(marker)), contextlib.redirect_stdout(io.StringIO()) as output:
            code = storage.main(["--machine", "mini", "--category", "source", "--postcode", marker,
                                 "--peak-bytes", "1", "--growth-buffer-bytes", "1"])
        self.assertEqual(code, 2)
        self.assertNotIn(marker, output.getvalue())

    def test_bounded_malformed_record_and_previous_input_fail_closed(self):
        for blob in (b"bad-json-secret", b"[]", b'{"bad":', b"x" * (storage.RECORD_LIMIT + 1)):
            with self.subTest(size=len(blob)):
                code, result, _ = self.cli(blobs={"fleet.json": blob})
                self.assertEqual((code, result["decision"]), (2, "HOLD"))
        for blob in (b"bad-json-secret", b"[]", b"x" * (storage.PREVIOUS_LIMIT + 1)):
            with self.subTest(size=len(blob)):
                code, result, reads = self.cli(previous=blob)
                self.assertEqual((code, result["decision"], reads), (2, "HOLD", []))


if __name__ == "__main__":
    unittest.main()
