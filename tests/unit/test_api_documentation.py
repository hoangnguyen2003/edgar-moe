"""The published API schema has to explain itself.

The README sends a reviewer to `/api/docs` as a headline link. What renders
there is the OpenAPI schema, so an endpoint without a description is an
endpoint the reviewer has to guess at.
"""

from __future__ import annotations

from typing import Any

import pytest

MINIMUM_DESCRIPTION_CHARACTERS = 60


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    from edgar_moe.api.app import app

    return app.openapi()


def operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (route, method, operation)
        for route, methods in schema["paths"].items()
        for method, operation in methods.items()
    ]


def test_every_operation_explains_itself(schema: dict[str, Any]) -> None:
    published = operations(schema)
    assert len(published) >= 15

    for route, method, operation in published:
        description = operation.get("description", "")
        assert description, f"{method.upper()} {route} has no description"
        assert len(description) >= MINIMUM_DESCRIPTION_CHARACTERS, (
            f"{method.upper()} {route} describes itself in {len(description)} characters"
        )


def test_every_operation_is_grouped_under_a_tag(schema: dict[str, Any]) -> None:
    for route, method, operation in operations(schema):
        assert operation.get("tags"), f"{method.upper()} {route} has no tag"


def test_the_overview_states_the_terms_of_use(schema: dict[str, Any]) -> None:
    description = schema["info"]["description"]

    assert "read-only" in description.lower()
    # A reader must not leave with the impression that the study made money.
    assert "not profitable" in description
    assert "investment advice" in description


def test_prospective_data_routes_document_only_the_withheld_contract(
    schema: dict[str, Any],
) -> None:
    for route in (
        "/api/v1/forward/runs",
        "/api/v1/forward/forecasts",
        "/api/v1/forward/performance",
        "/api/v1/forward/data-quality",
    ):
        operation = schema["paths"][route]["get"]
        assert set(operation["responses"]) == {"410"}
        assert "withheld" in operation["description"].lower()
        content = operation["responses"]["410"]["content"]["application/json"]["schema"]
        assert content["$ref"].endswith("/WithheldResponse")
    # The schema cannot advertise the historical forecast/performance fields
    # as a successful public response while those records are held.
    assert "ForwardForecastPage" not in schema["components"]["schemas"]
    assert "ForwardPerformanceResponse" not in schema["components"]["schemas"]


def test_documented_query_limits_match_the_code(schema: dict[str, Any]) -> None:
    events = schema["paths"]["/api/v1/events"]["get"]
    limit = next(item for item in events["parameters"] if item["name"] == "limit")

    assert limit["schema"]["maximum"] == 100
    assert limit["schema"]["minimum"] == 1
