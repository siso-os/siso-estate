#!/usr/bin/env python3
"""Export an existing home inventory and backup coverage as private JSONL.gz."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

import home_backup

MAX_COMPRESSED = 256 * 1024 * 1024
FREE_FLOOR = 15 * 1024**3
HOME = Path.home().resolve()


class CappedWriter:
    def __init__(self, stream, limit):
        self.stream, self.limit, self.count = stream, limit, 0
        self.digest = hashlib.sha256()

    def write(self, data):
        if self.count + len(data) > self.limit:
            raise OverflowError("compressed metadata export exceeds cap")
        written = self.stream.write(data)
        self.count += written
        self.digest.update(data[:written])
        return written

    def flush(self):
        self.stream.flush()


def _connect(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _schema(conn, schema="main"):
    tables = {}
    for (name,) in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' ORDER BY name"):
        tables[name] = [row[1] for row in conn.execute(f'PRAGMA {schema}.table_info("{name}")')]
    return tables


def _info(conn, schema="main"):
    if "scan_info" not in _schema(conn, schema):
        return {}
    return dict(conn.execute(f"SELECT key,value FROM {schema}.scan_info"))


def _is_denied(path, prefixes):
    path = os.path.abspath(path)
    return any(path == prefix or path.startswith(prefix.rstrip(os.sep) + os.sep) for prefix in prefixes)


def _relative(path, home):
    try:
        return os.path.relpath(path, home)
    except ValueError:
        return None


def export(inventory_path, coverage_path, run, output, *, home=HOME,
           free_floor=FREE_FLOOR, output_cap=MAX_COMPRESSED):
    """Stream databases to one atomically published gzip JSONL file."""
    run, output, home = Path(os.path.abspath(run)), Path(output), Path(os.path.abspath(home))
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output.parent, 0o700)
    if shutil_free(output.parent) < free_floor:
        raise OSError("free-space floor would be crossed")
    deny = [os.path.abspath(p) for p in home_backup.protected_prefixes(run)]
    deny.extend([str(run), str(run.parent / "estate-stream-stage")])
    inventory = _connect(inventory_path)
    pending = None
    try:
        coverage_uri = Path(coverage_path).resolve().as_uri() + "?mode=ro"
        inventory.execute("ATTACH DATABASE ? AS cov", (coverage_uri,))
        inventory.execute("BEGIN")
        inv_schema, cov_schema = _schema(inventory), _schema(inventory, "cov")
        if "entries" not in inv_schema or "coverage" not in cov_schema:
            raise ValueError("unexpected source database schema")
        inv_info = _info(inventory)
        if inv_info.get("roots"):
            inv_info["roots"] = [_relative(value, home) if _relative(value, home) is not None else "outside-home"
                                  for value in inv_info["roots"].splitlines()]
        cov_columns = set(cov_schema["coverage"])
        if not {"path", "status"}.issubset(cov_columns):
            raise ValueError("unexpected coverage schema")
        inventory_complete = bool(inv_info.get("completed_at"))
        # A read transaction pins each source snapshot before any rows are streamed.
        fd, temp_name = tempfile.mkstemp(prefix="." + output.name + ".", suffix=".pending", dir=output.parent)
        pending = Path(temp_name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as raw:
            capped = CappedWriter(raw, output_cap)
            gz = gzip.GzipFile(fileobj=capped, mode="wb", compresslevel=1, mtime=0)

            def emit(value):
                gz.write((json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode())

            emit({"record": "header", "format": "siso-home-metadata.v1", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "relative_path_fields": ["path", "relative_home_path", "nearest_git_root", "metadata_location"],
                  "inventory_source": "inventory-all.sqlite", "coverage_source": "coverage.sqlite",
                  "inventory_is_checkpoint": True, "inventory_complete_at_export": inventory_complete,
                  "inventory_scan_info": inv_info, "inventory_schema": inv_schema, "coverage_schema": cov_schema,
                  "coverage_is_partial": True, "full_reconstruction_claimed": False,
                  "acl_capture": False, "filesystem_rescan_performed": False,
                  "filesystem_attributes_absent": ["uid", "gid", "mode", "acl", "extended attributes", "birth time", "content"],
                  "protected_prefixes_filtered": True})
            count, covered, protected, errors = 0, 0, 0, 0
            coverage_fields = [key for key in ("status", "source_bytes", "sha256", "source_stat", "snapshot_url", "run_id", "note") if key in cov_columns]
            selects = ",".join('c."' + field + '" AS "coverage_' + field + '"' for field in coverage_fields)
            columns = inv_schema["entries"]
            sql = ("SELECT " + ",".join('e."' + name + '"' for name in columns) +
                   ("," + selects if selects else "") +
                   " FROM entries e LEFT JOIN cov.coverage c ON c.path=e.path ORDER BY e.path")
            cursor = inventory.execute(sql)
            scanned = 0
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                for row in rows:
                    scanned += 1
                    if scanned % 50000 == 0:
                        if shutil_free(output.parent) < free_floor:
                            raise OSError("free-space floor would be crossed during export")
                        if os.getloadavg()[0] >= 30:
                            raise RuntimeError("load ceiling reached during export")
                    item = dict(row)
                    absolute = item.get("path")
                    if not isinstance(absolute, str) or _is_denied(absolute, deny):
                        protected += 1
                        continue
                    rel = _relative(absolute, home)
                    if rel is None or rel == ".." or rel.startswith("../"):
                        continue
                    item["path"] = rel
                    item["relative_home_path"] = rel
                    for field in ("nearest_git_root", "metadata_location"):
                        value = item.get(field)
                        if isinstance(value, str) and os.path.isabs(value):
                            item[field] = _relative(value, home) if _relative(value, home) is not None else "outside-home"
                    joined_coverage = {field: item.pop("coverage_" + field) for field in coverage_fields}
                    item["coverage"] = joined_coverage if joined_coverage.get("status") is not None else None
                    if item["coverage"]:
                        covered += 1
                    if item.get("kind") == "error" or item.get("error"):
                        errors += 1
                    emit({"record": "entry", **item})
                    count += 1
            try:
                gz.close()
            except OverflowError:
                gz.fileobj = None
                raise
            digest, compressed_bytes = capped.digest.hexdigest(), capped.count
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(pending, output)
        pending = None
        return {"rows": count, "rows_with_coverage": covered, "filtered_protected_rows": protected,
                "error_rows": errors, "inventory_complete": inventory_complete,
                "compressed_bytes": compressed_bytes, "sha256": digest, "output": str(output)}
    finally:
        inventory.close()
        if pending and pending.exists():
            pending.unlink()


def shutil_free(path):
    """Return free bytes without importing any scanning/storage subsystem."""
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = export(args.inventory, args.coverage, args.run, args.output)
    # The receipt intentionally omits paths: the destination may contain private location data.
    result.pop("output", None)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
