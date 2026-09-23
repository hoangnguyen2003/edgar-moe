from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from urllib.request import Request

import orjson
import pytest

from edgar_moe.forward.alerts import (
    AlertDeliveryError,
    build_failure_alert,
    build_status_alert,
    classify_forward_status,
    send_webhook,
    verify_alert_receipt,
)


def test_failure_alert_is_allowlisted_and_deduplicable() -> None:
    payload = build_failure_alert(
        {
            "observed_at": "2026-09-19T01:02:03Z",
            "workflow": "forward-production",
            "run_id": "123",
            "run_attempt": "2",
            "commit": "deadbeef",
            "ref": "main",
            "cutoff": "2026-09-18",
            "device": "cpu",
            "cycle_outcome": "failure",
            "EDGAR_MOE_REGISTRY_DATABASE_URL": "postgresql://user:secret@example.test/db",
        }
    )

    assert payload["kind"] == "failed_run"
    assert payload["severity"] == "critical"
    assert payload["observed_at"] == "2026-09-19T01:02:03+00:00"
    assert len(payload["dedupe_key"]) == 24
    assert "secret" not in str(payload)
    assert (
        build_failure_alert(payload["details"] | {"observed_at": payload["observed_at"]})[
            "dedupe_key"
        ]
        == payload["dedupe_key"]
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"available": False}, "registry_unavailable"),
        ({"latest_run_status": "failed"}, "failed_run"),
        ({"latest_quality_failures": 1}, "quality_failure"),
        ({"age_seconds": 100, "stale_after_seconds": 96}, "stale_runner"),
        ({"latest_quality_warnings": 1}, "quality_warning"),
        ({"health_status": "ok"}, None),
        # A registry that has never recorded a success is degraded with no age;
        # it previously produced no alert at all.
        (
            {
                "health_status": "degraded",
                "latest_run_status": "running",
                "latest_successful_run_at": None,
                "age_seconds": None,
            },
            "stale_runner",
        ),
    ],
)
def test_forward_status_classification(status: dict[str, object], expected: str | None) -> None:
    assert classify_forward_status(status) == expected


def test_status_alert_contains_only_operational_details() -> None:
    payload = build_status_alert(
        {
            "health_status": "warning",
            "health_message": "quality review",
            "latest_quality_warnings": 2,
            "latest_quality_failures": 0,
            "age_seconds": 10,
            "stale_after_seconds": 96,
            "private_error": "do-not-copy",
        },
        observed_at=datetime(2026, 9, 19, 1, 2, 3, tzinfo=UTC),
    )

    assert payload is not None
    assert payload["kind"] == "quality_warning"
    assert "private_error" not in str(payload)
    assert payload["details"]["latest_quality_warnings"] == 2


def test_healthy_status_does_not_create_alert() -> None:
    assert build_status_alert({"health_status": "ok"}) is None


def test_webhook_requires_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        send_webhook("http://example.test/hook", {"kind": "failed_run"})


def test_webhook_posts_json_and_accepts_success(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    class Response:
        status = 204

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: object, *, timeout: float) -> Response:
        observed["request"] = request
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr("edgar_moe.forward.alerts.urlopen", fake_urlopen)
    assert send_webhook("https://example.test/hook", {"kind": "failed_run"}) == 204
    request = observed["request"]
    assert isinstance(request, Request)
    assert request.method == "POST"
    assert request.headers["Content-type"] == "application/json"


def test_webhook_non_success_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        status = 503

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("edgar_moe.forward.alerts.urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(AlertDeliveryError, match="HTTP 503"):
        send_webhook("https://example.test/hook", {"kind": "failed_run"})


def test_alert_receipt_hash_rejects_tampering() -> None:
    receipt = {
        "schema_version": 1,
        "observed_at": "2026-09-19T01:02:03+00:00",
        "delivered": False,
        "dry_run": True,
        "kind": "failed_run",
        "dedupe_key": "a" * 24,
        "payload_sha256": "b" * 64,
    }
    receipt["receipt_hash"] = hashlib.sha256(
        orjson.dumps(receipt, option=orjson.OPT_SORT_KEYS)
    ).hexdigest()
    verify_alert_receipt(receipt)
    receipt["kind"] = "quality_warning"
    with pytest.raises(ValueError, match="does not match"):
        verify_alert_receipt(receipt)


_HEALTHY = {
    "available": True,
    "latest_run_status": "succeeded",
    "age_seconds": 10,
    "stale_after_seconds": 100,
    "health_status": "ok",
}


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        # Most runs score nothing because no filing was accepted that day.
        (["prospective_candidate_count"], None),
        (["pre_open_schedule_margin"], "quality_warning"),
        # A new problem alongside the expected one still has to be seen.
        (["prospective_candidate_count", "pre_open_schedule_margin"], "quality_warning"),
        (["a_check_nobody_has_seen"], "quality_warning"),
        ([], "quality_warning"),
    ],
)
def test_an_expected_warning_does_not_page(names: list[str], expected: str | None) -> None:
    status = {**_HEALTHY, "latest_quality_warnings": max(len(names), 1)}
    if names:
        status["latest_quality_warning_names"] = names

    assert classify_forward_status(status) == expected


def test_a_failure_still_pages_even_among_expected_warnings() -> None:
    status = {
        **_HEALTHY,
        "latest_quality_warnings": 1,
        "latest_quality_warning_names": ["prospective_candidate_count"],
        "latest_quality_failures": 1,
    }

    assert classify_forward_status(status) == "quality_failure"


def test_an_alert_names_the_checks_that_warned() -> None:
    payload = build_status_alert(
        {
            **_HEALTHY,
            "health_status": "warning",
            "latest_quality_warnings": 2,
            "latest_quality_warning_names": ["pre_open_schedule_margin", "../../etc/passwd"],
        }
    )

    assert payload is not None
    # The names reach a recipient, so anything that is not a check name is dropped.
    assert payload["details"]["latest_quality_warning_names"] == ["pre_open_schedule_margin"]
