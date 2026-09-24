from pathlib import Path
from typing import Any, cast

import yaml

PREWARM = Path(".github/workflows/forward-cache-prewarm.yml")
PRODUCTION = Path(".github/workflows/forward-production.yml")


def _workflow(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


def test_prewarm_is_manual_main_only_and_cannot_cancel_production() -> None:
    prewarm = _workflow(PREWARM)
    production = _workflow(PRODUCTION)
    trigger = prewarm.get("on", prewarm.get(True))
    assert isinstance(trigger, dict)
    assert set(trigger) == {"workflow_dispatch"}
    assert prewarm["permissions"] == {"contents": "read"}
    assert prewarm["concurrency"] == production["concurrency"]
    assert prewarm["concurrency"]["cancel-in-progress"] is False
    job = prewarm["jobs"]["prewarm"]
    assert job["if"] == "${{ github.ref == 'refs/heads/main' }}"


def test_prewarm_reuses_production_cache_without_registry_credentials() -> None:
    prewarm = _workflow(PREWARM)
    production = _workflow(PRODUCTION)
    steps = prewarm["jobs"]["prewarm"]["steps"]
    production_steps = production["jobs"]["forecast-and-settle"]["steps"]
    restore = next(step for step in steps if step.get("id") == "runtime_cache")
    production_restore = next(
        step for step in production_steps if step.get("id") == "runtime_cache"
    )
    assert restore["with"]["path"] == production_restore["with"]["path"]
    assert restore["with"]["restore-keys"] == production_restore["with"]["restore-keys"]
    assert restore["with"]["key"] == steps[-1]["with"]["key"]
    assert "uv.lock" in restore["with"]["key"]

    refresh = next(
        step for step in steps if step.get("name") == "Refresh sources and prewarm embeddings"
    )
    assert "--prewarm-only" in refresh["run"]
    assert "EDGAR_MOE_REGISTRY_DATABASE_URL" not in repr(prewarm)
    assert "EDGAR_MOE_R2_" not in repr(prewarm)
    assert "EDGAR_MOE_ARTIFACT_" not in repr(prewarm)
    assert "frozen-model.pt" not in repr(prewarm)
