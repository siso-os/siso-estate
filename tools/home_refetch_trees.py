#!/usr/bin/env python3
"""Collect private evidence for exact public npm refetch matches in installed trees.

Only regular installed files whose SHA-256 matches a member of a public-verified
package tarball are covered. Unknown, modified, extra, protected, or unsafe files
remain raw-required. The tool never removes files or publishes its private output.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import sqlite3
import stat
import sys
import tarfile
from urllib.parse import urlsplit


DEFAULT_DB = Path.home() / "SISO_Workspace/_data/estate-home-backup-20261016/inventory-all.sqlite"
DEFAULT_OUTPUT = Path.home() / "SISO_Workspace/_data/estate-home-backup-20261016/refetch-trees/tree-manifest.json"
DEFAULT_NPM_MANIFEST = Path.home() / "SISO_Workspace/_data/estate-home-backup-20261016/refetch/npm-refetch-manifest-source-verified.json"
MIN_ALLOCATED_BYTES = 1 * 1024 * 1024
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_REFETCH_HASH_BYTES = 2 * 1024 * 1024 * 1024
SAFE_SOURCE_SPEC = importlib.util.spec_from_file_location("estate_safe_source", Path(__file__).with_name("safe_source.py"))
safe_source = importlib.util.module_from_spec(SAFE_SOURCE_SPEC)
SAFE_SOURCE_SPEC.loader.exec_module(safe_source)

LOCKS = {
    "node_modules": ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml",
                     "yarn.lock", "bun.lock", "bun.lockb"),
    "python-environment": ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.lock"),
}
MANIFESTS = {
    "node_modules": ("package.json",),
    "python-environment": ("pyproject.toml", "Pipfile", "requirements.txt"),
}


def _category(path: Path) -> str | None:
    """Classify only explicit tree/cache locations; a familiar name is a candidate, not proof."""
    parts = [part.casefold() for part in path.parts]
    name = parts[-1] if parts else ""
    if name == "node_modules":
        return "node_modules"
    if name in {".venv", "venv"}:
        return "python-environment"
    if name == "venvs" and ".local" in parts and "pipx" in parts:
        return "python-environment"
    if name == "models" and ".ollama" in parts:
        return "model-weights"
    if name == "deriveddata" and "xcode" in parts:
        return "xcode-derived-data"
    if name == "cellar" and "homebrew" in parts:
        return "brew-installation"
    if name in {"caches", ".cache", "cache", "pip", "_cacache"}:
        return "package-or-build-cache"
    if name == "cache" and (".npm" in parts or ".bun" in parts or ".cargo" in parts):
        return "package-or-build-cache"
    return None


def _is_halo(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    return "halocrm" in parts or any("halo" == part for part in parts)


def _hash_evidence(path: Path) -> dict | None:
    try:
        mode = path.lstat().st_mode
        size = path.stat(follow_symlinks=False).st_size
        if not stat.S_ISREG(mode) or size > MAX_EVIDENCE_BYTES:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return {"path": str(path.resolve()), "size": size, "sha256": digest.hexdigest()}
    except OSError:
        return None


def _hash_file_sha512(path: Path) -> tuple[int, str] | None:
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            return None
        digest = hashlib.sha512()
        size = 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_REFETCH_HASH_BYTES:
                    return None
                digest.update(block)
        return size, digest.hexdigest()
    except OSError:
        return None


def _verified_npm_files(db_path: Path, manifest_path: Path, home_root: Path) -> tuple[dict[str, dict], dict | None]:
    """Recheck exact npm cache blob bytes against previously public-verified rows."""
    cache_root = (home_root / ".npm/_cacache").resolve()
    evidence = _hash_evidence(manifest_path)
    if not evidence:
        return {}, None
    try:
        if manifest_path.stat().st_size > MAX_EVIDENCE_BYTES:
            return {}, evidence
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, evidence
    if (manifest.get("schema") != "siso.estate.refetch-manifest.v1"
            or manifest.get("source_root") != str(cache_root)
            or not isinstance(manifest.get("entries"), list)):
        return {}, evidence

    items: dict[str, dict] = {}
    paths = []
    for entry in manifest["entries"]:
        covered = entry.get("covered_file", {})
        rel = Path(covered.get("path", ""))
        source = entry.get("source", "")
        try:
            source_url = urlsplit(source)
            source_port = source_url.port
        except (TypeError, ValueError):
            continue
        digest = covered.get("sha512")
        if (entry.get("source_verification_status") != "verified_public"
                or source_url.scheme != "https" or source_url.hostname != "registry.npmjs.org"
                or source_url.username or source_url.password or source_port is not None
                or rel.is_absolute() or ".." in rel.parts or not rel.parts
                or not isinstance(digest, str) or len(digest) != 128
                or any(char not in "0123456789abcdef" for char in digest)
                or type(covered.get("size")) is not int or covered["size"] < 0):
            continue
        absolute = cache_root.joinpath(rel)
        if not absolute.resolve(strict=False).is_relative_to(cache_root):
            continue
        key = str(absolute)
        if key in items:
            continue
        items[key] = {"path": key, "relative_path": rel.as_posix(), "size": covered["size"],
                      "sha512": digest, "source": source, "name": entry.get("name"),
                      "version": entry.get("version"), "source_verified_at": entry.get("source_verified_at")}
        paths.append(key)

    if not paths:
        return {}, evidence
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        rows_by_path = {}
        for start in range(0, len(paths), 500):
            subset = paths[start:start + 500]
            marks = ",".join("?" for _ in subset)
            rows_by_path.update({row["path"]: dict(row) for row in db.execute(
                f"SELECT path,size,allocated_bytes,kind,is_directory FROM entries WHERE path IN ({marks})", subset)})
    finally:
        db.close()

    verified: dict[str, dict] = {}
    hashed_total = 0
    for key, item in items.items():
        inventory = rows_by_path.get(key)
        if not inventory or inventory["kind"] != "file" or inventory["is_directory"]:
            continue
        if hashed_total + item["size"] > MAX_REFETCH_HASH_BYTES:
            return {}, evidence
        observed = _hash_file_sha512(Path(key))
        if not observed:
            continue
        hashed_total += observed[0]
        if observed[0] != item["size"] or observed[1] != item["sha512"]:
            continue
        try:
            st = Path(key).lstat()
        except OSError:
            continue
        item.update({"physical_bytes": int(inventory["allocated_bytes"] or st.st_blocks * 512 or 0),
                     "inventory_size": int(inventory["size"] or 0),
                     "exact_refetch_verified": True,
                     "verification": "local_sha512_matches_source_verified_manifest"})
        verified[key] = item
    return verified, evidence


def _evidence_for(root: Path, category: str, git_root: str | None, home_root: Path) -> tuple[list[dict], dict]:
    if category not in LOCKS:
        return [], {"kind": None, "version": None, "lock_path": None, "manifest_path": None}

    stop = Path(git_root).resolve() if git_root else home_root
    if not stop.is_relative_to(home_root):
        stop = home_root
    current = root.parent.resolve()
    evidence_paths: list[Path] = []
    chosen_manifest: Path | None = None
    chosen_lock: Path | None = None
    while current.is_relative_to(home_root):
        manifests = [current / name for name in MANIFESTS[category] if (current / name).is_file()]
        locks = [current / name for name in LOCKS[category] if (current / name).is_file()]
        if manifests and locks:
            chosen_manifest, chosen_lock = manifests[0], locks[0]
            evidence_paths = [chosen_manifest, chosen_lock]
            break
        if current == stop or current == home_root:
            break
        current = current.parent

    hashed = [item for p in evidence_paths if (item := _hash_evidence(p)) is not None]
    details = {"kind": None, "version": None, "lock_path": None, "manifest_path": None}
    if chosen_manifest and chosen_lock and len(hashed) == 2:
        details.update(kind=chosen_lock.name, lock_path=str(chosen_lock.resolve()),
                       manifest_path=str(chosen_manifest.resolve()))
        if chosen_manifest.name == "package.json":
            try:
                data = json.loads(chosen_manifest.read_text(encoding="utf-8"))
                details["version"] = data.get("version") if isinstance(data, dict) else None
                details["package_name"] = data.get("name") if isinstance(data, dict) else None
                details["package_manager"] = data.get("packageManager") if isinstance(data, dict) else None
            except (OSError, UnicodeError, json.JSONDecodeError):
                details["manifest_parse"] = "unreadable_or_invalid"
    else:
        hashed = []
    return hashed, details


def _reconstruction_data(source: dict) -> str | None:
    """Return a fixed command string as evidence only; callers must never execute it."""
    return {
        "package-lock.json": "npm ci",
        "npm-shrinkwrap.json": "npm ci",
        "pnpm-lock.yaml": "pnpm install --frozen-lockfile",
        "yarn.lock": "yarn install --immutable",
        "bun.lock": "bun install --frozen-lockfile",
        "bun.lockb": "bun install --frozen-lockfile",
        "uv.lock": "uv sync --frozen",
        "poetry.lock": "poetry install --sync",
        "Pipfile.lock": "pipenv sync",
        "requirements.lock": "pip install -r requirements.lock",
    }.get(source.get("kind"))


def _within(path: Path, prefix: Path) -> bool:
    try:
        return path == prefix or path.is_relative_to(prefix)
    except (OSError, ValueError):
        return False


def _protected_paths(path: Path, prefixes: list[Path]) -> bool:
    parts = [part.casefold() for part in path.parts]
    if any(part == "halo" or part.startswith("halo-") for part in parts):
        return True
    return any(_within(path, prefix) or _within(prefix, path) for prefix in prefixes)


def _load_protected(path: Path) -> list[Path]:
    document = json.loads(path.read_text(encoding="utf-8"))
    values = document.get("protected_prefixes") if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get("schema") != "home-git-protected-prefixes.v1"
            or not isinstance(values, list) or any(not isinstance(item, str) or not Path(item).is_absolute() for item in values)):
        raise ValueError("protected-prefix inventory has an unknown schema")
    return [Path(item) for item in values]


def _package_json_rows(db_path: Path, home_root: Path) -> tuple[list[dict], set[Path]]:
    """Use the path primary key for bounded exact package.json lookups."""
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    found: dict[str, dict] = {}
    roots: set[Path] = set()
    try:
        summaries = db.execute("SELECT directory FROM directory_summary WHERE directory LIKE '%/node_modules'").fetchall()
        search_roots = {Path(row["directory"]).resolve(strict=False) for row in summaries
                        if Path(row["directory"]).resolve(strict=False).is_relative_to(home_root)}
        # Deep worktree node_modules do not appear in the depth-limited summary.
        worktrees = home_root / "SISO_Workspace/_data/worktrees"
        if worktrees.is_dir():
            low, high = str(worktrees) + os.sep, str(worktrees) + "0"
            pattern = str(worktrees) + "/*node_modules*/package.json"
            rows = db.execute(
                "SELECT path,size,mtime_ns,kind,is_directory,error FROM entries "
                "INDEXED BY sqlite_autoindex_entries_1 WHERE path>=? AND path<? AND path GLOB ?",
                (low, high, pattern),
            )
            for row in rows:
                if row["kind"] != "file" or row["is_directory"]:
                    continue
                path = Path(row["path"])
                if not path.is_relative_to(home_root):
                    continue
                # The first node_modules component is the non-overlapping scan root.
                parts = path.parts
                indices = [i for i, part in enumerate(parts) if part == "node_modules"]
                if not indices:
                    continue
                roots.add(Path(*parts[:indices[0] + 1]))
                found[str(path)] = dict(row)
        for root in search_roots:
            low, high = str(root) + os.sep, str(root) + "0"
            rows = db.execute(
                "SELECT path,size,mtime_ns,kind,is_directory,error FROM entries "
                "INDEXED BY sqlite_autoindex_entries_1 WHERE path>=? AND path<? AND path GLOB ?",
                (low, high, str(root) + "/*/package.json"),
            )
            for row in rows:
                if row["kind"] != "file" or row["is_directory"]:
                    continue
                path = Path(row["path"])
                found[str(path)] = dict(row)
                roots.add(root)
    finally:
        db.close()
    return list(found.values()), roots


def _read_package_identity(path: Path, metadata: dict) -> tuple[str, str, dict] | None:
    try:
        before_stat = path.lstat()
        before = safe_source.identity(before_stat)
        if not stat.S_ISREG(before_stat.st_mode) or before_stat.st_size > 1024 * 1024:
            return None
        if (metadata.get("size") is not None and before_stat.st_size != metadata["size"]
                or metadata.get("mtime_ns") is not None and before_stat.st_mtime_ns != metadata["mtime_ns"]):
            return None
        fd = safe_source.open_path(path, before)
        try:
            payload = bytearray()
            while True:
                block = os.read(fd, 64 * 1024)
                if not block:
                    break
                payload.extend(block)
                if len(payload) > 1024 * 1024:
                    return None
            safe_source.verify_unchanged(fd, before)
        finally:
            os.close(fd)
        data = json.loads(payload.decode("utf-8"))
        name, version = data.get("name"), data.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            return None
        return name, version, before
    except (OSError, ValueError, RuntimeError, UnicodeError, json.JSONDecodeError):
        return None


def _source_index(source_manifest_path: Path, home_root: Path) -> dict[tuple[str, str], dict]:
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("schema") != "siso.estate.refetch-manifest.v1"
            or manifest.get("source_root") != str((home_root / ".npm/_cacache").resolve())
            or not isinstance(manifest.get("entries"), list)):
        raise ValueError("source-verified npm manifest has an unknown schema")
    result: dict[tuple[str, str], dict] = {}
    ambiguous: set[tuple[str, str]] = set()
    for entry in manifest["entries"]:
        if entry.get("source_verification_status") != "verified_public":
            continue
        key = entry.get("name"), entry.get("version")
        if not all(isinstance(value, str) for value in key):
            continue
        file = entry.get("covered_file", {})
        rel = PurePosixPath(file.get("path", ""))
        url = entry.get("source", "")
        try:
            parts = urlsplit(url)
            port = parts.port
        except (TypeError, ValueError):
            continue
        if (parts.scheme != "https" or parts.hostname != "registry.npmjs.org" or parts.username
                or parts.password or port is not None or rel.is_absolute() or ".." in rel.parts
                or len(file.get("sha512", "")) != 128 or type(file.get("size")) is not int):
            continue
        record = dict(entry)
        record["relative_cache_path"] = rel.as_posix()
        if key in result and (result[key].get("source") != entry.get("source")
                              or result[key].get("integrity") != entry.get("integrity")):
            ambiguous.add(key)
        else:
            result[key] = record
    for key in ambiguous:
        result.pop(key, None)
    return result


class _ReadBudget:
    def __init__(self, maximum: int = MAX_REFETCH_HASH_BYTES):
        self.maximum = maximum
        self.used = 0

    def take(self, amount: int) -> bool:
        if amount < 0 or self.used + amount > self.maximum:
            return False
        self.used += amount
        return True


def _archive_member_index(source: dict, cache_root: Path, package_key: tuple[str, str],
                          budget: _ReadBudget, counters: dict) -> dict[str, dict]:
    covered = source["covered_file"]
    rel = source["relative_cache_path"]
    path = cache_root / rel
    try:
        lstat = path.lstat()
        identity = safe_source.identity(lstat)
        if not stat.S_ISREG(lstat.st_mode) or lstat.st_size != covered["size"]:
            return {}
        if not budget.take(covered["size"] * 2):
            counters["archive_budget_exhausted"] = True
            return {}
        fd = safe_source.open_path(path, identity)
        digest = hashlib.sha512()
        observed = 0
        try:
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                observed += len(block)
                digest.update(block)
            safe_source.verify_unchanged(fd, identity)
        finally:
            os.close(fd)
        if observed != covered["size"] or digest.hexdigest() != covered["sha512"]:
            return {}
        integrity = source.get("integrity", "")
        if not integrity.startswith("sha512-"):
            return {}
        try:
            if base64.b64decode(integrity[7:], validate=True).hex() != covered["sha512"]:
                return {}
        except (ValueError, base64.binascii.Error):
            return {}

        fd = safe_source.open_path(path, identity)
        members: dict[str, dict] = {}
        ambiguous: set[str] = set()
        try:
            with os.fdopen(fd, "rb", closefd=True) as archive_stream:
                with tarfile.open(fileobj=archive_stream, mode="r|gz") as archive:
                    while True:
                        try:
                            member = archive.next()
                        except (tarfile.TarError, OSError):
                            return {}
                        if member is None:
                            break
                        member_path = PurePosixPath(member.name)
                        if (not member.isfile() or member_path.is_absolute() or ".." in member_path.parts
                                or "\\" in member.name or len(member_path.parts) < 2
                                or member_path.parts[0] != "package"):
                            continue
                        inner = PurePosixPath(*member_path.parts[1:]).as_posix()
                        if not inner or inner in {".", ".."} or not budget.take(member.size):
                            counters["archive_budget_exhausted"] = True
                            return {}
                        stream = archive.extractfile(member)
                        if stream is None:
                            continue
                        file_hash, size = hashlib.sha256(), 0
                        with stream:
                            for block in iter(lambda: stream.read(1024 * 1024), b""):
                                size += len(block)
                                if size > member.size:
                                    return {}
                                file_hash.update(block)
                        if size != member.size:
                            return {}
                        record = {"sha256": file_hash.hexdigest(), "size": size,
                                  "archive_member": member_path.as_posix(), "archive_mode": member.mode}
                        if inner in members and members[inner] != record:
                            ambiguous.add(inner)
                        else:
                            members[inner] = record
                safe_source.verify_unchanged(archive_stream.fileno(), identity)
        finally:
            counters["archives_processed"] += 1
        for inner in ambiguous:
            members.pop(inner, None)
        return members
    except (OSError, ValueError, RuntimeError, tarfile.TarError):
        counters["archives_rejected"] += 1
        return {}


def build_installed_manifest(db_path: Path, source_manifest_path: Path, protected_path: Path,
                             output_path: Path, home_root: Path = Path.home(),
                             max_read_bytes: int = MAX_REFETCH_HASH_BYTES) -> dict:
    """Match installed regular files to exact safe members of known public tarballs."""
    home_root = home_root.resolve()
    protected = _load_protected(protected_path)
    source_index = _source_index(source_manifest_path, home_root)
    packages, scan_roots = _package_json_rows(db_path, home_root)
    install_roots = {}
    package_dirs: dict[str, dict] = {}
    for metadata in packages:
        path = Path(metadata["path"])
        if _protected_paths(path, protected):
            continue
        identity = _read_package_identity(path, metadata)
        if not identity:
            continue
        name, version, source_stat = identity
        key = name, version
        if key not in source_index:
            continue
        parts = path.parts
        indices = [i for i, part in enumerate(parts) if part == "node_modules"]
        if not indices:
            continue
        scan_root = Path(*parts[:indices[0] + 1])
        if _protected_paths(scan_root, protected):
            continue
        install_roots[str(scan_root)] = scan_root
        package_dirs[str(path.parent)] = {"name": name, "version": version, "key": key,
                                         "package_json": str(path), "package_json_stat": source_stat,
                                         "scan_root": str(scan_root)}

    instance_count: dict[tuple[str, str], int] = {}
    for package in package_dirs.values():
        instance_count[package["key"]] = instance_count.get(package["key"], 0) + 1
    ordered_keys = sorted(instance_count, key=lambda key: instance_count[key] * source_index[key]["covered_file"]["size"], reverse=True)

    result = {"schema": "siso-estate.exact-refetch-members.v1", "status": "in_progress", "done": False,
              "source_manifest": str(source_manifest_path.resolve()), "source_manifest_sha256": _hash_evidence(source_manifest_path),
              "inventory_db": str(db_path.resolve()), "inventory_completed_at": None,
              "protected_prefixes": str(protected_path.resolve()), "privacy": "private metadata; encrypt before publication",
              "coverage_rule": "exact installed bytes matching a source-verified public npm tarball member; unknown, modified, symlink and protected files stay raw",
              "covered_files": [], "summary": {"package_json_candidates": len(packages),
                  "matched_installed_package_roots": len(package_dirs), "candidate_node_modules_roots": len(install_roots),
                  "source_tarballs_available": len(ordered_keys), "archives_processed": 0,
                  "covered_files": 0, "logical_bytes": 0, "physical_bytes": 0, "read_bytes": 0,
                  "raw_required_exceptions": {"protected_or_halo": 0, "package_metadata_unreadable": 0,
                      "no_public_tarball_match": 0, "local_modified_or_unmatched_member": 0,
                      "symlink_or_path_error": 0}, "budget_exhausted": False}}
    # Publish a reviewable in-progress artifact before touching package archives.
    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_path.parent, 0o700)
    write_private(result, output_path, output_path.parent)

    cache_root = (home_root / ".npm/_cacache").resolve()
    archive_budget = _ReadBudget(min(max_read_bytes // 2, 1024 * 1024 * 1024))
    installed_budget = _ReadBudget(max_read_bytes - archive_budget.maximum)
    counters = {"archives_processed": 0, "archives_rejected": 0, "archive_budget_exhausted": False,
                "installed_budget_exhausted": False}
    member_indexes: dict[tuple[str, str], dict[str, dict]] = {}
    for key in ordered_keys:
        source = source_index[key]
        members = _archive_member_index(source, cache_root, key, archive_budget, counters)
        if members:
            member_indexes[key] = members
        if counters["archive_budget_exhausted"]:
            break

    # Group files by package root while querying only indexed node_modules path ranges.
    by_root: dict[str, list[tuple[str, dict]]] = {}
    for package_root, package in package_dirs.items():
        by_root.setdefault(package["scan_root"], []).append((package_root, package))
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        scan_info = dict(db.execute("SELECT key,value FROM scan_info"))
        result["inventory_completed_at"] = scan_info.get("completed_at")
        for scan_root, package_list in sorted(by_root.items()):
            if counters["installed_budget_exhausted"]:
                break
            roots_by_key = {package_root: package for package_root, package in package_list}
            low, high = scan_root + os.sep, scan_root + "0"
            cursor = db.execute(
                "SELECT path,size,allocated_bytes,mtime_ns,kind,is_directory,error FROM entries "
                "INDEXED BY sqlite_autoindex_entries_1 WHERE path>=? AND path<?", (low, high))
            for row in cursor:
                if row["kind"] != "file" or row["is_directory"] or row["error"]:
                    continue
                absolute = row["path"]
                path = Path(absolute)
                if _protected_paths(path, protected):
                    continue
                parent = str(path.parent)
                package = None
                while parent.startswith(scan_root + os.sep):
                    package = roots_by_key.get(parent)
                    if package:
                        break
                    next_parent = str(Path(parent).parent)
                    if next_parent == parent:
                        break
                    parent = next_parent
                if not package:
                    continue
                members = member_indexes.get(package["key"])
                if not members:
                    continue
                relative = path.relative_to(Path(package["package_json"]).parent).as_posix()
                member = members.get(relative)
                if not member or int(row["size"] or 0) != member["size"]:
                    continue
                try:
                    lstat = path.lstat()
                    source_stat = safe_source.identity(lstat)
                    if (not stat.S_ISREG(lstat.st_mode) or lstat.st_size != row["size"]
                            or lstat.st_mtime_ns != row["mtime_ns"]):
                        result["summary"]["raw_required_exceptions"]["local_modified_or_unmatched_member"] += 1
                        continue
                    fd = safe_source.open_path(path, source_stat)
                    digest = hashlib.sha256()
                    observed = 0
                    try:
                        while True:
                            block = os.read(fd, 1024 * 1024)
                            if not block:
                                break
                            observed += len(block)
                            if not installed_budget.take(len(block)):
                                counters["installed_budget_exhausted"] = True
                                break
                            digest.update(block)
                        safe_source.verify_unchanged(fd, source_stat)
                    finally:
                        os.close(fd)
                    if counters["installed_budget_exhausted"]:
                        break
                    if observed != member["size"] or digest.hexdigest() != member["sha256"]:
                        result["summary"]["raw_required_exceptions"]["local_modified_or_unmatched_member"] += 1
                        continue
                    source = source_index[package["key"]]
                    output = {"absolute_path": absolute, "size": observed, "sha256": member["sha256"],
                              "source_url": source["source"], "archive_member": member["archive_member"],
                              "source_integrity": source["integrity"], "package_name": package["name"],
                              "package_version": package["version"], "source_stat": source_stat,
                              "inventory_mtime_ns": row["mtime_ns"], "physical_bytes": int(row["allocated_bytes"] or lstat.st_blocks * 512 or 0),
                              "archive_member_mode": member["archive_mode"], "verification": "sha256_matches_verified_public_tar_member"}
                    result["covered_files"].append(output)
                    result["summary"]["covered_files"] += 1
                    result["summary"]["logical_bytes"] += observed
                    result["summary"]["physical_bytes"] += output["physical_bytes"]
                    # Keep the private artifact incrementally consumable in bounded chunks.
                    covered_count = result["summary"]["covered_files"]
                    if covered_count in {1, 25} or covered_count % 1000 == 0:
                        write_private(result, output_path, output_path.parent)
                except (OSError, ValueError, RuntimeError):
                    result["summary"]["raw_required_exceptions"]["symlink_or_path_error"] += 1
            if counters["installed_budget_exhausted"]:
                break
    finally:
        db.close()
    exhausted = counters["archive_budget_exhausted"] or counters["installed_budget_exhausted"]
    result["status"] = "complete" if not exhausted else "bounded_partial"
    result["done"] = True
    result["summary"]["archive_read_bytes"] = archive_budget.used
    result["summary"]["installed_read_bytes"] = installed_budget.used
    result["summary"]["read_bytes"] = archive_budget.used + installed_budget.used
    result["summary"]["archives_processed"] = counters["archives_processed"]
    result["summary"]["budget_exhausted"] = exhausted
    result["summary"]["archives_rejected"] = counters["archives_rejected"]
    write_private(result, output_path, output_path.parent)
    return result


def _read_inventory(db_path: Path, home_root: Path, min_bytes: int) -> tuple[list[dict], dict]:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        db.row_factory = sqlite3.Row
        info = dict(db.execute("SELECT key,value FROM scan_info"))
        if not info.get("completed_at"):
            raise ValueError("inventory scan is incomplete")
        columns = {row[1] for row in db.execute("PRAGMA table_info(directory_summary)")}
        needed = {"root", "directory", "depth", "logical_bytes", "allocated_bytes", "file_count", "error_count"}
        if not needed <= columns:
            raise ValueError("inventory directory_summary schema is incomplete")
        rows = db.execute(
            "SELECT root,directory,depth,logical_bytes,allocated_bytes,file_count,error_count "
            "FROM directory_summary WHERE allocated_bytes >= ? ORDER BY allocated_bytes DESC",
            (min_bytes,),
        ).fetchall()
        found = []
        for row in rows:
            path = Path(row["directory"])
            if not path.is_absolute() or not path.resolve(strict=False).is_relative_to(home_root):
                continue
            category = _category(path)
            if not category or _is_halo(path):
                continue
            found.append(dict(row) | {"category": category, "path_obj": path})
    finally:
        db.close()

    # Use only non-overlapping subtree rows so summary bytes cannot double count.
    selected: list[dict] = []
    for candidate in sorted(found, key=lambda row: (len(row["path_obj"].parts), -row["allocated_bytes"])):
        p = candidate["path_obj"]
        if any(p != prior["path_obj"] and p.is_relative_to(prior["path_obj"]) for prior in selected):
            continue
        selected.append(candidate)
    return selected, info


def build_manifest(db_path: Path, home_root: Path = Path.home(), min_bytes: int = MIN_ALLOCATED_BYTES,
                   npm_manifest: Path = DEFAULT_NPM_MANIFEST) -> dict:
    home_root = home_root.resolve()
    candidates, scan = _read_inventory(db_path, home_root, min_bytes)
    npm_files, npm_manifest_evidence = _verified_npm_files(db_path, npm_manifest, home_root)
    output_rows = []
    for item in candidates:
        root = item["path_obj"]
        category = item["category"]
        safety_exceptions = []
        if item["error_count"]:
            safety_exceptions.append("inventory_subtree_has_read_errors")
        try:
            mode = root.lstat().st_mode
            if not stat.S_ISDIR(mode):
                safety_exceptions.append("candidate_missing_or_not_plain_directory")
        except OSError:
            safety_exceptions.append("candidate_missing_or_not_observable")
        evidence, source = _evidence_for(root, category, None, home_root)
        covered_files = []
        if root.resolve(strict=False) == (home_root / ".npm/_cacache").resolve(strict=False):
            if npm_manifest_evidence:
                evidence.append(npm_manifest_evidence)
            covered_files = [file for file in npm_files.values()
                             if Path(file["path"]).is_relative_to(root.resolve())]
        if category in LOCKS and not evidence:
            safety_exceptions.append("no_hashable_adjacent_manifest_and_lock_pair")
        else:
            if category in LOCKS:
                safety_exceptions.append("installed_tree_contents_and_local_edits_not_verified")
        if category in {"model-weights", "package-or-build-cache", "xcode-derived-data", "brew-installation"}:
            safety_exceptions.append("unverified_files_remain_raw_required")

        covered_physical = sum(file["physical_bytes"] for file in covered_files)
        covered_logical = sum(file["size"] for file in covered_files)
        if covered_files:
            safety_exceptions.append("all_unlisted_or_hash_mismatching_files_remain_raw_required")

        output_rows.append({
            "absolute_root": str(root),
            "category": category,
            "logical_bytes": int(item["logical_bytes"]),
            "physical_bytes": int(item["allocated_bytes"]),
            "file_count": int(item["file_count"]),
            "inventory_error_count": int(item["error_count"]),
            "source": source,
            "reconstruction": {
                "command": _reconstruction_data(source),
                "semantics": "candidate command data only; never executed; lock recipe does not prove tree contents or local edits",
                "executed": False,
            },
            "evidence": evidence,
            "covered_files": covered_files,
            "classification": "exact-refetch-verified" if covered_files else "raw-required",
            "recipe_backed": False,
            "exact_refetch_verified": bool(covered_files),
            "safe_to_exclude_bytes": covered_physical,
            "safe_to_exclude_logical_bytes": covered_logical,
            "raw_required_physical_bytes": max(0, int(item["allocated_bytes"]) - covered_physical),
            "coverage_assurance": ("listed files match the current local SHA-512 and a source-verified public npm tarball manifest; only listed physical bytes are eligible after encrypted manifest publication is verified" if covered_files else "metadata-only inventory; no tree file hashes or complete local-edit audit; candidate bytes remain raw-required"),
            "raw_required_exceptions": safety_exceptions,
        })

    recipe_evidence_rows = sum(1 for row in output_rows if row["evidence"])
    totals = {key: sum(row[key] for row in output_rows) for key in ("logical_bytes", "physical_bytes", "file_count", "safe_to_exclude_bytes", "safe_to_exclude_logical_bytes", "raw_required_physical_bytes")}
    return {
        "schema": "siso-estate.refetch-trees.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "inventory_db": str(db_path.resolve()),
        "inventory_completed_at": scan["completed_at"],
        "privacy": "private metadata; encrypt before publication",
        "summary": {
            "candidate_count": len(output_rows),
            **totals,
            "recipe_evidence_candidate_count": recipe_evidence_rows,
            "recipe_backed_bytes": 0,
            "exact_refetch_bytes": totals["safe_to_exclude_bytes"],
            "raw_required_bytes": totals["raw_required_physical_bytes"],
            "candidate_byte_reduction": totals["safe_to_exclude_bytes"],
        },
        "rows": output_rows,
    }


def write_private(manifest: dict, output: Path, private_root: Path) -> None:
    private_root = private_root.resolve()
    output = output.resolve(strict=False)
    if not output.is_relative_to(private_root):
        raise ValueError("output must stay under the private refetch-trees directory")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output.parent, 0o700)
    temp = output.with_name(output.name + ".tmp")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temp, output)
        os.chmod(output, 0o600)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--home-root", type=Path, default=Path.home())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--npm-manifest", type=Path, default=DEFAULT_NPM_MANIFEST)
    parser.add_argument("--min-bytes", type=int, default=MIN_ALLOCATED_BYTES)
    args = parser.parse_args(argv)
    private_root = DEFAULT_OUTPUT.parent
    try:
        manifest = build_manifest(args.db, args.home_root, args.min_bytes, args.npm_manifest)
        write_private(manifest, args.output, private_root)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"home-refetch-trees: {error}", file=sys.stderr)
        return 2
    print(json.dumps(manifest["summary"], sort_keys=True))
    print(f"private_manifest={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
