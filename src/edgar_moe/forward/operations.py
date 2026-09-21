from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from edgar_moe.utils.hashing import sha256_file

SOURCE_TIMEZONE = ZoneInfo("America/New_York")
MINIMUM_FORWARD_LOOKBACK_DAYS = 400


def source_cutoff(now: datetime | None = None) -> str:
    """Return the current source-system date rather than the runner's local date."""
    clock = now or datetime.now(SOURCE_TIMEZONE)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("Cutoff clock must be timezone-aware")
    return clock.astimezone(SOURCE_TIMEZONE).date().isoformat()


def validate_cutoff(cutoff: str, *, now: datetime | None = None) -> str:
    parsed = datetime.strptime(cutoff, "%Y-%m-%d").date()
    current = (
        datetime.now(SOURCE_TIMEZONE).date()
        if now is None
        else now.astimezone(SOURCE_TIMEZONE).date()
    )
    if parsed > current:
        raise ValueError(
            f"Forward cutoff {parsed.isoformat()} is after the source-system date "
            f"{current.isoformat()}"
        )
    return parsed.isoformat()


def rolling_source_start(cutoff: str, *, lookback_days: int) -> str:
    """Return a bounded inference start date with enough history for 252-session features."""
    if lookback_days < MINIMUM_FORWARD_LOOKBACK_DAYS:
        raise ValueError(
            "Forward lookback must be at least "
            f"{MINIMUM_FORWARD_LOOKBACK_DAYS} days; received {lookback_days}"
        )
    parsed = date.fromisoformat(cutoff)
    return (parsed - timedelta(days=lookback_days)).isoformat()


def seed_filing_documents(
    *,
    raw_root: Path,
    filing_cache: Path,
    cutoff: str,
) -> int:
    """Hard-link immutable filing bodies from cache or the latest verified checkpoint."""
    target = raw_root / cutoff / "sec" / "filings"
    target.mkdir(parents=True, exist_ok=True)
    sources: list[Path] = []
    if filing_cache.is_dir():
        sources.append(filing_cache)
    previous = _latest_verified_filing_directory(raw_root, cutoff=cutoff)
    if previous is not None and previous not in sources:
        sources.append(previous)
    linked = 0
    for source in sources:
        linked += _merge_tree(source, target)
    return linked


def update_filing_cache(*, checkpoint: Path, filing_cache: Path) -> int:
    source = checkpoint / "sec" / "filings"
    if not source.is_dir():
        raise FileNotFoundError(f"Verified filing directory is unavailable: {source}")
    filing_cache.mkdir(parents=True, exist_ok=True)
    return _merge_tree(source, filing_cache)


def find_processed_dataset(
    *,
    processed_root: Path,
    checkpoint_manifest: Path,
    cutoff: str,
) -> Path:
    source_hash = sha256_file(checkpoint_manifest)
    matches: list[Path] = []
    if processed_root.is_dir():
        for manifest_path in processed_root.glob("*/manifest.json"):
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                payload.get("as_of") == cutoff
                and payload.get("source_manifest_hash") == source_hash
            ):
                matches.append(manifest_path.parent)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one processed dataset for cutoff {cutoff}, found {len(matches)}"
        )
    return matches[0]


def _latest_verified_filing_directory(raw_root: Path, *, cutoff: str) -> Path | None:
    candidates: list[tuple[str, Path]] = []
    if not raw_root.is_dir():
        return None
    for child in raw_root.iterdir():
        if not child.is_dir() or child.name >= cutoff:
            continue
        filing_directory = child / "sec" / "filings"
        if (child / "manifest.json").is_file() and filing_directory.is_dir():
            candidates.append((child.name, filing_directory))
    return max(candidates, default=("", None), key=lambda item: item[0])[1]


def _merge_tree(source: Path, destination: Path) -> int:
    source_root = source.resolve()
    destination_root = destination.resolve()
    if source_root == destination_root:
        return 0
    copied = 0
    for source_file in sorted(path for path in source_root.rglob("*") if path.is_file()):
        relative = source_file.relative_to(source_root)
        target = destination_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.stat().st_size != source_file.stat().st_size:
                raise ValueError(f"Conflicting cached filing document: {relative}")
            continue
        try:
            os.link(source_file, target)
        except OSError:
            shutil.copy2(source_file, target)
        copied += 1
    return copied
