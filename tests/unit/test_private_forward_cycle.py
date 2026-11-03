from __future__ import annotations

import importlib.util
import json
import shutil
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path("scripts/verify_private_forward_cycle.py")
spec = importlib.util.spec_from_file_location("private_forward_cycle_test", SCRIPT)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


@pytest.fixture
def scope(tmp_path):
    record = json.loads(Path("ops/issue288/private-verification-scope.json").read_text())
    for relative in verifier.INPUTS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(relative, target)
    return record, tmp_path


def test_input_bound_private_scope(scope):
    record, root = scope
    verifier.validate_scope(record, root)


def test_scope_record_tampering_is_rejected_before_credentials(scope):
    record, root = scope
    record["owner_statements"] = ["different record"]
    path = root / "scope.json"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="scope record changed"):
        verifier.read_scope(path, root)


def test_historical_scope_cannot_initialize_a_new_cycle(tmp_path):
    with pytest.raises(ValueError, match="new cycle needs a new reviewed scope"):
        verifier.retained_execution(tmp_path, verifier.SCOPE_SHA256, None)
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("change", ["cutoff", "output", "scope"])
def test_historical_execution_rejects_another_cutoff_output_or_scope(tmp_path, change):
    output = tmp_path / "data/artifacts" / verifier.APPROVED_OUTPUT
    output.mkdir(parents=True)
    state = {"cutoff": verifier.APPROVED_CUTOFF, "scope_sha256": verifier.SCOPE_SHA256}
    requested = None
    if change == "cutoff":
        state["cutoff"] = "2026-11-04"
    elif change == "scope":
        state["scope_sha256"] = "0" * 64
    else:
        requested = tmp_path / "another-output"
    (output / "execution.json").write_text(json.dumps(state))
    with pytest.raises(ValueError):
        verifier.retained_execution(tmp_path, verifier.SCOPE_SHA256, requested)


def test_successful_reverification_never_loads_credentials(scope, monkeypatch):
    _, root = scope
    output = root / "data/artifacts" / verifier.APPROVED_OUTPUT
    output.mkdir(parents=True)
    (output / "execution.json").write_text(
        json.dumps({"cutoff": verifier.APPROVED_CUTOFF, "scope_sha256": verifier.SCOPE_SHA256})
    )
    (output / "result.json").write_text(
        json.dumps(
            {
                "status": "verified",
                "existing_records_unchanged": True,
                "cutoff": verifier.APPROVED_CUTOFF,
            }
        )
    )
    evidence = verifier.build_evidence_manifest(["result.json"], root=output)
    (output / "evidence-manifest.json").write_text(json.dumps(evidence))
    monkeypatch.setattr(
        verifier, "RuntimeSettings", lambda **kwargs: pytest.fail("credentials loaded")
    )
    assert verifier.execute(root, Path("ops/issue288/private-verification-scope.json")) == output


@pytest.mark.parametrize(
    "field",
    [
        "new_training",
        "publication_of_provider_data_or_model_outputs",
        "scheduled_workflow_activation",
        "global_source_gate_release",
        "rewrite_existing_evidence",
        "paid_plan_or_upgrade",
    ],
)
def test_private_scope_rejects_expanded_uses(scope, field):
    record, root = scope
    record["bounds"][field] = True
    with pytest.raises(ValueError, match="exclusion"):
        verifier.validate_scope(record, root)


def test_private_scope_rejects_changed_universe_before_network(scope):
    record, root = scope
    (root / "config/universe.research.csv").write_text("changed")
    with pytest.raises(ValueError, match="Bound input changed"):
        verifier.validate_scope(record, root)


def test_private_scope_rejects_changed_macro_sources(scope):
    record, root = scope
    record["source_scope"]["fred_alfred_series"].remove("VIXCLS")
    with pytest.raises(ValueError, match="Macro source scope"):
        verifier.validate_scope(record, root)


@pytest.mark.parametrize("current", [{}, {"prior": "changed"}])
def test_registry_comparison_rejects_removed_or_changed_evidence(current):
    with pytest.raises(ValueError, match="changed or disappeared"):
        verifier.compare_snapshots(
            {"forward_forecasts": {"prior": "hash"}}, {"forward_forecasts": current}
        )


def test_registry_comparison_counts_only_new_identities():
    assert verifier.compare_snapshots(
        {"forward_forecasts": {"prior": "hash"}},
        {"forward_forecasts": {"prior": "hash", "new": "new-hash"}},
    ) == {"forward_forecasts": 1}


def test_postgres_snapshot_sets_transaction_bounds_before_reading(monkeypatch):
    statements = []
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        begin=lambda: nullcontext(),
        exec_driver_sql=statements.append,
    )
    database = SimpleNamespace(engine=SimpleNamespace(connect=lambda: nullcontext(connection)))
    monkeypatch.setattr(verifier, "TABLES", ())
    assert verifier.snapshot(database) == {}
    assert statements == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL statement_timeout = '30s'",
    ]


def test_target_binding_ignores_password_rotation_but_rejects_another_database():
    settings = SimpleNamespace(
        edgar_moe_registry_database_url="postgresql://user:one@db.example/registry",
        edgar_moe_r2_endpoint_url="https://bucket.example",
        edgar_moe_r2_bucket="private",
    )
    first = verifier.target_fingerprints(settings)
    settings.edgar_moe_registry_database_url = "postgresql://user:two@db.example/registry"
    assert verifier.target_fingerprints(settings) == first
    settings.edgar_moe_registry_database_url = "postgresql://user:two@db.example/another"
    assert verifier.target_fingerprints(settings) != first


def test_finbert_revision_is_passed_to_both_cached_loaders(monkeypatch):
    import transformers

    from edgar_moe.features.text import FinBertEmbedder

    calls = []
    model = SimpleNamespace(
        config=SimpleNamespace(
            _commit_hash="pinned", id2label={0: "positive", 1: "negative", 2: "neutral"}
        ),
        eval=lambda: None,
        to=lambda device: None,
    )

    def tokenizer(name, **kwargs):
        calls.append(("tokenizer", kwargs))
        return SimpleNamespace(init_kwargs={"_commit_hash": "pinned"})

    def weights(name, **kwargs):
        calls.append(("model", kwargs))
        return model

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", tokenizer)
    monkeypatch.setattr(transformers.AutoModelForSequenceClassification, "from_pretrained", weights)
    encoder = FinBertEmbedder(revision="pinned", device="cpu")
    assert calls == [("tokenizer", {"revision": "pinned"}), ("model", {"revision": "pinned"})]
    assert encoder.model_revision == encoder.tokenizer_revision == "pinned"
