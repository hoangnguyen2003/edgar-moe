"""Small, reviewed research-evidence catalog for the read-only serving tier.

The catalog contains derived aggregates only. Its lock is verified before any
value is served; private datasets, scores, and pretest review files are never
loaded by this module.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field, model_validator

from edgar_moe.api.models import (
    FrozenResearchEvidence,
    RankICInterval,
    ResearchEvidenceResponse,
    V2PendingEvidence,
    V2ReviewedEvidence,
)
from edgar_moe.api.repository import SnapshotRepository

CATALOG_PATH = Path(__file__).with_name("research_evidence_catalog.json")
LOCK_PATH = Path(__file__).with_name("research_evidence_catalog.sha256")
V2_COMPARATORS = frozenset(
    {
        "Elastic Net",
        "Gradient-Boosted Tabular",
        "Fundamental-Only Expert",
        "Static Early-Fusion MLP",
        "Equal-Weight Expert Ensemble",
    }
)


class CatalogIntegrityError(ValueError):
    """The public research catalog is absent, malformed, or not reviewed."""


class FrozenV1Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    selection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    locked_test_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    rank_ic_interval_95: RankICInterval


class PublicCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    frozen_v1: FrozenV1Catalog
    duration_aware_v2: V2PendingEvidence | V2ReviewedEvidence

    @model_validator(mode="after")
    def validate_reviewed_roster(self) -> PublicCatalog:
        reviewed = self.duration_aware_v2
        if not isinstance(reviewed, V2ReviewedEvidence):
            return self
        if {item.baseline for item in reviewed.comparisons} != V2_COMPARATORS or len(
            reviewed.comparisons
        ) != len(V2_COMPARATORS):
            raise ValueError("reviewed v2 comparison roster is invalid")
        for item in reviewed.comparisons:
            ready = item.interval_status == "ready"
            if ready != (item.interval_low is not None and item.interval_high is not None):
                raise ValueError("reviewed v2 interval readiness is inconsistent")
            if (
                ready
                and item.interval_low is not None
                and item.interval_high is not None
                and item.interval_low > item.interval_high
            ):
                raise ValueError("reviewed v2 interval bounds are reversed")
        if reviewed.portfolio_status == "development_only":
            if reviewed.cost_definition != (
                "10/25/50 bps per unit of one-sided turnover plus configured short borrow"
            ):
                raise ValueError("reviewed v2 cost definition is invalid")
            roster = {(item.model, item.cost_bps) for item in reviewed.cost_scenarios}
            expected = {
                (name, cost)
                for name in {reviewed.champion_name, "Fundamental-Only Expert"}
                for cost in (10, 25, 50)
            }
            if roster != expected or len(reviewed.cost_scenarios) != len(expected):
                raise ValueError("reviewed v2 cost scenario roster is invalid")
        elif reviewed.cost_scenarios or reviewed.cost_definition is not None:
            raise ValueError("unavailable portfolio cannot have cost scenarios")
        return self


def load_public_catalog(
    path: Path = CATALOG_PATH,
    lock_path: Path = LOCK_PATH,
) -> tuple[PublicCatalog, str]:
    """Reject mismatched bytes, unknown fields, and unreviewed states."""
    try:
        raw = path.read_bytes()
        expected = lock_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise CatalogIntegrityError("research evidence catalog or lock is unavailable") from error
    observed = hashlib.sha256(raw).hexdigest()
    if expected != observed:
        raise CatalogIntegrityError("research evidence catalog hash mismatch")
    try:
        payload: Any = orjson.loads(raw)
        catalog = PublicCatalog.model_validate(payload)
    except (orjson.JSONDecodeError, ValueError) as error:
        raise CatalogIntegrityError("research evidence catalog schema is invalid") from error
    return catalog, observed


def build_research_evidence(
    repo: SnapshotRepository,
    *,
    catalog_path: Path = CATALOG_PATH,
    lock_path: Path = LOCK_PATH,
) -> ResearchEvidenceResponse:
    catalog, catalog_sha256 = load_public_catalog(catalog_path, lock_path)
    identity = repo.frozen_identity()
    recorded = catalog.frozen_v1
    if (
        identity["selection_hash"] != recorded.selection_hash
        or identity["locked_test_hash"] != recorded.locked_test_hash
    ):
        raise CatalogIntegrityError("research catalog and frozen snapshot identities differ")
    summary = repo.summary()
    test_metrics = summary["predictive_metrics"]["locked_test"]
    ten_bps = next(
        (scenario for scenario in summary["portfolio_scenarios"] if scenario["cost_bps"] == 10),
        None,
    )
    if ten_bps is None or test_metrics.get("rank_ic") is None or ten_bps.get("sharpe") is None:
        raise CatalogIntegrityError("frozen study evidence is incomplete")
    interval = recorded.rank_ic_interval_95
    if not interval.low <= float(test_metrics["rank_ic"]) <= interval.high:
        raise CatalogIntegrityError("frozen rank IC lies outside its reviewed interval")
    frozen = FrozenResearchEvidence(
        status="frozen_locked_test",
        dataset_id=recorded.dataset_id,
        as_of=identity["as_of"],
        selection_hash=recorded.selection_hash,
        locked_test_hash=recorded.locked_test_hash,
        snapshot_sha256=identity["sha256"],
        total_events=summary["summary"]["events"],
        validation_events=summary["summary"]["validation_events"],
        locked_test_events=summary["summary"]["test_events"],
        locked_rank_ic=float(test_metrics["rank_ic"]),
        locked_rank_ic_interval_95=interval,
        portfolio_10bps_sharpe=float(ten_bps["sharpe"]),
        interpretation=(
            "The locked rank-IC interval includes zero and the 10 bps cost-aware "
            "Sharpe is negative; positive skill or tradable alpha is not established."
        ),
    )
    v2 = catalog.duration_aware_v2
    if isinstance(v2, V2PendingEvidence):
        v2 = V2PendingEvidence(
            status="pending_review",
            reason="No separately reviewed duration-aware v2 aggregate is published.",
        )
    return ResearchEvidenceResponse(
        schema_version=1,
        catalog_sha256=catalog_sha256,
        frozen_v1=frozen,
        duration_aware_v2=v2,
    )
