"""Run a credential-free smoke check against a deployed EDGAR-MoE origin."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
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
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: https:; "
        "connect-src 'self'"
    ),
}
_MINIMUM_HSTS_MAX_AGE_SECONDS = 31_536_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
) -> dict[str, Any]:
    """Check the public deployment boundary without sending credentials or mutations."""
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
        _check_endpoint(
            base,
            origin,
            "/api/v1/governance",
            "governance",
            "application/json",
            timeout,
        ),
        _check_endpoint(base, origin, "/api/v1/health", "health", "application/json", timeout),
    ]

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
                forward_status = payload.get("forward_status") if isinstance(payload, dict) else None
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
                controls_valid = isinstance(controls, list) and bool(controls) and all(
                    isinstance(control, dict)
                    and control.get("status") in {"enforced", "pending_operator_evidence"}
                    and control.get("owner") in {"repository", "operator"}
                    for control in controls
                )
                forward_valid = isinstance(forward_status, dict) and all(
                    isinstance(forward_status.get(field), bool)
                    for field in ("configured", "available")
                )
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

    failed = [check for check in checks if check["status"] != "passed"]
    return {
        "schema_version": 1,
        "checked_at": datetime.now(UTC).isoformat(),
        "base_url": base,
        "status": "failed" if failed else "passed",
        "checks": checks,
    }


def _check_endpoint(
    base: str,
    origin: tuple[str, str],
    path: str,
    name: str,
    expected_content_type: str,
    timeout: float,
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
        with _open_url(request, timeout=timeout, origin=origin) as response:
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
            if status_code != 200:
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
                if response_headers.get("x-request-id") != request_id:
                    check["error"] = "request_id_not_echoed"
                    return check
                # This is generated locally by this probe; never retain an
                # arbitrary response header value in the redacted artifact.
                check["request_id"] = request_id
            decoded = body.decode("utf-8")
            if name in {"health", "provenance", "governance"}:
                try:
                    check["_json"] = json.loads(decoded)
                except json.JSONDecodeError:
                    check["error"] = "invalid_json"
                    return check
            elif name in {"robots", "security_txt"}:
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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        report = run_smoke(
            args.base_url,
            timeout=args.timeout,
            allow_http=args.allow_http,
            allow_degraded=args.allow_degraded,
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
