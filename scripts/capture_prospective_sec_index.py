"""Pin a prospective SEC quarter-index byte snapshot; not a security universe.

Capture is opt-in and private. The redacted commitment must be posted to an
independently timestamped GitHub issue before the research cutoff. Offline
verification checks bytes and declared chronology, not GitHub authenticity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parents[1]
_POLICY = _ROOT / "config" / "prospective_sec_filer_cohort_v1.json"
_ARTIFACT_ROOT = _ROOT / "data" / "artifacts"
_MAX_INDEX_BYTES = 64 * 1024 * 1024
_MAX_ROSTER_BYTES = 16 * 1024 * 1024
_MAX_COMMITMENT_BYTES = 16 * 1024
_EXPECTED_KEYS = {
    "schema_version",
    "study_id",
    "source",
    "year",
    "quarter",
    "eligible_forms",
    "capture_not_before",
    "capture_before",
    "research_cutoff",
    "minimum_eligible_rows",
    "github_attestation",
}
_HEADER = {
    "CIK|Company Name|Form Type|Date Filed|File Name",
    "CIK|Company Name|Form Type|Date Filed|Filename",
}
_FILING_PATH = re.compile(r"edgar/data/(?P<cik>[0-9]+)/[0-9]{10}-[0-9]{2}-[0-9]{6}\.txt\Z")


class CohortCaptureError(RuntimeError):
    """A prospective capture contract was not satisfied."""


def _bounded_get(client: httpx.Client, url: str) -> bytes:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        result = bytearray()
        for chunk in response.iter_bytes():
            result.extend(chunk)
            if len(result) > _MAX_INDEX_BYTES:
                raise CohortCaptureError("SEC index exceeds the byte bound")
    return bytes(result)


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CohortCaptureError("capture JSON contains duplicate fields")
        result[key] = value
    return result


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _instant(value: str) -> datetime:
    if not isinstance(value, str):
        raise CohortCaptureError("policy timestamp is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CohortCaptureError("policy timestamp is invalid") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise CohortCaptureError("policy timestamp must be timezone-aware")
    return result.astimezone(UTC)


def load_policy(path: Path = _POLICY) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != _EXPECTED_KEYS:
        raise CohortCaptureError("capture policy has unexpected fields")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["source"] != "sec-edgar-full-index-master"
        or value["eligible_forms"] != ["10-K", "10-Q"]
    ):
        raise CohortCaptureError("capture policy has unsupported source or forms")
    if not isinstance(value["study_id"], str) or not value["study_id"].isascii():
        raise CohortCaptureError("capture policy has invalid study identity")
    if type(value["year"]) is not int or not 1994 <= value["year"] <= 2100:
        raise CohortCaptureError("capture policy has invalid year")
    if type(value["quarter"]) is not int or value["quarter"] not in (1, 2, 3, 4):
        raise CohortCaptureError("capture policy has invalid quarter")
    if type(value["minimum_eligible_rows"]) is not int or value["minimum_eligible_rows"] < 1:
        raise CohortCaptureError("capture policy has invalid row floor")
    attestation = value["github_attestation"]
    if (
        not isinstance(attestation, dict)
        or set(attestation) != {"repository", "issue_number", "author_user_id"}
        or not isinstance(attestation["repository"], str)
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", attestation["repository"]
        )
        is None
        or any(
            type(attestation[key]) is not int or attestation[key] <= 0
            for key in ("issue_number", "author_user_id")
        )
    ):
        raise CohortCaptureError("capture policy has invalid GitHub attestation target")
    start = _instant(value["capture_not_before"])
    end = _instant(value["capture_before"])
    cutoff = _instant(value["research_cutoff"])
    if not start < end <= cutoff:
        raise CohortCaptureError("capture window must precede research cutoff")
    if (start.year, start.month) <= (value["year"], value["quarter"] * 3):
        raise CohortCaptureError("capture window must follow the source quarter")
    return value


def _check_time(policy: dict[str, Any], now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise CohortCaptureError("capture time must be timezone-aware")
    instant = now.astimezone(UTC)
    if not _instant(policy["capture_not_before"]) <= instant < _instant(policy["capture_before"]):
        raise CohortCaptureError("capture is outside the predeclared window")


def _periodic_rows(payload: bytes, policy: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    if not payload or len(payload) > _MAX_INDEX_BYTES:
        raise CohortCaptureError("SEC index is empty or exceeds the byte bound")
    lines = payload.decode("latin-1").splitlines()
    header_at = next((n for n, line in enumerate(lines) if line in _HEADER), None)
    if header_at is None:
        raise CohortCaptureError("SEC index header is missing")
    seen_paths: set[str] = set()
    rows: list[tuple[str, str, str, str]] = []
    quarter_start = (policy["quarter"] - 1) * 3 + 1
    for line in lines[header_at + 1 :]:
        if not line.strip() or set(line.strip()) == {"-"}:
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 5:
            raise CohortCaptureError("SEC index row has invalid field count")
        cik, company, form, filed, archive_path = fields
        if form not in policy["eligible_forms"]:
            continue
        try:
            filed_date = datetime.strptime(filed, "%Y-%m-%d").date()
        except ValueError as error:
            raise CohortCaptureError("SEC index periodic row has invalid date") from error
        if not (
            filed_date.year == policy["year"]
            and quarter_start <= filed_date.month < quarter_start + 3
        ):
            raise CohortCaptureError("SEC index periodic row falls outside source quarter")
        path_match = _FILING_PATH.fullmatch(archive_path)
        if (
            not cik.isdecimal()
            or not company
            or path_match is None
            or int(path_match.group("cik")) != int(cik)
        ):
            raise CohortCaptureError("SEC index periodic row has invalid CIK/path")
        if archive_path in seen_paths:
            raise CohortCaptureError("SEC index repeats a periodic filing path")
        seen_paths.add(archive_path)
        rows.append((cik.zfill(10), filed_date.isoformat(), form, archive_path))
    if len(rows) < policy["minimum_eligible_rows"]:
        raise CohortCaptureError("SEC index has too few eligible rows")
    return sorted(rows)


def inspect_index(payload: bytes, policy: dict[str, Any]) -> dict[str, int]:
    rows = _periodic_rows(payload, policy)
    return {"eligible_filing_rows": len(rows), "eligible_filer_ciks": len({row[0] for row in rows})}


def build_filer_roster(payload: bytes, policy: dict[str, Any]) -> bytes:
    """Freeze every eligible CIK and accession, without ticker or listing claims."""
    by_cik: dict[str, list[dict[str, str]]] = {}
    for cik, filed, form, archive_path in _periodic_rows(payload, policy):
        by_cik.setdefault(cik, []).append(
            {"filed_date": filed, "form": form, "archive_path": archive_path}
        )
    roster = {
        "schema_version": 1,
        "status": "filing_cohort_only_not_tradable_security_membership",
        "study_id": policy["study_id"],
        "source_index_sha256": _digest(payload),
        "filers": [{"cik": cik, "filings": filings} for cik, filings in sorted(by_cik.items())],
    }
    encoded = _canonical(roster) + b"\n"
    if len(encoded) > _MAX_ROSTER_BYTES:
        raise CohortCaptureError("filer roster exceeds the byte bound")
    return encoded


def build_commitment(
    payload: bytes, policy: dict[str, Any], *, captured_at: datetime
) -> dict[str, Any]:
    _check_time(policy, captured_at)
    counts = inspect_index(payload, policy)
    roster_bytes = build_filer_roster(payload, policy)
    return {
        "schema_version": 2,
        "status": "filing_cohort_source_only_not_tradable_security_membership",
        "study_id": policy["study_id"],
        "source": policy["source"],
        "source_year": policy["year"],
        "source_quarter": policy["quarter"],
        "source_url": f"https://www.sec.gov/Archives/edgar/full-index/{policy['year']}/QTR{policy['quarter']}/master.idx",
        "captured_at": captured_at.astimezone(UTC).isoformat(),
        "research_cutoff": policy["research_cutoff"],
        "policy_sha256": _digest(_canonical(policy)),
        "index_sha256": _digest(payload),
        "index_byte_count": len(payload),
        "filer_roster_sha256": _digest(roster_bytes),
        "filer_roster_byte_count": len(roster_bytes),
        **counts,
    }


def _private_path(path: Path, *, root: Path) -> Path:
    # Resolve the trusted artifact root, not the untrusted capture components.
    # Resolving the latter would silently accept directory symlink aliases.
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise CohortCaptureError("capture output must be under ignored data/artifacts") from error
    if not relative.parts or ".." in relative.parts:
        raise CohortCaptureError("capture output must be under ignored data/artifacts")
    return root.resolve() / relative


def _preflight_new_capture(path: Path, *, root: Path) -> None:
    destination = _private_path(path, root=root)
    current = root.resolve()
    for part in destination.relative_to(current).parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if current == destination:
            raise CohortCaptureError("capture output already exists")
        if not stat.S_ISDIR(metadata.st_mode):
            raise CohortCaptureError("capture parent must be a directory, not an alias")


@contextmanager
def _capture_directory(path: Path, *, root: Path, create: bool = False) -> Iterator[int]:
    destination = _private_path(path, root=root)
    anchor = root.resolve()
    if create:
        anchor.mkdir(parents=True, exist_ok=True)
    # Descriptor-relative traversal prevents a checked parent from subsequently
    # being substituted with a symlink before a file is opened or created.
    with ExitStack() as stack:
        descriptor = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, descriptor)
        parts = destination.relative_to(anchor).parts
        for position, part in enumerate(parts):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError as error:
                    if position == len(parts) - 1:
                        raise CohortCaptureError("capture output already exists") from error
            try:
                descriptor = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except FileNotFoundError:
                raise
            except OSError as error:
                raise CohortCaptureError("capture directory must not contain aliases") from error
            stack.callback(os.close, descriptor)
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise CohortCaptureError("capture directory must be owner-only")
        yield descriptor


def _read_private_file(directory_fd: int, name: str, maximum: int) -> bytes:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise CohortCaptureError("capture file must not be an alias") from error
    with ExitStack() as stack:
        stack.callback(os.close, descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o077
            or before.st_nlink != 1
        ):
            raise CohortCaptureError("capture file must be owner-only, regular, and single-linked")
        if not 0 < before.st_size <= maximum:
            raise CohortCaptureError("capture file is empty or exceeds the byte bound")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        stable_fields = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")
        if len(payload) != before.st_size or any(
            getattr(before, field) != getattr(after, field) for field in stable_fields
        ):
            raise CohortCaptureError("capture file changed while reading")
    return bytes(payload)


def _write_new(directory_fd: int, name: str, payload: bytes) -> None:
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd
    )
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)


def save_capture(
    payload: bytes,
    commitment: dict[str, Any],
    directory: Path,
    policy: dict[str, Any],
    *,
    root: Path = _ARTIFACT_ROOT,
) -> None:
    expected = build_commitment(payload, policy, captured_at=_instant(commitment["captured_at"]))
    # Dict equality conflates JSON booleans, integers, and floats; commitments
    # must preserve the exact canonical JSON identity used by the attestation.
    if _canonical(commitment) != _canonical(expected):
        raise CohortCaptureError("capture commitment does not match source and policy")
    roster_bytes = build_filer_roster(payload, policy)
    commitment_bytes = json.dumps(commitment, indent=2, sort_keys=True).encode() + b"\n"
    if len(commitment_bytes) > _MAX_COMMITMENT_BYTES:
        raise CohortCaptureError("capture commitment exceeds the byte bound")
    _preflight_new_capture(directory, root=root)
    with _capture_directory(directory, root=root, create=True) as directory_fd:
        _write_new(directory_fd, "master.idx", payload)
        _write_new(directory_fd, "filer-roster.json", roster_bytes)
        _write_new(directory_fd, "commitment.json", commitment_bytes)


def verify_capture(
    directory: Path, policy: dict[str, Any], *, root: Path = _ARTIFACT_ROOT
) -> dict[str, Any]:
    with _capture_directory(directory, root=root) as directory_fd:
        payload = _read_private_file(directory_fd, "master.idx", _MAX_INDEX_BYTES)
        roster_bytes = _read_private_file(directory_fd, "filer-roster.json", _MAX_ROSTER_BYTES)
        commitment = json.loads(
            _read_private_file(directory_fd, "commitment.json", _MAX_COMMITMENT_BYTES).decode(
                "utf-8"
            ),
            object_pairs_hook=_unique_object,
        )
    if not isinstance(commitment, dict) or "captured_at" not in commitment:
        raise CohortCaptureError("capture commitment is invalid")
    expected = build_commitment(payload, policy, captured_at=_instant(commitment["captured_at"]))
    if _canonical(commitment) != _canonical(expected) or roster_bytes != build_filer_roster(
        payload, policy
    ):
        raise CohortCaptureError("capture bytes or commitment differ from policy")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly fetch one SEC index")
    parser.add_argument("--verify", action="store_true", help="offline private-byte verification")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.live == args.verify:
            raise CohortCaptureError("choose exactly one of --live or --verify")
        policy = load_policy()
        if args.verify:
            commitment = verify_capture(args.output_dir, policy)
        else:
            _check_time(policy, datetime.now(UTC))
            _preflight_new_capture(args.output_dir, root=_ARTIFACT_ROOT)
            user_agent = os.environ.get("SEC_USER_AGENT", "")
            if "@" not in user_agent:
                raise CohortCaptureError("SEC_USER_AGENT with contact email is required")
            url = f"https://www.sec.gov/Archives/edgar/full-index/{policy['year']}/QTR{policy['quarter']}/master.idx"
            with httpx.Client(
                headers={"User-Agent": user_agent}, timeout=60, follow_redirects=False
            ) as client:
                payload = _bounded_get(client, url)
            commitment = build_commitment(payload, policy, captured_at=datetime.now(UTC))
            save_capture(payload, commitment, args.output_dir, policy)
    except (
        CohortCaptureError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        httpx.HTTPError,
    ) as error:
        # Never emit exception detail: network errors may contain the request URL.
        print(f"prospective SEC index capture failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(commitment, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
