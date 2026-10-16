"""Descriptor-anchored, no-follow source file access for backup producers."""
import os
from pathlib import Path, PurePosixPath
import stat


def identity(value):
    return {"device": value.st_dev, "inode": value.st_ino, "size": value.st_size,
            "mtime_ns": value.st_mtime_ns, "mode": value.st_mode}


def _directory_identity(value):
    return {"device": value.st_dev, "inode": value.st_ino, "mode": value.st_mode}


def _open_directory(path):
    absolute = Path(path)
    if not absolute.is_absolute():
        raise ValueError("source root must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current = os.open(os.sep, flags)
    try:
        for component in absolute.parts[1:]:
            if component in ("", ".", ".."):
                raise ValueError("unsafe source root")
            next_fd = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = next_fd
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise ValueError("source root is not a directory")
        return current
    except Exception:
        os.close(current)
        raise


def _parts(relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("invalid source-relative path")
    path = PurePosixPath(relative)
    if path.is_absolute() or path.as_posix() != relative or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("unsafe source-relative path")
    return path.parts


def open_source(root, relative, expected_identity=None, expected_root_identity=None):
    """Open one regular source beneath root, refusing every symlink component.

    The returned descriptor is bound to the opened directory chain. Compare its
    fstat identity before and after reading with the planned identity.
    """
    parts = _parts(relative)
    directory = _open_directory(root)
    try:
        root_identity = _directory_identity(os.fstat(directory))
        if expected_root_identity is not None and root_identity != expected_root_identity:
            raise RuntimeError("source root identity changed")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        for component in parts[:-1]:
            next_fd = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(parts[-1], file_flags, dir_fd=directory)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            os.close(fd)
            raise ValueError("source is not a regular file")
        opened_identity = identity(opened)
        if expected_identity is not None and opened_identity != expected_identity:
            os.close(fd)
            raise RuntimeError("source identity changed before read")
        return fd, root_identity, opened_identity
    finally:
        os.close(directory)


def open_path(path, expected_identity=None):
    """Open an absolute path with no-follow parent and leaf traversal.

    macOS exposes `/var` as a symlink to `/private/var`. Normalize that known
    OS alias, then walk the caller's remaining path from `/` with O_NOFOLLOW at
    every component. Do not resolve arbitrary parents: doing so would accept a
    caller-supplied symlink directory.
    """
    absolute = Path(path).absolute()
    if len(absolute.parts) > 1 and absolute.parts[1] == "var" and Path("/var").is_symlink():
        alias_target = Path("/var").resolve(strict=True)
        if alias_target != Path("/private/var"):
            raise ValueError("unexpected /var source alias")
        absolute = alias_target.joinpath(*absolute.parts[2:])
    fd, _, opened_identity = open_source(absolute.parent, absolute.name, expected_identity)
    return fd


def snapshot(root, relatives):
    """Capture root and file identities through no-follow descriptors."""
    root_fd = _open_directory(root)
    try:
        root_identity = _directory_identity(os.fstat(root_fd))
    finally:
        os.close(root_fd)
    files = {}
    for relative in relatives:
        fd, _, file_identity = open_source(root, relative, expected_root_identity=root_identity)
        try:
            if identity(os.fstat(fd)) != file_identity:
                raise RuntimeError("source changed during identity capture")
            files[relative] = file_identity
        finally:
            os.close(fd)
    return root_identity, files


def verify_unchanged(fd, expected_identity):
    if identity(os.fstat(fd)) != expected_identity:
        raise RuntimeError("source changed while reading")
