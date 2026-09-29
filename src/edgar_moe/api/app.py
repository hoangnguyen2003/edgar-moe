from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import date
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Annotated, NoReturn
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi import Path as APIPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response as StarletteResponse
from starlette.staticfiles import StaticFiles

from edgar_moe import __version__
from edgar_moe.api.models import (
    EquityCurveResponse,
    EventPage,
    EventRecord,
    ExperimentRecord,
    ForwardPublicationPolicyResponse,
    FreshnessResponse,
    GovernanceControl,
    GovernanceResponse,
    HealthResponse,
    MethodologyResponse,
    PublicDataBoundary,
    PublishedSnapshotIdentity,
    ResearchEvidenceResponse,
    SummaryResponse,
    WithheldResponse,
)
from edgar_moe.api.repository import SnapshotNotFoundError, SnapshotRepository
from edgar_moe.api.research_evidence import CatalogIntegrityError, build_research_evidence
from edgar_moe.settings import runtime_settings

settings = runtime_settings()
_GIT_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_first_app_request_seen = False
_first_app_request_lock = Lock()


def _worker_request_state() -> str:
    """Mark only the first request this app process handles as cold.

    This is an origin-process observation, not proof of a platform cold start.
    A cached edge response can replay the header and must not be classified as
    a fresh origin request by a client observer.
    """
    global _first_app_request_seen
    with _first_app_request_lock:
        if not _first_app_request_seen:
            _first_app_request_seen = True
            return "first"
    return "subsequent"


def _served_commit_sha() -> str | None:
    """Expose only a validated, non-secret deployment identity."""
    value = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    return value if _GIT_COMMIT_SHA.fullmatch(value) else None


repository = SnapshotRepository(
    settings.edgar_moe_demo_snapshot,
    lock_path=settings.edgar_moe_public_snapshot_lock,
)


app = FastAPI(
    title="EDGAR-MoE Research API",
    version=__version__,
    description=(
        "Read-only access to a hash-locked synthetic software demo.\n\n"
        "- **Demo routes** serve a hash-locked synthetic fixture only while historical "
        "source-use review is unresolved.\n"
        "- Historical and prospective research outputs are withheld from the public "
        "application pending source-rights review.\n"
        "- Every route is a `GET` and needs no credentials. The public API does not "
        "connect to the forward registry.\n\n"
        "This synthetic demo is not profitable, does not represent a live trading strategy, "
        "and is not investment advice."
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
    started = perf_counter()
    worker_state = _worker_request_state()
    response = await call_next(request)
    app_header_ms = (perf_counter() - started) * 1000
    # Fixed numeric fields only. Registry duration includes query execution,
    # connection acquisition, and Python calculation; it is not database-only
    # time. Shared caches may replay this header, so observers use it only on
    # responses that the edge reports as MISS or BYPASS.
    registry_read_ms = getattr(request.state, "registry_read_ms", None)
    timing = f"app_header_ms={app_header_ms:.3f};worker={worker_state}"
    if isinstance(registry_read_ms, (int, float)) and not isinstance(registry_read_ms, bool):
        timing += f";registry_read_ms={registry_read_ms:.3f}"
    response.headers["X-EDGAR-Read-Timing"] = timing
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
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' data:; img-src 'self' data: https:; "
        "connect-src 'self'"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def get_repository() -> SnapshotRepository:
    return repository


RepositoryDependency = Annotated[SnapshotRepository, Depends(get_repository)]


# Vercel's edge caches a function response only when it carries ``s-maxage``.
# Without one, every visitor invokes the function, and the first visitor after
# an idle period waits for the function and the database to resume. A new
# deployment starts with an empty edge cache, so a snapshot response can never
# outlive the snapshot it came from, however long its edge lifetime.
_EDGE_SNAPSHOT_SECONDS = 86_400
_EDGE_SNAPSHOT_STALE_SECONDS = 604_800


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


_FORWARD_REVIEW_DETAIL = (
    "Prospective forecasts and outcomes are withheld from the public application "
    "pending source-rights review. The registry remains private."
)


def _withheld_forward_status() -> ForwardPublicationPolicyResponse:
    """Describe the public policy without querying or disclosing registry state."""
    return ForwardPublicationPolicyResponse(
        public_visibility="withheld_review",
        message=_FORWARD_REVIEW_DETAIL,
    )


def _reject_public_forward_evidence() -> NoReturn:
    """Keep database-backed derived records outside the synthetic public app."""
    raise HTTPException(
        status_code=410,
        detail=_FORWARD_REVIEW_DETAIL,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/v1/health", response_model=HealthResponse, tags=["operations"])
def health(response: Response, repo: RepositoryDependency) -> HealthResponse:
    """Report whether this deployment can read the snapshot it was built with.

    Never cached: it describes this request, not an earlier one.
    """
    # A cached health answer would report the state of some earlier moment.
    response.headers["Cache-Control"] = "no-store"
    commit_sha = _served_commit_sha()
    try:
        snapshot = repo.load()
    except (SnapshotNotFoundError, ValueError):
        return HealthResponse(
            status="degraded",
            snapshot_loaded=False,
            data_mode=None,
            as_of=None,
            commit_sha=commit_sha,
        )
    return HealthResponse(
        status="ok",
        snapshot_loaded=True,
        data_mode=snapshot["metadata"]["data_mode"],
        as_of=snapshot["metadata"]["as_of"],
        commit_sha=commit_sha,
    )


@app.get("/api/v1/summary", response_model=SummaryResponse, tags=["research"])
def summary(response: Response, repo: RepositoryDependency) -> SummaryResponse:
    """Headline figures from the hash-locked synthetic software demo."""
    _cache(response)
    return SummaryResponse.model_validate(repo.summary())


@app.get("/api/v1/research-evidence", response_model=ResearchEvidenceResponse, tags=["research"])
def research_evidence(response: Response, repo: RepositoryDependency) -> ResearchEvidenceResponse:
    """Withhold frozen-v1 aggregates while the public snapshot is synthetic."""
    response.headers["Cache-Control"] = "no-store"
    if repo.load().get("metadata", {}).get("data_mode") != "authenticated_locked_test":
        raise HTTPException(
            status_code=410,
            detail="Frozen v1 research evidence is withheld pending source-rights review.",
            headers={"Cache-Control": "no-store"},
        )
    try:
        result = build_research_evidence(repo)
    except CatalogIntegrityError as error:
        response.headers["Cache-Control"] = "no-store"
        raise HTTPException(
            status_code=503, detail="Public research evidence unavailable"
        ) from error
    _cache(response)
    return result


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
) -> GovernanceResponse:
    """Expose the synthetic public boundary and prospective-evaluation controls."""
    response.headers["Cache-Control"] = "no-store"
    try:
        published_identity = repo.published_identity()
    except (SnapshotNotFoundError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Frozen snapshot unavailable") from error
    return GovernanceResponse(
        schema_version=2,
        published_snapshot=PublishedSnapshotIdentity.model_validate(published_identity),
        public_data=PublicDataBoundary(
            raw_sources_public=False,
            current_output_mode="synthetic_fixture",
            historical_v1_served_by_application=False,
            prospective_outputs_served_by_application=False,
            redistribution_status="historical_v1_review_required",
        ),
        controls=[
            GovernanceControl(
                key="published_snapshot_identity",
                status="enforced",
                owner="repository",
                summary="The current public snapshot is a content-addressed synthetic fixture; frozen-v1 output is withheld.",
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
                key="prospective_publication",
                status="withheld_review",
                owner="repository",
                summary="Database-backed prospective forecasts and outcomes are withheld from the public app while source-rights review remains open.",
            ),
            GovernanceControl(
                key="provider_operations",
                status="pending_operator_evidence",
                owner="operator",
                summary="Provider grants, backups, restore timing, and object-store failure evidence require an external exercise.",
            ),
        ],
        forward_status=_withheld_forward_status(),
    )


@app.get(
    "/api/v1/forward/status",
    response_model=ForwardPublicationPolicyResponse,
    tags=["forward testing"],
)
def forward_status(
    response: Response,
) -> ForwardPublicationPolicyResponse:
    """Report the publication policy without probing the private registry."""
    response.headers["Cache-Control"] = "no-store"
    return _withheld_forward_status()


@app.get(
    "/api/v1/forward/runs",
    status_code=410,
    response_model=None,
    tags=["forward testing"],
    responses={410: {"model": WithheldResponse, "description": _FORWARD_REVIEW_DETAIL}},
)
def forward_runs() -> NoReturn:
    """Withheld from public access: database-backed run metadata stays private."""
    _reject_public_forward_evidence()


@app.get(
    "/api/v1/forward/forecasts",
    status_code=410,
    response_model=None,
    tags=["forward testing"],
    responses={410: {"model": WithheldResponse, "description": _FORWARD_REVIEW_DETAIL}},
)
def forward_forecasts() -> NoReturn:
    """Withheld from public access: database-backed forecast records stay private."""
    _reject_public_forward_evidence()


@app.get(
    "/api/v1/forward/performance",
    status_code=410,
    response_model=None,
    tags=["forward testing"],
    responses={410: {"model": WithheldResponse, "description": _FORWARD_REVIEW_DETAIL}},
)
def forward_performance() -> NoReturn:
    """Withheld from public access: database-backed performance metrics stay private."""
    _reject_public_forward_evidence()


@app.get(
    "/api/v1/forward/data-quality",
    status_code=410,
    response_model=None,
    tags=["forward testing"],
    responses={410: {"model": WithheldResponse, "description": _FORWARD_REVIEW_DETAIL}},
)
def forward_data_quality() -> NoReturn:
    """Withheld from public access: database-backed quality records stay private."""
    _reject_public_forward_evidence()


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
