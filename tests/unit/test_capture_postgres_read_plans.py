from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import capture_postgres_read_plans as plans  # noqa: E402


def _raw_plan() -> list[dict[str, Any]]:
    return [
        {
            "Plan": {
                "Node Type": "Index Scan",
                "Relation Name": "private_relation",
                "Index Cond": "ticker = 'PRIVATE'",
                "Actual Rows": 7,
                "Shared Hit Blocks": 4,
                "Plans": [{"Node Type": "Sort", "Sort Key": ["private_value"], "Actual Rows": 2}],
            },
            "Planning Time": 0.3,
            "Execution Time": 1.2,
            "Query Identifier": "PRIVATE_IDENTIFIER",
        }
    ]


def test_plan_allowlist_removes_private_text_and_raw_queries() -> None:
    sanitized = plans._safe_plan(_raw_plan())
    payload = json.dumps(sanitized)

    assert sanitized["execution_ms"] == 1.2
    assert sanitized["shared_hit_blocks"] == 4
    assert "PRIVATE" not in payload
    assert "private" not in payload
    assert "Index Cond" not in payload
    assert "Relation Name" not in payload


@pytest.mark.parametrize(
    "raw",
    [
        [],
        [{"Plan": {"Node Type": "Scan\npassword=secret"}, "Planning Time": 1, "Execution Time": 1}],
        [
            {
                "Plan": {"Node Type": "Scan", "Actual Rows": "secret"},
                "Planning Time": 1,
                "Execution Time": 1,
            }
        ],
        [{"Plan": {"Node Type": "Scan"}, "Planning Time": float("nan"), "Execution Time": 1}],
    ],
)
def test_malformed_plan_is_rejected(raw: Any) -> None:
    with pytest.raises(plans.PlanCaptureError):
        plans._safe_plan(raw)


def test_fixed_workload_only_explains_selects() -> None:
    assert len(plans._QUERIES) >= 5
    for _, query, params in plans._QUERIES:
        assert query.startswith("SELECT ")
        assert ";" not in query
        assert params == ()
    assert plans._FILTER_QUERY.startswith("SELECT ")
    assert plans._FILTER_QUERY.count("%s") == 1


def test_report_only_written_inside_ignored_artifact_directory(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(plans, "_ARTIFACT_ROOT", tmp_path / "data" / "artifacts")
    report = {"plans": {"page": plans._safe_plan(_raw_plan())}}
    destination = tmp_path / "data" / "artifacts" / "plans.json"

    plans._write_report(destination, report)

    assert destination.stat().st_mode & 0o777 == 0o600
    assert json.loads(destination.read_text())["plans"]["page"]["execution_ms"] == 1.2
    with pytest.raises(FileExistsError):
        plans._write_report(destination, report)
    with pytest.raises(plans.PlanCaptureError):
        plans._write_report(tmp_path / "public" / "plans.json", report)


def test_capture_audits_role_and_does_not_retain_filter_value(monkeypatch: Any) -> None:
    statements: list[tuple[str, tuple[Any, ...] | None]] = []
    audited: list[str] = []
    secret_url = "postgresql://reader:secret@host.example/db"

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def execute(self, statement: str, params: tuple[Any, ...] | None = None) -> Any:
            statements.append((statement, params))
            if statement.startswith("SELECT count(*)"):
                return SimpleNamespace(fetchone=lambda: (25,))
            if statement.startswith("SELECT ticker"):
                return SimpleNamespace(fetchone=lambda: ("PRIVATE_TICKER",))
            if statement.startswith("EXPLAIN"):
                return SimpleNamespace(fetchone=lambda: (_raw_plan(),))
            return SimpleNamespace(fetchone=lambda: None)

        def rollback(self) -> None:
            statements.append(("ROLLBACK", None))

    monkeypatch.setattr(plans, "audit_reader_role", lambda url: audited.append(url))
    monkeypatch.setattr(plans.psycopg, "connect", lambda url: Connection())

    report = plans.capture(secret_url)

    assert audited == [secret_url]
    assert len(report["plans"]) == len(plans._QUERIES) + 1
    assert "PRIVATE_TICKER" not in json.dumps(report)
    assert "secret" not in json.dumps(report)
    assert statements[0][0] == "SET LOCAL transaction_read_only = on"
    assert all(
        statement.startswith("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT ")
        for statement, _ in statements
        if statement.startswith("EXPLAIN")
    )
    assert ("ROLLBACK", None) in statements


def test_capture_rejects_missing_or_non_postgres_url_before_network() -> None:
    with pytest.raises(plans.PlanCaptureError):
        plans.capture("")
    with pytest.raises(plans.PlanCaptureError):
        plans.capture("sqlite:///writer.db")
