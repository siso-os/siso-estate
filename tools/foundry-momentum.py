#!/usr/bin/env python3
"""Append daily star snapshots from the current Foundry catalog (single writer)."""
import argparse
import datetime
import json
from pathlib import Path
import sqlite3


def snapshot(source, destination, day):
    source, destination = Path(source), Path(destination)
    # Never create an empty source DB when a catalog moved.
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
        src.execute("PRAGMA query_only=ON")
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table = "repo_card" if "repo_card" in tables else "repos" if "repos" in tables else None
        if table is None:
            raise ValueError("source contains neither repo_card nor repos; snapshot was not written")
        cols = {r[1] for r in src.execute(f"PRAGMA table_info({table})")}
        if not {"full_name", "stars"} <= cols:
            raise ValueError("source catalog is missing full_name/stars")
        optional = [name if name in cols else "NULL" for name in ("forks", "pushed_at", "star_velocity")]
        cursor = src.execute(f"SELECT full_name, stars, {', '.join(optional)} FROM {table} WHERE full_name IS NOT NULL AND stars IS NOT NULL")
        first = cursor.fetchmany(1000)
        if not first:
            raise ValueError("source catalog is empty; snapshot was not written")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination, timeout=60) as dst:
            dst.execute("CREATE TABLE IF NOT EXISTS repo_snapshot(day TEXT NOT NULL, full_name TEXT NOT NULL, stars INTEGER, forks INTEGER, pushed_at TEXT, star_velocity REAL, PRIMARY KEY(day,full_name))")
            dst.execute("CREATE TABLE IF NOT EXISTS snapshot_run(day TEXT PRIMARY KEY, source_table TEXT NOT NULL, source_mtime REAL NOT NULL, source_rows INTEGER NOT NULL, completed_at TEXT NOT NULL)")
            count = 0
            batch = first
            while batch:
                dst.executemany("INSERT OR IGNORE INTO repo_snapshot VALUES (?,?,?,?,?,?)", [(day, *r) for r in batch])
                count += len(batch)
                batch = cursor.fetchmany(1000)
            completed = datetime.datetime.now(datetime.timezone.utc).isoformat()
            dst.execute("INSERT OR REPLACE INTO snapshot_run VALUES (?,?,?,?,?)", (day, table, source.stat().st_mtime, count, completed))
            held = dst.execute("SELECT count(*) FROM repo_snapshot WHERE day=?", (day,)).fetchone()[0]
    return {"day": day, "source_table": table, "source_rows": count, "snapshot_rows": held, "completed_at": completed}


def main():
    home = Path.home()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=home / "foundry-data/domains/github/identity/identity.sqlite")
    ap.add_argument("--destination", type=Path, default=home / "foundry-data/domains/github/momentum.sqlite")
    ap.add_argument("--day", default=datetime.date.today().isoformat())
    args = ap.parse_args()
    print(json.dumps(snapshot(args.source, args.destination, args.day)))

if __name__ == "__main__":
    main()
