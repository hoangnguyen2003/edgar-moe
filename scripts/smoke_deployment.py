"""Run a credential-free smoke check against a deployed EDGAR-MoE origin."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, TypeGuard
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

_USER_AGENT = "edgar-moe-deployment-smoke/1.0"
_MAX_BODY_BYTES = 128 * 1024
_REQUIRED_SECURITY_FIELDS = ("Contact:", "Policy:", "Preferred-Languages:", "Expires:")
_REQUIRED_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "deny",
    "referrer-policy": "strict-origin-when-cross-origin",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-site",
    "strict-transport-security": "max-age=31536000; includesubdomains",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "content-security-policy": (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
        "form-action 'self'; script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' data:; img-src 'self' data: https:; "
        "connect-src 'self'"
    ),
}
_MINIMUM_HSTS_MAX_AGE_SECONDS = 31_536_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# The frozen identity a deployment publishes twice (governance API and provenance
# manifest) and the repository pins in config/public_snapshot.lock.json.
_IDENTITY_FIELDS = ("path", "data_mode", "as_of", "sha256", "selection_hash", "locked_test_hash")
_FORWARD_INTERVAL_STATUSES = frozenset(
    {
        "ready",
        "insufficient_pairs",
        "insufficient_months",
        "undefined_rank_ic",
        "degenerate_resamples",
        "capacity_review_required",
    }
)
_FORWARD_INTERVAL_METHOD = "calendar_month_moving_block"


class _ScriptCollector(HTMLParser):
    """Collect each script element's attributes and inline text.

    An HTML parser, unlike a regular expression, tokenizes tags the way a
    browser does: any letter case, end tags such as ``</script >``, and
    attributes in any order or quoting style.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[tuple[dict[str, str | None], str]] = []
        self._attributes: dict[str, str | None] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self._attributes = dict(attrs)
            self._text = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.scripts.append((dict(attrs), ""))

    def handle_data(self, data: str) -> None:
        if self._attributes is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._attributes is not None:
            self.scripts.append((self._attributes, "".join(self._text)))
            self._attributes = None

    def close(self) -> None:
        super().close()
        # A browser runs an unterminated script's text too, so keep it as inline content.
        if self._attributes is not None:
            self.scripts.append((self._attributes, "".join(self._text)))
            self._attributes = None


def _script_elements(body: str) -> list[tuple[dict[str, str | None], str]]:
    """Return every script element in ``body`` as (attributes, inline text)."""
    collector = _ScriptCollector()
    collector.feed(body)
    collector.close()
    return collector.scripts


class _CrossOriginRedirectError(ValueError):
    """Raised when a smoke request attempts to leave its supplied origin."""


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str]) -> None:
        self._origin = origin

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        target = urlsplit(newurl)
        target_origin = (target.scheme.lower(), target.netloc.lower())
        if target_origin != self._origin:
            raise _CrossOriginRedirectError("redirected_to_different_origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def normalize_base_url(value: str, *, allow_http: bool = False) -> str:
    """Return a credential-free origin or reject an unsafe smoke target."""
    parsed = urlsplit(value.strip())
    allowed_schemes = {"https"} | ({"http"} if allow_http else set())
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise ValueError("base URL must use https and include a hostname")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, query parameters, or fragments")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def run_smoke(
    base_url: str,
    *,
    timeout: float = 10.0,
    allow_http: bool = False,
    allow_degraded: bool = False,
    expected_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Check the public deployment boundary without sending credentials or mutations.

    With ``expected_identity`` (see :func:`load_expected_identity`), the served
    frozen identity must also equal the reviewed lock field by field, so a
    well-formed but unreviewed snapshot cannot pass.
    """
    base = normalize_base_url(base_url, allow_http=allow_http)
    base_parts = urlsplit(base)
    origin = (base_parts.scheme.lower(), base_parts.netloc.lower())
    checks = [
        _check_endpoint(base, origin, "/", "homepage", "text/html", timeout),
        _check_endpoint(base, origin, "/robots.txt", "robots", "text/plain", timeout),
        _check_endpoint(
            base,
            origin,
            "/.well-known/security.txt",
            "security_txt",
            "text/plain",
            timeout,
        ),
        _check_endpoint(
            base,
            origin,
            "/data-provenance.json",
            "provenance",
            "application/json",
            timeout,
        ),
        _check_endpoint(base, origin, "/api/docs", "api_docs", "text/html", timeout),
        _check_endpoint(
            base,
            origin,
            "/api/v1/governance",
            "governance",
            "application/json",
            timeout,
        ),
        _check_endpoint(
            base,
            origin,
            "/api/v1/forward/performance",
            "forward_performance",
            "application/json",
            timeout,
            accepted_statuses=frozenset({200, 503}),
        ),
        _check_endpoint(base, origin, "/api/v1/health", "health", "application/json", timeout),
    ]

    forward_registry_configured: bool | None = None
    forward_registry_available: bool | None = None
    for check in checks:
        if check["name"] == "robots":
            body = check.pop("_body", "")
            if check["status"] == "passed" and (
                "User-agent: *" not in body or "Disallow: /api/" not in body
            ):
                check.update(status="failed", error="robots_policy_missing")
        elif check["name"] == "security_txt":
            body = check.pop("_body", "")
            if check["status"] == "passed" and any(
                field not in body for field in _REQUIRED_SECURITY_FIELDS
            ):
                check.update(status="failed", error="security_txt_field_missing")
        elif check["name"] == "api_docs":
            body = check.pop("_body", "")
            if check["status"] == "passed" and not _scripts_are_external(body):
                check.update(status="failed", error="api_docs_inline_script")
        elif check["name"] == "health":
            payload = check.pop("_json", None)
            if check["status"] == "passed":
                if not isinstance(payload, dict):
                    check.update(status="failed", error="health_response_not_object")
                else:
                    health_status = payload.get("status")
                    snapshot_loaded = payload.get("snapshot_loaded")
                    check["health_status"] = health_status
                    check["snapshot_loaded"] = snapshot_loaded
                    if health_status not in {"ok", "degraded"}:
                        check.update(status="failed", error="health_status_invalid")
                    elif health_status == "degraded" and not allow_degraded:
                        check.update(status="failed", error="health_status_degraded")
        elif check["name"] == "governance":
            payload = check.pop("_json", None)
            if check["status"] == "passed":
                frozen = payload.get("frozen_v1") if isinstance(payload, dict) else None
                public_data = payload.get("public_data") if isinstance(payload, dict) else None
                controls = payload.get("controls") if isinstance(payload, dict) else None
                forward_status = (
                    payload.get("forward_status") if isinstance(payload, dict) else None
                )
                frozen_valid = isinstance(frozen, dict) and all(
                    (
                        frozen.get("path") == "data/demo/snapshot.json",
                        frozen.get("data_mode") == "authenticated_locked_test",
                        frozen.get("research_only") is True,
                        _SHA256.fullmatch(str(frozen.get("sha256", ""))) is not None,
                        _SHA256.fullmatch(str(frozen.get("selection_hash", ""))) is not None,
                        _SHA256.fullmatch(str(frozen.get("locked_test_hash", ""))) is not None,
                    )
                )
                boundary_valid = isinstance(public_data, dict) and (
                    public_data.get("raw_sources_public") is False
                    and public_data.get("derived_output_public") is True
                    and public_data.get("redistribution_status") == "operator_review_required"
                )
                controls_valid = (
                    isinstance(controls, list)
                    and bool(controls)
                    and all(
                        isinstance(control, dict)
                        and control.get("status") in {"enforced", "pending_operator_evidence"}
                        and control.get("owner") in {"repository", "operator"}
                        for control in controls
                    )
                )
                forward_valid = isinstance(forward_status, dict) and all(
                    isinstance(forward_status.get(field), bool)
                    for field in ("configured", "available")
                )
                if forward_valid and isinstance(forward_status, dict):
                    forward_registry_configured = forward_status["configured"]
                    forward_registry_available = forward_status["available"]
                if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                    check.update(status="failed", error="governance_schema_invalid")
                elif not frozen_valid:
                    check.update(status="failed", error="governance_identity_contract_invalid")
                elif not boundary_valid:
                    check.update(status="failed", error="governance_public_boundary_invalid")
                elif not controls_valid:
                    check.update(status="failed", error="governance_controls_invalid")
                elif not forward_valid:
                    check.update(status="failed", error="governance_forward_status_invalid")
                elif expected_identity is not None:
                    _verify_identity(check, frozen, expected_identity, "governance")
        elif check["name"] == "forward_performance":
            payload = check.pop("_json", None)
            if check["status"] == "passed":
                if check.get("http_status") == 503:
                    if (
                        isinstance(payload, dict)
                        and payload.get("detail") == "Forward registry unavailable"
                        and forward_registry_configured is True
                        and forward_registry_available is False
                    ):
                        # A read-path outage is an explicitly supported degraded
                        # mode: historical results stay available and governance
                        # reports that live registry data is unavailable.
                        check["availability"] = "unavailable"
                        check["degraded_mode"] = "registry_unavailable"
                    else:
                        check.update(
                            status="failed",
                            error="forward_registry_unavailability_unconfirmed",
                        )
                else:
                    error = _forward_performance_error(payload)
                    if error is not None:
                        check.update(status="failed", error=error)
                    else:
                        assert isinstance(payload, dict)
                        method = payload["rank_ic_interval_method"]
                        status = payload["rank_ic_interval_status"]
                        check.update(
                            availability="unconfigured" if method is None else "available",
                            forecast_count=payload["forecast_count"],
                            matured_count=payload["matured_count"],
                            pending_count=payload["pending_count"],
                            rank_ic_interval_method=method,
                            rank_ic_interval_status=status,
                            rank_ic_calendar_months=payload["rank_ic_calendar_months"],
                        )
        elif check["name"] == "provenance":
            payload = check.pop("_json", None)
            if check["status"] == "passed":
                snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
                review = payload.get("review") if isinstance(payload, dict) else None
                if not isinstance(snapshot, dict) or not isinstance(review, dict):
                    check.update(status="failed", error="provenance_contract_invalid")
                elif (
                    snapshot.get("raw_sources_public") is not False
                    or snapshot.get("derived_output_public") is not True
                    or review.get("redistribution_status") != "operator_review_required"
                    or review.get("legal_approval") is not False
                ):
                    check.update(status="failed", error="provenance_review_contract_invalid")
                elif expected_identity is not None:
                    _verify_identity(check, snapshot, expected_identity, "provenance")

    failed = [check for check in checks if check["status"] != "passed"]
    report: dict[str, Any] = {
        "schema_version": 1,
        "checked_at": datetime.now(UTC).isoformat(),
        "base_url": base,
        "status": "failed" if failed else "passed",
        "checks": checks,
    }
    if expected_identity is not None:
        # Public digests only: the snapshot identity is published on the site itself.
        report["expected_snapshot_sha256"] = expected_identity["sha256"]
    return report


def load_expected_identity(path: Path) -> dict[str, str]:
    """Read the reviewed frozen identity that a deployment must serve."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"expected lock is unreadable ({type(error).__name__})") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("expected lock must be a schema_version 1 object")
    identity = {field: payload.get(field) for field in _IDENTITY_FIELDS}
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise ValueError("expected lock is missing identity fields")
    for field in ("sha256", "selection_hash", "locked_test_hash"):
        if _SHA256.fullmatch(str(identity[field])) is None:
            raise ValueError(f"expected lock {field} must be a lowercase SHA-256 digest")
    return {field: str(value) for field, value in identity.items()}


def _verify_identity(
    check: dict[str, Any], served: Any, expected: dict[str, str], source: str
) -> None:
    """Fail ``check`` unless ``served`` carries exactly the expected frozen identity."""
    mismatched = [
        field
        for field in _IDENTITY_FIELDS
        if not isinstance(served, dict) or served.get(field) != expected[field]
    ]
    if mismatched:
        # Field names only; the report never needs to repeat served values.
        check.update(
            status="failed",
            error=f"{source}_identity_mismatch",
            mismatched_fields=mismatched,
        )
    else:
        check["identity_verified"] = True


def _shared_cacheable(cache_control: str) -> bool:
    """Whether a shared cache may store this response and serve it to others."""

    names = {
        item.strip().lower().split("=", 1)[0] for item in cache_control.split(",") if item.strip()
    }
    if names & {"no-store", "no-cache", "private"}:
        return False
    return bool(names & {"public", "s-maxage", "max-age"})


def _is_finite_number(value: Any) -> TypeGuard[int | float]:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _is_nonnegative_int(value: Any) -> TypeGuard[int]:
    return type(value) is int and value >= 0


def _is_positive_int(value: Any) -> TypeGuard[int]:
    return type(value) is int and value > 0


def _forward_performance_error(payload: Any) -> str | None:
    """Validate public performance invariants without retaining numeric metrics."""
    if not isinstance(payload, dict):
        return "forward_performance_response_not_object"
    required_fields = {
        "model_id",
        "forecast_count",
        "matured_count",
        "pending_count",
        "coverage",
        "rank_ic",
        "rank_ic_low",
        "rank_ic_high",
        "rank_ic_interval_method",
        "rank_ic_interval_status",
        "rank_ic_calendar_months",
        "rank_ic_block_months",
        "rank_ic_bootstrap_samples",
        "rmse",
        "mae",
        "directional_accuracy",
    }
    if not required_fields.issubset(payload):
        return "forward_performance_response_fields_missing"
    if payload["model_id"] is not None and not isinstance(payload["model_id"], str):
        return "forward_performance_response_fields_invalid"

    forecast_count = payload.get("forecast_count")
    matured_count = payload.get("matured_count")
    pending_count = payload.get("pending_count")
    if not all(
        _is_nonnegative_int(value) for value in (forecast_count, matured_count, pending_count)
    ):
        return "forward_performance_counts_invalid"
    assert _is_nonnegative_int(forecast_count)
    assert _is_nonnegative_int(matured_count)
    assert _is_nonnegative_int(pending_count)
    if forecast_count != matured_count + pending_count:
        return "forward_performance_counts_inconsistent"

    coverage = payload.get("coverage")
    if not _is_finite_number(coverage) or not 0 <= coverage <= 1:
        return "forward_performance_metrics_invalid"
    expected_coverage = matured_count / forecast_count if forecast_count else 0.0
    if not math.isclose(float(coverage), expected_coverage, rel_tol=0.0, abs_tol=1e-9):
        return "forward_performance_coverage_inconsistent"

    bounded_metrics = ("rank_ic", "rank_ic_low", "rank_ic_high")
    for name in bounded_metrics:
        value = payload.get(name)
        if value is not None and (not _is_finite_number(value) or not -1 <= value <= 1):
            return "forward_performance_metrics_invalid"
    for name in ("rmse", "mae"):
        value = payload.get(name)
        if value is not None and (not _is_finite_number(value) or value < 0):
            return "forward_performance_metrics_invalid"
    directional_accuracy = payload.get("directional_accuracy")
    if directional_accuracy is not None and (
        not _is_finite_number(directional_accuracy) or not 0 <= directional_accuracy <= 1
    ):
        return "forward_performance_metrics_invalid"

    calendar_months = payload.get("rank_ic_calendar_months")
    if not _is_nonnegative_int(calendar_months):
        return "forward_performance_interval_contract_invalid"
    if matured_count == 0 and any(
        payload[name] is not None
        for name in (
            "rank_ic",
            "rank_ic_low",
            "rank_ic_high",
            "rmse",
            "mae",
            "directional_accuracy",
        )
    ):
        return "forward_performance_metrics_invalid"
    low, high = payload.get("rank_ic_low"), payload.get("rank_ic_high")
    if (low is None) != (high is None):
        return "forward_performance_interval_contract_invalid"

    method = payload.get("rank_ic_interval_method")
    status = payload.get("rank_ic_interval_status")
    if method is None and status is None:
        if (
            forecast_count != 0
            or matured_count != 0
            or pending_count != 0
            or calendar_months != 0
            or low is not None
            or high is not None
        ):
            return "forward_performance_interval_contract_invalid"
        return None
    if method != _FORWARD_INTERVAL_METHOD or status not in _FORWARD_INTERVAL_STATUSES:
        return "forward_performance_interval_contract_invalid"

    block_months = payload.get("rank_ic_block_months")
    bootstrap_samples = payload.get("rank_ic_bootstrap_samples")
    if not _is_positive_int(block_months) or not _is_positive_int(bootstrap_samples):
        return "forward_performance_interval_contract_invalid"
    if status == "ready":
        if (
            low is None
            or high is None
            or payload.get("rank_ic") is None
            or matured_count < 100
            or calendar_months < 12
        ):
            return "forward_performance_interval_contract_invalid"
        if not _is_finite_number(low) or not _is_finite_number(high) or low > high:
            return "forward_performance_interval_contract_invalid"
    elif low is not None or high is not None:
        return "forward_performance_interval_contract_invalid"
    return None


def _check_endpoint(
    base: str,
    origin: tuple[str, str],
    path: str,
    name: str,
    expected_content_type: str,
    timeout: float,
    *,
    accepted_statuses: frozenset[int] = frozenset({200}),
) -> dict[str, Any]:
    request_id = uuid4().hex
    request = Request(
        f"{base}{path}",
        headers={
            "Accept": expected_content_type,
            "User-Agent": _USER_AGENT,
            "X-Request-ID": request_id,
        },
        method="GET",
    )
    check: dict[str, Any] = {"name": name, "path": path, "status": "failed"}
    try:
        try:
            opened_response = _open_url(request, timeout=timeout, origin=origin)
        except HTTPError as error:
            if error.code not in accepted_statuses:
                check.update(http_status=error.code, error="unexpected_http_status")
                return check
            opened_response = error
        with opened_response as response:
            body = response.read(_MAX_BODY_BYTES + 1)
            status_code = int(response.status)
            content_type = response.headers.get("Content-Type", "").lower()
            response_headers = {
                str(key).lower(): str(value).strip().lower()
                for key, value in response.headers.items()
            }
            final_parts = urlsplit(response.geturl())
            final_origin = (final_parts.scheme.lower(), final_parts.netloc.lower())
            media_type = content_type.split(";", 1)[0].strip()
            check.update(http_status=status_code, content_type=media_type)
            if final_origin != origin:
                check["error"] = "redirected_to_different_origin"
                return check
            if status_code not in accepted_statuses:
                check["error"] = "unexpected_http_status"
                return check
            if len(body) > _MAX_BODY_BYTES:
                check["error"] = "response_too_large"
                return check
            if media_type != expected_content_type:
                check["error"] = "unexpected_content_type"
                return check
            missing_headers = [
                key
                for key, expected in _REQUIRED_SECURITY_HEADERS.items()
                if not _security_header_matches(key, response_headers.get(key, ""), expected)
            ]
            if missing_headers:
                check["error"] = "required_security_header_missing"
                check["missing_headers"] = missing_headers
                return check
            if path.startswith("/api/"):
                if _shared_cacheable(response_headers.get("cache-control", "")):
                    # A shared cache serves one stored response to everyone, so
                    # an identifier on it would belong to whoever filled the
                    # cache. Its absence is the contract, not its echo.
                    if response_headers.get("x-request-id"):
                        check["error"] = "request_id_on_shared_cacheable_response"
                        return check
                elif response_headers.get("x-request-id") != request_id:
                    check["error"] = "request_id_not_echoed"
                    return check
                else:
                    # This is generated locally by this probe; never retain an
                    # arbitrary response header value in the redacted artifact.
                    check["request_id"] = request_id
            decoded = body.decode("utf-8")
            if name in {"health", "provenance", "governance", "forward_performance"}:
                try:
                    check["_json"] = json.loads(decoded)
                except json.JSONDecodeError:
                    check["error"] = "invalid_json"
                    return check
            elif name in {"robots", "security_txt", "api_docs"}:
                check["_body"] = decoded
            check["status"] = "passed"
            return check
    except _CrossOriginRedirectError:
        check["error"] = "redirected_to_different_origin"
    except HTTPError as error:
        check.update(http_status=error.code, error="unexpected_http_status")
    except (OSError, URLError, UnicodeDecodeError, ValueError) as error:
        check["error"] = type(error).__name__
    return check


def _scripts_are_external(body: str) -> bool:
    """Return whether a page loads only external scripts, as its CSP requires."""
    scripts = _script_elements(body)
    return bool(scripts) and all(
        bool(attributes.get("src")) and not content.strip() for attributes, content in scripts
    )


def _security_header_matches(key: str, actual: str, expected: str) -> bool:
    """Validate required headers while allowing provider-managed HSTS extensions.

    Vercel documents a two-year HSTS default and may append ``preload`` to a
    project-level value. The smoke check therefore enforces a one-year minimum
    with ``includeSubDomains`` instead of requiring one exact max-age string.
    Other security headers remain exact contracts so an accidental policy
    weakening is still caught.
    """
    if key != "strict-transport-security":
        return actual == expected
    directives: dict[str, str] = {}
    for part in actual.split(";"):
        token = part.strip().lower()
        if not token:
            continue
        name, separator, value = token.partition("=")
        directives[name] = value if separator else ""
    try:
        max_age = int(directives.get("max-age", "0"))
    except ValueError:
        return False
    return max_age >= _MINIMUM_HSTS_MAX_AGE_SECONDS and "includesubdomains" in directives


def _open_url(
    request: Request,
    *,
    timeout: float,
    origin: tuple[str, str],
) -> Any:
    """Open a request while refusing redirects outside the supplied origin."""
    opener = build_opener(_SameOriginRedirectHandler(origin))
    return opener.open(request, timeout=timeout)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="HTTPS deployment origin to probe")
    parser.add_argument("--output", type=Path, help="write the redacted JSON report to this path")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--allow-http", action="store_true", help="allow HTTP for local-only smoke tests"
    )
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="accept a health response whose snapshot status is degraded",
    )
    parser.add_argument(
        "--expect-lock",
        type=Path,
        help=(
            "require the served frozen identity to equal this reviewed lock "
            "(for example, config/public_snapshot.lock.json)"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        expected_identity = (
            load_expected_identity(args.expect_lock) if args.expect_lock is not None else None
        )
        report = run_smoke(
            args.base_url,
            timeout=args.timeout,
            allow_http=args.allow_http,
            allow_degraded=args.allow_degraded,
            expected_identity=expected_identity,
        )
    except ValueError as error:
        print(f"Deployment smoke check rejected: {error}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
