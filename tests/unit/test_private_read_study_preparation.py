from __future__ import annotations

import hashlib
import importlib.util
import json

import pytest

SPEC = importlib.util.spec_from_file_location(
    "study_preparation", "ops/private-read-study/study_preparation.py"
)
assert SPEC and SPEC.loader
prep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prep)


def write_inputs(tmp_path, value=None):
    raw = prep.synthetic_inputs() if value is None else json.dumps(value).encode()
    (tmp_path / "study-preparation.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_exact_public_inputs_are_ordered_aligned_and_deterministic(tmp_path):
    sha = write_inputs(tmp_path)
    scores, labels, accepted = prep.load_inputs(tmp_path, sha)
    assert len(scores) == len(labels) == len(accepted) == 5000
    assert accepted == sorted(accepted)
    assert len({(stamp.year, stamp.month) for stamp in accepted}) == 20
    assert len(prep.synthetic_inputs()) < 1_048_576
    assert hashlib.sha256(prep.synthetic_inputs()).hexdigest() == sha
    assert scores[0] == 0.0 and labels[0] == 0.1


@pytest.mark.parametrize(
    "alteration",
    [
        lambda d: d.update(profile="unapproved"),
        lambda d: d.update(fixture_payload_sha256="0" * 64),
        lambda d: d.update(result={"low": -1, "high": 1}),
        lambda d: d["pairs"].pop(),
        lambda d: d["pairs"][0].__setitem__(0, True),
        lambda d: d["pairs"][0].__setitem__(1, float("nan")),
        lambda d: d["pairs"][0].__setitem__(2, "2019-01-01T00:00:00+07:00"),
    ],
)
def test_unapproved_vectors_and_precomputed_results_are_refused(tmp_path, alteration):
    value = json.loads(prep.synthetic_inputs())
    alteration(value)
    sha = write_inputs(tmp_path, value)
    with pytest.raises(ValueError):
        prep.load_inputs(tmp_path, sha)


def test_unbound_or_changed_input_bytes_are_refused(tmp_path):
    write_inputs(tmp_path)
    with pytest.raises(ValueError):
        prep.load_inputs(tmp_path, "0" * 64)


def test_original_algorithm_is_called_in_request_and_its_cache_must_be_used(tmp_path):
    from edgar_moe.forward.uncertainty import _bootstrap, clustered_rank_ic_interval

    sha = write_inputs(tmp_path)
    _bootstrap.cache_clear()
    prep.prepare(tmp_path, sha)
    before = _bootstrap.cache_info()._asdict()
    interval = clustered_rank_ic_interval(*prep.load_inputs(tmp_path, sha))
    after = _bootstrap.cache_info()._asdict()
    assert interval.status == "ready"
    assert before["misses"] == 1
    assert prep.cache_used({"bootstrap_cache_before": before, "bootstrap_cache_after": after})
    wrong = after | {"misses": after["misses"] + 1}
    assert not prep.cache_used({"bootstrap_cache_before": before, "bootstrap_cache_after": wrong})
    _bootstrap.cache_clear()
