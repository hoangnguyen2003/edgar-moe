"""Finite owner-attested #156 operator path; the general source gate stays held.

Provider payloads, model outputs and logs are retained in the existing private
R2 store only. This command accepts only fresh signed Cloudflare Cron slots.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import traceback
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from private_scheduler_scope import SCOPE_ID, SCOPE_SHA256, admit, read_scope
from validate_frozen_runtime import validate_frozen_runtime
from verify_private_forward_cycle import compare_snapshots, snapshot, target_fingerprints
from verify_scheduler_runtime_targets import compare_targets

from edgar_moe.data.alpaca import AlpacaDataClient
from edgar_moe.data.fred import FredClient
from edgar_moe.data.refresh import load_universe_csv, refresh_authenticated_to_disk
from edgar_moe.data.sec import SecClient
from edgar_moe.features.dataset import build_research_dataset
from edgar_moe.features.text import FinBertEmbedder
from edgar_moe.forward.artifacts import R2ArtifactStore
from edgar_moe.forward.config import FrozenModelSpec
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.operations import rolling_source_start, seed_filing_documents, source_cutoff
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.forward.workflow import ForwardWorkflow
from edgar_moe.settings import ResearchConfig, RuntimeSettings
from edgar_moe.utils.time import target_nyse_open

MAX_ARCHIVE_BYTES = 1_000_000_000
MAX_EXTRACT_BYTES = 6_000_000_000


class BudgetedStore:
    """Conservative upload-byte ceiling across workflow and evidence objects.

    Counting deduplicated attempts as new bytes gives an upper bound. Reserve
    1 KiB for the separate atomic claim; the seed is downloaded, never copied
    back into R2. Private output files are complete before put_file is called.
    """

    def __init__(self, store: Any, limit: int = MAX_ARCHIVE_BYTES - 1024) -> None:
        self.store = store
        self.client, self.bucket = store.client, store.bucket
        self.limit = limit
        self.bytes_reserved = 0

    def reserve(self, size: int) -> None:
        if size < 0 or self.bytes_reserved + size > self.limit:
            raise ValueError("Private cycle upload budget exceeded")
        self.bytes_reserved += size

    def put_file(self, source: str | Path, *, logical_name: str | None = None) -> Any:
        self.reserve(Path(source).stat().st_size)
        return self.store.put_file(source, logical_name=logical_name)

    def put_bytes(self, content: bytes, *, logical_name: str) -> Any:
        self.reserve(len(content))
        return self.store.put_bytes(content, logical_name=logical_name)

    def read_bytes(self, reference: Any) -> bytes:
        result: bytes = self.store.read_bytes(reference)
        return result


def write_json(path: Path, payload: Any) -> None:
    with path.open("x") as stream:
        json.dump(payload, stream, indent=2, default=str)
        stream.write("\n")


def restore_seed(
    store: R2ArtifactStore | BudgetedStore, record: dict[str, Any], output: Path
) -> None:
    seed = record["private_cache_seed"]
    path = output / "seed.tar.gz"
    store.client.download_file(store.bucket, seed["key"], str(path))
    if path.stat().st_size != seed["size_bytes"] or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Private cache seed size differs from reviewed metadata")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != seed["sha256"]:
            raise ValueError("Private cache seed digest mismatch")
    with tarfile.open(path) as archive:
        size = 0
        names: set[str] = set()
        for member in archive:
            relative = Path(member.name)
            size += member.size
            if (
                member.name in names
                or relative.is_absolute()
                or ".." in relative.parts
                or not relative.parts
                or relative.parts[0] not in {"filing-cache", "embedding-cache"}
                or not (member.isfile() or member.isdir())
                or size > MAX_EXTRACT_BYTES
            ):
                raise ValueError("Unsafe or excessive private cache seed")
            names.add(member.name)
            archive.extract(member, output, filter="data")


async def refresh(
    root: Path, output: Path, cutoff: str, settings: RuntimeSettings, config: ResearchConfig
) -> Path:
    raw = output / "raw"
    async with (
        SecClient(
            settings.sec_user_agent,
            output / "sec-http-cache",
            cache_filings=False,
            requests_per_second=config.data.sec_requests_per_second,
        ) as sec,
        AlpacaDataClient(settings.alpaca_api_key, settings.alpaca_api_secret) as market,
        FredClient(settings.fred_api_key) as macro,
    ):
        return await refresh_authenticated_to_disk(
            sec,
            market,
            macro,
            load_universe_csv(root / "config/universe.research.csv"),
            output_root=raw,
            start=date.fromisoformat(rolling_source_start(cutoff, lookback_days=730)),
            end=date.fromisoformat(cutoff),
            as_of=date.fromisoformat(cutoff),
            macro_series=("VIXCLS", "DGS10", "DFF", "BAA10Y"),
            forms=config.data.sec_forms,
            feed="iex",
            resume=True,
            seed_filings=lambda: seed_filing_documents(
                raw_root=raw, filing_cache=output / "filing-cache", cutoff=cutoff
            ),
        )


def run(root: Path, record: dict[str, Any], scheduled: datetime, output: Path) -> dict[str, Any]:
    # No .env fallback: only the narrowly scoped workflow step receives secrets.
    settings = RuntimeSettings(_env_file=None)  # type: ignore[call-arg]
    if (
        target_fingerprints(settings) != record["private_target_fingerprints"]
        or compare_targets(dict(os.environ), record["private_target_fingerprints"])["status"]
        != "matches_private_reference"
    ):
        raise ValueError("Private database/storage target mismatch")
    errors = validate_frozen_runtime(root / "config/forward.yaml")
    if errors:
        raise ValueError("Reviewed frozen runtime failed validation")
    config = ResearchConfig.from_yaml(root / "config/authenticated-free.yaml")
    raw_store = R2ArtifactStore(
        endpoint_url=settings.edgar_moe_r2_endpoint_url,
        bucket=settings.edgar_moe_r2_bucket,
        access_key_id=settings.edgar_moe_r2_access_key_id,
        secret_access_key=settings.edgar_moe_r2_secret_access_key,
    )
    store = BudgetedStore(raw_store)
    database = RegistryDatabase(
        settings.edgar_moe_registry_database_url, pool_size=1, max_overflow=0
    )
    stage = "snapshot"
    report: dict[str, Any] = {
        "status": "failed",
        "scope_id": SCOPE_ID,
        "scope_sha256": SCOPE_SHA256,
        "scheduled_at": scheduled.isoformat(),
        "source_cutoff_rule_unchanged": True,
        "filing_coverage_configuration_unchanged": True,
        "origin_signature_verified": True,
        "actual_forecasts_recorded": False,
        "forecast_margin_proven": False,
        "issue_complete": False,
    }
    try:
        stage = "claim_schedule_slot"
        store.client.put_object(
            Bucket=store.bucket,
            Key=f"private-scheduler/{SCOPE_ID}/{scheduled.date().isoformat()}.claim.json",
            Body=json.dumps({"run_id": os.environ["GITHUB_RUN_ID"]}).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )
        stage = "snapshot"
        before = snapshot(database)
        write_json(output / "registry-before.json", before)
        stage = "restore_private_seed"
        restore_seed(store, record, output)
        cutoff = source_cutoff(scheduled)
        if source_cutoff() != cutoff:
            raise ValueError("Queue delay crossed the original source cutoff date")
        stage = "provider_refresh"
        checkpoint = asyncio.run(refresh(root, output, cutoff, settings, config))
        stage = "frozen_dataset"
        encoder = FinBertEmbedder(
            model_name=config.features.embedding_model,
            chunk_tokens=config.features.embedding_chunk_tokens,
            max_chunks=config.features.embedding_max_chunks,
            device="cpu",
            revision=record["cached_finbert_revision"],
        )
        if encoder.model_revision != record["cached_finbert_revision"]:
            raise ValueError("FinBERT revision differs from the frozen model provenance")
        dataset = build_research_dataset(
            checkpoint, config=config, embedder=encoder, embedding_cache=output / "embedding-cache"
        )
        dataset_dir = dataset.save(output / "processed")
        stage = "pre_open_admission"
        now = datetime.now(UTC)
        if now - scheduled > timedelta(hours=3) or target_nyse_open(now) - now < timedelta(
            minutes=90
        ):
            raise ValueError("Refresh finished too late for the reviewed pre-entry margin")
        stage = "forecast_and_settle"
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        registry = ForwardRegistry(database, actor="issue156-private-scheduler")
        workflow = ForwardWorkflow(
            registry, store, FrozenModelSpec.from_yaml(root / "config/forward.yaml")
        )
        forecast = workflow.forecast(dataset_dir, as_of=now, code_revision=revision, device="cpu")
        settlement = workflow.settle(dataset_dir, as_of=datetime.now(UTC), code_revision=revision)
        write_json(
            output / "cycle.json", {"forecast": vars(forecast), "settlement": vars(settlement)}
        )
        checks = [
            c for c in registry.list_quality_checks(limit=1000) if c["run_id"] == forecast.run_id
        ]
        write_json(output / "quality-checks.json", checks)
        margin = next(c for c in checks if c["name"] == "pre_open_schedule_margin")
        appended = compare_snapshots(before, snapshot(database))
        write_json(output / "registry-appended.json", appended)
        report.update(
            status="recorded" if forecast.counts["forecasts_inserted"] else "no_eligible_forecasts",
            cutoff=cutoff,
            forecast_run_id=forecast.run_id,
            forecasts_inserted=forecast.counts["forecasts_inserted"],
            recorded_at_utc=margin.get("details", {}).get("observed_at"),
            target_regular_open_utc=margin.get("details", {}).get("target_regular_open"),
            actual_forecasts_recorded=forecast.counts["forecasts_inserted"] > 0,
            forecast_margin_proven=forecast.counts["forecasts_inserted"] > 0
            and margin["status"] == "passed",
            pre_open_margin_seconds=margin["observed_value"],
            existing_records_unchanged=True,
        )
    except (Exception, KeyboardInterrupt) as error:
        traceback.print_exc()
        report.update(stage=stage, error_type=type(error).__name__)
        if stage == "claim_schedule_slot":
            return report  # No replay may add evidence objects or touch providers/DB.
    finally:
        database.dispose()
    write_json(output / "result.json", report)
    # Flush the private redirected log before it enters the durable archive.
    import sys

    sys.stdout.flush()
    sys.stderr.flush()
    # Archive all new evidence privately, excluding the separately retained seed
    # and duplicated caches. Never upload provider payloads as Actions artifacts.
    archive_path = output.parent / f"{output.name}.tar.gz"
    with tarfile.open(archive_path, "x:gz") as archive:
        for child in sorted(output.iterdir()):
            if child.name not in {
                "seed.tar.gz",
                "filing-cache",
                "embedding-cache",
                "sec-http-cache",
            }:
                archive.add(child, arcname=child.name)
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Private evidence archive exceeds the bounded storage allocation")
    reference = store.put_file(archive_path, logical_name="private-scheduler-evidence.tar.gz")
    report["private_evidence_sha256"] = reference.sha256
    report["new_storage_upper_bound_bytes"] = store.bytes_reserved + 1024
    return report


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    record = read_scope(root)  # Before secrets, provider clients or database connections.
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    scheduled = admit(
        event.get("inputs", {}),
        key=os.environ.get("SCHEDULER_SIGNATURE_KEY", ""),
        now=datetime.now(UTC),
        event=os.environ.get("GITHUB_EVENT_NAME", ""),
        ref=os.environ.get("GITHUB_REF", ""),
    )
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("Replays/reruns need a new reviewed scope; this cycle is append-only")
    os.umask(0o077)
    os.chdir(root)
    output = root / "data/private-scheduler" / os.environ["GITHUB_RUN_ID"]
    output.mkdir(parents=True, exist_ok=False)
    destination = root / "data/artifacts/frozen-studies/research-2026-07-31-3553e7ad78dd"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("frozen-model.pt", "locked-test.json"):
        target = destination / name
        if target.exists():
            raise ValueError("Refusing to replace an existing frozen artifact")
        shutil.copyfile(root / "ops/frozen" / name, target)
    with (
        (output / "private-runtime.log").open("x") as log,
        contextlib.redirect_stdout(log),
        contextlib.redirect_stderr(log),
    ):
        report = run(root, record, scheduled, output)
    public = root / "reports/private-scheduler"
    public.mkdir(parents=True, exist_ok=False)
    write_json(public / "receipt.json", report)
    print(json.dumps(report))
    return 0 if report["status"] in {"recorded", "no_eligible_forecasts"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as failure:
        # Driver/URL-bearing exception text must never enter public Actions logs.
        print(json.dumps({"status": "refused", "error_type": type(failure).__name__}))
        raise SystemExit(1) from None
