"""Aggregate-only verified backup rates; logical and allocated bytes stay distinct."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def observations(path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        try:
            value = json.loads(line)
            if 'coverage' not in value:
                continue
            when = datetime.fromisoformat(value['at']).timestamp()
            # Fragment coverage includes sparse logical holes, so exclude it from transfer rate.
            size = sum(x['bytes'] or 0 for x in value['coverage'] if x['status'] == 'verified-bytes')
            rows.append((when, size))
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(set(rows))


def rate(rows, now=None):
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    elapsed = (now if now is not None else last[0]) - first[0]
    if elapsed <= 0 or last[1] < first[1]:
        return None
    return {'gb_per_hour': (last[1] - first[1]) * 3600 / elapsed / 1e9,
            'seconds': elapsed, 'verified_source_bytes': last[1] - first[1],
            'last_verified_at': datetime.fromtimestamp(last[0], timezone.utc).isoformat()}


def measure(run):
    run = Path(run)
    serial = observations(run / 'resume-20261017-0010.log')
    parallel = []
    for path in sorted(run.glob('parallel-*.log')):
        samples = observations(path)
        process_path = path.with_name(path.stem + '-process.json')
        if samples and process_path.exists():
            started = datetime.fromisoformat(json.loads(process_path.read_text())['started_at']).timestamp()
            # The first line is a saved coverage baseline, not a new completion.
            if samples[0][0] < started:
                samples[0] = (started, samples[0][1])
        parallel.extend(samples)
    parallel = sorted(set(parallel))
    current = parallel if parallel else serial
    now = datetime.now(timezone.utc)
    current_rate = rate(current[-7:], now.timestamp())
    eligible_path = run / 'eligible-allocated.json'
    eligible = json.loads(eligible_path.read_text()) if eligible_path.exists() else {}
    allocated = {}
    with sqlite3.connect((run / 'coverage.sqlite').resolve().as_uri() + '?mode=ro', uri=True, timeout=60) as db:
        db.execute('ATTACH DATABASE ? AS inventory', ((run / 'inventory-all.sqlite').resolve().as_uri() + '?mode=ro',))
        allocated = dict(db.execute("SELECT c.status,coalesce(sum(i.allocated_bytes),0) "
                                   "FROM coverage c JOIN inventory.entries i ON i.path=c.path "
                                   "WHERE c.status IN ('verified-bytes','verified-fragments','verified-refetch','recipe-refetch') "
                                   "GROUP BY c.status").fetchall())
    covered = sum(allocated.values())
    remaining = max(0, eligible.get('allocated_bytes', 0) - covered)
    baseline = rate(serial)
    speed = current_rate['gb_per_hour'] if current_rate else (baseline['gb_per_hour'] if baseline else 0)
    return {'measured_at': now.isoformat(), 'serial_baseline': baseline,
            'current_rate': current_rate, 'mode': 'parallel' if parallel else 'serial',
            'eligible_allocated_bytes': eligible.get('allocated_bytes'),
            'covered_allocated_bytes': covered, 'allocated_by_status': allocated,
            'remaining_allocated_bytes': remaining,
            'eta_hours': remaining / 1e9 / speed if speed > 0 and eligible else None,
            'eta_uses_serial_baseline': current_rate is None,
            'eta_basis': 'Observed eligible allocated bytes remaining / recent verified source-byte rate. '
                         'Estimate only: compression, sparse extents, file counts, changing sources and recipes change the rate; '
                         'unknown unreadable content and protected roots are outside this estimate.',
            'load_1m': os.getloadavg()[0], 'load_ceiling': 30}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    result = measure(args.run)
    (args.run / 'throughput.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
