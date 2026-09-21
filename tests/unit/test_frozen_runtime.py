from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import orjson

_SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "validate_frozen_runtime.py"
_SPEC = importlib.util.spec_from_file_location("validate_frozen_runtime", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _write_fixture(root: Path) -> tuple[Path, Path]:
    bundle = root / "ops" / "frozen"
    bundle.mkdir(parents=True)
    model = bundle / "frozen-model.pt"
    model.write_bytes(b"reviewed frozen model")
    model_hash = hashlib.sha256(model.read_bytes()).hexdigest()
    selection_hash = "1" * 64
    unsigned = {
        "dataset_id": "research-fixture",
        "selection_hash": selection_hash,
        "model_sha256": model_hash,
    }
    locked_hash = hashlib.sha256(orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)).hexdigest()
    (bundle / "locked-test.json").write_bytes(
        orjson.dumps({**unsigned, "locked_test_hash": locked_hash})
    )
    (bundle / "SHA256SUMS").write_text(
        f"{model_hash}  ops/frozen/frozen-model.pt\n"
        f"{hashlib.sha256((bundle / 'locked-test.json').read_bytes()).hexdigest()}  "
        "ops/frozen/locked-test.json\n",
        encoding="utf-8",
    )
    config = root / "config" / "forward.yaml"
    config.parent.mkdir()
    config.write_text(
        "\n".join(
            [
                "model_id: edgar-moe-frozen-v1",
                "name: Fixture",
                "version: 1.0.0-frozen",
                "training_dataset_id: research-fixture",
                "training_dataset_dir: data/processed/research-fixture",
                "model_path: data/artifacts/frozen-studies/research-fixture/frozen-model.pt",
                "locked_result_path: data/artifacts/frozen-studies/research-fixture/locked-test.json",
                f"artifact_sha256: {model_hash}",
                f"selection_hash: {selection_hash}",
                f"locked_test_hash: {locked_hash}",
                'frozen_at: "2026-09-19T00:00:00Z"',
            ]
        ),
        encoding="utf-8",
    )
    return config, bundle


def test_checked_in_frozen_runtime_bundle_is_consistent() -> None:
    assert _MODULE.validate_frozen_runtime() == []


def test_validator_rejects_manifest_hash_drift(tmp_path: Path) -> None:
    config, bundle = _write_fixture(tmp_path)
    manifest = (bundle / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    manifest[0] = "0" * 64 + manifest[0][64:]
    (bundle / "SHA256SUMS").write_text(
        "\n".join(manifest) + "\n",
        encoding="utf-8",
    )
    errors = _MODULE.validate_frozen_runtime(config)

    assert "frozen manifest hash mismatch: frozen-model.pt" in errors


def test_validator_rejects_locked_identity_drift(tmp_path: Path) -> None:
    config, bundle = _write_fixture(tmp_path)
    payload = orjson.loads((bundle / "locked-test.json").read_bytes())
    payload["model_sha256"] = "2" * 64
    (bundle / "locked-test.json").write_bytes(orjson.dumps(payload))

    errors = _MODULE.validate_frozen_runtime(config)

    assert "locked-test artifact content hash is invalid" in errors
    assert "locked-test artifact identity mismatch: model_sha256" in errors
