#!/usr/bin/env python3
"""Stream a metadata-only inventory of home directories to SQLite.

No file contents are opened. Symlinks are recorded and never followed. Excluded
trees receive one explicit row with the reason and are not traversed.
"""
import argparse
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  path TEXT PRIMARY KEY, size INTEGER, allocated_bytes INTEGER, mtime_ns INTEGER, is_directory INTEGER, kind TEXT NOT NULL,
  nearest_git_root TEXT, exclusion TEXT, reason TEXT, backup_status TEXT NOT NULL,
  github_location TEXT, what_it_is TEXT, destination_guess TEXT,
  metadata_location TEXT, git_kind TEXT, link_target TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS entries_git_root ON entries(nearest_git_root);
CREATE INDEX IF NOT EXISTS entries_kind ON entries(kind);
CREATE INDEX IF NOT EXISTS entries_exclusion ON entries(exclusion);
CREATE TABLE IF NOT EXISTS scan_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS directory_summary (
  root TEXT NOT NULL, directory TEXT NOT NULL, depth INTEGER NOT NULL,
  logical_bytes INTEGER NOT NULL, allocated_bytes INTEGER NOT NULL,
  file_count INTEGER NOT NULL, error_count INTEGER NOT NULL,
  PRIMARY KEY(root,directory)
);
"""

# These are intentional, path based exclusions. Library user data is otherwise scanned.
DEFAULT_EXCLUDES = {
    ".cache": "application cache",
    ".npm": "package download cache",
    ".pnpm-store": "package download cache",
    ".bun/install/cache": "package download cache",
    ".cargo/registry": "package source cache",
    "Library/Caches": "macOS application caches",
    "Library/Developer/Xcode/DerivedData": "rebuildable Xcode build cache",
    "Library/Developer/CoreSimulator/Caches": "rebuildable simulator cache",
}
HALO_CANONICAL = "SISO_Workspace/SISO_Agency/partners/halo"
CACHE_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".next", ".turbo",
              ".pytest_cache", ".mypy_cache", ".playwright-cli"}


def norm(path):
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def under(path, prefix):
    try:
        return os.path.commonpath((path, prefix)) == prefix
    except ValueError:
        return False


def git_marker(path, names):
    """Return (metadata path, kind) for a working tree or bare repository."""
    if ".git" in names:
        marker = os.path.join(path, ".git")
        if os.path.isdir(marker):
            return marker, "repository"
        if os.path.isfile(marker):
            return marker, "gitfile"
    # A bare Git root has HEAD, config, objects and refs at its top level.
    if os.path.basename(path) != ".git" and {"HEAD", "config", "objects", "refs"}.issubset(names):
        return path, "bare"
    return None, None


def classify(path, mode):
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISSOCK(mode):
        return "excluded_socket"
    if stat.S_ISFIFO(mode):
        return "excluded_fifo"
    if stat.S_ISCHR(mode):
        return "excluded_char_device"
    if stat.S_ISBLK(mode):
        return "excluded_block_device"
    return "excluded_special"


def destination(path, kind):
    """A transparent heuristic only; this is not a backup decision."""
    low = path.lower()
    if "/siso_workspace/" in low and ("/personal/" in low or "/private/" in low):
        return "private-data-repo (guess; assess encryption)"
    if kind in ("git_root", "bare_git_root", "git_metadata"):
        return "private GitHub code repo or umbrella (guess)"
    if "/siso_workspace/" in low:
        return "private GitHub data repo or machine umbrella (guess)"
    return "private GitHub home-data repo (guess)"


def scan(roots, db_path, exclusions=(), halo_prefixes=(), all_content=False, measure_protected=False):
    db_path = norm(db_path)
    output_paths = {db_path, db_path + "-journal", db_path + "-wal", db_path + "-shm"}
    roots = [norm(r) for r in roots]
    home = norm(str(Path.home()))
    exmap = {} if all_content else {os.path.join(home, rel): reason for rel, reason in DEFAULT_EXCLUDES.items()}
    exmap.update({norm(p): "operator-supplied exclusion" for p in exclusions})
    if not measure_protected:
        exmap[os.path.join(home, HALO_CANONICAL)] = "HALO island: never copy into SISO backup"
        for prefix in halo_prefixes:
            exmap[norm(prefix)] = "operator-supplied HALO exclusion"

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(entries)")}
    if "allocated_bytes" not in columns:
        conn.execute("ALTER TABLE entries ADD COLUMN allocated_bytes INTEGER")
    if "is_directory" not in columns:
        conn.execute("ALTER TABLE entries ADD COLUMN is_directory INTEGER")
    conn.execute("DELETE FROM entries")
    conn.execute("DELETE FROM scan_info")
    conn.execute("DELETE FROM directory_summary")
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    conn.executemany("INSERT INTO scan_info VALUES (?,?)", [("started_at", now), ("roots", "\n".join(roots)),
                                                               ("metadata_only", "true"),
                                                               ("all_content", str(bool(all_content)).lower()),
                                                               ("measure_protected", str(bool(measure_protected)).lower()),
                                                               ("directory_summary_semantics", "subtree totals over regular files only; choose one depth for non-overlapping breakdown")])
    pending = 0

    def record(path, kind, st=None, gitroot=None, exclusion=None, reason=None,
               metadata=None, gitkind=None, link_target=None, error=None, what=None):
        nonlocal pending
        size = st.st_size if st else None
        mtime = st.st_mtime_ns if st else None
        allocated = getattr(st, "st_blocks", 0) * 512 if st else None
        is_directory = int(stat.S_ISDIR(st.st_mode)) if st else None
        if path in output_paths:
            exclusion, reason = "inventory_output", "scanner output excluded to prevent recursion"
        conn.execute("""INSERT OR REPLACE INTO entries
          (path,size,allocated_bytes,mtime_ns,is_directory,kind,nearest_git_root,exclusion,reason,backup_status,
           github_location,what_it_is,destination_guess,metadata_location,git_kind,link_target,error)
          VALUES (?,?,?,?,?,?,?,?,?,'unassessed',NULL,?,?,?,?,?,?)""",
          (path, size, allocated, mtime, is_directory, kind, gitroot, exclusion, reason, what or kind,
           destination(path, kind), metadata, gitkind, link_target, error))
        pending += 1
        if pending >= 500:
            conn.commit()
            pending = 0

    for root in roots:
        try:
            rootst = os.lstat(root)
        except OSError as e:
            record(root, "error", error=f"{type(e).__name__}: {e}", reason="root stat failed")
            continue
        if not stat.S_ISDIR(rootst.st_mode):
            record(root, classify(root, rootst.st_mode), rootst)
            continue
        stack = [(root, None)]
        while stack:
            path, inherited_git = stack.pop()
            try:
                st = os.lstat(path)
            except OSError as e:
                record(path, "error", gitroot=inherited_git, error=f"{type(e).__name__}: {e}", reason="lstat failed")
                continue
            modekind = classify(path, st.st_mode)
            if modekind != "directory":
                link_target = None
                error = None
                if modekind == "symlink":
                    try:
                        link_target = os.readlink(path)
                    except OSError as e:
                        error = f"{type(e).__name__}: {e}"
                record(path, modekind, st, inherited_git, error=error, link_target=link_target,
                       reason="symlink recorded without following" if modekind == "symlink" else
                              "special filesystem object excluded" if modekind.startswith("excluded_") else None)
                continue
            excluded = next(((p, why) for p, why in exmap.items() if under(path, p)), None)
            if not all_content and not excluded and os.path.basename(path) in CACHE_DIRS:
                excluded = (path, "rebuildable dependency or tool cache")
            if not all_content and not excluded and os.path.basename(path).lower().endswith(".app"):
                excluded = (path, "installed application bundle; rebuildable")
            if excluded:
                record(path, "excluded_directory", st, inherited_git, excluded[0], excluded[1])
                continue
            if path in output_paths:
                record(path, "excluded_directory", st, inherited_git, "inventory_output", "scanner output excluded to prevent recursion")
                continue
            try:
                with os.scandir(path) as it:
                    children = sorted(list(it), key=lambda e: e.name)
            except OSError as e:
                record(path, "error", st, inherited_git, error=f"{type(e).__name__}: {e}", reason="directory listing failed")
                continue
            names = {e.name for e in children}
            marker, gitkind = git_marker(path, names)
            current_git = path if marker else inherited_git
            kind = "git_root" if marker and gitkind != "bare" else "bare_git_root" if marker else "directory"
            record(path, kind, st, current_git, metadata=marker, gitkind=gitkind,
                   what="Git root; metadata recorded, contents will be captured separately" if marker else None)
            descend = []
            for ent in children:
                child = os.path.join(path, ent.name)
                if marker and (ent.name == ".git" or gitkind == "bare"):
                    try:
                        gst = ent.stat(follow_symlinks=False)
                    except OSError as e:
                        record(child, "error", gitroot=current_git, metadata=child, gitkind=gitkind,
                               error=f"{type(e).__name__}: {e}", reason="Git metadata stat failed")
                        continue
                    if all_content:
                        metadata_kind = ("git_metadata_file" if stat.S_ISREG(gst.st_mode) else
                                         "git_metadata" if stat.S_ISDIR(gst.st_mode) else classify(child, gst.st_mode))
                        target = None
                        error = None
                        if metadata_kind == "symlink":
                            try:
                                target = os.readlink(child)
                            except OSError as e:
                                error = f"{type(e).__name__}: {e}"
                        record(child, metadata_kind, gst, current_git, metadata=child, gitkind=gitkind,
                               link_target=target, error=error,
                               reason="symlink recorded without following" if metadata_kind == "symlink" else None,
                               what="Git metadata; included in all-content inventory")
                        if stat.S_ISDIR(gst.st_mode):
                            descend.append((child, current_git))
                    else:
                        record(child, "git_metadata", gst, current_git, metadata=child, gitkind=gitkind,
                               what="Git metadata location; refs and WIP captured separately" if gitkind != "bare" else
                                    "Bare Git metadata; refs and history captured separately")
                    continue
                try:
                    est = ent.stat(follow_symlinks=False)
                except OSError as e:
                    record(child, "error", gitroot=current_git, error=f"{type(e).__name__}: {e}", reason="entry stat failed")
                    continue
                ck = classify(child, est.st_mode)
                if ck == "directory":
                    descend.append((child, current_git))
                else:
                    link_target = None
                    error = None
                    if ck == "symlink":
                        try:
                            link_target = os.readlink(child)
                        except OSError as e:
                            error = f"{type(e).__name__}: {e}"
                    record(child, ck, est, current_git, error=error, link_target=link_target,
                           reason="symlink recorded without following" if ck == "symlink" else
                           "special filesystem object excluded" if ck.startswith("excluded_") else None)
            stack.extend(reversed(descend))
    conn.commit()
    write_directory_summaries(conn, roots)
    conn.execute("INSERT OR REPLACE INTO scan_info VALUES (?,?)", ("completed_at", time.strftime("%Y-%m-%dT%H:%M:%S%z")))
    conn.execute("INSERT OR REPLACE INTO scan_info VALUES (?,?)", ("row_count", str(conn.execute("SELECT count(*) FROM entries").fetchone()[0])))
    conn.commit()
    summary = conn.execute("""SELECT count(*),
      sum(CASE WHEN kind IN ('file','git_metadata_file') AND exclusion IS NULL THEN size ELSE 0 END),
      sum(kind='error'), sum(kind='git_root' OR kind='bare_git_root'), sum(exclusion IS NOT NULL)
      FROM entries""").fetchone()
    conn.close()
    return summary


def write_directory_summaries(conn, roots, max_depth=3):
    """Store subtree totals for directories through depth 3; directory blocks are never added."""
    from collections import defaultdict
    totals = defaultdict(lambda: [0, 0, 0, 0])
    rows = conn.execute("SELECT path,kind,size,allocated_bytes,is_directory,exclusion,error FROM entries")
    for path, kind, size, allocated, is_dir, exclusion, error in rows:
        if exclusion:
            continue
        for root in roots:
            if not under(path, root):
                continue
            rel = os.path.relpath(path, root)
            parts = [] if rel == "." else rel.split(os.sep)
            depth = len(parts)
            if is_dir:
                if depth <= max_depth:
                    totals[(root, path)]
            if kind in ("file", "git_metadata_file"):
                parent_max = min(depth - 1, max_depth)
                for parent_depth in range(parent_max + 1):
                    directory = root if parent_depth == 0 else os.path.join(root, *parts[:parent_depth])
                    bucket = totals[(root, directory)]
                    bucket[0] += size or 0
                    bucket[1] += allocated or 0
                    bucket[2] += 1
            if kind == "error" or error:
                parent_max = min(depth if is_dir else depth - 1, max_depth)
                for parent_depth in range(parent_max + 1):
                    directory = root if parent_depth == 0 else os.path.join(root, *parts[:parent_depth])
                    totals[(root, directory)][3] += 1
    conn.executemany("INSERT OR REPLACE INTO directory_summary VALUES (?,?,?,?,?,?,?)",
                     [(root, path, len(os.path.relpath(path, root).split(os.sep)) if path != root else 0,
                       *values) for (root, path), values in totals.items()])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", action="append", dest="roots", help="scan root; repeatable (default: home)")
    ap.add_argument("--output", required=True, help="SQLite inventory path; excluded from its own scan")
    ap.add_argument("--exclude-prefix", action="append", default=[], help="explicit directory path to omit; repeatable")
    ap.add_argument("--halo-prefix", action="append", default=[], help="HALO path to omit; repeatable")
    ap.add_argument("--all-content", action="store_true", help="include apps, caches, Library and Git metadata; explicit/HALO exclusions remain")
    ap.add_argument("--measure-protected", action="store_true", help="metadata only: measure protected roots in a separate inventory; never authorizes their upload")
    args = ap.parse_args(argv)
    roots = args.roots or [str(Path.home())]
    try:
        count, size, errors, repos, excluded = scan(roots, args.output, args.exclude_prefix, args.halo_prefix, args.all_content, args.measure_protected)
    except (OSError, sqlite3.Error) as e:
        print(f"inventory failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    with sqlite3.connect(args.output) as conn:
        summaries = conn.execute("SELECT count(*) FROM directory_summary").fetchone()[0]
        allocated = conn.execute("SELECT sum(allocated_bytes) FROM entries WHERE kind IN ('file','git_metadata_file') AND exclusion IS NULL").fetchone()[0]
    print(f"rows={count} logical_file_bytes={size or 0} allocated_file_bytes={allocated or 0} git_roots={repos or 0} excluded={excluded or 0} errors={errors or 0} directory_summary_rows={summaries} output={norm(args.output)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
