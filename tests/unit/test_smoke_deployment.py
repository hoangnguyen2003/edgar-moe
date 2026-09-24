from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
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
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "font-src 'self' data:; img-src 'self' data: https:; "
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


def fake_urlopen_factory(responses: dict[str, FakeResponse], *, echo_request_id: bool = True):
    def fake_open(request: Any, *, timeout: float, origin: tuple[str, str]) -> FakeResponse:
        assert timeout == 2.0
        assert origin == ("https", "terminal.example")
        response = responses[request.full_url]
        if echo_request_id:
            request_id = request.get_header("X-request-id")
            if request_id is not None:
                response.headers["X-Request-ID"] = request_id
        return response

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
        f"{base}/api/docs": FakeResponse(
            f"{base}/api/docs",
            '<div id="swagger-ui"></div>'
            '<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.33.0/swagger-ui-bundle.js"'
            ' integrity="sha384-test" crossorigin="anonymous"></script>'
            '<script src="/api/docs/swagger-init.js"></script>',
            "text/html; charset=utf-8",
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
        f"{base}/api/v1/forward/performance": FakeResponse(
            f"{base}/api/v1/forward/performance",
            json.dumps(
                {
                    "model_id": None,
                    "forecast_count": 0,
                    "matured_count": 0,
                    "pending_count": 0,
                    "coverage": 0.0,
                    "rank_ic": None,
                    "rank_ic_low": None,
                    "rank_ic_high": None,
                    "rank_ic_interval_method": None,
                    "rank_ic_interval_status": None,
                    "rank_ic_calendar_months": 0,
                    "rank_ic_block_months": None,
                    "rank_ic_bootstrap_samples": None,
                    "rmse": None,
                    "mae": None,
                    "directional_accuracy": None,
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


_EXPECTED_IDENTITY = {
    "path": "data/demo/snapshot.json",
    "data_mode": "authenticated_locked_test",
    "as_of": "2026-07-31",
    "sha256": "a" * 64,
    "selection_hash": "b" * 64,
    "locked_test_hash": "c" * 64,
}


def identity_responses() -> dict[str, FakeResponse]:
    """Complete responses whose provenance manifest also publishes the frozen identity."""
    responses = complete_responses()
    url = "https://terminal.example/data-provenance.json"
    responses[url] = FakeResponse(
        url,
        json.dumps(
            {
                "snapshot": {
                    **_EXPECTED_IDENTITY,
                    "raw_sources_public": False,
                    "derived_output_public": True,
                },
                "review": {
                    "redistribution_status": "operator_review_required",
                    "legal_approval": False,
                },
            }
        ),
        "application/json",
    )
    return responses


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
    api_checks = [check for check in report["checks"] if check["path"].startswith("/api/")]
    assert len(api_checks) == 4
    for check in api_checks:
        request_id = check["request_id"]
        assert isinstance(request_id, str)
        assert len(request_id) == 32
        assert all(character in "0123456789abcdef" for character in request_id)


def _configured_performance_payload() -> dict[str, Any]:
    return {
        "model_id": None,
        "forecast_count": 48,
        "matured_count": 24,
        "pending_count": 24,
        "coverage": 0.5,
        "rank_ic": -0.17928633594429938,
        "rank_ic_low": None,
        "rank_ic_high": None,
        "rank_ic_interval_method": "calendar_month_moving_block",
        "rank_ic_interval_status": "insufficient_pairs",
        "rank_ic_calendar_months": 1,
        "rank_ic_block_months": 2,
        "rank_ic_bootstrap_samples": 1000,
        "rmse": 0.10668738446157225,
        "mae": 0.07864952580658127,
        "directional_accuracy": 0.5416666666666666,
    }


def _replace_performance(responses: dict[str, FakeResponse], payload: dict[str, Any]) -> None:
    url = "https://terminal.example/api/v1/forward/performance"
    responses[url] = FakeResponse(url, json.dumps(payload), "application/json")


def test_smoke_checks_forward_performance_contract_without_retaining_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = complete_responses()
    governance_url = "https://terminal.example/api/v1/governance"
    governance_payload = json.loads(responses[governance_url]._body)
    governance_payload["forward_status"] = {"configured": True, "available": True}
    responses[governance_url] = FakeResponse(
        governance_url, json.dumps(governance_payload), "application/json"
    )
    _replace_performance(responses, _configured_performance_payload())
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "passed"
    check = next(item for item in report["checks"] if item["name"] == "forward_performance")
    assert check["availability"] == "available"
    assert check["forecast_count"] == 48
    assert check["matured_count"] == 24
    assert check["rank_ic_interval_method"] == "calendar_month_moving_block"
    assert check["rank_ic_interval_status"] == "insufficient_pairs"
    assert "rank_ic" not in check
    assert "-0.17928633594429938" not in json.dumps(report)


def test_smoke_accepts_ready_interval_only_after_the_minimum_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = complete_responses()
    payload = {
        **_configured_performance_payload(),
        "forecast_count": 144,
        "matured_count": 120,
        "pending_count": 24,
        "coverage": 120 / 144,
        "rank_ic": 0.3,
        "rank_ic_low": 0.2,
        "rank_ic_high": 0.4,
        "rank_ic_interval_status": "ready",
        "rank_ic_calendar_months": 12,
    }
    _replace_performance(responses, payload)
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "passed"
    check = next(item for item in report["checks"] if item["name"] == "forward_performance")
    assert check["rank_ic_interval_status"] == "ready"
    assert "rank_ic_low" not in check
    assert "rank_ic_high" not in check


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("pending_count", 23, "forward_performance_counts_inconsistent"),
        ("coverage", 0.6, "forward_performance_coverage_inconsistent"),
        (
            "rank_ic_interval_method",
            "independent_event",
            "forward_performance_interval_contract_invalid",
        ),
        ("rank_ic_interval_status", "ready", "forward_performance_interval_contract_invalid"),
        ("rank_ic_low", -0.5, "forward_performance_interval_contract_invalid"),
    ],
)
def test_smoke_rejects_invalid_forward_performance_contract(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
    error: str,
) -> None:
    responses = complete_responses()
    _replace_performance(responses, {**_configured_performance_payload(), field: value})
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    check = next(item for item in report["checks"] if item["name"] == "forward_performance")
    assert check["error"] == error
    assert "-0.17928633594429938" not in json.dumps(report)


def test_smoke_requires_interval_fields_in_performance_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = complete_responses()
    payload = _configured_performance_payload()
    del payload["rank_ic_interval_status"]
    _replace_performance(responses, payload)
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    check = next(item for item in report["checks"] if item["name"] == "forward_performance")
    assert check["error"] == "forward_performance_response_fields_missing"


def test_smoke_accepts_explicit_registry_unavailable_degraded_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = complete_responses()
    governance_url = "https://terminal.example/api/v1/governance"
    governance_payload = json.loads(responses[governance_url]._body)
    governance_payload["forward_status"] = {"configured": True, "available": False}
    responses[governance_url] = FakeResponse(
        governance_url, json.dumps(governance_payload), "application/json"
    )
    performance_url = "https://terminal.example/api/v1/forward/performance"
    unavailable = FakeResponse(
        performance_url,
        json.dumps({"detail": "Forward registry unavailable"}),
        "application/json",
    )
    unavailable.status = 503
    responses[performance_url] = unavailable
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "passed"
    check = next(item for item in report["checks"] if item["name"] == "forward_performance")
    assert check["http_status"] == 503
    assert check["availability"] == "unavailable"
    assert check["degraded_mode"] == "registry_unavailable"
    assert "_json" not in check


def test_smoke_accepts_urllib_http_error_for_documented_registry_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = complete_responses()
    governance_url = "https://terminal.example/api/v1/governance"
    governance_payload = json.loads(responses[governance_url]._body)
    governance_payload["forward_status"] = {"configured": True, "available": False}
    responses[governance_url] = FakeResponse(
        governance_url, json.dumps(governance_payload), "application/json"
    )
    performance_url = "https://terminal.example/api/v1/forward/performance"
    performance_response = FakeResponse(
        performance_url,
        json.dumps({"detail": "Forward registry unavailable"}),
        "application/json",
    )
    fallback = fake_urlopen_factory(responses)

    def fake_open(request: Any, *, timeout: float, origin: tuple[str, str]) -> FakeResponse:
        if request.full_url != performance_url:
            return fallback(request, timeout=timeout, origin=origin)
        headers = FakeHeaders(performance_response.headers.copy())
        request_id = request.get_header("X-request-id")
        assert request_id is not None
        headers["X-Request-ID"] = request_id
        raise HTTPError(
            performance_url,
            503,
            "Service Unavailable",
            headers,
            io.BytesIO(performance_response._body),
        )

    monkeypatch.setattr(_MODULE, "_open_url", fake_open)

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "passed"
    check = next(item for item in report["checks"] if item["name"] == "forward_performance")
    assert check["http_status"] == 503
    assert check["availability"] == "unavailable"
    assert check["degraded_mode"] == "registry_unavailable"


def test_smoke_rejects_unconfirmed_registry_unavailable_degraded_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = complete_responses()
    performance_url = "https://terminal.example/api/v1/forward/performance"
    unavailable = FakeResponse(
        performance_url,
        json.dumps({"detail": "Forward registry unavailable"}),
        "application/json",
    )
    unavailable.status = 503
    responses[performance_url] = unavailable
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    check = next(item for item in report["checks"] if item["name"] == "forward_performance")
    assert check["error"] == "forward_registry_unavailability_unconfirmed"


@pytest.mark.parametrize(
    ("served", "expected_error"),
    [
        ("b" * 40, None),
        ("c" * 40, "served_commit_mismatch"),
        (None, "served_commit_unavailable"),
        ("not-a-commit", "served_commit_unavailable"),
    ],
)
def test_smoke_verifies_served_commit(
    monkeypatch: pytest.MonkeyPatch, served: str | None, expected_error: str | None
) -> None:
    responses = complete_responses()
    health_url = "https://terminal.example/api/v1/health"
    responses[health_url] = FakeResponse(
        health_url,
        json.dumps({"status": "ok", "snapshot_loaded": True, "commit_sha": served}),
        "application/json",
    )
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke(
        "https://terminal.example", timeout=2.0, expected_commit_sha="b" * 40
    )

    assert report["expected_commit_sha"] == "b" * 40
    health = report["checks"][-1]
    if expected_error is None:
        assert report["status"] == "passed"
        assert health["commit_verified"] is True
    else:
        assert report["status"] == "failed"
        assert health["error"] == expected_error
        assert "c" * 40 not in json.dumps(health)


def test_smoke_rejects_invalid_expected_commit() -> None:
    with pytest.raises(ValueError, match="expected commit"):
        _MODULE.run_smoke("https://terminal.example", expected_commit_sha="short")


def test_smoke_requires_api_request_id_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = complete_responses()
    monkeypatch.setattr(
        _MODULE, "_open_url", fake_urlopen_factory(responses, echo_request_id=False)
    )

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    health = report["checks"][-1]
    assert health["error"] == "request_id_not_echoed"
    assert "request_id" not in health


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


def test_smoke_rejects_api_docs_that_the_csp_would_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = complete_responses()
    responses["https://terminal.example/api/docs"] = FakeResponse(
        "https://terminal.example/api/docs",
        '<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>'
        "<script>const ui = SwaggerUIBundle({url: '/api/openapi.json'})</script>",
        "text/html; charset=utf-8",
    )
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(responses))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0)

    assert report["status"] == "failed"
    docs = next(check for check in report["checks"] if check["name"] == "api_docs")
    assert docs["error"] == "api_docs_inline_script"
    assert "_body" not in docs


@pytest.mark.parametrize(
    ("body", "external"),
    [
        ('<script src="/app.js"></script>', True),
        ('<SCRIPT SRC="/app.js"></SCRIPT>', True),
        ("<script src='/a.js'></script ><script type=module src=/b.js></script>", True),
        ('<script src="/app.js">alert(1)</script>', False),
        ("<SCRIPT>alert(1)</SCRIPT>", False),
        # A regular expression looking for "</script>" missed this inline script.
        ('<script src="/a.js"></script><script>alert(1)</script >', False),
        ("<script>alert(1)", False),
        ('<script src="">alert(1)</script>', False),
        ("<p>No scripts at all</p>", False),
    ],
)
def test_script_check_tokenizes_tags_like_a_browser(body: str, external: bool) -> None:
    assert _MODULE._scripts_are_external(body) is external


def test_smoke_verifies_the_served_identity_against_the_reviewed_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = tmp_path / "public_snapshot.lock.json"
    lock.write_text(json.dumps({"schema_version": 1, **_EXPECTED_IDENTITY}), encoding="utf-8")
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(identity_responses()))

    report = _MODULE.run_smoke(
        "https://terminal.example",
        timeout=2.0,
        expected_identity=_MODULE.load_expected_identity(lock),
    )

    assert report["status"] == "passed"
    assert report["expected_snapshot_sha256"] == "a" * 64
    verified = {check["name"] for check in report["checks"] if check.get("identity_verified")}
    assert verified == {"governance", "provenance"}


def test_smoke_rejects_a_well_formed_but_unreviewed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The deployment serves valid digests that simply are not the reviewed ones.
    expected = {**_EXPECTED_IDENTITY, "sha256": "d" * 64}
    monkeypatch.setattr(_MODULE, "_open_url", fake_urlopen_factory(identity_responses()))

    report = _MODULE.run_smoke("https://terminal.example", timeout=2.0, expected_identity=expected)

    assert report["status"] == "failed"
    failed = {check["name"]: check for check in report["checks"] if check["status"] == "failed"}
    assert set(failed) == {"governance", "provenance"}
    assert failed["governance"]["error"] == "governance_identity_mismatch"
    assert failed["provenance"]["error"] == "provenance_identity_mismatch"
    assert failed["governance"]["mismatched_fields"] == ["sha256"]
    # Only field names are reported, never the served values.
    assert "a" * 64 not in json.dumps(failed)


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"schema_version": 2, **_EXPECTED_IDENTITY}),
        json.dumps({"schema_version": 1, **{**_EXPECTED_IDENTITY, "as_of": ""}}),
        json.dumps({"schema_version": 1, **{**_EXPECTED_IDENTITY, "sha256": "A" * 64}}),
    ],
)
def test_expected_lock_must_be_a_complete_reviewed_identity(tmp_path: Path, content: str) -> None:
    lock = tmp_path / "public_snapshot.lock.json"
    lock.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        _MODULE.load_expected_identity(lock)


def test_repository_lock_is_a_usable_expected_identity() -> None:
    lock = Path(__file__).parents[2] / "config" / "public_snapshot.lock.json"

    identity = _MODULE.load_expected_identity(lock)

    assert identity["path"] == "data/demo/snapshot.json"
    assert identity["data_mode"] == "authenticated_locked_test"


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


def test_the_smoke_check_reads_shared_cacheability_like_the_api() -> None:
    # The two definitions have to agree, or the gate fails on responses the API
    # deliberately leaves untraceable.
    from edgar_moe.api.app import shared_cacheable as api_rule

    for value in (
        "public, max-age=300, s-maxage=86400",
        "private, max-age=3600",
        "no-store",
        "no-cache",
        "max-age=60",
        "",
    ):
        assert _MODULE._shared_cacheable(value) is api_rule(value), value
