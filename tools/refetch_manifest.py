"""Build a private, metadata-only re-fetch manifest for verified npm tarballs.

The npm cacache index is read without invoking npm. Only exact registry tarball
URLs with a single SHA-512 SRI, matching cacache path, and matching bytes are
eligible. pnpm v11's msgpackr index is deliberately left raw-required until a
local decoder can prove its package metadata.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from urllib.parse import unquote, urlsplit, urlunsplit
import urllib.error
import urllib.request


SHA512_HEX = re.compile(r"[0-9a-f]{128}\Z")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
REGISTRY_HOST = "registry.npmjs.org"
MAX_SOURCE_BYTES = 1_000_000_000
MAX_SOURCE_WORKERS = 4
DEFAULT_SOURCE_TIMEOUT = 180


def _parse_registry_tarball(key: str) -> tuple[str, str, str] | None:
    """Return (package, version, sanitized URL) only for canonical npm tarballs."""
    if not isinstance(key, str):
        return None
    marker = "https://"
    start = key.find(marker)
    if start < 0:
        return None
    candidate = key[start:].strip()
    try:
        parts = urlsplit(candidate)
        if (parts.scheme != "https" or parts.hostname != REGISTRY_HOST
                or parts.username is not None or parts.password is not None
                or parts.port is not None or parts.query or parts.fragment):
            return None
    except ValueError:
        return None

    segments = [unquote(piece) for piece in parts.path.split("/") if piece]
    if len(segments) == 3 and segments[1] == "-":
        package, _, filename = segments
    elif len(segments) == 4 and segments[0].startswith("@") and segments[2] == "-":
        package, filename = f"{segments[0]}/{segments[1]}", segments[3]
    else:
        return None

    if not filename.endswith(".tgz"):
        return None
    expected_filename = package.rsplit("/", 1)[-1] + "-"
    stem = filename[:-4]
    if not stem.startswith(expected_filename):
        return None
    version = stem[len(expected_filename):]
    if not SEMVER.fullmatch(version):
        return None
    if not re.fullmatch(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*", package):
        return None

    safe_path = "/" + "/".join(segments)
    sanitized = urlunsplit(("https", REGISTRY_HOST, safe_path, "", ""))
    return package, version, sanitized


def _decode_single_sha512(sri: object) -> tuple[str, str] | None:
    """Return (SRI token, lower-case digest hex) for one unambiguous SHA-512."""
    if not isinstance(sri, str):
        return None
    tokens = sri.split()
    if len(tokens) != 1 or not tokens[0].startswith("sha512-"):
        return None
    token = tokens[0]
    try:
        digest = base64.b64decode(token[7:], validate=True)
    except (ValueError, base64.binascii.Error):
        return None
    if len(digest) != 64:
        return None
    return token, digest.hex()


def _hash_file(path: Path) -> tuple[int, str] | None:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return None
    digest = hashlib.sha512()
    size = 0
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
    except OSError:
        return None
    return size, digest.hexdigest()


def _registry_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == REGISTRY_HOST
                and parts.username is None and parts.password is None
                and parts.port is None and not parts.fragment)
    except (TypeError, ValueError):
        return False


class _RegistryRedirects(urllib.request.HTTPRedirectHandler):
    """Allow only bounded HTTPS redirects that stay on the public npm host."""
    def __init__(self, max_redirects: int = 3):
        super().__init__()
        self.remaining = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _registry_url(newurl):
            raise ValueError("redirect left public npm registry")
        self.remaining -= 1
        if self.remaining < 0:
            raise ValueError("too many registry redirects")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _set_response_timeout(response, seconds: float) -> None:
    """Tighten socket reads to the remaining total deadline when urllib exposes it."""
    try:
        response.fp.raw._sock.settimeout(seconds)
    except (AttributeError, OSError):
        pass


def _verify_source(entry: dict, timeout_seconds: float = DEFAULT_SOURCE_TIMEOUT,
                   opener_factory=urllib.request.build_opener) -> dict:
    """Anonymous streaming GET. The response is hashed in memory and never saved."""
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = {"source_verified_at": checked_at, "source_verification_status": "unverified"}
    source = entry.get("source")
    parsed = _parse_registry_tarball(source)
    if not parsed or not _registry_url(source):
        result["source_verification_status"] = "invalid_source"
        return result
    name, version, _ = parsed
    if name != entry.get("name") or version != entry.get("version"):
        result["source_verification_status"] = "identity_mismatch"
        return result
    sri = _decode_single_sha512(entry.get("integrity"))
    covered = entry.get("covered_file", {})
    if (not sri or type(covered.get("size")) is not int or covered["size"] < 0
            or sri[1] != covered.get("sha512")):
        result["source_verification_status"] = "manifest_mismatch"
        return result

    expected_size, expected_hex = covered["size"], covered["sha512"]
    request = urllib.request.Request(
        source,
        headers={"Accept": "application/octet-stream", "Accept-Encoding": "identity",
                 "User-Agent": "siso-estate-refetch-verifier/1"},
        method="GET",
    )
    deadline = time.monotonic() + timeout_seconds
    opener = opener_factory(_RegistryRedirects())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            if not _registry_url(final_url):
                result["source_verification_status"] = "redirect_rejected"
                return result
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status != 200:
                result["source_verification_status"] = "http_error"
                result["http_status"] = status
                return result
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    if int(length) != expected_size:
                        result["source_verification_status"] = "length_mismatch"
                        return result
                except ValueError:
                    result["source_verification_status"] = "invalid_content_length"
                    return result
            digest, observed_size = hashlib.sha512(), 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result["source_verification_status"] = "timeout"
                    return result
                _set_response_timeout(response, min(timeout_seconds, remaining))
                block = response.read(64 * 1024)
                if not block:
                    break
                observed_size += len(block)
                if observed_size > expected_size:
                    result["source_verification_status"] = "length_mismatch"
                    return result
                digest.update(block)
            if observed_size != expected_size:
                result["source_verification_status"] = "length_mismatch"
            elif digest.hexdigest() != expected_hex:
                result["source_verification_status"] = "integrity_mismatch"
            else:
                result["source_verification_status"] = "verified_public"
    except urllib.error.HTTPError as error:
        result["source_verification_status"] = "not_public" if error.code in (401, 403, 404, 410) else "http_error"
        result["http_status"] = error.code
        error.close()
    except (TimeoutError, urllib.error.URLError) as error:
        result["source_verification_status"] = "timeout" if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError) else "network_error"
    except ValueError as error:
        result["source_verification_status"] = "redirect_rejected" if "redirect" in str(error) else "network_error"
    except OSError:
        result["source_verification_status"] = "network_error"
    return result


def verify_sources(entries: list[dict], timeout_seconds: int = DEFAULT_SOURCE_TIMEOUT,
                   opener_factory=urllib.request.build_opener) -> tuple[list[dict], list[dict]]:
    """Return (publicly verified, raw-required) after bounded no-disk GET checks."""
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("source timeout must be between 1 and 300 seconds")
    candidate_bytes = sum(entry.get("covered_file", {}).get("size", 0) for entry in entries)
    if candidate_bytes > MAX_SOURCE_BYTES:
        raise ValueError("source verification exceeds the 1 GB transfer ceiling")
    verified, raw_required = [], []
    with ThreadPoolExecutor(max_workers=MAX_SOURCE_WORKERS) as pool:
        futures = {pool.submit(_verify_source, entry, timeout_seconds, opener_factory): entry for entry in entries}
        for future in as_completed(futures):
            entry = dict(futures[future])
            try:
                entry.update(future.result())
            except Exception:
                entry.update({"source_verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                              "source_verification_status": "verification_error"})
            (verified if entry["source_verification_status"] == "verified_public" else raw_required).append(entry)
    order = lambda entry: (entry.get("name", ""), entry.get("version", ""), entry.get("source", ""))
    return sorted(verified, key=order), sorted(raw_required, key=order)


def _index_records(index_root: Path):
    """Yield decoded npm cacache index records without exposing malformed lines."""
    if not index_root.is_dir():
        return
    for index_path in sorted(index_root.rglob("*")):
        if not index_path.is_file() or index_path.is_symlink():
            continue
        try:
            with index_path.open("rb") as source:
                for raw_line in source:
                    marker = raw_line.find(b"{")
                    if marker < 0:
                        continue
                    try:
                        record = json.loads(raw_line[marker:])
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if isinstance(record, dict):
                        yield record
        except OSError:
            continue


def _pnpm_raw_required(store: Path | None) -> dict:
    if store is None or not store.is_dir():
        return {"status": "unavailable", "reason": "pnpm store was not observed"}
    index_db = store / "index.db"
    local_tarballs = sorted(p.name for p in store.glob("file+*.tgz") if p.is_dir())
    rows = None
    if index_db.is_file():
        try:
            import sqlite3
            connection = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
            try:
                rows = connection.execute("SELECT count(*) FROM package_index").fetchone()[0]
            finally:
                connection.close()
        except (OSError, ImportError, Exception):
            rows = None
    return {
        "status": "raw_required",
        "reason": "v11 msgpackr package_index metadata was not decoded and verified",
        "index_rows": rows,
        "local_tarball_directories": len(local_tarballs),
        "local_tarballs_included_by_name": False,
    }


def build_manifest(npm_cache: Path, pnpm_store: Path | None = None, *,
                   verify_source_urls: bool = False, source_timeout_seconds: int = DEFAULT_SOURCE_TIMEOUT,
                   opener_factory=urllib.request.build_opener) -> dict:
    npm_cache = npm_cache.expanduser().resolve(strict=True)
    content_root = npm_cache / "content-v2" / "sha512"
    index_root = npm_cache / "index-v5"
    entries: dict[tuple[str, str, str], dict] = {}
    conflicts: set[tuple[str, str, str]] = set()
    rejected = 0

    for record in _index_records(index_root):
        parsed = _parse_registry_tarball(record.get("key", ""))
        sri = _decode_single_sha512(record.get("integrity"))
        if not parsed or not sri:
            rejected += 1
            continue
        package, version, source_url = parsed
        sri_token, expected_hex = sri
        # cacache shards the hex SRI digest into 2/2/remaining characters.
        content_path = content_root / expected_hex[:2] / expected_hex[2:4] / expected_hex[4:]
        if any(path.is_symlink() for path in (content_root.parent, content_root, content_path.parent.parent,
                                               content_path.parent, content_path)):
            rejected += 1
            continue
        observed = _hash_file(content_path)
        if observed is None or observed[1] != expected_hex:
            rejected += 1
            continue
        size, digest_hex = observed
        key = (package, version, source_url)
        candidate = {
            "name": package,
            "version": version,
            "source": source_url,
            "integrity": sri_token,
            "covered_file": {
                "path": content_path.relative_to(npm_cache).as_posix(),
                "size": size,
                "sha512": digest_hex,
            },
        }
        if key in conflicts:
            rejected += 1
            continue
        previous = entries.get(key)
        if previous and previous["integrity"] != sri_token:
            entries.pop(key, None)
            conflicts.add(key)
            rejected += 1
            continue
        entries[key] = candidate

    sorted_entries = sorted(entries.values(), key=lambda entry: (entry["name"], entry["version"], entry["source"]))
    local_integrity_verified = len(sorted_entries)
    raw_required_entries = []
    source_summary = {}
    if verify_source_urls:
        candidate_bytes = sum(entry["covered_file"]["size"] for entry in sorted_entries)
        verified_entries, raw_required_entries = verify_sources(
            sorted_entries, source_timeout_seconds, opener_factory)
        source_summary = {
            "source_verification_enabled": True,
            "source_verification_candidate_bytes": candidate_bytes,
            "source_verified_registry_tarballs": len(verified_entries),
            "source_verification_failed": len(raw_required_entries),
        }
        sorted_entries = verified_entries
    else:
        source_summary = {"source_verification_enabled": False}

    manifest = {
        "schema": "siso.estate.refetch-manifest.v1",
        "coverage_status": "provisional_not_uploaded",
        "manifest_scope": "npm registry tarballs with local integrity proof; public re-fetch proof when enabled",
        "source_root": str(npm_cache),
        "entries": sorted_entries,
        "raw_required_entries": raw_required_entries,
        "summary": {
            "local_integrity_verified_tarballs": local_integrity_verified,
            "index_records_rejected_or_unverified": rejected,
            **source_summary,
        },
        "pnpm_store": _pnpm_raw_required(pnpm_store),
        "coverage_rule": "Do not mark backed up until this manifest is uploaded encrypted and receipt verified.",
    }
    return manifest


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def write_manifest(manifest: dict, output: Path, protected_roots: list[Path]) -> None:
    output = output.expanduser().resolve()
    for root in protected_roots:
        if _is_under(output, root.expanduser().resolve()):
            raise ValueError("manifest output must be outside package caches")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".tmp-{os.getpid()}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(manifest, target, sort_keys=True, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npm-cache", default="~/.npm/_cacache", help="npm cacache root")
    parser.add_argument("--pnpm-store", default="~/Library/pnpm/store/v11", help="pnpm v11 store root")
    parser.add_argument("--output", required=True, help="private manifest destination outside caches")
    parser.add_argument("--verify-sources", action="store_true",
                        help="anonymous bounded GET and SHA-512 check each public npm tarball")
    parser.add_argument("--source-timeout-seconds", type=int, default=DEFAULT_SOURCE_TIMEOUT,
                        help="total per-tarball deadline, 1..300 seconds")
    args = parser.parse_args(argv)
    npm_cache = Path(args.npm_cache).expanduser()
    pnpm_store = Path(args.pnpm_store).expanduser()
    try:
        manifest = build_manifest(npm_cache, pnpm_store, verify_source_urls=args.verify_sources,
                                  source_timeout_seconds=args.source_timeout_seconds)
        write_manifest(manifest, Path(args.output), [npm_cache, pnpm_store])
    except (OSError, ValueError) as error:
        print(f"refetch manifest failed: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({
        "local_integrity_verified_tarballs": manifest["summary"]["local_integrity_verified_tarballs"],
        "index_records_rejected_or_unverified": manifest["summary"]["index_records_rejected_or_unverified"],
        "source_verification_enabled": manifest["summary"]["source_verification_enabled"],
        "source_verified_registry_tarballs": manifest["summary"].get("source_verified_registry_tarballs", 0),
        "source_verification_failed": manifest["summary"].get("source_verification_failed", 0),
        "source_verification_candidate_bytes": manifest["summary"].get("source_verification_candidate_bytes", 0),
        "pnpm_store_status": manifest["pnpm_store"]["status"],
        "coverage_status": manifest["coverage_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
