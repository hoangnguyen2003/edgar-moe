"""Public synthetic inputs for the frozen uncertainty cache, never DB results.

Only input vectors are packaged. The original 1,000-draw algorithm still runs
inside the measured HTTP request, before either database connection is opened.
The real response is subsequently built and validated by the unchanged snapshot
kernel. This avoids holding a read transaction idle during CPU-only work.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROFILE = "portfolio290_500_issuers_20_quarters_half_labeled_v1"
PAYLOAD_SHA256 = "d6d852a89cf6221704b045775e85134cba94d3c0871677e8a5321d39e3562f7d"


def synthetic_inputs() -> bytes:
    """Materialize invented floats once on the same host as fixture generation.

    Serialize exact floats rather than recomputing libm sin/cos on Linux, whose
    last bits may differ. Order matches accepted_at, forecast_id in the frozen
    registry's joined-pair SELECT. No database or network access occurs here.
    """
    ordered = []
    for quarter in range(20):
        start = datetime(2019 + quarter // 4, 1 + 3 * (quarter % 4), 1, tzinfo=UTC)
        for issuer in range(0, 500, 2):
            index = quarter * 500 + issuer
            accepted = start + timedelta(days=issuer % 20)
            ordered.append(
                (accepted, index, math.sin(index * 0.17) * 0.1, math.cos(index * 0.23) * 0.1)
            )
    pairs = [[score, label, accepted.isoformat()] for accepted, _, score, label in sorted(ordered)]
    return json.dumps(
        {"profile": PROFILE, "fixture_payload_sha256": PAYLOAD_SHA256, "pairs": pairs},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def load_inputs(
    bundle: Path, expected_sha256: str
) -> tuple[list[float], list[float], list[datetime]]:
    path = bundle / "study-preparation.json"
    if path.is_symlink() or path.stat().st_size > 1_048_576:
        raise ValueError("preparation_refused")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("preparation_refused")
    value = json.loads(raw)
    if (
        set(value) != {"profile", "fixture_payload_sha256", "pairs"}
        or value["profile"] != PROFILE
        or value["fixture_payload_sha256"] != PAYLOAD_SHA256
        or not isinstance(value["pairs"], list)
        or len(value["pairs"]) != 5000
    ):
        raise ValueError("preparation_refused")
    scores, labels, accepted = [], [], []
    for pair in value["pairs"]:
        if not isinstance(pair, list) or len(pair) != 3:
            raise ValueError("preparation_refused")
        score, label, stamp = pair
        if (
            type(score) not in (int, float)
            or type(label) not in (int, float)
            or not math.isfinite(score)
            or not math.isfinite(label)
            or not isinstance(stamp, str)
        ):
            raise ValueError("preparation_refused")
        time = datetime.fromisoformat(stamp)
        if time.utcoffset() != timedelta(0) or not 2019 <= time.year <= 2023:
            raise ValueError("preparation_refused")
        scores.append(float(score))
        labels.append(float(label))
        accepted.append(time)
    return scores, labels, accepted


def prepare(bundle: Path, expected_sha256: str) -> None:
    from edgar_moe.forward.uncertainty import clustered_rank_ic_interval

    # No cached final-result artifact and no calculation during deployment.
    interval = clustered_rank_ic_interval(*load_inputs(bundle, expected_sha256))
    if interval.status != "ready" or interval.resamples != 1000:
        raise ValueError("preparation_refused")


def cache_used(result: dict[str, Any]) -> bool:
    before, after = result["bootstrap_cache_before"], result["bootstrap_cache_after"]
    return after["misses"] == before["misses"] and after["hits"] == before["hits"] + 1
