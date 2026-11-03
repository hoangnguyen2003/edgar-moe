"""One owner-attested private #288 cycle, using the production library components.

This explicit, input-bound operator path does not release the CLI, scheduler,
new-study or publication holds. It retains real provider data only privately.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import orjson
from sqlalchemy import select

from edgar_moe.data.alpaca import AlpacaDataClient
from edgar_moe.data.fred import FredClient
from edgar_moe.data.refresh import (
    load_universe_csv,
    refresh_authenticated_to_disk,
    verify_authenticated_bundle,
)
from edgar_moe.data.sec import SecClient
from edgar_moe.features.dataset import ResearchDataset, build_research_dataset
from edgar_moe.features.text import FinBertEmbedder
from edgar_moe.forward.artifacts import artifact_store_from_settings
from edgar_moe.forward.config import FrozenModelSpec
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.diagnostics import diagnostic_report
from edgar_moe.forward.evidence_manifest import build_evidence_manifest, verify_evidence_manifest
from edgar_moe.forward.models import Base
from edgar_moe.forward.operations import (
    find_processed_dataset,
    rolling_source_start,
    seed_filing_documents,
    source_cutoff,
    validate_cutoff,
)
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.forward.workflow import ForwardWorkflow
from edgar_moe.settings import ResearchConfig, RuntimeSettings
from edgar_moe.utils.hashing import sha256_file

SERIES = ("VIXCLS", "DGS10", "DFF", "BAA10Y")
FINBERT_REVISION = "4556d13015211d73dccd3fdd39d39232506f3e43"
SCOPE_SHA256 = "0a7cf03033bc7f551a1313f259f086cc57a9c952b2466e8214adef677e6e7e5d"
INPUTS = {
    "config/forward.yaml": "6fee9ade5a98b291cc1a895b10e0213a1d98911ff446252dbe9844b3e15ac318",
    "config/authenticated-free.yaml": "beaf32a8ef6b05218ab5e9d601400ea01dd6669d0ebae060fc1d002f6f6f6beb",
    "config/default.yaml": "d0181ea0a280251367289d9bb5fb562a27dee62f05084017a5c58752af41abe6",
    "config/universe.research.csv": "630270ba5e17858ce9a543e7af8a7bf0e8b9edba6adb2d8d8db6fb4e5ba4e51d",
}
TABLES = tuple(sorted(name for name in Base.metadata.tables if name.startswith("forward_")))


class PlannedInterruption(RuntimeError):
    """Stop after real provider responses and persisted partial checkpoint work."""


def write_json(path: Path, payload: Any) -> None:
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2, default=str) + b"\n")


def validate_scope(record: dict[str, Any], root: Path) -> None:
    if record.get("issue") != 288 or record.get("execution_mode") != "one_private_cycle":
        raise ValueError("This operator record does not authorize one private #288 cycle")
    if record.get("evidence_kind") != "project-owner attestation in the task conversation":
        raise ValueError("Unexpected approval basis")
    scope = record.get("source_scope", {})
    if set(scope.get("fred_alfred_series", [])) != set(SERIES):
        raise ValueError("Macro source scope differs from the bound four-series profile")
    if scope.get("retained_model") != "edgar-moe-frozen-v1":
        raise ValueError("Only the original frozen v1 model is in scope")
    if scope.get("retained_training_dataset") != "research-2026-07-31-3553e7ad78dd":
        raise ValueError("Training dataset scope changed")
    bounds = record.get("bounds", {})
    for name in (
        "new_training",
        "publication_of_provider_data_or_model_outputs",
        "scheduled_workflow_activation",
        "global_source_gate_release",
        "rewrite_existing_evidence",
        "paid_plan_or_upgrade",
    ):
        if bounds.get(name) is not False:
            raise ValueError(f"Private verification exclusion is missing: {name}")
    for relative, expected in INPUTS.items():
        if sha256_file(root / relative) != expected:
            raise ValueError(f"Bound input changed: {relative}")


def read_scope(record_path: Path, root: Path) -> dict[str, Any]:
    if sha256_file(record_path) != SCOPE_SHA256:
        raise ValueError("Private scope record changed from the reviewed version")
    record: dict[str, Any] = json.loads(record_path.read_text())
    validate_scope(record, root)
    return record


def snapshot(database: RegistryDatabase) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with database.engine.connect() as connection, connection.begin():
        for name in TABLES:
            table = Base.metadata.tables[name]
            keys = [column.name for column in table.primary_key]
            rows: dict[str, str] = {}
            for row in connection.execute(select(table)).mappings():
                identity = orjson.dumps([row[key] for key in keys], default=str).decode()
                content = orjson.dumps(dict(row), option=orjson.OPT_SORT_KEYS, default=str)
                rows[identity] = hashlib.sha256(content).hexdigest()
            result[name] = rows
    return result


def compare_snapshots(
    before: dict[str, dict[str, str]], after: dict[str, dict[str, str]]
) -> dict[str, int]:
    appended: dict[str, int] = {}
    for name, rows in before.items():
        current = after[name]
        if any(current.get(identity) != digest for identity, digest in rows.items()):
            raise ValueError(f"Existing registry evidence changed or disappeared: {name}")
        appended[name] = len(set(current) - set(rows))
    return appended


def target_fingerprints(settings: RuntimeSettings) -> dict[str, str]:
    registry = urlsplit(settings.edgar_moe_registry_database_url)
    r2 = urlsplit(settings.edgar_moe_r2_endpoint_url)
    registry_target = f"{registry.hostname}:{registry.port or 5432}{registry.path}"
    storage_target = f"{r2.hostname}/{settings.edgar_moe_r2_bucket}"
    return {
        "registry": hashlib.sha256(registry_target.encode()).hexdigest(),
        "storage": hashlib.sha256(storage_target.encode()).hexdigest(),
    }


def execute(root: Path, record_path: Path, *, resume_output: Path | None = None) -> Path:
    record = read_scope(record_path, root)  # Before credentials, network or model/data loading.
    os.umask(0o077)
    parent = root / "data/artifacts"
    record_hash = sha256_file(record_path)
    if resume_output is None:
        output = Path(tempfile.mkdtemp(prefix="issue288-private-live-", dir=parent))
        execution = {
            "scope_sha256": record_hash,
            "cutoff": source_cutoff(),
            "created_utc": datetime.now(UTC).isoformat(),
        }
        write_json(output / "execution.json", execution)
    else:
        output = resume_output.resolve()
        if output.parent != parent.resolve() or not output.name.startswith(
            "issue288-private-live-"
        ):
            raise ValueError("Resume output must be this verifier's private artifact directory")
        execution = json.loads((output / "execution.json").read_text())
        if execution["scope_sha256"] != record_hash:
            raise ValueError("Cannot resume under a different scope record")
    print(f"PRIVATE_OUTPUT={output}", flush=True)
    result_path = output / "result.json"
    if result_path.exists():
        evidence = json.loads((output / "evidence-manifest.json").read_text())
        verify_evidence_manifest(evidence, root=output)
        print(
            "Existing successful verification evidence is unchanged; no new cycle run.", flush=True
        )
        return output
    cutoff = validate_cutoff(execution["cutoff"])
    config = ResearchConfig.from_yaml(root / "config/authenticated-free.yaml")
    # Pydantic accepts this documented BaseSettings runtime keyword, but its
    # generated subclass signature omits it from static type information.
    settings = RuntimeSettings(_env_file=(root / ".env", root / ".env.local"))  # type: ignore[call-arg]
    if target_fingerprints(settings) != record.get("private_target_fingerprints"):
        raise ValueError("Registry/storage target differs from the successful prerequisite checks")
    settings.edgar_moe_artifact_backend = "local"
    settings.edgar_moe_artifact_mirror_backend = "r2"
    settings.edgar_moe_artifact_dir = output / "artifacts"
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=record_path.parents[2], text=True
    ).strip()
    os.chdir(root)  # The pinned frozen model config intentionally has relative paths.
    readonly = RegistryDatabase(
        settings.edgar_moe_registry_database_url,
        read_only=True,
        statement_timeout_ms=30_000,
        pool_size=1,
        max_overflow=0,
    )
    before_path = output / "registry-before.json"
    if not before_path.exists():
        write_json(before_path, snapshot(readonly))
    before = json.loads(before_path.read_text())
    stats: Counter[str] = Counter()
    raw = output / "raw"
    checkpoint = raw / cutoff
    request = checkpoint / "request.json"
    raw_candidates = sorted(
        p
        for p in (root / "data/raw/authenticated").iterdir()
        if p.is_dir() and p.name < cutoff and (p / "manifest.json").is_file()
    )
    if not raw_candidates:
        raise ValueError("A verified retained filing checkpoint is required for cache seeding")
    cached = raw_candidates[-1]
    verify_authenticated_bundle(cached)

    def seed() -> int:
        if not request.is_file():
            raise ValueError("Request contract is missing before cache seeding")
        count = seed_filing_documents(
            raw_root=raw, filing_cache=cached / "sec/filings", cutoff=cutoff
        )
        if not (output / "seed-witness.json").exists():
            if count < 1:
                raise ValueError("Cache-seeding rehearsal requires actual retained filing files")
            write_json(
                output / "seed-witness.json",
                {
                    "request_sha256": sha256_file(request),
                    "cached_filing_count": count,
                    "request_existed_before_seed": True,
                },
            )
        return count

    async def refresh(*, interrupt: bool) -> Path:
        async with (
            SecClient(
                settings.sec_user_agent,
                raw / "cache/sec",
                cache_filings=False,
                requests_per_second=config.data.sec_requests_per_second,
            ) as sec,
            AlpacaDataClient(settings.alpaca_api_key, settings.alpaca_api_secret) as market,
            FredClient(settings.fred_api_key) as macro,
        ):
            for name, client in (
                ("sec", sec.client),
                ("market", market.client),
                ("macro", macro.client),
            ):

                async def response_hook(response: httpx.Response, service: str = name) -> None:
                    stats[f"{service}:http:{response.status_code}"] += 1

                client.event_hooks["response"].append(response_hook)

            def progress(message: str) -> None:
                print(message, flush=True)
                if interrupt and message.startswith("SEC issuers "):
                    if not stats["sec:http:200"]:
                        raise ValueError("Interruption requires observed successful SEC responses")
                    raise PlannedInterruption()

            return await refresh_authenticated_to_disk(
                sec,
                market,
                macro,
                load_universe_csv(root / "config/universe.research.csv"),
                output_root=raw,
                start=date.fromisoformat(rolling_source_start(cutoff, lookback_days=730)),
                end=date.fromisoformat(cutoff),
                as_of=date.fromisoformat(cutoff),
                macro_series=SERIES,
                forms=config.data.sec_forms,
                feed="iex",
                resume=True,
                progress=progress,
                seed_filings=seed,
            )

    stage = "refresh_interruption"
    try:
        interruption_path = output / "interruption.json"
        if not interruption_path.exists():
            try:
                asyncio.run(refresh(interrupt=True))
            except PlannedInterruption:
                if (checkpoint / "manifest.json").exists():
                    raise ValueError("Rehearsal did not leave a partial checkpoint") from None
                write_json(
                    interruption_path,
                    {
                        "request_sha256": sha256_file(request),
                        "provider_responses": dict(stats),
                        "partial_checkpoint": True,
                    },
                )
            else:
                raise ValueError("Expected a real partial-checkpoint interruption")
        stage = "refresh_resume"
        asyncio.run(refresh(interrupt=False))
        if sha256_file(request) != json.loads(interruption_path.read_text())["request_sha256"]:
            raise ValueError("Resume changed the original request contract")
        manifest_hash = sha256_file(checkpoint / "manifest.json")
        requests_before = sum(stats.values())
        asyncio.run(refresh(interrupt=False))
        if (
            sum(stats.values()) != requests_before
            or sha256_file(checkpoint / "manifest.json") != manifest_hash
        ):
            raise ValueError("Completed-checkpoint resume was not immutable and network-free")
        write_json(
            output / "refresh-witness.json",
            {
                "request_sha256": sha256_file(request),
                "manifest_sha256": manifest_hash,
                "same_request_resumed": True,
                "completed_resume_network_requests": 0,
                "provider_responses": dict(stats),
            },
        )
        stage = "dataset"
        os.environ["HF_HOME"] = str(root / "data/cache/huggingface")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        processed = output / "processed"
        if list(processed.glob("*/manifest.json")):
            dataset_dir = find_processed_dataset(
                processed_root=processed,
                checkpoint_manifest=checkpoint / "manifest.json",
                cutoff=cutoff,
            )
            dataset = ResearchDataset.load(dataset_dir)
        else:
            encoder = FinBertEmbedder(
                model_name=config.features.embedding_model,
                chunk_tokens=config.features.embedding_chunk_tokens,
                max_chunks=config.features.embedding_max_chunks,
                device="cpu",
                revision=FINBERT_REVISION,
            )
            if encoder.model_revision != FINBERT_REVISION:
                raise ValueError("FinBERT revision differs from the frozen training provenance")
            dataset = build_research_dataset(
                checkpoint,
                config=config,
                embedder=encoder,
                embedding_cache=output / "embedding-cache",
                progress=lambda m: print(m, flush=True),
            )
            dataset_dir = dataset.save(processed)
        stage = "forecast_and_settlement"
        database = RegistryDatabase(settings.edgar_moe_registry_database_url)
        try:
            registry = ForwardRegistry(database, actor="issue288-private-verification")
            workflow = ForwardWorkflow(
                registry,
                artifact_store_from_settings(settings),
                FrozenModelSpec.from_yaml(root / "config/forward.yaml"),
            )
            forecast = workflow.forecast(
                dataset_dir, as_of=datetime.now(UTC), code_revision=revision, device="cpu"
            )
            settlement = workflow.settle(
                dataset_dir, as_of=datetime.now(UTC), code_revision=revision
            )
            attempt = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            write_json(
                output / f"cycle-{attempt}.json",
                {"forecast": vars(forecast), "settlement": vars(settlement)},
            )
            stage = "diagnostic"
            report = diagnostic_report(
                dataset,
                registry.list_forecasts(limit=100_000)["items"],
                as_of=datetime.now(UTC),
                horizon_sessions=5,
            )
            write_json(output / "diagnostic.json", report)
        finally:
            database.dispose()
        stage = "append_only_comparison"
        after = snapshot(readonly)
        write_json(output / "registry-after.json", after)
        appended = compare_snapshots(before, after)
        if not appended.get("forward_runs") or not appended.get("forward_artifacts"):
            raise ValueError("No new run/artifact evidence was appended")
        write_json(
            output / "result.json",
            {
                "status": "verified",
                "approval_basis": record["evidence_kind"],
                "cutoff": cutoff,
                "dataset_id": dataset.dataset_id,
                "existing_records_unchanged": True,
                "appended": appended,
                "source_manifest_sha256": manifest_hash,
                "code_revision": revision,
            },
        )
        evidence_paths = [p.relative_to(output) for p in output.glob("*.json")]
        evidence_paths.extend(
            [
                checkpoint.relative_to(output) / "manifest.json",
                dataset_dir.relative_to(output) / "manifest.json",
            ]
        )
        evidence = build_evidence_manifest(
            evidence_paths,
            root=output,
            context={
                "workflow": "private-issue288",
                "commit": revision,
                "cutoff": cutoff,
                "cycle_outcome": "verified",
            },
        )
        verify_evidence_manifest(evidence, root=output)
        write_json(output / "evidence-manifest.json", evidence)
        print(
            json.dumps({"status": "verified", "output": str(output), "appended": appended}),
            flush=True,
        )
    except BaseException as error:
        attempt = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        write_json(
            output / f"failure-{attempt}.json",
            {
                "stage": stage,
                "error_type": type(error).__name__,
                "provider_responses": dict(stats),
                "observed_utc": datetime.now(UTC).isoformat(),
            },
        )
        print(f"Private verification stopped at {stage}: {type(error).__name__}", flush=True)
        raise SystemExit(1) from None
    finally:
        readonly.dispose()
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--resume-output", type=Path)
    args = parser.parse_args()
    record = Path(__file__).resolve().parents[1] / "ops/issue288/private-verification-scope.json"
    try:
        execute(args.repository_root.resolve(), record, resume_output=args.resume_output)
    except Exception as error:
        print(f"Private verification prerequisite failed: {type(error).__name__}", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
