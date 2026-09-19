"""Run a credential-free smoke check against a deployed EDGAR-MoE origin."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

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
    request = Request(
        f"{base}{path}",
        headers={"Accept": expected_content_type, "User-Agent": _USER_AGENT},
        method="GET",
    )
    check: dict[str, Any] = {"name": name, "path": path, "status": "failed"}
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is operator supplied
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
                if response_headers.get(key) != expected
            ]
            if missing_headers:
                check["error"] = "required_security_header_missing"
                check["missing_headers"] = missing_headers
                return check
            decoded = body.decode("utf-8")
            if name in {"health", "provenance"}:
                try:
                    check["_json"] = json.loads(decoded)
                except json.JSONDecodeError:
                    check["error"] = "invalid_json"
                    return check
            elif name in {"robots", "security_txt"}:
                check["_body"] = decoded
            check["status"] = "passed"
            return check
    except HTTPError as error:
        check.update(http_status=error.code, error="unexpected_http_status")
    except (OSError, URLError, UnicodeDecodeError, ValueError) as error:
        check["error"] = type(error).__name__
    return check


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
