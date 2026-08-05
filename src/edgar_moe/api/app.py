from __future__ import annotations

from collections.abc import MutableMapping
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response as StarletteResponse
from starlette.staticfiles import StaticFiles

from edgar_moe import __version__
from edgar_moe.api.models import (
    EquityCurveResponse,
    EventPage,
    EventRecord,
    ExperimentRecord,
    FreshnessResponse,
    HealthResponse,
    MethodologyResponse,
    SummaryResponse,
)
from edgar_moe.api.repository import SnapshotNotFoundError, SnapshotRepository
from edgar_moe.settings import runtime_settings

settings = runtime_settings()
repository = SnapshotRepository(settings.edgar_moe_demo_snapshot)

app = FastAPI(
    title="EDGAR-MoE Research API",
    version=__version__,
    description="Snapshot API for point-in-time SEC filing alpha research.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_repository() -> SnapshotRepository:
    return repository


RepositoryDependency = Annotated[SnapshotRepository, Depends(get_repository)]


def _cache(response: Response, seconds: int = 300) -> None:
    response.headers["Cache-Control"] = f"public, max-age={seconds}, stale-while-revalidate=600"


@app.get("/api/v1/health", response_model=HealthResponse, tags=["operations"])
def health(repo: RepositoryDependency) -> HealthResponse:
    try:
        snapshot = repo.load()
    except (SnapshotNotFoundError, ValueError):
        return HealthResponse(status="degraded", snapshot_loaded=False, data_mode=None, as_of=None)
    return HealthResponse(
        status="ok",
        snapshot_loaded=True,
        data_mode=snapshot["metadata"]["data_mode"],
        as_of=snapshot["metadata"]["as_of"],
    )


@app.get("/api/v1/summary", response_model=SummaryResponse, tags=["research"])
def summary(response: Response, repo: RepositoryDependency) -> SummaryResponse:
    _cache(response)
    return SummaryResponse.model_validate(repo.summary())


@app.get("/api/v1/experiments", response_model=list[ExperimentRecord], tags=["research"])
def experiments(response: Response, repo: RepositoryDependency) -> list[ExperimentRecord]:
    _cache(response)
    return [ExperimentRecord.model_validate(item) for item in repo.experiments()]


@app.get("/api/v1/equity-curves", response_model=EquityCurveResponse, tags=["portfolio"])
def equity_curve(
    response: Response,
    repo: RepositoryDependency,
    cost_bps: int = Query(default=10),
) -> EquityCurveResponse:
    if cost_bps not in {10, 25, 50}:
        raise HTTPException(status_code=422, detail="cost_bps must be one of 10, 25, or 50")
    _cache(response)
    return EquityCurveResponse.model_validate(repo.equity_curve(cost_bps))


@app.get("/api/v1/events", response_model=EventPage, tags=["filings"])
def events(
    response: Response,
    repo: RepositoryDependency,
    ticker: str | None = None,
    form: str | None = Query(default=None, pattern=r"^10-[KQ]$"),
    direction: str | None = Query(default=None, pattern=r"^(long|short|neutral)$"),
    from_date: date | None = None,
    to_date: date | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
) -> EventPage:
    _cache(response)
    try:
        payload = repo.event_page(
            ticker=ticker,
            form=form,
            direction=direction,
            from_date=from_date,
            to_date=to_date,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return EventPage.model_validate(payload)


@app.get("/api/v1/events/{accession_number}", response_model=EventRecord, tags=["filings"])
def event(
    accession_number: str,
    response: Response,
    repo: RepositoryDependency,
) -> EventRecord:
    payload = repo.event(accession_number)
    if payload is None:
        raise HTTPException(status_code=404, detail="Filing event not found")
    _cache(response)
    return EventRecord.model_validate(payload)


@app.get("/api/v1/latest-signals", response_model=list[EventRecord], tags=["signals"])
def latest_signals(response: Response, repo: RepositoryDependency) -> list[EventRecord]:
    _cache(response, seconds=900)
    return [EventRecord.model_validate(item) for item in repo.latest_signals()]


@app.get("/api/v1/methodology", response_model=MethodologyResponse, tags=["research"])
def methodology(response: Response, repo: RepositoryDependency) -> MethodologyResponse:
    _cache(response, seconds=3600)
    return MethodologyResponse.model_validate(repo.methodology())


@app.get("/api/v1/freshness", response_model=FreshnessResponse, tags=["operations"])
def freshness(response: Response, repo: RepositoryDependency) -> FreshnessResponse:
    response.headers["Cache-Control"] = "no-cache"
    return FreshnessResponse.model_validate(repo.freshness())


class SpaStaticFiles(StaticFiles):
    """Serve the React entrypoint for non-file client-side routes."""

    async def get_response(
        self, path: str, scope: MutableMapping[str, object]
    ) -> StarletteResponse:
        is_api_path = path == "api" or path.startswith("api/")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404 or is_api_path or "." in Path(path).name:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and not is_api_path and "." not in Path(path).name:
            return await super().get_response("index.html", scope)
        return response


static_directory = Path("static")
if static_directory.is_dir():
    app.mount("/", SpaStaticFiles(directory=static_directory, html=True), name="web")
