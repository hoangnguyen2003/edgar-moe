import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/locked_rank_ic_interval.py"
SPEC = importlib.util.spec_from_file_location("locked_rank_ic_interval", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
interval = MODULE.interval
month_blocks = MODULE.month_blocks
rank_ic = MODULE.rank_ic
resample_month_indices = MODULE.resample_month_indices


def test_month_blocks_keep_contemporaneous_filings_together_and_empty_months() -> None:
    months = np.array(["2025-01", "2025-01", "2025-03", "2025-04"], dtype="datetime64[M]")
    blocks = month_blocks(months)
    assert [block.tolist() for block in blocks] == [[0, 1], [], [2], [3]]
    sampled = resample_month_indices(blocks, block_months=2, rng=np.random.default_rng(5))
    assert len(sampled) > 0
    assert np.count_nonzero(sampled == 0) == np.count_nonzero(sampled == 1)


def test_interval_is_seeded_and_uses_original_fold_weights() -> None:
    first = (
        np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        np.array([1.0, 3.0, 2.0, 4.0, 6.0, 5.0]),
        np.array(
            ["2023-01", "2023-01", "2023-02", "2023-03", "2023-04", "2023-04"],
            dtype="datetime64[M]",
        ),
    )
    second = (
        np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]),
        np.array([7.0, 8.0, 5.0, 6.0, 3.0, 4.0, 1.0, 2.0]),
        np.array(
            [
                "2024-01",
                "2024-01",
                "2024-02",
                "2024-02",
                "2024-03",
                "2024-03",
                "2024-04",
                "2024-04",
            ],
            dtype="datetime64[M]",
        ),
    )
    a = interval([first, second], block_months=2, samples=100, seed=123)
    b = interval([first, second], block_months=2, samples=100, seed=123)
    assert a == b
    assert a["rank_ic"] == pytest.approx((6 * rank_ic(*first[:2]) + 8 * rank_ic(*second[:2])) / 14)
    assert a["events"] == 14
    assert a["calendar_months_per_cohort"] == [4, 4]
    assert a["ci_low"] <= a["ci_high"]


def test_rejects_invalid_or_undefined_inputs() -> None:
    with pytest.raises(ValueError, match="finite"):
        rank_ic(np.array([1.0, np.nan, 3.0]), np.array([1.0, 2.0, 3.0]))
    with pytest.raises(ValueError, match="constant"):
        rank_ic(np.array([1.0, 1.0, 1.0]), np.array([1.0, 2.0, 3.0]))
    with pytest.raises(ValueError, match="block length"):
        resample_month_indices([np.array([0])], block_months=2, rng=np.random.default_rng(0))
