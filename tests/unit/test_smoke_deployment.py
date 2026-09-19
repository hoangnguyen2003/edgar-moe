from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "smoke_deployment.py"
_SPEC = importlib.util.spec_from_file_location("smoke_deployment", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class FakeHeaders(dict[str, str]):
    def get(self, key: str, default: str = "") -> str:
        return super().get(key, default)


class FakeResponse:
    def __init__(self, url: str, body: str, content_type: str = "text/plain") -> None:
        self.status = 200
        self.headers = FakeHeaders(
            {
                "Content-Type": content_type,
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "strict-origin-when-cross-origin",
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-site",
                "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Content-Security-Policy": (
                    "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
                    "form-action 'self'; script-src 'self' https://cdn.jsdelivr.net; "
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
                    "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: https:; "
                    "connect-src 'self'"
                ),
            }
        )
        self._url = url
        self._body = body.encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url


def fake_urlopen_factory(responses: dict[str, FakeResponse]):
    def fake_open(
        request: Any, *, timeout: float, origin: tuple[str, str]
    ) -> FakeResponse:
        assert timeout == 2.0
        assert origin == ("https", "terminal.example")
        return responses[request.full_url]

    return fake_open


def complete_responses() -> dict[str, FakeResponse]:
    base = "https://terminal.example"
    return {
        f"{base}/": FakeResponse(f"{base}/", "<html></html>", "text/html; charset=utf-8"),
        f"{base}/robots.txt": FakeResponse(
            f"{base}/robots.txt", "User-agent: *\nDisallow: /api/\n"
        ),
        f"{base}/.well-known/security.txt": FakeResponse(
            f"{base}/.well-known/security.txt",
            "Contact: https://example.test/security\n"
            "Policy: https://example.test/policy\n"
            "Preferred-Languages: en, vi\n"
            "Expires: 2027-09-19T00:00:00.000Z\n",
        ),
        f"{base}/data-provenance.json": FakeResponse(
            f"{base}/data-provenance.json",
            json.dumps(
                {
                    "snapshot": {"raw_sources_public": False, "derived_output_public": True},
                    "review": {
                        "redistribution_status": "operator_review_required",
                        "legal_approval": False,
                    },
                }
            ),
            "application/json",
        ),
        f"{base}/api/v1/governance": FakeResponse(
            f"{base}/api/v1/governance",
            json.dumps(
                {
                    "schema_version": 1,
                    "frozen_v1": {
                        "path": "data/demo/snapshot.json",
                        "sha256": "a" * 64,
                        "data_mode": "authenticated_locked_test",
                        "as_of": "2026-07-31",
                        "selection_hash": "b" * 64,
                        "locked_test_hash": "c" * 64,
                        "research_only": True,
                    },
                    "public_data": {
                        "raw_sources_public": False,
                        "derived_output_public": True,
                        "redistribution_status": "operator_review_required",
                    },
                    "controls": [
                        {"status": "enforced", "owner": "repository"},
                        {"status": "pending_operator_evidence", "owner": "operator"},
                    ],
                    "forward_status": {"configured": False, "available": False},
                }
            ),
            "application/json",
        ),
        f"{base}/api/v1/health": FakeResponse(
            f"{base}/api/v1/health",
            json.dumps({"status": "ok", "snapshot_loaded": True}),
            "application/json",
        ),
    }


def test_normalize_base_url_rejects_credentials_and_non_https() -> None:
    with pytest.raises(ValueError):
        _MODULE.normalize_base_url("https://user:secret@terminal.example")
    with pytest.raises(ValueError):
        _MODULE.normalize_base_url("http://terminal.example")
    assert _MODULE.normalize_base_url("http://terminal.example/path", allow_http=True) == (
        "http://terminal.example"
    )


def test_smoke_passes_and_redacts_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example/", timeout=2.0)

    assert report["status"] == "passed"
    assert report["base_url"] == "https://terminal.example"
    assert all("_body" not in check and "_json" not in check for check in report["checks"])
    assert report["checks"][-1]["snapshot_loaded"] is True


def test_smoke_rejects_public_raw_source_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    responses["https://terminal.example/data-provenance.json"] = FakeResponse(
        "https://terminal.example/data-provenance.json",
        json.dumps(
            {
                "snapshot": {"raw_sources_public": True, "derived_output_public": True},
                "review": {
                    "redistribution_status": "operator_review_required",
                    "legal_approval": False,
                },
            }
        ),
        "application/json",
    )
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    provenance = next(check for check in report["checks"] if check["name"] == "provenance")
    assert provenance["error"] == "provenance_review_contract_invalid"


def test_smoke_rejects_degraded_health_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    responses["https://terminal.example/api/v1/health"] = FakeResponse(
        "https://terminal.example/api/v1/health",
        json.dumps({"status": "degraded", "snapshot_loaded": False}),
        "application/json",
    )
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    assert report["checks"][-1]["error"] == "health_status_degraded"


def test_smoke_rejects_cross_origin_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    responses["https://terminal.example/"].geturl = lambda: "https://other.example/"  # type: ignore[method-assign]
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    assert report["checks"][0]["error"] == "redirected_to_different_origin"


def test_redirect_handler_allows_same_origin_and_rejects_cross_origin() -> None:
    handler = _MODULE._SameOriginRedirectHandler(("https", "terminal.example"))
    request = Request("https://terminal.example/")

    same_origin = handler.redirect_request(
        request, None, 302, "Found", {}, "https://terminal.example/next"
    )
    assert same_origin is not None
    with pytest.raises(ValueError, match="redirected_to_different_origin"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://other.example/")


def test_smoke_rejects_missing_security_header(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    responses["https://terminal.example/"].headers.pop("X-Frame-Options")
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    assert report["checks"][0]["error"] == "required_security_header_missing"
    assert report["checks"][0]["missing_headers"] == ["x-frame-options"]


def test_smoke_accepts_provider_managed_hsts_default(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    responses["https://terminal.example/"].headers["Strict-Transport-Security"] = (
        "max-age=63072000; includeSubDomains; preload"
    )
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "passed"


def test_smoke_rejects_weak_hsts_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    responses["https://terminal.example/"].headers["Strict-Transport-Security"] = (
        "max-age=86400; includeSubDomains"
    )
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    assert report["checks"][0]["missing_headers"] == ["strict-transport-security"]


def test_smoke_rejects_invalid_content_security_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    responses["https://terminal.example/"].headers["Content-Security-Policy"] = "default-src *"
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    assert report["checks"][0]["error"] == "required_security_header_missing"
    assert "content-security-policy" in report["checks"][0]["missing_headers"]


def test_smoke_requires_exact_media_type(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    responses["https://terminal.example/"].headers["Content-Type"] = "text/html-malicious"
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    assert report["checks"][0]["error"] == "unexpected_content_type"
