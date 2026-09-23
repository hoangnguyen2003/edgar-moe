from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as APIPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response as StarletteResponse
from starlette.staticfiles import StaticFiles

from edgar_moe import __version__
from edgar_moe.api.models import (
    EquityCurveResponse,
    EventPage,
    EventRecord,
    ExperimentRecord,
    ForwardForecastPage,
    ForwardPerformanceResponse,
    ForwardQualityRecord,
    ForwardRunRecord,
    ForwardStatusResponse,
    FreshnessResponse,
    FrozenSnapshotIdentity,
    GovernanceControl,
    GovernanceResponse,
    HealthResponse,
    MethodologyResponse,
    PublicDataBoundary,
    SummaryResponse,
)
from edgar_moe.api.repository import SnapshotNotFoundError, SnapshotRepository
from edgar_moe.forward.database import RegistryDatabase
from edgar_moe.forward.registry import ForwardRegistry
from edgar_moe.settings import RuntimeSettings, runtime_settings

settings = runtime_settings()
repository = SnapshotRepository(
    settings.edgar_moe_demo_snapshot,
    lock_path=settings.edgar_moe_public_snapshot_lock,
)


def _api_registry_database_url(settings: RuntimeSettings) -> str:
    """Select the API URL without silently exposing a production writer secret.

    A dedicated reader URL is mandatory for a hosted Postgres deployment. The
    writer URL is retained only for local SQLite development, where it is a
    file path rather than a network credential.
    """
    reader_url = settings.edgar_moe_registry_read_database_url.strip()
    if reader_url:
        return reader_url

    writer_url = settings.edgar_moe_registry_database_url.strip()
    if writer_url.lower().startswith("sqlite"):
        return writer_url
    return ""


api_registry_database_url = _api_registry_database_url(settings)


def _build_api_registry_database(settings: RuntimeSettings) -> RegistryDatabase | None:
    database_url = _api_registry_database_url(settings)
    if not database_url:
        return None
    return RegistryDatabase(
        database_url,
        pool_size=settings.edgar_moe_registry_api_pool_size,
        max_overflow=settings.edgar_moe_registry_api_max_overflow,
        pool_timeout=settings.edgar_moe_registry_api_pool_timeout_seconds,
        read_only=True,
        statement_timeout_ms=settings.edgar_moe_registry_api_statement_timeout_ms,
    )


forward_database = _build_api_registry_database(settings)
forward_registry = (
    ForwardRegistry(forward_database, actor="edgar-moe-api") if forward_database else None
)

app = FastAPI(
    title="EDGAR-MoE Research API",
    version=__version__,
    description=(
        "Read-only access to the frozen v1 study and to the registry that records "
        "new forecasts before their tradable entry time.\n\n"
        "- **Study routes** serve the reviewed snapshot bundled with this deployment. "
        "It cannot change while the deployment lives, so answers are cacheable.\n"
        "- **Forward routes** read the append-only registry, which changes when a run lands.\n"
        "- Every route is a `GET`, needs no credentials, and runs in a read-only "
        "database session.\n\n"
        "Figures are research output. The study's own cost-aware portfolio was not "
        "profitable, and nothing here is investment advice."
    ),
    # FastAPI's built-in docs pages bootstrap with inline script, which the
    # Content-Security-Policy below blocks. Swagger UI is served by the
    # CSP-compatible routes near the end of this module instead.
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def security_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[StarletteResponse]],
) -> StarletteResponse:
    response = await call_next(request)
    request_id = request.headers.get("x-request-id", "")
    valid_request_id = (
        request_id
        and len(request_id) <= 128
        and all(character.isalnum() or character in "-_." for character in request_id)
    )
    if not valid_request_id:
        request_id = uuid4().hex
    # A shared cache may replay this response to other callers, so it must not
    # carry an identifier belonging to one of them: a reader who quotes it
    # would send an operator to an unrelated invocation. Every route whose
    # answer is about this request - health, freshness, forward status, the
    # documentation - is uncacheable, so the identifier survives where it
    # means something.
    if not shared_cacheable(response.headers.get("Cache-Control", "")):
        response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-site"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
        "form-action 'self'; script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: https:; "
        "connect-src 'self'"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def get_repository() -> SnapshotRepository:
    return repository


RepositoryDependency = Annotated[SnapshotRepository, Depends(get_repository)]


def get_forward_registry() -> ForwardRegistry | None:
    return forward_registry


ForwardRegistryDependency = Annotated[ForwardRegistry | None, Depends(get_forward_registry)]


# Vercel's edge caches a function response only when it carries ``s-maxage``.
# Without one, every visitor invokes the function, and the first visitor after
# an idle period waits for the function and the database to resume. A new
# deployment starts with an empty edge cache, so a snapshot response can never
# outlive the snapshot it came from, however long its edge lifetime.
_EDGE_SNAPSHOT_SECONDS = 86_400
_EDGE_SNAPSHOT_STALE_SECONDS = 604_800
_EDGE_LIVE_SECONDS = 60
_EDGE_LIVE_STALE_SECONDS = 300


def shared_cacheable(cache_control: str) -> bool:
    """Whether a shared cache may store this response and serve it to others."""

    names = {
        item.strip().lower().split("=", 1)[0] for item in cache_control.split(",") if item.strip()
    }
    if names & {"no-store", "no-cache", "private"}:
        return False
    return bool(names & {"public", "s-maxage", "max-age"})


def _cache(
    response: Response,
    seconds: int = 300,
    *,
    edge_seconds: int = _EDGE_SNAPSHOT_SECONDS,
    stale_seconds: int = _EDGE_SNAPSHOT_STALE_SECONDS,
) -> None:
    """Cache a reviewed-snapshot read: briefly in the browser, long at the edge."""

    response.headers["Cache-Control"] = (
        f"public, max-age={seconds}, s-maxage={edge_seconds}, "
        f"stale-while-revalidate={stale_seconds}"
    )


def _cache_live(response: Response, seconds: int = 60) -> None:
    """Cache a registry read, which changes when a forward run lands."""

    _cache(
        response,
        seconds,
        edge_seconds=_EDGE_LIVE_SECONDS,
        stale_seconds=_EDGE_LIVE_STALE_SECONDS,
    )


def _forward_status_response(registry: ForwardRegistry | None) -> ForwardStatusResponse:
    if registry is None:
        return ForwardStatusResponse(
            configured=False,
            available=False,
            message="Forward registry is not configured in this deployment.",
        )
    try:
        payload = registry.status()
    except SQLAlchemyError:
        return ForwardStatusResponse(
            configured=True,
            available=False,
            message="Forward registry is configured but currently unavailable.",
        )
    return ForwardStatusResponse(
        **payload,
        available=True,
        message="Append-only forward registry is available.",
    )


@app.get("/api/v1/health", response_model=HealthResponse, tags=["operations"])
def health(response: Response, repo: RepositoryDependency) -> HealthResponse:
    """Report whether this deployment can read the snapshot it was built with.

    Never cached: it describes this request, not an earlier one.
    """
    # A cached health answer would report the state of some earlier moment.
    response.headers["Cache-Control"] = "no-store"
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
    """Headline figures from the frozen v1 study, with the snapshot's identity.

    Includes the locked-test rank IC and the cost-aware portfolio result, which
    was negative. The snapshot cannot change while this deployment lives.
    """
    _cache(response)
    return SummaryResponse.model_validate(repo.summary())


@app.get("/api/v1/experiments", response_model=list[ExperimentRecord], tags=["research"])
def experiments(response: Response, repo: RepositoryDependency) -> list[ExperimentRecord]:
    """Every candidate configuration from the selection protocol, including the losers.

    Keeping the rejected candidates visible is what makes the champion's margin
    readable rather than asserted.
    """
    _cache(response)
    return [ExperimentRecord.model_validate(item) for item in repo.experiments()]


@app.get("/api/v1/equity-curves", response_model=EquityCurveResponse, tags=["portfolio"])
def equity_curve(
    response: Response,
    repo: RepositoryDependency,
    cost_bps: int = Query(default=10),
) -> EquityCurveResponse:
    """The cost-aware portfolio's equity curve at one round-trip cost.

    `cost_bps` accepts 10, 25, or 50 - the three costs the study charges.
    Any other value is a 422 rather than an interpolated answer.
    """
    if cost_bps not in {10, 25, 50}:
        raise HTTPException(status_code=422, detail="cost_bps must be one of 10, 25, or 50")
    _cache(response)
    return EquityCurveResponse.model_validate(repo.equity_curve(cost_bps))


@app.get("/api/v1/events", response_model=EventPage, tags=["filings"])
def events(
    response: Response,
    repo: RepositoryDependency,
    ticker: str | None = Query(default=None, min_length=1, max_length=32),
    form: str | None = Query(default=None, pattern=r"^10-[KQ]$"),
    direction: str | None = Query(default=None, pattern=r"^(long|short|neutral)$"),
    from_date: date | None = None,
    to_date: date | None = None,
    cursor: str | None = Query(default=None, max_length=20, pattern=r"^\d+$"),
    limit: int = Query(default=25, ge=1, le=100),
    q: str | None = Query(
        default=None,
        min_length=1,
        max_length=64,
        description="Case-insensitive substring match on ticker or company name.",
    ),
) -> EventPage:
    """One page of scored filings, newest first.

    Filters are optional and combine. Paging uses an opaque cursor, so a page
    stays stable while the caller walks it.
    """
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
            query=q,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid event query") from error
    return EventPage.model_validate(payload)


@app.get("/api/v1/events/{accession_number}", response_model=EventRecord, tags=["filings"])
def event(
    accession_number: Annotated[
        str,
        APIPath(
            min_length=1,
            max_length=20,
            pattern=r"^\d{10}-\d{2}-\d{6}$",
        ),
    ],
    response: Response,
    repo: RepositoryDependency,
) -> EventRecord:
    """One scored filing, by its SEC accession number.

    Carries the per-expert scores, the gate weights that combined them, and the
    realized outcome once the horizon has passed.
    """
    payload = repo.event(accession_number)
    if payload is None:
        raise HTTPException(status_code=404, detail="Filing event not found")
    _cache(response)
    return EventRecord.model_validate(payload)


@app.get("/api/v1/latest-signals", response_model=list[EventRecord], tags=["signals"])
def latest_signals(response: Response, repo: RepositoryDependency) -> list[EventRecord]:
    """The last cohort of filings the frozen study scored.

    These are study outputs with known outcomes, not live forecasts. New
    forecasts recorded before their entry time are under `/api/v1/forward`.
    """
    _cache(response, seconds=900)
    return [EventRecord.model_validate(item) for item in repo.latest_signals()]


@app.get("/api/v1/methodology", response_model=MethodologyResponse, tags=["research"])
def methodology(response: Response, repo: RepositoryDependency) -> MethodologyResponse:
    """How the study was run: sources, the point-in-time rule, splits, and costs.

    The same protocol the model card describes, served as data so a reader can
    check the site against it.
    """
    _cache(response, seconds=3600)
    return MethodologyResponse.model_validate(repo.methodology())


@app.get("/api/v1/freshness", response_model=FreshnessResponse, tags=["operations"])
def freshness(response: Response, repo: RepositoryDependency) -> FreshnessResponse:
    """When the bundled snapshot was built, and how old it is now.

    Revalidated on every request, because its answer is about the present.
    """
    response.headers["Cache-Control"] = "no-cache"
    return FreshnessResponse.model_validate(repo.freshness())


@app.get(
    "/api/v1/governance",
    response_model=GovernanceResponse,
    tags=["governance"],
)
def governance(
    response: Response,
    repo: RepositoryDependency,
    registry: ForwardRegistryDependency,
) -> GovernanceResponse:
    """Expose the immutable-v1 and prospective-evaluation control contract."""
    response.headers["Cache-Control"] = "no-store"
    try:
        frozen_identity = repo.frozen_identity()
    except (SnapshotNotFoundError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Frozen snapshot unavailable") from error
    return GovernanceResponse(
        schema_version=1,
        frozen_v1=FrozenSnapshotIdentity.model_validate(frozen_identity),
        public_data=PublicDataBoundary(
            raw_sources_public=False,
            derived_output_public=True,
            redistribution_status="operator_review_required",
        ),
        controls=[
            GovernanceControl(
                key="frozen_v1_identity",
                status="enforced",
                owner="repository",
                summary="Model, selection, locked result, and snapshot identity remain content-addressed.",
            ),
            GovernanceControl(
                key="pre_entry_forecasts",
                status="enforced",
                owner="repository",
                summary="Prospective forecasts require a recorded timestamp before tradable entry.",
            ),
            GovernanceControl(
                key="append_only_outcomes",
                status="enforced",
                owner="repository",
                summary="Outcomes are appended after maturity; the frozen v1 result is not overwritten.",
            ),
            GovernanceControl(
                key="provider_operations",
                status="pending_operator_evidence",
                owner="operator",
                summary="Provider grants, backups, restore timing, and object-store failure evidence require an external exercise.",
            ),
        ],
        forward_status=_forward_status_response(registry),
    )


@app.get(
    "/api/v1/forward/status",
    response_model=ForwardStatusResponse,
    tags=["forward testing"],
)
def forward_status(
    response: Response,
    registry: ForwardRegistryDependency,
) -> ForwardStatusResponse:
    """Whether the forward registry is reachable and the runner is on time.

    Reports counts, the latest run, and whether the runner is inside its
    freshness window. Never cached, so an outage is visible immediately.
    """
    response.headers["Cache-Control"] = "no-store"
    return _forward_status_response(registry)


@app.get(
    "/api/v1/forward/runs",
    response_model=list[ForwardRunRecord],
    tags=["forward testing"],
)
def forward_runs(
    response: Response,
    registry: ForwardRegistryDependency,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[ForwardRunRecord]:
    """Recent forward runs, newest first, with status and quality-check counts.

    Returns an empty list, not an error, when no registry is configured for
    this deployment.
    """
    _cache_live(response)
    if registry is None:
        return []
    try:
        return [ForwardRunRecord.model_validate(item) for item in registry.list_runs(limit=limit)]
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Forward registry unavailable") from error


@app.get(
    "/api/v1/forward/forecasts",
    response_model=ForwardForecastPage,
    tags=["forward testing"],
)
def forward_forecasts(
    response: Response,
    registry: ForwardRegistryDependency,
    ticker: str | None = Query(default=None, min_length=1, max_length=32),
    model_id: str | None = Query(default=None, min_length=1, max_length=160),
    limit: int = Query(default=50, ge=1, le=100),
    # Bounded so an oversized value is a 422 rather than a database overflow.
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> ForwardForecastPage:
    """Recorded forward forecasts, newest first, each bound to its own run.

    A forecast's rank is its position within that run's batch of candidate
    filings, and `cohort_size` gives that batch's size, so a run that scored a
    single filing reads as one filing rather than as a top rank.
    """
    _cache_live(response)
    if registry is None:
        return ForwardForecastPage(items=[], total=0, offset=offset, limit=limit)
    try:
        payload = registry.list_forecasts(
            ticker=ticker,
            model_id=model_id,
            limit=limit,
            offset=offset,
        )
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Forward registry unavailable") from error
    return ForwardForecastPage.model_validate(payload)


@app.get(
    "/api/v1/forward/performance",
    response_model=ForwardPerformanceResponse,
    tags=["forward testing"],
)
def forward_performance(
    response: Response,
    registry: ForwardRegistryDependency,
    model_id: str | None = Query(default=None, min_length=1, max_length=160),
) -> ForwardPerformanceResponse:
    """Scores over the forecasts that have matured.

    Rank IC, RMSE, MAE, and directional accuracy, with the share of recorded
    forecasts they cover. Early in a forward test the sample is small and these
    figures move a great deal, so the counts are part of the answer.

    The rank IC is one Spearman correlation pooled over every settled
    (score, return) pair, not the average of per-run cross-sectional
    correlations: a run scores one to four filings, too few for a
    cross-section. It therefore mixes ordering with variation between periods
    and is not comparable to the locked study's rank IC.

    `rank_ic_low` and `rank_ic_high` bound the rank IC at 95% confidence,
    treating settled forecasts as independent. Forecasts from one run share a
    trading day, so a true interval is wider.
    """
    _cache_live(response)
    if registry is None:
        return ForwardPerformanceResponse(
            model_id=model_id,
            forecast_count=0,
            matured_count=0,
            pending_count=0,
            coverage=0.0,
            rank_ic=None,
            rank_ic_low=None,
            rank_ic_high=None,
            rmse=None,
            mae=None,
            directional_accuracy=None,
        )
    try:
        return ForwardPerformanceResponse.model_validate(registry.performance(model_id=model_id))
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Forward registry unavailable") from error


@app.get(
    "/api/v1/forward/data-quality",
    response_model=list[ForwardQualityRecord],
    tags=["forward testing"],
)
def forward_data_quality(
    response: Response,
    registry: ForwardRegistryDependency,
    limit: int = Query(default=100, ge=1, le=250),
) -> list[ForwardQualityRecord]:
    """The quality checks each run appended to the registry.

    Includes the point-in-time availability audit and the margin between the
    run and the market open, with the observed value and threshold for each.
    """
    _cache_live(response)
    if registry is None:
        return []
    try:
        return [
            ForwardQualityRecord.model_validate(item)
            for item in registry.list_quality_checks(limit=limit)
        ]
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Forward registry unavailable") from error


_SWAGGER_UI_CDN = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.33.0"
# Subresource Integrity pins the exact CDN bytes that the CSP allows.
_SWAGGER_UI_BUNDLE_INTEGRITY = (
    "sha384-YDALVcy8kj8yltLBVi1vBiBAUqdxvus673gM8XKwiy6aDUJFXivF/KCufekjYbVf"
)
_SWAGGER_UI_CSS_INTEGRITY = (
    "sha384-Ov4/wv3j2bmct8cDc5X4ngJZohVPzEmc6uDPH8WeljUxO5vtoykvMEfbu9Vh6RaW"
)
_SWAGGER_UI_HTML = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{app.title} - Swagger UI</title>
<link rel="stylesheet" href="{_SWAGGER_UI_CDN}/swagger-ui.css" integrity="{_SWAGGER_UI_CSS_INTEGRITY}" crossorigin="anonymous">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{_SWAGGER_UI_CDN}/swagger-ui-bundle.js" integrity="{_SWAGGER_UI_BUNDLE_INTEGRITY}" crossorigin="anonymous"></script>
<script src="/api/docs/swagger-init.js"></script>
</body>
</html>
"""
_SWAGGER_UI_INITIALIZER = """window.ui = SwaggerUIBundle({
  url: "/api/openapi.json",
  dom_id: "#swagger-ui",
  layout: "BaseLayout",
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
});
"""


@app.get("/api/docs", include_in_schema=False)
def api_docs() -> HTMLResponse:
    """Serve Swagger UI with only external scripts so the strict CSP still applies."""
    # private: the page is cheap to render, and the deployment smoke check
    # traces it by request identifier, which a shared cache would replay.
    return HTMLResponse(_SWAGGER_UI_HTML, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/docs/swagger-init.js", include_in_schema=False)
def api_docs_initializer() -> Response:
    return Response(
        _SWAGGER_UI_INITIALIZER,
        media_type="text/javascript",
        headers={"Cache-Control": "private, max-age=3600"},
    )


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
