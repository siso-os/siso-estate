"""Backend-neutral archive manifests and verified, transactional restore.

Only archive names are public metadata; source file names remain in the archive.
All byte hashing/copying is streaming. A location must verify in full before use.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import time
import shutil
import subprocess
import tarfile
import tempfile
import threading

CHUNK_LIMIT = 95_000_000                 # strictly below this decimal bound
PUSH_LIMIT = 2_000_000_000
SHARD_LIMIT = 5_000_000_000
ASSET_LIMIT = 2 * 1024 ** 3


def github(location, write=False):
    match = re.fullmatch(r"github:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:#([A-Za-z0-9_.-]+))?", location or "")
    if not match:
        raise ValueError("invalid GitHub location")
    owner, repo, release = match.groups()
    if write and owner.lower() != "siso-data":
        raise ValueError("data writes require the isolated siso-data storage organization")
    return owner, repo, release


def digest(path):
    sha, size = hashlib.sha256(), 0
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
            size += len(block)
    return size, sha.hexdigest()


def build(directory, names, location, encrypted):
    parts, whole, size = [], hashlib.sha256(), 0
    for name in names:
        length, sha = digest(Path(directory) / name)
        parts.append({"name": name, "size": length, "sha256": sha})
        size += length
        with open(Path(directory) / name, "rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                whole.update(block)
    return {"version": 1, "path": "data.tar.zst.age" if encrypted else "data.tar.zst",
            "sha256": whole.hexdigest(), "size": size, "parts": parts,
            "locations": [location], "pins": {}, "encrypted": encrypted}


def validate(manifest):
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("parts"), list)
            or not isinstance(manifest.get("locations"), list) or not isinstance(manifest.get("pins", {}), dict)
            or type(manifest.get("encrypted", False)) is not bool):
        raise ValueError("invalid archive manifest shape")
    safe_name(manifest["path"])
    parts = manifest["parts"]
    if not parts or not manifest["locations"]:
        raise ValueError("empty archive manifest")
    seen = set()
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("sha256"), str):
            raise ValueError("invalid archive part shape")
        safe_name(part["name"])
        if part["name"] in seen or type(part["size"]) is not int or not 0 < part["size"] <= ASSET_LIMIT:
            raise ValueError("invalid part inventory")
        seen.add(part["name"])
        if not re.fullmatch(r"[a-f0-9]{64}", part["sha256"]):
            raise ValueError("invalid part checksum")
    if type(manifest["size"]) is not int or sum(p["size"] for p in parts) != manifest["size"]:
        raise ValueError("invalid archive size")
    if not isinstance(manifest.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"]):
        raise ValueError("invalid archive checksum")
    for location in manifest["locations"]:
        if not isinstance(location, str):
            raise ValueError("invalid location shape")
        if location.startswith("github:"):
            _, _, release = github(location)
            pin = manifest.get("pins", {}).get(location, {})
            if not isinstance(pin, dict):
                raise ValueError("invalid location pin shape")
            if release:
                if not isinstance(pin.get("assets", {}), dict):
                    raise ValueError("invalid asset pin shape")
                if not isinstance(pin.get("release_id"), int):
                    raise ValueError("release identifier missing")
                for part in parts:
                    if not isinstance(pin.get("assets", {}).get(part["name"]), int):
                        raise ValueError("asset identifier missing")
            else:
                commits = pin.get("commits")
                if commits is not None:
                    if (not isinstance(commits, dict) or set(commits) != {p["name"] for p in parts}
                            or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{40,64}", v) for v in commits.values())):
                        raise ValueError("per-part Git revisions missing or malformed")
                elif not isinstance(pin.get("commit"), str) or not re.fullmatch(r"[a-f0-9]{40,64}", pin["commit"]):
                    raise ValueError("Git revision missing")
                if any(p["size"] >= CHUNK_LIMIT for p in parts):
                    raise ValueError("Git part exceeds decimal blob bound")
        elif not re.fullmatch(r"disk:(HOLD|mini):/.+", location):
            raise ValueError("invalid fallback location")


def safe_name(name):
    if not isinstance(name, str) or not name or PurePosixPath(name).name != name or name in (".", "..") or "\\" in name or "\0" in name:
        raise ValueError("unsafe part name")


def git_environment():
    # Do not inherit hook/index/config selectors from the calling source repository.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def binary_command(args, destination, expected_size=None, check=None, register=None):
    if check:
        check()
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=git_environment())
    if register:
        register(process)
    deadline, count = time.monotonic() + 7200, 0
    try:
        with open(destination, "wb") as out:
            while True:
                if check:
                    check()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                    raise RuntimeError("download timeout")
                block = process.stdout.read1(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if expected_size is not None and count > expected_size:
                    raise ValueError("download exceeds declared size")
                if check:
                    check()
                out.write(block)
                out.flush()
                if check:
                    check()
        if process.wait(timeout=max(1, deadline - time.monotonic())) or (expected_size is not None and count != expected_size):
            raise RuntimeError("download failed or truncated")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def fetch_location(location, manifest, target, sh, disk_machine=None, check=None, register=None):
    if location.startswith("disk:"):
        _, machine, directory = location.split(":", 2)
        if machine != disk_machine:
            raise RuntimeError("disk location not mounted on this machine")
        root = Path(directory).resolve(strict=True)
        for part in manifest["parts"]:
            source = root / part["name"]
            if source.is_symlink() or not source.is_file() or source.stat().st_size != part["size"]:
                raise RuntimeError("disk part unavailable")
            shutil.copyfile(source, Path(target) / part["name"])
        return
    owner, repo, release = github(location)
    pin = manifest["pins"][location]
    if pin.get("commits"):
        for part in manifest["parts"]:
            fetch_part(location, part, pin, target, sh, disk_machine, check, register)
        return
    binary_options = {"check": check, "register": register} if check or register else {}
    if release:
        for part in manifest["parts"]:
            binary_command(["gh", "api", f"repos/{owner}/{repo}/releases/assets/{pin['assets'][part['name']]}",
                            "-H", "Accept: application/octet-stream"], Path(target) / part["name"], part["size"], **binary_options)
    else:
        checkout = Path(target) / ".git-download"
        checkout.mkdir()
        for args in [("git", "init", "-q"),
                     ("git", "remote", "add", "origin", f"https://github.com/{owner}/{repo}.git"),
                     ("git", "fetch", "-q", "--depth", "1", "origin", pin["commit"])]:
            if sh(*args, cwd=str(checkout), timeout=7200)[0]:
                raise RuntimeError("Git snapshot download failed")
        code, head, _ = sh("git", "rev-parse", "FETCH_HEAD", cwd=str(checkout))
        if code or head != pin["commit"]:
            raise RuntimeError("Git snapshot revision mismatch")
        for part in manifest["parts"]:
            binary_command(["git", "-C", str(checkout), "show", head + ":" + part["name"]], Path(target) / part["name"], part["size"], **binary_options)
        shutil.rmtree(checkout)


def verified_archive(manifest, destination, sh, disk_machine=None, fetch=None):
    """Try complete locations in order; atomically publish only a fully verified archive."""
    validate(manifest)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    for location in manifest["locations"]:
        with tempfile.TemporaryDirectory(prefix=".estate-download-", dir=destination.parent) as temporary:
            try:
                (fetch or fetch_location)(location, manifest, temporary, sh, disk_machine)
                archive = Path(temporary) / "verified-archive"
                whole, length = hashlib.sha256(), 0
                with archive.open("wb") as out:
                    for part in manifest["parts"]:
                        source = Path(temporary) / part["name"]
                        if source.is_symlink() or digest(source) != (part["size"], part["sha256"]):
                            raise ValueError("part integrity failed")
                        with source.open("rb") as chunk:
                            for block in iter(lambda: chunk.read(1024 * 1024), b""):
                                out.write(block)
                                whole.update(block)
                                length += len(block)
                if length != manifest["size"] or whole.hexdigest() != manifest["sha256"]:
                    raise ValueError("archive integrity failed")
                os.replace(archive, destination)
                return location
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                continue
    raise RuntimeError("no complete verified archive location available")


def extract_archive(archive, destination, encrypted=False, key=None):
    """Extract in an isolated sibling, reject links/devices/traversal, publish on success."""
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError("restore destination must not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".estate-extract-", dir=destination.parent) as stage:
        tree = Path(stage) / "tree"
        tree.mkdir()
        source = open(archive, "rb")
        processes = []
        try:
            if encrypted:
                process = subprocess.Popen(["age", "-d", "-i", key], stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                processes.append(process)
                source.close()
                source = process.stdout
            process = subprocess.Popen(["zstd", "-q", "-d", "-c"], stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processes.append(process)
            source.close()
            with tarfile.open(fileobj=process.stdout, mode="r|") as tar:
                for member in tar:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or "\\" in member.name or not (member.isfile() or member.isdir()):
                        raise ValueError("unsafe archive entry")
                    target = tree.joinpath(*path.parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as src, target.open("xb") as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
            # zstd may still be draining after tar's end marker; a failed decoder is never success.
            while process.stdout.read(1024 * 1024):
                pass
            if any(p.wait() != 0 for p in reversed(processes)):
                raise RuntimeError("archive decoder failed")
            os.rename(tree, destination)
        except tarfile.TarError:
            raise ValueError("archive structure is invalid") from None
        finally:
            source.close()
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait()
                if process.stdout:
                    process.stdout.close()


def verify_parts(manifest, directory):
    """Verify a mounted block's parts without fetching anything."""
    validate(manifest)
    whole, total = hashlib.sha256(), 0
    for part in manifest["parts"]:
        path = Path(directory) / part["name"]
        if path.is_symlink() or digest(path) != (part["size"], part["sha256"]):
            raise ValueError("mounted part integrity failed")
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                whole.update(block)
                total += len(block)
    if (total, whole.hexdigest()) != (manifest["size"], manifest["sha256"]):
        raise ValueError("mounted archive integrity failed")
    return True


def _shell(*args, cwd=None, timeout=3600):
    result = subprocess.run(args, cwd=cwd, env=git_environment(), capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def restore_manifest(manifest, destination, sh=None, disk_machine=None, key=None):
    """Independently fetch and verify then safely extract to a new destination."""
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError("restore destination must not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".estate-restore-", dir=destination.parent) as stage:
        archive = Path(stage) / "archive"
        location = verified_archive(manifest, archive, sh or _shell, disk_machine)
        extract_archive(archive, destination, manifest.get("encrypted", False), key or os.path.expanduser("~/.config/siso/age/estate-backup.key"))
        return location


def fetch_part(location, part, pin, target, sh, disk_machine=None, check=None, register=None):
    """Fetch one immutable part; a per-part commit never pulls prior parts' objects."""
    single_pin = dict(pin)
    if single_pin.get("commits"):
        single_pin["commit"] = single_pin.pop("commits")[part["name"]]
    fetch_location(location, {"parts": [part], "pins": {location: single_pin}}, target, sh, disk_machine, check, register)
    path = Path(target) / part["name"]
    if path.is_symlink() or digest(path) != (part["size"], part["sha256"]):
        raise ValueError("remote part integrity failed")
    return path


def verify_streamed_part(location, part, pin, target, sh, validator, archive_hash,
                         disk_machine=None, check=None, register=None):
    """Independently verify and stream one bounded remote part into the archive validator.

    Call once per selected part for random sampling, or for every part before publishing
    a completion marker. The caller owns and removes the one-part target directory.
    """
    path = fetch_part(location, part, pin, target, sh, disk_machine, check, register)
    validator.write_from(path, archive_hash)
    return path


class StreamValidator:
    """Decrypt/decompress/hash a remote tar stream, optionally saving bounded samples."""
    def __init__(self, encrypted=False, key=None, check=lambda: None, register=lambda p: None,
                 sample_paths=None, sample_max_bytes=16_000_000):
        sample_paths = {} if sample_paths is None else sample_paths
        if (not isinstance(sample_paths, dict) or type(sample_max_bytes) is not int or sample_max_bytes < 0
                or any(not isinstance(name, str) or not name or PurePosixPath(name).as_posix() != name
                       or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name
                       for name in sample_paths)
                or any(not isinstance(target, (str, os.PathLike)) for target in sample_paths.values())):
            raise ValueError("invalid streamed sample selection")
        targets = [Path(target).absolute() for target in sample_paths.values()]
        if len(set(targets)) != len(targets):
            raise ValueError("duplicate streamed sample destination")
        resolved_targets = []
        for target in targets:
            parent = target.parent
            if not parent.is_dir() or target.exists() or target.is_symlink():
                raise ValueError("streamed sample destination must be new in an existing real directory")
            resolved_targets.append(parent.resolve(strict=True) / target.name)
        if len(set(resolved_targets)) != len(resolved_targets):
            raise ValueError("duplicate streamed sample destination")
        self.check = check
        self.processes = []
        self.error = None
        self.result = None
        self.input = None
        self.sample_paths = dict(zip(sample_paths, resolved_targets))
        self.sample_max_bytes = sample_max_bytes
        self.samples = {}
        self.samples_verified = False
        self._owned_samples = []
        if encrypted:
            age = subprocess.Popen(["age", "-d", "-i", key], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            register(age)
            self.processes.append(age)
            self.input = age.stdin
            zstd = subprocess.Popen(["zstd", "-q", "-d", "-c"], stdin=age.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            age.stdout.close()
        else:
            zstd = subprocess.Popen(["zstd", "-q", "-d", "-c"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.input = zstd.stdin
        register(zstd)
        self.processes.append(zstd)
        self.output = zstd.stdout
        self.thread = threading.Thread(target=self._validate, name="estate-remote-tar", daemon=True)
        self.thread.start()

    def _validate(self):
        files, expected = {}, None
        metadata_bytes, sample_bytes = 0, 0
        try:
            with tarfile.open(fileobj=self.output, mode="r|") as archive:
                for member in archive:
                    self.check()
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or ".." in path.parts or "\\" in member.name or not (member.isfile() or member.isdir()):
                        raise ValueError("unsafe streamed tar entry")
                    if member.isdir():
                        continue
                    if expected is not None or member.name in files:
                        raise ValueError("duplicate or trailing streamed tar file")
                    if member.name == "MANIFEST.json":
                        if member.size > 16_000_000:
                            raise ValueError("internal manifest too large")
                        with archive.extractfile(member) as source:
                            expected = json.load(source)
                        continue
                    sample_target = self.sample_paths.get(member.name)
                    if sample_target is not None:
                        if sample_bytes + member.size > self.sample_max_bytes:
                            raise ValueError("streamed samples exceed aggregate byte limit")
                        fd, provisional = tempfile.mkstemp(prefix=".estate-sample-", dir=sample_target.parent)
                        opened = os.fdopen(fd, "wb")
                        status = os.fstat(fd)
                        owned = {"path": Path(provisional), "device": status.st_dev, "inode": status.st_ino}
                        self._owned_samples.append(owned)
                    else:
                        opened = None
                    sha, length = hashlib.sha256(), 0
                    try:
                        with archive.extractfile(member) as source:
                            for block in iter(lambda: source.read(1024 * 1024), b""):
                                self.check()
                                sha.update(block)
                                length += len(block)
                                if opened:
                                    opened.write(block)
                        if opened:
                            opened.flush()
                            os.fsync(opened.fileno())
                    finally:
                        if opened:
                            opened.close()
                    if length != member.size:
                        raise ValueError("truncated streamed sample source")
                    if sample_target is not None:
                        sample_bytes += length
                        self.samples[member.name] = {"path": str(sample_target), "size": length,
                                                     "sha256": sha.hexdigest()}
                    files[member.name] = {"path": member.name, "size": length, "sha256": sha.hexdigest()}
                    metadata_bytes += len(member.name.encode()) + 180
                    if metadata_bytes > 16_000_000:
                        raise ValueError("streamed file inventory too large")
            while self.output.read(1024 * 1024):
                self.check()
            if not isinstance(expected, dict) or not isinstance(expected.get("files"), list):
                raise ValueError("streamed internal manifest missing")
            entries = expected["files"]
            if (len(entries) != len(files) or any(not isinstance(p, dict) or files.get(p.get("path")) != p for p in entries)
                    or len({p["path"] for p in entries}) != len(entries)):
                raise ValueError("streamed source file integrity failed")
            self.result = {"files": len(files), "bytes": sum(p["size"] for p in files.values()),
                           "files_by_path": files, "samples": self.samples}
        except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
            reasons = {'unsafe streamed tar entry', 'duplicate or trailing streamed tar file',
                       'internal manifest too large', 'streamed samples exceed aggregate byte limit',
                       'truncated streamed sample source', 'streamed file inventory too large',
                       'streamed internal manifest missing', 'streamed source file integrity failed'}
            reason = str(error) if str(error) in reasons else type(error).__name__
            self.error = ValueError("remote streamed archive validation failed: " + reason)
            self._discard_samples()
        finally:
            self.output.close()

    def _discard_samples(self):
        for owned in self._owned_samples:
            path = owned["path"]
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == (owned["device"], owned["inode"]) and path.is_file():
                    path.unlink()
            except OSError:
                pass
        self._owned_samples.clear()
        self.samples.clear()

    def _publish_samples(self):
        """Atomically expose only complete validated samples, without replacing existing files."""
        for name, sample in self.samples.items():
            target = self.sample_paths[name]
            provisional = next((item for item in self._owned_samples
                                if item["path"].parent == target.parent and item["path"].name.startswith(".estate-sample-")), None)
            if provisional is None:
                raise RuntimeError("streamed sample staging file is missing")
            os.link(provisional["path"], target, follow_symlinks=False)
            self._owned_samples.append({"path": target, "device": provisional["device"], "inode": provisional["inode"]})
            provisional["path"].unlink()
            self._owned_samples.remove(provisional)

    def write_from(self, path, whole):
        with open(path, "rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                self.check()
                if self.error:
                    raise self.error
                whole.update(block)
                self.input.write(block)
                self.input.flush()

    def finish(self):
        try:
            self.input.close()
            self.thread.join(timeout=300)
            if self.thread.is_alive():
                raise RuntimeError("remote streamed validator timeout")
            if self.error:
                raise self.error
            for process in self.processes:
                if process.wait(timeout=60):
                    raise ValueError("remote streamed decoder failed")
            if self.result is None:
                raise ValueError("remote streamed restore incomplete")
            if set(self.sample_paths) != set(self.samples):
                raise ValueError("requested streamed sample is missing")
            self._publish_samples()
            self.samples_verified = True
            return self.result
        except Exception:
            self._discard_samples()
            raise

    def close(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()
        if self.input and not self.input.closed:
            try:
                self.input.close()
            except BrokenPipeError:
                pass
        self.thread.join(timeout=5)
        if not self.samples_verified:
            self._discard_samples()
