from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from edgar_moe.data.alpaca import AlpacaDataClient
from edgar_moe.data.demo import build_demo_snapshot
from edgar_moe.data.fred import FredClient
from edgar_moe.data.refresh import (
    collect_authenticated_data,
    load_universe_csv,
    write_authenticated_bundle,
)
from edgar_moe.data.sec import SecClient
from edgar_moe.settings import ResearchConfig, runtime_settings

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
    snapshot = build_demo_snapshot(output, config=config, seed=config.project.random_seed, max_epochs=epochs)
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
            active, inactive = await asyncio.gather(client.assets("active"), client.assets("inactive"))
        return [*active, *inactive]

    assets = asyncio.run(run())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(assets, indent=2), encoding="utf-8")
    typer.echo(f"Wrote {len(assets):,} assets to {output}")


@app.command("refresh-data")
def refresh_data(
    universe: Annotated[Path, typer.Option(help="CSV with cik and symbol columns.")] = Path(
        "config/universe.example.csv"
    ),
    output_dir: Annotated[Path, typer.Option()] = Path("data/raw/authenticated"),
    start: Annotated[str, typer.Option(help="First market/macro date (YYYY-MM-DD).")] = "2016-01-01",
    end: Annotated[str | None, typer.Option(help="Last observation date; defaults to as-of.")] = None,
    as_of: Annotated[str | None, typer.Option(help="Point-in-time vintage date.")] = None,
    macro_series: Annotated[str, typer.Option()] = "VIXCLS,DGS10,DFF,BAA10Y",
    feed: Annotated[str, typer.Option(help="Alpaca feed; IEX works on free plans.")] = "iex",
) -> None:
    """Collect an authenticated, hashed SEC/market/macro research input bundle."""
    settings = runtime_settings()
    members = load_universe_csv(universe)
    as_of_date = date.fromisoformat(as_of) if as_of else date.today()
    end_date = date.fromisoformat(end) if end else as_of_date
    start_date = date.fromisoformat(start)
    series = [item.strip() for item in macro_series.split(",") if item.strip()]

    async def run() -> Path:
        async with (
            SecClient(settings.sec_user_agent, output_dir / "cache" / "sec") as sec,
            AlpacaDataClient(settings.alpaca_api_key, settings.alpaca_api_secret) as market,
            FredClient(settings.fred_api_key) as macro,
        ):
            bundle = await collect_authenticated_data(
                sec,
                market,
                macro,
                members,
                start=start_date,
                end=end_date,
                as_of=as_of_date,
                macro_series=series,
                feed=feed,
            )
        return write_authenticated_bundle(bundle, output_dir)

    destination = asyncio.run(run())
    typer.echo(
        f"Wrote authenticated research inputs for {len(members)} symbols to {destination}. "
        "No credentials were stored."
    )


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
) -> None:
    """Serve the snapshot-backed FastAPI application."""
    import uvicorn

    uvicorn.run("edgar_moe.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
