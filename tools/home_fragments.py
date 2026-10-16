#!/usr/bin/env python3
"""Bounded, sparse-aware fragments for whole-home streamed backups.

The module has no transport or policy side effects. ``plan_file`` records file
identity and data extents; ``archive_factory`` implements the contract consumed
by data-backup.run_streamed; ``reconstruct_file`` verifies all fragment hashes
before publishing a new sparse destination.
"""
import errno
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tarfile
import threading

MAX_FRAGMENT_BYTES = 256 * 1024 * 1024
IO_BLOCK_BYTES = 1024 * 1024
DESCRIPTOR_VERSION = 1
_safe_source_spec = importlib.util.spec_from_file_location("estate_safe_source", Path(__file__).with_name("safe_source.py"))
safe_source = importlib.util.module_from_spec(_safe_source_spec)
_safe_source_spec.loader.exec_module(safe_source)


def _identity(value):
    return {"device": value.st_dev, "inode": value.st_ino, "size": value.st_size,
            "mtime_ns": value.st_mtime_ns, "mode": value.st_mode}


def _open_source(path, expected_identity=None):
    return safe_source.open_path(path, expected_identity)


def _validate_archive_path(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts
            or PurePosixPath(value).as_posix() != value or value == "MANIFEST.json"):
        raise ValueError("unsafe fragment archive path")
    return value


def _data_extents(fd, size):
    """Return data extents. Unsupported SEEK_DATA falls back to the full file."""
    if size == 0:
        return []
    if not hasattr(os, "SEEK_DATA") or not hasattr(os, "SEEK_HOLE"):
        return [(0, size)]
    extents = []
    cursor = 0
    try:
        while cursor < size:
            try:
                start = os.lseek(fd, cursor, os.SEEK_DATA)
            except OSError as error:
                if error.errno == errno.ENXIO:
                    break
                if error.errno in (errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
                    return [(0, size)]
                raise
            try:
                end = os.lseek(fd, start, os.SEEK_HOLE)
            except OSError as error:
                if error.errno in (errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
                    return [(0, size)]
                raise
            if start < cursor or end <= start:
                raise ValueError("filesystem returned an invalid sparse extent")
            end = min(end, size)
            extents.append((start, end - start))
            cursor = end
    except OSError:
        raise
    return extents


def plan_file(path, archive_prefix="fragments", fragment_bytes=MAX_FRAGMENT_BYTES):
    """Plan bounded pieces without reading payload bytes or allocating storage.

    Each fragment records its source offset and the source's logical size,
    permission bits, nanosecond mtime, and stable device/inode identity.
    """
    if type(fragment_bytes) is not int or not 0 < fragment_bytes <= MAX_FRAGMENT_BYTES:
        raise ValueError("fragment size must be between 1 byte and 256 MiB")
    prefix = _validate_archive_path(archive_prefix)
    fd = _open_source(path)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("source is not a regular file")
        identity = _identity(before)
        extents = _data_extents(fd, before.st_size)
        after = os.fstat(fd)
        if _identity(after) != identity:
            raise RuntimeError("source changed while planning fragments")
    finally:
        os.close(fd)
    fragments = []
    number = 0
    for offset, length in extents:
        consumed = 0
        while consumed < length:
            piece_size = min(fragment_bytes, length - consumed)
            archive_path = f"{prefix}/{number:08d}.bin"
            fragments.append({"path": archive_path, "offset": offset + consumed, "size": piece_size,
                              "logical_size": before.st_size, "mode": stat.S_IMODE(before.st_mode),
                              "mtime_ns": before.st_mtime_ns, "source_identity": identity})
            number += 1
            consumed += piece_size
    return {"version": DESCRIPTOR_VERSION, "logical_size": before.st_size,
            "mode": stat.S_IMODE(before.st_mode), "mtime_ns": before.st_mtime_ns,
            "source_identity": identity, "fragments": fragments}


def _descriptor_entry(descriptor, hashes):
    result = {key: descriptor[key] for key in ("version", "logical_size", "mode", "mtime_ns", "source_identity")}
    result["fragments"] = [dict(fragment, sha256=hashes[fragment["path"]])
                           for fragment in descriptor["fragments"]]
    return result


def fragment_groups(descriptor, max_group_bytes=MAX_FRAGMENT_BYTES):
    """Split one file plan into independently streamed groups by payload bytes."""
    if type(max_group_bytes) is not int or not 0 < max_group_bytes <= MAX_FRAGMENT_BYTES:
        raise ValueError("group size must be between 1 byte and 256 MiB")
    _validate_descriptor(descriptor, require_hashes=False)
    groups, current, current_bytes = [], [], 0
    for fragment in descriptor["fragments"]:
        if current and current_bytes + fragment["size"] > max_group_bytes:
            groups.append(current)
            current, current_bytes = [], 0
        if fragment["size"] > max_group_bytes:
            raise ValueError("fragment is larger than bounded group")
        current.append(fragment)
        current_bytes += fragment["size"]
    if current or not groups:
        groups.append(current)
    base = {key: descriptor[key] for key in ("version", "logical_size", "mode", "mtime_ns", "source_identity")}
    return [dict(base, fragments=group) for group in groups]


def archive_upper_bytes(descriptor, descriptor_path="DESCRIPTOR.json"):
    """Conservative encoded-size reservation for one group, based on stored extents.

    Logical file size is deliberately excluded: a 494 GB sparse image with a few
    allocated blocks reserves by its fragment bytes and small tar/manifest bounds.
    """
    descriptor_path = _validate_archive_path(descriptor_path)
    _validate_descriptor(descriptor, require_hashes=False)
    hashes = {item["path"]: "0" * 64 for item in descriptor["fragments"]}
    descriptor_size = len(json.dumps(_descriptor_entry(descriptor, hashes), separators=(",", ":")).encode())
    tar_metadata = 10_240 * (len(descriptor["fragments"]) + 3)
    payload = sum(item["size"] for item in descriptor["fragments"])
    # The fixed allowance covers zstd framing, archive terminators, age framing,
    # and small MANIFEST.json metadata; groups are capped at 256 MiB.
    return payload + descriptor_size + tar_metadata + 2_000_000


def merge_descriptors(descriptors, expected_plan=None):
    """Join verified groups only when they exactly cover the original plan."""
    if not isinstance(descriptors, (list, tuple)) or not descriptors or expected_plan is None:
        raise ValueError("verified groups and the original whole-file plan are required")
    _validate_descriptor(expected_plan, require_hashes=False)
    first = descriptors[0]
    shared = {key: first.get(key) for key in ("version", "logical_size", "mode", "mtime_ns", "source_identity")}
    fragments = []
    for descriptor in descriptors:
        if any(descriptor.get(key) != value for key, value in shared.items()):
            raise ValueError("fragment groups describe different source files")
        fragments.extend(descriptor.get("fragments", []))
    merged = dict(shared, fragments=fragments)
    ordered = _validate_descriptor(merged, require_hashes=True)
    expected = _validate_descriptor(expected_plan, require_hashes=False)
    expected_by_path = {item["path"]: item for item in expected}
    if len(expected_by_path) != len(expected) or len(ordered) != len(expected):
        raise ValueError("verified fragment groups do not cover the complete planned file")
    for item in ordered:
        original = expected_by_path.get(item["path"])
        if original is None or any(item.get(key) != original.get(key)
                                   for key in ("offset", "size", "logical_size", "mode", "mtime_ns", "source_identity")):
            raise ValueError("verified fragment group differs from original whole-file plan")
    return merged


def archive_factory(descriptor, source_path, source_relative, descriptor_path="DESCRIPTOR.json",
                    encrypt=True, recipient=None):
    """Make a run_streamed-compatible factory for one bounded fragment group.

    ``descriptor`` is normally a plan_file result. The returned factory emits
    fragment files, DESCRIPTOR.json, and the validator's ordinary MANIFEST.json
    through tar | zstd [| age], with no payload staging files.
    """
    descriptor_path = _validate_archive_path(descriptor_path)
    if descriptor_path == "MANIFEST.json":
        raise ValueError("descriptor path collides with transport manifest")
    planned = json.loads(json.dumps(descriptor))
    _validate_descriptor(planned, require_hashes=False)
    if descriptor_path in {item["path"] for item in planned["fragments"]}:
        raise ValueError("descriptor path collides with a fragment")
    source_relative = str(source_relative).replace(os.sep, "/")
    if not source_relative or source_relative.startswith("/") or ".." in PurePosixPath(source_relative).parts:
        raise ValueError("unsafe original source path")

    def factory(pl, inc, stage, guard, sqlite_limit):
        if len(inc) != 1 or os.path.abspath(inc[0]) != os.path.abspath(source_path):
            raise ValueError("fragment stream must contain exactly its planned source")
        compressor = guard.register(subprocess.Popen(
            ["zstd", "-q", "-T1", "-3", "-c"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
        if encrypt:
            if not recipient:
                raise ValueError("encrypted fragment stream requires a recipient")
            age = guard.register(subprocess.Popen(
                ["age", "-r", recipient], stdin=compressor.stdout,
                stdout=__import__("subprocess").PIPE, stderr=__import__("subprocess").DEVNULL))
            compressor.stdout.close()
            output = age.stdout
        else:
            age, output = None, compressor.stdout
        state = {"error": None, "files": 0, "source_inputs": {}, "descriptor": None}
        factory.last_state = state

        def produce():
            fd = None
            try:
                fd = _open_source(source_path, planned["source_identity"])
                before = os.fstat(fd)
                if _identity(before) != planned["source_identity"]:
                    raise RuntimeError("source identity changed after fragment planning")
                entries, hashes, total_metadata = [], {}, 0
                with tarfile.open(fileobj=compressor.stdin, mode="w|") as archive:
                    for fragment in planned["fragments"]:
                        guard.check()
                        path = fragment["path"]
                        length, sha = 0, hashlib.sha256()

                        class Reader:
                            def read(self, amount=-1):
                                nonlocal length
                                guard.check()
                                remaining = fragment["size"] - length
                                if remaining <= 0:
                                    return b""
                                amount = min(IO_BLOCK_BYTES, remaining) if amount < 0 else min(amount, IO_BLOCK_BYTES, remaining)
                                data = os.pread(fd, amount, fragment["offset"] + length)
                                if not data:
                                    raise RuntimeError("source fragment was truncated")
                                length += len(data)
                                sha.update(data)
                                return data

                        info = tarfile.TarInfo(path)
                        info.size = fragment["size"]
                        info.mode = fragment["mode"]
                        info.mtime = fragment["mtime_ns"] // 1_000_000_000
                        archive.addfile(info, Reader())
                        if length != fragment["size"]:
                            raise RuntimeError("source fragment length changed")
                        digest = sha.hexdigest()
                        hashes[path] = digest
                        entries.append({"path": path, "size": length, "sha256": digest})
                        state["source_inputs"][path] = {
                            "path": source_relative,
                            "source_stat": {"file": planned["source_identity"], "offset": fragment["offset"],
                                            "logical_size": fragment["logical_size"], "mode": fragment["mode"],
                                            "mtime_ns": fragment["mtime_ns"]}}
                        total_metadata += len(path.encode()) + 180
                    after = os.fstat(fd)
                    current_path = os.stat(source_path, follow_symlinks=False)
                    if _identity(after) != planned["source_identity"] or _identity(current_path) != planned["source_identity"]:
                        raise RuntimeError("source changed or was replaced while streaming")
                    archive_descriptor = _descriptor_entry(planned, hashes)
                    descriptor_bytes = json.dumps(archive_descriptor, separators=(",", ":")).encode()
                    descriptor_sha = hashlib.sha256(descriptor_bytes).hexdigest()
                    info = tarfile.TarInfo(descriptor_path)
                    info.size = len(descriptor_bytes)
                    archive.addfile(info, io.BytesIO(descriptor_bytes))
                    entries.append({"path": descriptor_path, "size": len(descriptor_bytes), "sha256": descriptor_sha})
                    state["source_inputs"][descriptor_path] = {
                        "path": source_relative,
                        "source_stat": {"file": planned["source_identity"], "descriptor": True}}
                    total_metadata += len(descriptor_path.encode()) + 180
                    if total_metadata > 16_000_000:
                        raise RuntimeError("fragment stream manifest exceeds metadata quota")
                    manifest = json.dumps({"files": entries}, separators=(",", ":")).encode()
                    if len(manifest) > 16_000_000:
                        raise RuntimeError("fragment stream manifest exceeds metadata quota")
                    info = tarfile.TarInfo("MANIFEST.json")
                    info.size = len(manifest)
                    archive.addfile(info, io.BytesIO(manifest))
                    state["descriptor"] = archive_descriptor
                    state["files"] = len(entries)
            except Exception:
                state["error"] = RuntimeError("fragment archive source failed")
            finally:
                if fd is not None:
                    os.close(fd)
                try:
                    compressor.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

        thread = threading.Thread(target=produce, name="estate-home-fragments", daemon=True)
        thread.start()
        return output, thread, state, [process for process in (compressor, age) if process]

    factory.last_state = None
    return factory


def _validate_descriptor(descriptor, require_hashes=True):
    if not isinstance(descriptor, dict) or descriptor.get("version") != DESCRIPTOR_VERSION:
        raise ValueError("unsupported fragment descriptor")
    logical_size, mode, mtime_ns = descriptor.get("logical_size"), descriptor.get("mode"), descriptor.get("mtime_ns")
    if any(type(value) is not int or value < 0 for value in (logical_size, mode, mtime_ns)):
        raise ValueError("invalid fragment file metadata")
    if mode > 0o7777 or not isinstance(descriptor.get("source_identity"), dict):
        raise ValueError("invalid fragment source identity")
    fragments = descriptor.get("fragments")
    if not isinstance(fragments, list):
        raise ValueError("invalid fragment inventory")
    ordered = sorted(fragments, key=lambda item: (item.get("offset", -1), item.get("path", "")) if isinstance(item, dict) else (-1, ""))
    end = 0
    paths = set()
    for fragment in ordered:
        if not isinstance(fragment, dict):
            raise ValueError("invalid fragment descriptor")
        path = _validate_archive_path(fragment.get("path"))
        offset, size = fragment.get("offset"), fragment.get("size")
        if type(offset) is not int or type(size) is not int or offset < 0 or size <= 0 or size > MAX_FRAGMENT_BYTES:
            raise ValueError("unsafe fragment offset or size")
        if offset + size > logical_size or offset < end or path in paths:
            raise ValueError("overlapping or out-of-bounds fragment")
        if fragment.get("logical_size") != logical_size or fragment.get("mode") != mode or fragment.get("mtime_ns") != mtime_ns:
            raise ValueError("fragment metadata differs from descriptor")
        if require_hashes and (not isinstance(fragment.get("sha256"), str)
                               or len(fragment["sha256"]) != 64
                               or any(char not in "0123456789abcdef" for char in fragment["sha256"])):
            raise ValueError("invalid fragment hash")
        end = offset + size
        paths.add(path)
    return ordered


def reconstruct_file(descriptor, fragment_paths, destination):
    """Verify fragment sources and reconstruct into a new sparse file only."""
    fragments = _validate_descriptor(descriptor, require_hashes=True)
    if not isinstance(fragment_paths, dict) or set(fragment_paths) != {item["path"] for item in fragments}:
        raise ValueError("fragment source mapping is incomplete or has extras")
    target = Path(destination).absolute()
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink() or target.exists() or target.is_symlink():
        raise ValueError("reconstruction destination must be new in an existing directory")
    parent_real = parent.resolve(strict=True)
    target = parent_real / target.name
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    created = os.fstat(fd)
    try:
        os.ftruncate(fd, descriptor["logical_size"])
        for fragment in fragments:
            digest = hashlib.sha256()
            source_fd = _open_source(fragment_paths[fragment["path"]])
            with os.fdopen(source_fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                    raise ValueError("fragment source is not a regular file")
                written = 0
                while True:
                    block = source.read(IO_BLOCK_BYTES)
                    if not block:
                        break
                    written += len(block)
                    if written > fragment["size"]:
                        raise ValueError("fragment payload is larger than descriptor")
                    digest.update(block)
                    view = memoryview(block)
                    position = fragment["offset"] + written - len(block)
                    while view:
                        count = os.pwrite(fd, view, position)
                        if count <= 0:
                            raise OSError("short reconstruction write")
                        position += count
                        view = view[count:]
            if written != fragment["size"] or digest.hexdigest() != fragment["sha256"]:
                raise ValueError("fragment size or hash verification failed")
        os.fchmod(fd, descriptor["mode"])
        os.utime(fd, ns=(descriptor["mtime_ns"], descriptor["mtime_ns"]))
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            current = target.lstat()
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                target.unlink()
        except OSError:
            pass
        raise
    else:
        os.close(fd)
    return target
