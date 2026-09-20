from __future__ import annotations

import asyncio
import csv
import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from zoneinfo import ZoneInfo

import orjson
import typer

from edgar_moe.capacity import DEFAULT_BASELINE_PATHS, build_capacity_baseline
from edgar_moe.settings import runtime_settings

if TYPE_CHECKING:
    from edgar_moe.settings import RuntimeSettings

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
    from edgar_moe.data.demo import build_demo_snapshot
    from edgar_moe.settings import ResearchConfig

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
    from edgar_moe.settings import ResearchConfig

    config = ResearchConfig.from_yaml(config_path)
    typer.echo(config.model_dump_json(indent=2))


@app.command("ingest-sec")
def ingest_sec(
    cik: Annotated[str, typer.Option(help="SEC CIK, with or without leading zeroes.")],
    output_dir: Annotated[Path, typer.Option()] = Path("data/raw/sec"),
) -> None:
    """Download a filer's submissions and point-in-time company facts."""
    from edgar_moe.data.sec import SecClient

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
    from edgar_moe.data.alpaca import AlpacaDataClient

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
    from edgar_moe.data.alpaca import AlpacaDataClient
    from edgar_moe.data.sec import SecClient
    from edgar_moe.data.security_master import build_security_master
    from edgar_moe.settings import ResearchConfig

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
    from edgar_moe.data.alpaca import AlpacaDataClient
    from edgar_moe.data.fred import FredClient
    from edgar_moe.data.refresh import load_universe_csv, refresh_authenticated_to_disk
    from edgar_moe.data.sec import SecClient
    from edgar_moe.settings import ResearchConfig

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
    from edgar_moe.data.alpaca import AlpacaDataClient
    from edgar_moe.data.refresh import load_universe_csv
    from edgar_moe.data.universe import screen_liquid_universe

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
    import numpy as np

    from edgar_moe.features.dataset import build_research_dataset
    from edgar_moe.features.text import FinBertEmbedder, HashingTextEmbedder
    from edgar_moe.settings import ResearchConfig

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


@app.command("research-drift")
def research_drift(
    baseline_dataset_dir: Annotated[
        Path,
        typer.Option(
            "--baseline-dataset",
            help="Frozen training dataset directory used as the comparison baseline.",
        ),
    ],
    prospective_dataset_dir: Annotated[
        Path,
        typer.Option(
            "--prospective-dataset",
            help="Later processed dataset to inspect prospectively.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="JSON drift report destination."),
    ] = Path("reports/research-drift.json"),
    model_config: Annotated[Path, typer.Option("--model-config")] = Path(
        "config/forward.yaml"
    ),
    device: Annotated[
        str,
        typer.Option(help="Frozen predictor device: 'cpu' or 'mps'."),
    ] = "cpu",
) -> None:
    """Measure prospective feature and frozen-component drift without retraining."""
    from edgar_moe.features.dataset import ResearchDataset
    from edgar_moe.features.drift import build_research_drift_report
    from edgar_moe.forward.config import FrozenModelSpec
    from edgar_moe.forward.inference import FrozenPredictor

    if device not in {"cpu", "mps"}:
        raise typer.BadParameter("device must be 'cpu' or 'mps'")
    spec = FrozenModelSpec.from_yaml(model_config)
    baseline = ResearchDataset.load(baseline_dataset_dir)
    prospective = ResearchDataset.load(prospective_dataset_dir)
    if baseline.dataset_id != spec.training_dataset_id:
        raise typer.BadParameter(
            "Baseline dataset identity does not match the frozen model specification: "
            f"expected {spec.training_dataset_id}, observed {baseline.dataset_id}"
        )
    spec.verify_locked_evidence()
    predictor = FrozenPredictor.load(
        spec.model_path,
        expected_sha256=spec.artifact_sha256,
        expected_selection_hash=spec.selection_hash,
        device=device,
    )
    report = build_research_drift_report(
        baseline,
        prospective,
        baseline_components=predictor.component_outputs(baseline, device=device),
        prospective_components=predictor.component_outputs(prospective, device=device),
        context={
            "model_id": spec.model_id,
            "model_version": spec.version,
            "artifact_sha256": spec.artifact_sha256,
            "selection_hash": spec.selection_hash,
            "frozen_at": spec.frozen_at.isoformat(),
        },
    )
    serialized = orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    temporary_output.write_bytes(serialized)
    temporary_output.replace(output)
    typer.echo(
        f"Research drift status: {report['status']}; "
        f"report hash: {report['report_hash']}; wrote {output}"
    )


@app.command("research-drift-history")
def research_drift_history(
    reports: Annotated[
        list[Path],
        typer.Option(
            "--report",
            help="Content-hashed research-drift report; repeat for each later dataset.",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="JSON drift-history review destination."),
    ] = Path("reports/research-drift-history.json"),
    minimum_reports: Annotated[
        int,
        typer.Option("--minimum-reports", min=1, help="Reports required before stable history."),
    ] = 3,
) -> None:
    """Aggregate prospective drift reports without retraining or reading outcomes."""
    from edgar_moe.features.drift_history import DriftHistoryError, build_research_drift_history

    if not reports:
        raise typer.BadParameter("at least one --report is required")
    payloads: list[dict[str, Any]] = []
    for report_path in reports:
        try:
            payload = orjson.loads(report_path.read_bytes())
        except (OSError, orjson.JSONDecodeError) as error:
            raise typer.BadParameter(f"cannot read drift report: {report_path}") from error
        if not isinstance(payload, dict):
            raise typer.BadParameter(f"drift report must be a JSON object: {report_path}")
        payloads.append(payload)
    try:
        history = build_research_drift_history(
            payloads,
            report_paths=reports,
            minimum_reports=minimum_reports,
        )
    except DriftHistoryError as error:
        raise typer.BadParameter(str(error)) from error
    serialized = orjson.dumps(history, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    temporary_output.write_bytes(serialized)
    temporary_output.replace(output)
    typer.echo(
        f"Research drift history status: {history['status']}; "
        f"reports: {history['report_count']}; history hash: {history['history_hash']}; "
        f"wrote {output}"
    )


@app.command("research-drift-history-verify")
def research_drift_history_verify(
    history: Annotated[Path, typer.Argument(help="Content-addressed research-drift history JSON.")],
) -> None:
    """Verify a retained prospective drift history without rebuilding it."""
    from edgar_moe.features.drift_history import DriftHistoryError, verify_research_drift_history

    try:
        payload = orjson.loads(history.read_bytes())
    except (OSError, orjson.JSONDecodeError) as error:
        raise typer.BadParameter(f"cannot read drift history: {history}") from error
    if not isinstance(payload, dict):
        raise typer.BadParameter("drift history must be a JSON object")
    try:
        verify_research_drift_history(payload)
    except DriftHistoryError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Verified research drift history {payload['history_hash']} "
        f"({payload['report_count']} reports; status={payload['status']}; "
        f"warning_streak={payload['warning_streak']})"
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
    from edgar_moe.features.dataset import ResearchDataset
    from edgar_moe.modeling.experiment import run_authenticated_experiment, save_study_artifacts
    from edgar_moe.reporting import (
        build_authenticated_snapshot,
        write_research_report,
        write_validation_report,
    )
    from edgar_moe.settings import ResearchConfig

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
    from edgar_moe.features.dataset import ResearchDataset
    from edgar_moe.modeling.walk_forward import run_walk_forward_study, save_walk_forward_artifacts
    from edgar_moe.reporting import write_walk_forward_report
    from edgar_moe.settings import ResearchConfig

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
    from edgar_moe.features.dataset import ResearchDataset
    from edgar_moe.modeling.frozen import evaluate_frozen_selection, save_frozen_evaluation
    from edgar_moe.reporting import build_frozen_snapshot, write_frozen_evaluation_report

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

    from edgar_moe.forward.database import RegistryDatabase, normalize_database_url

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
    from edgar_moe.forward.artifacts import (
        LocalArtifactStore,
        R2ArtifactStore,
        mirror_local_artifacts,
    )

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


@app.command("forward-reconcile-artifacts")
def forward_reconcile_artifacts(
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="EDGAR_MOE_REGISTRY_DATABASE_URL"),
    ] = None,
    repair: Annotated[
        bool,
        typer.Option(
            "--repair",
            help="Mirror verified referenced objects to R2; default mode is read-only.",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Optional JSON report destination."),
    ] = None,
) -> None:
    """Verify registry-referenced evidence and optionally repair the R2 mirror."""
    from edgar_moe.forward.artifacts import LocalArtifactStore, R2ArtifactStore
    from edgar_moe.forward.database import RegistryDatabase
    from edgar_moe.forward.reconciliation import reconcile_registry_artifacts
    from edgar_moe.forward.registry import ForwardRegistry

    settings = runtime_settings()
    database = RegistryDatabase(_forward_database_url(settings, database_url))
    mirror = None
    if repair:
        mirror = R2ArtifactStore(
            endpoint_url=settings.edgar_moe_r2_endpoint_url,
            bucket=settings.edgar_moe_r2_bucket,
            access_key_id=settings.edgar_moe_r2_access_key_id,
            secret_access_key=settings.edgar_moe_r2_secret_access_key,
        )
    try:
        report = reconcile_registry_artifacts(
            ForwardRegistry(database, actor="edgar-moe-reconciler"),
            LocalArtifactStore(settings.edgar_moe_artifact_dir),
            mirror=mirror,
            repair=repair,
        )
    finally:
        database.dispose()
    serialized = orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(serialized)
        typer.echo(f"Wrote artifact reconciliation report to {output}")
    typer.echo(serialized.decode())
    if report["status"] != "passed":
        raise typer.Exit(code=1)


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
    from edgar_moe.forward.artifacts import artifact_store_from_settings
    from edgar_moe.forward.config import FrozenModelSpec
    from edgar_moe.forward.database import RegistryDatabase
    from edgar_moe.forward.registry import ForwardRegistry
    from edgar_moe.forward.workflow import ForwardWorkflow

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
    from edgar_moe.forward.artifacts import artifact_store_from_settings
    from edgar_moe.forward.config import FrozenModelSpec
    from edgar_moe.forward.database import RegistryDatabase
    from edgar_moe.forward.registry import ForwardRegistry
    from edgar_moe.forward.workflow import ForwardWorkflow

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


@app.command("forward-diagnostic")
def forward_diagnostic(
    dataset_dir: Annotated[Path, typer.Option(help="Processed point-in-time dataset directory.")],
    horizon_sessions: Annotated[
        int,
        typer.Option(
            "--horizon-sessions",
            min=2,
            max=19,
            help="Short diagnostic horizon; the official target remains 20 sessions.",
        ),
    ] = 5,
    as_of: Annotated[
        str | None,
        typer.Option(help="As-of timestamp; defaults to the current UTC clock."),
    ] = None,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="EDGAR_MOE_REGISTRY_DATABASE_URL"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Optional JSON report destination."),
    ] = None,
) -> None:
    """Report short-horizon diagnostics without mutating forward evidence."""
    from edgar_moe.features.dataset import ResearchDataset
    from edgar_moe.forward.database import RegistryDatabase
    from edgar_moe.forward.diagnostics import diagnostic_report
    from edgar_moe.forward.registry import ForwardRegistry

    settings = runtime_settings()
    database = RegistryDatabase(_forward_database_url(settings, database_url))
    try:
        dataset = ResearchDataset.load(dataset_dir)
        registry = ForwardRegistry(database, actor="edgar-moe-cli")
        forecasts = registry.list_forecasts(limit=100_000)["items"]
        report = diagnostic_report(
            dataset,
            forecasts,
            as_of=_parse_timestamp(as_of),
            horizon_sessions=horizon_sessions,
        )
    finally:
        database.dispose()
    serialized = orjson.dumps(report, option=orjson.OPT_INDENT_2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(serialized)
        typer.echo(f"Wrote diagnostic report to {output}")
    typer.echo(serialized.decode())


@app.command("forward-status")
def forward_status(
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="EDGAR_MOE_REGISTRY_DATABASE_URL"),
    ] = None,
) -> None:
    """Print registry coverage and prospective performance as JSON."""
    from edgar_moe.forward.database import RegistryDatabase
    from edgar_moe.forward.registry import ForwardRegistry

    database = RegistryDatabase(_forward_database_url(runtime_settings(), database_url))
    registry = ForwardRegistry(database, actor="edgar-moe-cli")
    try:
        payload = {"status": registry.status(), "performance": registry.performance()}
    finally:
        database.dispose()
    typer.echo(orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode())


@app.command("capacity-baseline")
def capacity_baseline(
    snapshot: Annotated[
        Path,
        typer.Option("--snapshot", help="Snapshot JSON used for local read-path timings."),
    ] = Path("data/demo/snapshot.json"),
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", envvar="EDGAR_MOE_REGISTRY_DATABASE_URL"),
    ] = None,
    api_url: Annotated[
        str | None,
        typer.Option("--api-url", help="Optional public API origin to measure over HTTP."),
    ] = None,
    workflow_runtime_seconds: Annotated[
        float | None,
        typer.Option(
            "--workflow-runtime-seconds",
            min=0,
            help="Optional observed GitHub Actions runtime to retain in the baseline.",
        ),
    ] = None,
    path: Annotated[
        list[Path] | None,
        typer.Option("--path", help="Filesystem path to inventory; repeat for additional paths."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Optional JSON report destination."),
    ] = None,
) -> None:
    """Capture local latency, storage, and cost-capacity observations."""
    settings = runtime_settings()
    report = build_capacity_baseline(
        snapshot_path=snapshot,
        database_url=_forward_database_url(settings, database_url),
        api_url=api_url,
        paths=path or DEFAULT_BASELINE_PATHS,
        workflow_runtime_seconds=workflow_runtime_seconds,
    )
    serialized = orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(serialized)
        typer.echo(f"Wrote capacity baseline to {output}")
    typer.echo(serialized.decode())


@app.command("research-copilot")
def research_copilot(
    question: Annotated[
        str,
        typer.Argument(help="Evidence-grounded research question; no trading instructions."),
    ],
    snapshot: Annotated[
        Path,
        typer.Option("--snapshot", help="Immutable snapshot JSON used by the read-only tools."),
    ] = Path("data/demo/snapshot.json"),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="EDGAR_MOE_REGISTRY_READ_DATABASE_URL",
            help="Optional SELECT-only forward-registry URL; local SQLite is also supported.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Optional private JSON answer/evidence report."),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Optional OpenAI-compatible chat-completions endpoint."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Optional provider model override."),
    ] = None,
    max_tool_calls: Annotated[
        int | None,
        typer.Option("--max-tool-calls", min=1, max=8, help="Bound the agent tool-call loop."),
    ] = None,
    plan_only: Annotated[
        bool,
        typer.Option(
            "--plan-only",
            help="Print the frozen identity and tool contract without contacting an LLM.",
        ),
    ] = False,
) -> None:
    """Ask an optional, read-only, citation-backed research copilot.

    This command is intentionally operator-run. It never exposes the provider
    key through the public API and never mutates the model, registry, labels, or
    deployment state. Use --plan-only to inspect the agent boundary for free.
    """
    from edgar_moe.api.repository import SnapshotRepository
    from edgar_moe.copilot import OpenAICompatibleProvider, ReadOnlyToolset, ResearchCopilot
    from edgar_moe.forward.database import RegistryDatabase
    from edgar_moe.forward.registry import ForwardRegistry

    settings = runtime_settings()
    repository = SnapshotRepository(snapshot)
    registry_database = None
    registry = None
    resolved_database_url = _copilot_database_url(settings, database_url)
    if resolved_database_url:
        registry_database = RegistryDatabase(resolved_database_url)
        registry = ForwardRegistry(registry_database, actor="edgar-moe-copilot")
    try:
        toolset = ReadOnlyToolset(repository, registry)
        if plan_only:
            report: dict[str, object] = {
                "schema_version": 1,
                "research_only": True,
                "frozen_identity": repository.frozen_identity(),
                "tools": [tool.as_provider_schema() for tool in toolset.definitions()],
                "provider_contacted": False,
                "disclaimer": (
                    "The copilot is read-only and cannot modify forecasts, labels, registry records, "
                    "or deployment state."
                ),
            }
        else:
            resolved_endpoint = endpoint or settings.edgar_moe_copilot_endpoint
            provider = OpenAICompatibleProvider(
                endpoint=resolved_endpoint,
                api_key=settings.edgar_moe_copilot_api_key.get_secret_value(),
                model=model or settings.edgar_moe_copilot_model,
                timeout_seconds=settings.edgar_moe_copilot_timeout_seconds,
                max_tokens=settings.edgar_moe_copilot_max_tokens,
            )
            answer = ResearchCopilot(
                provider=provider,
                toolset=toolset,
                max_tool_calls=max_tool_calls or settings.edgar_moe_copilot_max_tool_calls,
            ).ask(question)
            report = answer.as_dict()
    finally:
        if registry_database is not None:
            registry_database.dispose()

    serialized = orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_suffix(output.suffix + ".tmp")
        temporary_output.write_bytes(serialized)
        temporary_output.replace(output)
        typer.echo(f"Wrote private research-copilot report to {output}")
    else:
        typer.echo(serialized.decode())


@app.command("research-copilot-eval")
def research_copilot_eval(
    answers: Annotated[
        list[Path],
        typer.Argument(help="Private JSON answer reports produced by research-copilot."),
    ],
    corpus: Annotated[
        Path,
        typer.Option("--corpus", help="Reviewed evaluation corpus JSON."),
    ] = Path("config/copilot_eval_cases.json"),
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Optional private JSON evaluation report."),
    ] = None,
    fail_under: Annotated[
        float,
        typer.Option(min=0.0, max=1.0, help="Minimum pass rate; exit non-zero below this value."),
    ] = 1.0,
    require_complete: Annotated[
        bool,
        typer.Option("--require-complete", help="Fail unless every corpus case has an answer."),
    ] = False,
) -> None:
    """Score private copilot reports against the reviewed evidence contract.

    This is an offline structural evaluation. It never contacts an LLM and it
    never writes answer text into the aggregate report.
    """
    from edgar_moe.copilot.evaluation import (
        EvaluationInputError,
        evaluate_reports,
        load_evaluation_corpus,
    )

    try:
        evaluation_corpus = load_evaluation_corpus(corpus)
        reports: list[dict[str, object]] = []
        for answer_path in answers:
            parsed = json.loads(answer_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise EvaluationInputError(f"answer report must be a JSON object: {answer_path}")
            reports.append({str(key): value for key, value in parsed.items()})
        suite = evaluate_reports(tuple(reports), evaluation_corpus)
    except (EvaluationInputError, OSError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error

    report = suite.as_dict()
    if require_complete and not suite.complete:
        report["complete_gate_failed"] = True
    serialized = orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_suffix(output.suffix + ".tmp")
        temporary_output.write_bytes(serialized)
        temporary_output.replace(output)
        typer.echo(f"Wrote private copilot evaluation to {output}")
    typer.echo(serialized.decode())
    if (require_complete and not suite.complete) or suite.pass_rate < fail_under:
        raise typer.Exit(code=1)


@app.command("research-copilot-benchmark")
def research_copilot_benchmark(
    corpus: Annotated[
        Path,
        typer.Option("--corpus", help="Reviewed evaluation corpus JSON."),
    ] = Path("config/copilot_eval_cases.json"),
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Private directory for individual answer reports and the aggregate score.",
        ),
    ] = Path("/tmp/edgar-moe-copilot-benchmark"),
    case_ids: Annotated[
        list[str] | None,
        typer.Option("--case", help="Run only this case id; repeat for multiple cases."),
    ] = None,
    snapshot: Annotated[
        Path,
        typer.Option("--snapshot", help="Immutable snapshot JSON used by the read-only tools."),
    ] = Path("data/demo/snapshot.json"),
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            envvar="EDGAR_MOE_REGISTRY_READ_DATABASE_URL",
            help="Optional SELECT-only forward-registry URL.",
        ),
    ] = None,
    endpoint: Annotated[
        str | None,
        typer.Option("--endpoint", help="Optional OpenAI-compatible chat-completions endpoint."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Optional provider model override."),
    ] = None,
    max_tool_calls: Annotated[
        int | None,
        typer.Option("--max-tool-calls", min=1, max=8, help="Bound each agent tool-call loop."),
    ] = None,
    fail_under: Annotated[
        float,
        typer.Option(min=0.0, max=1.0, help="Minimum structural pass rate."),
    ] = 1.0,
    plan_only: Annotated[
        bool,
        typer.Option("--plan-only", help="List selected cases without contacting an LLM."),
    ] = False,
) -> None:
    """Run the reviewed copilot corpus as a bounded private benchmark.

    Individual answer envelopes are written only to ``output_dir``. The
    aggregate report contains hashes and structural observations, not answer
    text, provider payloads, credentials, or endpoint URLs.
    """
    from edgar_moe.api.repository import SnapshotRepository
    from edgar_moe.copilot import (
        OpenAICompatibleProvider,
        ReadOnlyToolset,
        ResearchCopilot,
    )
    from edgar_moe.copilot.benchmark import run_benchmark, write_benchmark_report
    from edgar_moe.copilot.evaluation import (
        EvaluationCorpus,
        EvaluationInputError,
        load_evaluation_corpus,
    )
    from edgar_moe.forward.database import RegistryDatabase
    from edgar_moe.forward.registry import ForwardRegistry

    try:
        evaluation_corpus = load_evaluation_corpus(corpus)
        requested_case_ids = tuple(case_ids or ())
        unknown_case_ids = sorted(
            set(requested_case_ids) - {case.case_id for case in evaluation_corpus.cases}
        )
        if unknown_case_ids:
            raise EvaluationInputError(f"unknown evaluation case id: {', '.join(unknown_case_ids)}")
        if len(set(requested_case_ids)) != len(requested_case_ids):
            raise EvaluationInputError("--case values must not contain duplicates")
        selected_cases = tuple(
            case
            for case in evaluation_corpus.cases
            if not requested_case_ids or case.case_id in requested_case_ids
        )
        selected_corpus = EvaluationCorpus(
            corpus_id=evaluation_corpus.corpus_id,
            cases=selected_cases,
            sha256=evaluation_corpus.sha256,
        )
    except (EvaluationInputError, OSError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error

    if plan_only:
        typer.echo(
            orjson.dumps(
                {
                    "schema_version": 1,
                    "corpus_id": selected_corpus.corpus_id,
                    "corpus_sha256": selected_corpus.sha256,
                    "cases": [
                        {"id": case.case_id, "question": case.question}
                        for case in selected_corpus.cases
                    ],
                    "provider_contacted": False,
                    "research_only": True,
                },
                option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
            ).decode()
        )
        return

    settings = runtime_settings()
    repository = SnapshotRepository(snapshot)
    registry_database = None
    registry = None
    resolved_database_url = _copilot_database_url(settings, database_url)
    if resolved_database_url:
        registry_database = RegistryDatabase(resolved_database_url)
        registry = ForwardRegistry(registry_database, actor="edgar-moe-copilot-benchmark")

    try:
        toolset = ReadOnlyToolset(repository, registry)
        provider = OpenAICompatibleProvider(
            endpoint=endpoint or settings.edgar_moe_copilot_endpoint,
            api_key=settings.edgar_moe_copilot_api_key.get_secret_value(),
            model=model or settings.edgar_moe_copilot_model,
            timeout_seconds=settings.edgar_moe_copilot_timeout_seconds,
            max_tokens=settings.edgar_moe_copilot_max_tokens,
        )
        copilot = ResearchCopilot(
            provider=provider,
            toolset=toolset,
            max_tool_calls=max_tool_calls or settings.edgar_moe_copilot_max_tool_calls,
        )
        benchmark = run_benchmark(selected_corpus, copilot, output_dir)
    finally:
        if registry_database is not None:
            registry_database.dispose()

    aggregate = benchmark.as_dict(provider=provider.provider_name, model=provider.model)
    summary_path = output_dir / "evaluation.json"
    write_benchmark_report(summary_path, aggregate)
    typer.echo(f"Wrote private copilot benchmark to {output_dir}")
    typer.echo(orjson.dumps(aggregate, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode())
    if benchmark.failures or benchmark.suite.pass_rate < fail_under or not benchmark.suite.complete:
        raise typer.Exit(code=1)


@app.command("research-copilot-review")
def research_copilot_review(
    benchmark: Annotated[
        Path,
        typer.Option("--benchmark", help="Private evaluation.json from research-copilot-benchmark."),
    ],
    review: Annotated[
        Path,
        typer.Option("--review", help="Private rubric review batch JSON."),
    ],
    history: Annotated[
        Path,
        typer.Option("--history", help="Append-only review history JSON destination."),
    ] = Path("/tmp/edgar-moe-copilot-review-history.json"),
    minimum_reviews: Annotated[
        int,
        typer.Option(
            "--minimum-reviews",
            min=1,
            max=256,
            help="Reviews required before an all-pass history is marked accepted.",
        ),
    ] = 4,
) -> None:
    """Append a private human rubric review to a content-addressed history."""
    from edgar_moe.copilot.review import (
        ReviewInputError,
        append_copilot_reviews,
        write_copilot_review_history,
    )

    try:
        benchmark_payload = json.loads(benchmark.read_text(encoding="utf-8"))
        review_payload = json.loads(review.read_text(encoding="utf-8"))
        if not isinstance(benchmark_payload, dict):
            raise ReviewInputError("benchmark report must be a JSON object")
        if not isinstance(review_payload, dict):
            raise ReviewInputError("review batch must be a JSON object")
        existing_payload: dict[str, Any] | None = None
        if history.exists():
            parsed_history = json.loads(history.read_text(encoding="utf-8"))
            if not isinstance(parsed_history, dict):
                raise ReviewInputError("review history must be a JSON object")
            existing_payload = parsed_history
        updated = append_copilot_reviews(
            benchmark_payload,
            review_payload,
            existing_payload,
            minimum_reviews=minimum_reviews,
        )
        write_copilot_review_history(history, updated)
    except (ReviewInputError, OSError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error

    summary = {
        key: updated[key]
        for key in (
            "schema_version",
            "scope",
            "corpus_id",
            "corpus_sha256",
            "minimum_reviews",
            "entry_count",
            "accepted_count",
            "revise_count",
            "rejected_count",
            "status",
            "history_sha256",
        )
    }
    typer.echo(f"Wrote private copilot review history to {history}")
    typer.echo(orjson.dumps(summary, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode())


@app.command("research-copilot-review-verify")
def research_copilot_review_verify(
    history: Annotated[Path, typer.Argument(help="Content-addressed copilot review history JSON.")],
) -> None:
    """Verify a private copilot review history without printing its entries."""
    from edgar_moe.copilot.review import ReviewInputError, verify_copilot_review_history

    try:
        payload = json.loads(history.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ReviewInputError("review history must be a JSON object")
        verify_copilot_review_history(payload)
    except (ReviewInputError, OSError, json.JSONDecodeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Verified copilot review history {payload['history_sha256']} "
        f"({payload['entry_count']} entries; status={payload['status']})"
    )


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


def _copilot_database_url(settings: RuntimeSettings, override: str | None) -> str:
    """Prefer the SELECT-only URL and keep local SQLite development convenient."""
    if override:
        return override
    if settings.edgar_moe_registry_read_database_url:
        return settings.edgar_moe_registry_read_database_url
    if settings.edgar_moe_registry_database_url.lower().startswith("sqlite"):
        return settings.edgar_moe_registry_database_url
    return ""


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
