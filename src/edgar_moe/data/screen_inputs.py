"""Private, immutable parsed daily-bar inputs for offline screen replay."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

_ARTIFACT_ROOT = Path("data/artifacts")
_MAX_INPUT_BYTES = 64 * 1024 * 1024
_FILE = "daily-bars.json"
SCREEN_COLUMNS = (
    "cik",
    "symbol",
    "security_id",
    "company_name",
    "exchange",
    "industry_code",
    "screen_sessions",
    "screen_last_price",
    "screen_median_dollar_volume",
    "screen_liquidity_rank",
)
_KEYS = {
    "schema_version",
    "provider",
    "timeframe",
    "feed",
    "lookback_start",
    "as_of",
    "candidate_count",
    "minimum_sessions",
    "minimum_price",
    "captured_at_utc",
    "screen_algorithm_sha256",
    "source_sha256",
    "source_capture_sha256",
    "mapping_review_sha256",
    "bars",
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def screen_algorithm_digest() -> str:
    return hashlib.sha256(Path(__file__).with_name("universe.py").read_bytes()).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Screen input JSON contains duplicate fields")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("Screen input JSON contains non-finite numbers")


def decode_json(payload: bytes) -> Any:
    return json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def _private_path(path: Path) -> tuple[Path, Path]:
    root = _ARTIFACT_ROOT.absolute()
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as error:
        raise ValueError("Screen inputs must be under ignored data/artifacts") from error
    if not relative.parts or ".." in relative.parts:
        raise ValueError("Screen inputs must be under ignored data/artifacts")
    anchor = root.resolve()
    return anchor, anchor / relative


def preflight_screen_inputs(path: Path) -> None:
    anchor, destination = _private_path(path)
    current = anchor
    for part in destination.relative_to(anchor).parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if current == destination:
            raise ValueError("Screen inputs already exist; refusing overwrite")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Screen input parent must be a directory, not an alias")


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    anchor, destination = _private_path(path)
    if create:
        anchor.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        descriptor = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        parts = destination.relative_to(anchor).parts
        for index, part in enumerate(parts):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError as error:
                    if index == len(parts) - 1:
                        raise ValueError(
                            "Screen inputs already exist; refusing overwrite"
                        ) from error
            try:
                descriptor = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except OSError as error:
                raise ValueError("Screen input directory is missing or contains aliases") from error
            stack.callback(os.close, descriptor)
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError("Screen input directory must be owner-only")
        yield descriptor


def _validate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _KEYS:
        raise ValueError("Screen inputs have an invalid schema")
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["provider"] != "alpaca"
        or payload["timeframe"] != "1Day"
        or not isinstance(payload["feed"], str)
        or re.fullmatch(r"[a-z][a-z_]{0,30}", payload["feed"]) is None
        or any(
            type(payload[key]) is not int or payload[key] <= 0
            for key in ("candidate_count", "minimum_sessions")
        )
        or type(payload["minimum_price"]) not in (int, float)
        or not math.isfinite(payload["minimum_price"])
        or payload["minimum_price"] <= 0
    ):
        raise ValueError("Screen input policy is invalid")
    for key in (
        "screen_algorithm_sha256",
        "source_sha256",
        "source_capture_sha256",
        "mapping_review_sha256",
    ):
        if not isinstance(payload[key], str) or re.fullmatch(r"[a-f0-9]{64}", payload[key]) is None:
            raise ValueError("Screen input source binding is invalid")
    captured = payload["captured_at_utc"]
    try:
        captured_instant = datetime.fromisoformat(captured.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Screen input observation time is invalid") from error
    if not captured.endswith("Z") or captured_instant.utcoffset() != UTC.utcoffset(
        captured_instant
    ):
        raise ValueError("Screen input observation time must be UTC")
    try:
        start, end = (date.fromisoformat(payload[key]) for key in ("lookback_start", "as_of"))
    except (ValueError, TypeError) as error:
        raise ValueError("Screen input dates are invalid") from error
    if not start < end:
        raise ValueError("Screen input dates are invalid")
    bars = payload["bars"]
    if not isinstance(bars, dict) or not all(
        isinstance(symbol, str)
        and isinstance(rows, list)
        and all(isinstance(row, dict) for row in rows)
        for symbol, rows in bars.items()
    ):
        raise ValueError("Screen input bars are invalid")
    for rows in bars.values():
        for row in rows:
            timestamp = str(row.get("t", row.get("timestamp", "")))
            if timestamp:
                try:
                    observed = date.fromisoformat(timestamp[:10])
                except ValueError as error:
                    raise ValueError("Screen bar has an invalid date") from error
                if not start <= observed <= end:
                    raise ValueError("Screen inputs extend outside the requested date window")
    return payload


def write_screen_inputs(path: Path, value: dict[str, Any]) -> tuple[str, int]:
    """Create one owner-only input file; never overwrite or repair a capture."""
    payload = canonical_bytes(_validate(value)) + b"\n"
    if not 0 < len(payload) <= _MAX_INPUT_BYTES:
        raise ValueError("Screen inputs exceed the byte bound")
    preflight_screen_inputs(path)
    with _directory(path, create=True) as directory:
        descriptor = os.open(
            _FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def read_screen_inputs(path: Path, digest: object, byte_count: object) -> dict[str, Any]:
    """Bind a stable bounded private-file read before decoding any bar rows."""
    if (
        not isinstance(digest, str)
        or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        or type(byte_count) is not int
        or not 0 < byte_count <= _MAX_INPUT_BYTES
    ):
        raise ValueError("Screen input commitment is invalid")
    with _directory(path) as directory, ExitStack() as stack:
        try:
            descriptor = os.open(
                _FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
        except OSError as error:
            raise ValueError("Screen input file is missing or contains aliases") from error
        stack.callback(os.close, descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o077
            or before.st_nlink != 1
            or before.st_size != byte_count
        ):
            raise ValueError("Screen input file must be private, regular, and match its size")
        payload = bytearray()
        while len(payload) <= byte_count:
            chunk = os.read(descriptor, min(65536, byte_count + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")
        if len(payload) != byte_count or any(
            getattr(before, key) != getattr(after, key) for key in fields
        ):
            raise ValueError("Screen inputs changed while reading")
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Screen input hash mismatch")
    return _validate(decode_json(bytes(payload)))
