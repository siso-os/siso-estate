#!/usr/bin/env python3
"""Read-only preflight for ONE future allocation; never runs an allocator or a scan.

  storage.py --machine mini --category runtime --postcode siso-estate \
      --peak-bytes 2000000000 --growth-buffer-bytes 5000000000 [--previous-stdin]

Reads only machines/fleet.json and machines/register.json beside this tool. The
postcode identifies the allocation's producer/keeper for all four categories,
not a destination or permission to copy its data. Peak includes transient copies.
The fixed reserve is 15 GiB (storage-guard HOLD_GIB); 60/100 GiB are cleaning targets, not reserves. The required growth
buffer protects concurrent writes (including swap); it is not reclaimable space.
An optional previous stdout JSON on stdin raises that buffer to cover observed
free-space loss. No history is written. Exit 0 = preflight PASS, 1 = HOLD, 2 = bad
input/record. PASS is not a reservation, restore proof, or deployment permission.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
from estate_state import record_path

MACHINES = ("laptop", "mini")
CATEGORIES = ("source", "data", "runtime", "archive")
GIB = 2 ** 30
RESERVE_BYTES = 15 * GIB  # same pause line as storage-guard HOLD_GIB: 5 GiB never-write reserve + 10 GiB build growth
CAPACITY_AGE = 300
REGISTER_AGE = 86400
PREVIOUS_AGE = 3600
RECORD_LIMIT = 8 * 1024 * 1024
PREVIOUS_LIMIT = 4096


class Hold(ValueError):
    """Only static reason codes may cross the output boundary."""


def require(ok, reason):
    if not ok:
        raise Hold(reason)


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 2 ** 63 - 1


def stamp(value):
    require(isinstance(value, str) and len(value) <= 32, "timestamp-invalid")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.utcoffset() is not None, "timestamp-invalid")
        return parsed.timestamp()
    except (ValueError, OverflowError):
        raise Hold("timestamp-invalid") from None


def fresh(value, now, max_age):
    age = now - stamp(value)
    require(0 <= age <= max_age, "record-stale-or-future")


def read_json(stream, limit):
    raw = stream.read(limit + 1)
    require(len(raw) <= limit, "record-too-large")
    value = json.loads(raw)
    require(isinstance(value, dict), "record-shape-invalid")
    return value


def held(reason):
    return {"schema": 1, "decision": "HOLD", "action": "defer-future-allocation", "reason": reason}


def check(fleet, register, *, machine, category, postcode, peak_bytes,
          growth_buffer_bytes, previous=None, now=None):
    now = time.time() if now is None else now
    out = held("unchecked")
    try:
        require(machine in MACHINES and category in CATEGORIES, "allocation-category-invalid")
        require(integer(peak_bytes, 1) and integer(growth_buffer_bytes, 1), "allocation-budget-invalid")
        require(isinstance(postcode, str) and 0 < len(postcode) <= 200, "producer-invalid")
        out.update(machine=machine, category=category, peak_bytes=peak_bytes,
                   reserve_bytes=RESERVE_BYTES, declared_growth_buffer_bytes=growth_buffer_bytes)

        require(isinstance(fleet, dict) and isinstance(fleet.get("machines"), dict), "capacity-record-invalid")
        row = fleet["machines"].get(machine)
        require(isinstance(row, dict) and row.get("machine") == machine and row.get("state") == "ok",
                "capacity-unavailable")
        fresh(row.get("at"), now, CAPACITY_AGE)  # NOT fleet's global timestamp / last_ok
        free, total = row.get("disk_free_gb"), row.get("disk_total_gb")
        require(integer(free) and integer(total, 1) and free <= total, "capacity-record-invalid")
        require(row.get("os") in ("Darwin", "Linux"), "capacity-volume-unknown")
        # fleet.py PROBE uses int(df KiB / 1048576): whole GiB, despite its gb field names.
        snapshot = {"machine": machine, "os": row["os"],
                    "at": dt.datetime.fromtimestamp(stamp(row["at"]), dt.timezone.utc).isoformat(),
                    "free_bytes_lower_bound": free * GIB, "total_bytes_lower_bound": total * GIB}
        out["snapshot"] = snapshot
        growth = 0
        out["free_change_bytes"] = None
        if previous is not None:
            require(isinstance(previous, dict) and type(previous.get("schema")) is int
                    and previous["schema"] == 1, "previous-invalid")
            old = previous.get("snapshot")
            require(isinstance(old, dict) and old.get("machine") == machine
                    and old.get("os") == snapshot["os"]
                    and type(old.get("total_bytes_lower_bound")) is int
                    and old["total_bytes_lower_bound"] == total * GIB, "previous-not-comparable")
            old_free = old.get("free_bytes_lower_bound")
            require(integer(old_free) and old_free % GIB == 0 and old_free <= total * GIB, "previous-invalid")
            fresh(old.get("at"), now, PREVIOUS_AGE)
            require(stamp(old["at"]) < stamp(snapshot["at"]), "previous-not-earlier")
            change = free * GIB - old_free
            out["free_change_bytes"] = change  # Difference of rounded samples, not attribution.
            growth = max(0, -change + GIB - 1)  # Upper bound including <1 GiB rounding uncertainty.
        buffer = max(growth_buffer_bytes, growth)
        out.update(observed_loss_upper_bound_bytes=growth if previous is not None else None,
                   effective_growth_buffer_bytes=buffer,
                   remaining_after_allocation_bytes=free * GIB - peak_bytes,
                   headroom_bytes=free * GIB - peak_bytes - RESERVE_BYTES - buffer)

        require(isinstance(register, dict) and isinstance(register.get("buildings"), list), "register-invalid")
        fresh(register.get("generated_at"), now, REGISTER_AGE)
        buildings = register["buildings"]
        require(all(isinstance(b, dict) for b in buildings), "register-invalid")
        matches = [b for b in buildings if b.get("postcode") == postcode]
        require(len(matches) == 1, "producer-missing-or-ambiguous")
        building = matches[0]
        require(building.get("provenance") in ("ours", "adopted"), "producer-provenance-held")
        require(building.get("island") in ("agency", "engine", "library", "territory", "utilities", "vault")
                and building.get("partner") != "halo" and not building.get("client_of")
                and not building.get("client"), "producer-protected-or-unclassified")
        require(isinstance(building.get("seat"), str) and bool(building["seat"].strip())
                and building.get("state_land") is False, "producer-keeper-missing")
        if building["provenance"] == "adopted":
            require(isinstance(building.get("upstream"), str) and bool(building["upstream"].strip()),
                    "producer-upstream-missing")
        require(building.get("lifecycle") in ("active", "warm", "unscored", "dormant", "dark", "archived"),
                "producer-lifecycle-unknown")
        require(category == "archive" or building["lifecycle"] != "archived", "producer-archived")
        out["provenance"] = {"basis": "estate-register", "producer_matches": 1,
                             "kind": building["provenance"], "keeper_recorded": True}
        require(out["headroom_bytes"] >= 0, "capacity-reserve")
        out.update(decision="PASS", action="future-allocation-preflight-passed", reason="capacity-and-producer")
    except Hold as exc:
        out["reason"] = str(exc)
    return out


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse otherwise echoes arbitrary arguments (including paths) into logs.
        raise Hold("arguments-invalid")


def main(argv=None):
    try:
        ap = Parser(description=__doc__)
        ap.add_argument("--machine", choices=MACHINES, required=True)
        ap.add_argument("--category", choices=CATEGORIES, required=True)
        ap.add_argument("--postcode", required=True)
        ap.add_argument("--peak-bytes", type=int, required=True)
        ap.add_argument("--growth-buffer-bytes", type=int, required=True)
        ap.add_argument("--previous-stdin", action="store_true")
        args = vars(ap.parse_args(argv))
        previous = read_json(sys.stdin.buffer, PREVIOUS_LIMIT) if args.pop("previous_stdin") else None
        records = []
        for name in ("fleet.json", "register.json"):
            with open(record_path(REPO, "machines", name), "rb") as stream:
                records.append(read_json(stream, RECORD_LIMIT))
        out = check(*records, **args, previous=previous)
        code = 0 if out["decision"] == "PASS" else 1
    except Hold as exc:
        out, code = held(str(exc)), 2
    except (OSError, ValueError, RecursionError):
        out, code = held("record-unreadable-or-invalid"), 2
    print(json.dumps(out, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
