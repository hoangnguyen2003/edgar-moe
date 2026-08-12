from __future__ import annotations

import asyncio
import csv
import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import numpy as np
import orjson
import typer

from edgar_moe.data.alpaca import AlpacaDataClient
from edgar_moe.data.demo import build_demo_snapshot
from edgar_moe.data.fred import FredClient
from edgar_moe.data.refresh import (
    load_universe_csv,
    refresh_authenticated_to_disk,
)
from edgar_moe.data.sec import SecClient
from edgar_moe.data.security_master import build_security_master
from edgar_moe.data.universe import screen_liquid_universe
from edgar_moe.features.dataset import ResearchDataset, build_research_dataset
from edgar_moe.features.text import FinBertEmbedder, HashingTextEmbedder
from edgar_moe.forward.artifacts import (
    LocalArtifactStore,
    R2ArtifactStore,
    artifact_store_from_settings,
    mirror_local_artifacts,
)
from edgar_moe.forward.config import FrozenModelSpec
from edgar_moe.forward.database import RegistryDatabase, normalize_database_url
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.forward.workflow import ForwardWorkflow
from edgar_moe.modeling.experiment import (
    run_authenticated_experiment,
    save_study_artifacts,
)
from edgar_moe.modeling.frozen import (
    evaluate_frozen_selection,
    save_frozen_evaluation,
)
from edgar_moe.modeling.walk_forward import (
    run_walk_forward_study,
    save_walk_forward_artifacts,
)
from edgar_moe.reporting import (
    build_authenticated_snapshot,
    build_frozen_snapshot,
    write_frozen_evaluation_report,
    write_research_report,
    write_validation_report,
    write_walk_forward_report,
)
from edgar_moe.settings import ResearchConfig, RuntimeSettings, runtime_settings

app = typer.Typer(
    name="edgar-moe",
    help="Point-in-time multimodal SEC filing alpha research.",
    no_args_is_help=True,
)


@app.command()
def demo(
    output: Annotated[Path, typer.Option(help="Destination snapshot JSON.")] = Path(
        "data/demo/snapshot.json"
    ),
    config_path: Annotated[Path, typer.Option("--config")] = Path("config/default.yaml"),
    epochs: Annotated[int, typer.Option(min=1, max=200)] = 35,
) -> None:
    """Train the real MoE on deterministic synthetic fixtures and export the app snapshot."""
    config = ResearchConfig.from_yaml(config_path)
    snapshot = build_demo_snapshot(
        output, config=config, seed=config.project.random_seed, max_epochs=epochs
    )
    typer.echo(
        f"Wrote {output} with {snapshot['summary']['events']:,} synthetic events and "
        f"{snapshot['summary']['test_events']:,} locked-test events."
    )


@app.command("validate-config")
def validate_config(
    config_path: Annotated[Path, typer.Option("--config")] = Path("config/default.yaml"),
) -> None:
    """Validate and print the resolved research configuration."""
    config = ResearchConfig.from_yaml(config_path)
    typer.echo(config.model_dump_json(indent=2))


@app.command("ingest-sec")
def ingest_sec(
    cik: Annotated[str, typer.Option(help="SEC CIK, with or without leading zeroes.")],
    output_dir: Annotated[Path, typer.Option()] = Path("data/raw/sec"),
) -> None:
    """Download a filer's submissions and point-in-time company facts."""
    settings = runtime_settings()

    async def run() -> None:
        async with SecClient(settings.sec_user_agent, output_dir / "filings") as client:
            submissions, facts = await asyncio.gather(
                client.submissions(cik), client.company_facts(cik)
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        padded = cik.zfill(10)
        (output_dir / f"CIK{padded}-submissions.json").write_text(
            json.dumps(submissions, indent=2), encoding="utf-8"
        )
        (output_dir / f"CIK{padded}-companyfacts.json").write_text(
            json.dumps(facts, indent=2), encoding="utf-8"
        )

    asyncio.run(run())
    typer.echo(f"Downloaded SEC metadata for CIK {cik.zfill(10)} to {output_dir}")


@app.command("ingest-assets")
def ingest_assets(
    output: Annotated[Path, typer.Option()] = Path("data/raw/alpaca/assets.json"),
) -> None:
    """Download active and inactive US equity asset metadata from Alpaca."""
    settings = runtime_settings()

    async def run() -> list[dict[str, object]]:
        async with AlpacaDataClient(settings.alpaca_api_key, settings.alpaca_api_secret) as client:
            active, inactive = await asyncio.gather(
                client.assets("active"), client.assets("inactive")
            )
        return [*active, *inactive]

    assets = asyncio.run(run())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(assets, indent=2), encoding="utf-8")
    typer.echo(f"Wrote {len(assets):,} assets to {output}")


@app.command("build-universe")
def build_universe(
    output: Annotated[Path, typer.Option()] = Path("config/universe.csv"),
    review_output: Annotated[Path, typer.Option()] = Path(
        "data/interim/security-mapping-review.json"
    ),
    config_path: Annotated[Path, typer.Option("--config")] = Path("config/default.yaml"),
) -> None:
    """Build a reviewed SEC-to-Alpaca security master including inactive assets."""
    settings = runtime_settings()
    config = ResearchConfig.from_yaml(config_path)
    _require_source_configuration(settings, needs_fred=False)

    async def run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with (
            SecClient(
                settings.sec_user_agent,
                settings.edgar_moe_data_dir / "raw" / "sec" / "cache",
                requests_per_second=config.data.sec_requests_per_second,
                cache_filings=False,
            ) as sec,
            AlpacaDataClient(settings.alpaca_api_key, settings.alpaca_api_secret) as market,
        ):
            sec_companies, active_assets, inactive_assets = await asyncio.gather(
                sec.company_tickers_exchange(),
                market.assets("active"),
                market.assets("inactive"),
            )
        mappings = build_security_master(
            sec_companies,
            [*active_assets, *inactive_assets],
            threshold=config.data.mapping_confidence_threshold,
        )
        confident = [
            {
                "cik": item.cik,
                "symbol": item.symbol,
                "security_id": item.security_id,
                "company_name": item.company_name,
                "exchange": item.exchange,
                "industry_code": "",
            }
            for item in mappings
            if item.status.value == "confident"
            and item.exchange.upper() in {"AMEX", "NASDAQ", "NYSE", "NYSEARCA", "ARCA"}
        ]
        review = [item.model_dump(mode="json") for item in mappings]
        return confident, review

    confident_rows, review_rows = asyncio.run(run())
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    with temporary_output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "cik",
                "symbol",
                "security_id",
                "company_name",
                "exchange",
                "industry_code",
            ],
        )
        writer.writeheader()
        writer.writerows(confident_rows)
    temporary_output.replace(output)
    review_output.parent.mkdir(parents=True, exist_ok=True)
    review_output.write_bytes(
        orjson.dumps(review_rows, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    )
    typer.echo(
        f"Wrote {len(confident_rows):,} confident mappings to {output}; "
        f"review evidence for {len(review_rows):,} mappings is at {review_output}."
    )


@app.command("refresh-data")
def refresh_data(
    universe: Annotated[Path, typer.Option(help="CSV with cik and symbol columns.")] = Path(
        "config/universe.example.csv"
    ),
    output_dir: Annotated[Path, typer.Option()] = Path("data/raw/authenticated"),
    start: Annotated[
        str, typer.Option(help="First market/macro date (YYYY-MM-DD).")
    ] = "2016-01-01",
    end: Annotated[
        str | None, typer.Option(help="Last observation date; defaults to as-of.")
    ] = None,
    as_of: Annotated[str | None, typer.Option(help="Point-in-time vintage date.")] = None,
    macro_series: Annotated[str, typer.Option()] = "VIXCLS,DGS10,DFF,BAA10Y",
    feed: Annotated[str, typer.Option(help="Alpaca feed; IEX works on free plans.")] = "iex",
    maximum_filings_per_issuer: Annotated[
        int | None, typer.Option(min=1, help="Optional connectivity-run cap per issuer.")
    ] = None,
    resume: Annotated[bool, typer.Option(help="Resume an incomplete dated checkpoint.")] = False,
    config_path: Annotated[Path, typer.Option("--config")] = Path("config/default.yaml"),
) -> None:
    """Collect a resumable, hashed SEC filing/market/macro research checkpoint."""
    settings = runtime_settings()
    config = ResearchConfig.from_yaml(config_path)
    _require_source_configuration(settings, needs_fred=True)
    members = load_universe_csv(universe)
    as_of_date = (
        date.fromisoformat(as_of)
        if as_of
        else datetime.now(ZoneInfo(config.project.timezone)).date()
    )
    end_date = date.fromisoformat(end) if end else as_of_date
    start_date = date.fromisoformat(start)
    series = [item.strip() for item in macro_series.split(",") if item.strip()]

    async def run() -> Path:
        async with (
            SecClient(
                settings.sec_user_agent,
                output_dir / "cache" / "sec",
                requests_per_second=config.data.sec_requests_per_second,
                cache_filings=False,
            ) as sec,
            AlpacaDataClient(settings.alpaca_api_key, settings.alpaca_api_secret) as market,
            FredClient(settings.fred_api_key) as macro,
        ):
            return await refresh_authenticated_to_disk(
                sec,
                market,
                macro,
                members,
                output_root=output_dir,
                start=start_date,
                end=end_date,
                as_of=as_of_date,
                macro_series=series,
                feed=feed,
                forms=config.data.sec_forms,
                maximum_filings_per_issuer=maximum_filings_per_issuer,
                resume=resume,
                progress=typer.echo,
            )

    destination = asyncio.run(run())
    typer.echo(
        f"Wrote and verified authenticated research inputs for {len(members)} symbols "
        f"to {destination}. No credentials were stored."
    )


@app.command("screen-universe")
def screen_universe(
    source: Annotated[Path, typer.Option(help="Reviewed broad-universe CSV.")] = Path(
        "config/universe.csv"
    ),
    output: Annotated[Path, typer.Option()] = Path("config/universe.research.csv"),
    audit_output: Annotated[Path, typer.Option()] = Path("data/interim/universe-screen.json"),
    as_of: Annotated[str | None, typer.Option(help="Screen cutoff (YYYY-MM-DD).")] = None,
    lookback_days: Annotated[int, typer.Option(min=60)] = 120,
    candidate_count: Annotated[int, typer.Option(min=50)] = 500,
    minimum_sessions: Annotated[int, typer.Option(min=20)] = 40,
    minimum_price: Annotated[float, typer.Option(min=0.01)] = 5.0,
    feed: Annotated[str, typer.Option()] = "iex",
    batch_size: Annotated[int, typer.Option(min=1, max=500)] = 200,
) -> None:
    """Create a free-tier research candidate set using trailing IEX liquidity."""
    settings = runtime_settings()
    _require_source_configuration(settings, needs_fred=False)
    members = load_universe_csv(source)
    cutoff = (
        date.fromisoformat(as_of)
        if as_of
        else datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    )
    start = cutoff - timedelta(days=lookback_days)

    async def run() -> dict[str, list[dict[str, Any]]]:
        combined: dict[str, list[dict[str, Any]]] = {}
        async with AlpacaDataClient(settings.alpaca_api_key, settings.alpaca_api_secret) as market:
            for offset in range(0, len(members), batch_size):
                batch = [member.symbol for member in members[offset : offset + batch_size]]
                payload = await market.daily_bars(batch, start, cutoff, feed=feed)
                for symbol, rows in payload.items():
                    combined.setdefault(symbol, []).extend(rows)
                typer.echo(
                    f"Screened {min(offset + batch_size, len(members)):,}/{len(members):,} mappings"
                )
        return combined

    bars = asyncio.run(run())
    selected, exclusions = screen_liquid_universe(
        members,
        bars,
        candidate_count=candidate_count,
        minimum_sessions=minimum_sessions,
        minimum_price=minimum_price,
    )
    if len(selected) < candidate_count:
        raise typer.BadParameter(
            f"Only {len(selected)} symbols passed the screen; requested {candidate_count}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    fieldnames = [
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
    ]
    with temporary_output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)
    temporary_output.replace(output)
    audit = {
        "as_of": cutoff.isoformat(),
        "lookback_start": start.isoformat(),
        "source_universe": str(source),
        "source_members": len(members),
        "candidate_count": candidate_count,
        "minimum_sessions": minimum_sessions,
        "minimum_price": minimum_price,
        "feed": feed,
        "exclusions": exclusions,
    }
    audit_output.parent.mkdir(parents=True, exist_ok=True)
    audit_output.write_bytes(orjson.dumps(audit, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))
    typer.echo(
        f"Wrote {len(selected):,} liquidity-ranked candidates to {output}; audit: {audit_output}"
    )


@app.command("build-dataset")
def build_dataset(
    checkpoint: Annotated[Path, typer.Option(help="Authenticated dated checkpoint directory.")],
    output_dir: Annotated[Path, typer.Option()] = Path("data/processed"),
    embedding_cache: Annotated[Path, typer.Option()] = Path("data/artifacts/embedding-cache"),
    embedder: Annotated[
        str,
        typer.Option(help="Use 'finbert' for research or 'hashing' only for offline verification."),
    ] = "finbert",
    device: Annotated[
        str,
        typer.Option(help="FinBERT device: 'cpu', 'mps', or 'auto'."),
    ] = "cpu",
    config_path: Annotated[Path, typer.Option("--config")] = Path("config/default.yaml"),
) -> None:
    """Convert an authenticated checkpoint into a point-in-time model dataset."""
    config = ResearchConfig.from_yaml(config_path)
    text_encoder: FinBertEmbedder | HashingTextEmbedder
    if embedder == "finbert":
        if device not in {"cpu", "mps", "auto"}:
            raise typer.BadParameter("device must be 'cpu', 'mps', or 'auto'")
        text_encoder = FinBertEmbedder(
            model_name=config.features.embedding_model,
            chunk_tokens=config.features.embedding_chunk_tokens,
            max_chunks=config.features.embedding_max_chunks,
            device=None if device == "auto" else device,
        )
    elif embedder == "hashing":
        text_encoder = HashingTextEmbedder()
    else:
        raise typer.BadParameter("embedder must be 'finbert' or 'hashing'")
    dataset = build_research_dataset(
        checkpoint,
        config=config,
        embedder=text_encoder,
        embedding_cache=embedding_cache,
        progress=typer.echo,
    )
    destination = dataset.save(output_dir)
    typer.echo(
        f"Wrote {len(dataset.events):,} audited filing events to {destination}; "
        f"{int(np.isfinite(dataset.target).sum()):,} labels are mature."
    )


@app.command("run-study")
def run_study(
    dataset_dir: Annotated[Path, typer.Option(help="Processed research dataset directory.")],
    output_dir: Annotated[Path, typer.Option()] = Path("data/artifacts/studies"),
    open_locked_test: Annotated[
        bool,
        typer.Option(
            "--open-locked-test",
            help="Explicitly evaluate the frozen test period after validation selection.",
        ),
    ] = False,
    publish_snapshot: Annotated[Path | None, typer.Option()] = None,
    report_output: Annotated[Path, typer.Option()] = Path(
        "reports/authenticated_research_report.md"
    ),
    validation_report_output: Annotated[Path, typer.Option()] = Path(
        "reports/validation_report.md"
    ),
    max_epochs: Annotated[int | None, typer.Option(min=1)] = None,
    maximum_candidates: Annotated[int | None, typer.Option(min=1)] = None,
    force: Annotated[
        bool,
        typer.Option(help="Acknowledge and overwrite an existing locked-test artifact."),
    ] = False,
    config_path: Annotated[Path, typer.Option("--config")] = Path("config/default.yaml"),
) -> None:
    """Select models on validation and optionally open the locked test exactly once."""
    config = ResearchConfig.from_yaml(config_path)
    dataset = ResearchDataset.load(dataset_dir)
    artifact_directory = output_dir / dataset.dataset_id
    locked_path = artifact_directory / "locked-test.json"
    if open_locked_test and locked_path.exists() and not force:
        raise typer.BadParameter(
            f"Locked test already exists at {locked_path}; pass --force only with an audit reason"
        )
    if publish_snapshot is not None and not open_locked_test:
        raise typer.BadParameter("--publish-snapshot requires --open-locked-test")
    result = run_authenticated_experiment(
        dataset,
        config=config,
        evaluate_locked_test=open_locked_test,
        max_epochs=max_epochs,
        maximum_candidates=maximum_candidates,
    )
    destination = save_study_artifacts(
        result,
        output_dir,
        config=config,
        force=force,
    )
    typer.echo(
        f"Selected {result.selected_candidate.name} on validation "
        f"(RMSE={result.validation_metrics['rmse']:.6f}); artifacts: {destination}"
    )
    write_validation_report(
        dataset,
        result,
        validation_report_output,
        config=config,
    )
    typer.echo(f"Wrote validation report to {validation_report_output}")
    if open_locked_test:
        write_research_report(
            dataset,
            result,
            report_output,
            config=config,
        )
        typer.echo(f"Wrote locked research report to {report_output}")
        if publish_snapshot is not None:
            build_authenticated_snapshot(
                dataset,
                result,
                publish_snapshot,
                config=config,
            )
            typer.echo(f"Published authenticated snapshot to {publish_snapshot}")


@app.command("walk-forward-study")
def walk_forward_study(
    dataset_dir: Annotated[Path, typer.Option(help="Processed research dataset directory.")],
    output_dir: Annotated[Path, typer.Option()] = Path("data/artifacts/walk-forward"),
    report_output: Annotated[Path, typer.Option()] = Path("reports/walk_forward_report.md"),
    max_epochs: Annotated[int | None, typer.Option(min=1)] = None,
    maximum_candidates: Annotated[int | None, typer.Option(min=1)] = None,
    device: Annotated[
        str,
        typer.Option(help="Training device: 'cpu', 'mps', or 'auto'."),
    ] = "cpu",
    config_path: Annotated[Path, typer.Option("--config")] = Path("config/default.yaml"),
) -> None:
    """Choose a stable champion on expanding pre-test folds; never score the test."""
    if device not in {"cpu", "mps", "auto"}:
        raise typer.BadParameter("device must be 'cpu', 'mps', or 'auto'")
    config = ResearchConfig.from_yaml(config_path)
    dataset = ResearchDataset.load(dataset_dir)
    result = run_walk_forward_study(
        dataset,
        config=config,
        max_epochs=max_epochs,
        maximum_candidates=maximum_candidates,
        device=None if device == "auto" else device,
    )
    destination = save_walk_forward_artifacts(
        result,
        output_dir,
        config=config,
    )
    selection_payload = orjson.loads((destination / "walk-forward-selection.json").read_bytes())
    write_walk_forward_report(
        dataset,
        result,
        report_output,
        config=config,
        selection_hash=str(selection_payload["selection_hash"]),
    )
    typer.echo(
        f"Selected {result.champion.name} by worst-fold rank IC "
        f"({result.champion.worst_fold_rank_ic:.6f}); artifacts: {destination}"
    )
    typer.echo(f"Wrote {report_output}; locked-test predictions: 0")


@app.command("open-frozen-test")
def open_frozen_test(
    dataset_dir: Annotated[Path, typer.Option(help="Processed research dataset directory.")],
    selection_path: Annotated[
        Path, typer.Option("--selection", help="Frozen walk-forward selection JSON.")
    ],
    confirmation_hash: Annotated[
        str,
        typer.Option(
            "--confirm-selection-hash",
            help="Exact SHA-256 recorded in the reviewed selection artifact.",
        ),
    ],
    output_dir: Annotated[Path, typer.Option()] = Path("data/artifacts/frozen-studies"),
    report_output: Annotated[Path, typer.Option()] = Path(
        "reports/authenticated_research_report.md"
    ),
    publish_snapshot: Annotated[Path | None, typer.Option()] = None,
    recovery_note: Annotated[
        str | None,
        typer.Option(
            help=(
                "Audit disclosure when an earlier opening attempt failed before persistence; "
                "the note is included in the immutable result hash and report."
            )
        ),
    ] = None,
    device: Annotated[
        str,
        typer.Option(help="Training device: 'cpu', 'mps', or 'auto'."),
    ] = "cpu",
) -> None:
    """Open the locked period once using only the hash-confirmed frozen champion."""
    if device not in {"cpu", "mps", "auto"}:
        raise typer.BadParameter("device must be 'cpu', 'mps', or 'auto'")
    dataset = ResearchDataset.load(dataset_dir)
    locked_path = output_dir / dataset.dataset_id / "locked-test.json"
    if locked_path.exists():
        raise typer.BadParameter(
            f"Locked test already exists at {locked_path}; refusing to evaluate again"
        )
    result = evaluate_frozen_selection(
        dataset,
        selection_path,
        confirmation_hash=confirmation_hash,
        device=None if device == "auto" else device,
    )
    destination = save_frozen_evaluation(result, output_dir, recovery_note=recovery_note)
    locked_payload = orjson.loads((destination / "locked-test.json").read_bytes())
    locked_test_hash = str(locked_payload["locked_test_hash"])
    write_frozen_evaluation_report(
        dataset,
        result,
        report_output,
        recovery_note=recovery_note,
        locked_test_hash=locked_test_hash,
    )
    if publish_snapshot is not None:
        build_frozen_snapshot(
            dataset,
            result,
            publish_snapshot,
            locked_test_hash=locked_test_hash,
            recovery_note=recovery_note,
        )
    typer.echo(
        f"Completed the locked test for {result.champion_name}: "
        f"rank IC={result.test_metrics['rank_ic']:.6f}, "
        f"RMSE={result.test_metrics['rmse']:.6f}; artifacts: {destination}"
    )
    typer.echo(f"Wrote immutable locked report to {report_output}")
    if publish_snapshot is not None:
        typer.echo(f"Published authenticated snapshot to {publish_snapshot}")


@app.command("forward-init")
def forward_init(
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="EDGAR_MOE_REGISTRY_DATABASE_URL",
            help="SQLite or Postgres registry URL.",
        ),
    ] = None,
) -> None:
    """Apply all forward-registry migrations to a local or production database."""
    from alembic import command
    from alembic.config import Config

    resolved_url = _forward_database_url(runtime_settings(), database_url)
    bootstrap_database = RegistryDatabase(resolved_url)
    bootstrap_database.dispose()
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", normalize_database_url(resolved_url))
    command.upgrade(alembic_config, "head")
    typer.echo(f"Forward registry is at schema head ({_safe_database_label(resolved_url)}).")


@app.command("forward-mirror-artifacts")
def forward_mirror_artifacts() -> None:
    """Copy and verify all local content-addressed evidence in Cloudflare R2."""
    settings = runtime_settings()
    local = LocalArtifactStore(settings.edgar_moe_artifact_dir)
    mirror = R2ArtifactStore(
        endpoint_url=settings.edgar_moe_r2_endpoint_url,
        bucket=settings.edgar_moe_r2_bucket,
        access_key_id=settings.edgar_moe_r2_access_key_id,
        secret_access_key=settings.edgar_moe_r2_secret_access_key,
    )
    result = mirror_local_artifacts(local, mirror)
    typer.echo(
        f"Mirrored and verified {result['objects']:,} artifact(s) "
        f"({result['bytes']:,} bytes) in R2."
    )


@app.command("forward-forecast")
def forward_forecast(
    dataset_dir: Annotated[Path, typer.Option(help="Processed point-in-time dataset directory.")],
    as_of: Annotated[
        str | None,
        typer.Option(help="Timezone-aware recording timestamp; defaults to the current UTC clock."),
    ] = None,
    model_config: Annotated[Path, typer.Option("--model-config")] = Path(
        "config/forward.yaml"
    ),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="EDGAR_MOE_REGISTRY_DATABASE_URL"),
    ] = None,
    device: Annotated[str, typer.Option(help="Inference device: cpu, mps, or auto.")] = "cpu",
) -> None:
    """Score only not-yet-tradable events with the immutable frozen model."""
    if device not in {"cpu", "mps", "auto"}:
        raise typer.BadParameter("device must be 'cpu', 'mps', or 'auto'")
    settings = runtime_settings()
    database = RegistryDatabase(_forward_database_url(settings, database_url))
    registry = ForwardRegistry(database, actor="edgar-moe-cli")
    workflow = ForwardWorkflow(
        registry,
        artifact_store_from_settings(settings),
        FrozenModelSpec.from_yaml(model_config),
    )
    try:
        result = workflow.forecast(
            dataset_dir,
            as_of=_parse_timestamp(as_of),
            code_revision=_code_revision(),
            device="cpu" if device == "auto" else device,
        )
    finally:
        database.dispose()
    typer.echo(
        f"Forward run {result.run_id} {result.status}: "
        f"{result.counts['forecasts_inserted']} new forecast(s); evidence {result.artifact_uri}"
    )


@app.command("forward-settle")
def forward_settle(
    dataset_dir: Annotated[
        Path, typer.Option(help="Later processed dataset containing newly matured labels.")
    ],
    as_of: Annotated[
        str | None,
        typer.Option(help="Timezone-aware settlement cutoff; defaults to the current UTC clock."),
    ] = None,
    model_config: Annotated[Path, typer.Option("--model-config")] = Path(
        "config/forward.yaml"
    ),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="EDGAR_MOE_REGISTRY_DATABASE_URL"),
    ] = None,
) -> None:
    """Append matured outcomes without changing any recorded forecast."""
    settings = runtime_settings()
    database = RegistryDatabase(_forward_database_url(settings, database_url))
    registry = ForwardRegistry(database, actor="edgar-moe-cli")
    workflow = ForwardWorkflow(
        registry,
        artifact_store_from_settings(settings),
        FrozenModelSpec.from_yaml(model_config),
    )
    try:
        result = workflow.settle(
            dataset_dir,
            as_of=_parse_timestamp(as_of),
            code_revision=_code_revision(),
        )
    finally:
        database.dispose()
    typer.echo(
        f"Settlement run {result.run_id} {result.status}: "
        f"{result.counts['labels_inserted']} new label(s); evidence {result.artifact_uri}"
    )


@app.command("forward-status")
def forward_status(
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="EDGAR_MOE_REGISTRY_DATABASE_URL"),
    ] = None,
) -> None:
    """Print registry coverage and prospective performance as JSON."""
    database = RegistryDatabase(_forward_database_url(runtime_settings(), database_url))
    registry = ForwardRegistry(database, actor="edgar-moe-cli")
    try:
        payload = {"status": registry.status(), "performance": registry.performance()}
    finally:
        database.dispose()
    typer.echo(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode())


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
) -> None:
    """Serve the snapshot-backed FastAPI application."""
    import uvicorn

    uvicorn.run("edgar_moe.api.app:app", host=host, port=port, reload=False)


def _require_source_configuration(settings: RuntimeSettings, *, needs_fred: bool) -> None:
    missing: list[str] = []
    if not settings.alpaca_api_key:
        missing.append("ALPACA_API_KEY")
    if not settings.alpaca_api_secret:
        missing.append("ALPACA_API_SECRET")
    if needs_fred and not settings.fred_api_key:
        missing.append("FRED_API_KEY")
    if not settings.sec_user_agent or "example.com" in settings.sec_user_agent.lower():
        missing.append("SEC_USER_AGENT with your real contact email")
    if missing:
        raise typer.BadParameter(
            "Missing authenticated-source configuration: " + ", ".join(missing)
        )


def _forward_database_url(settings: RuntimeSettings, override: str | None) -> str:
    return (
        override
        or settings.edgar_moe_registry_database_url
        or "sqlite:///data/forward/registry.sqlite3"
    )


def _parse_timestamp(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise typer.BadParameter("Timestamp must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise typer.BadParameter("Timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _code_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _safe_database_label(database_url: str) -> str:
    if database_url.startswith("sqlite"):
        return database_url
    return "configured Postgres database"


if __name__ == "__main__":
    app()
