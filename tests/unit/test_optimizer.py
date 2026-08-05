import builtins

import numpy as np
import pandas as pd

from edgar_moe.backtest.optimizer import allocate_neutral


def test_allocator_produces_capped_balanced_book() -> None:
    rows = 120
    frame = pd.DataFrame(
        {
            "security_id": [f"s{index}" for index in range(rows)],
            "score": np.linspace(-1, 1, rows),
            "beta": 1 + 0.1 * np.sin(np.arange(rows)),
            "industry_code": [str(index % 10) for index in range(rows)],
        }
    )
    weights, diagnostics = allocate_neutral(frame)
    assert np.abs(weights).max() <= 0.020001
    assert abs(weights.sum()) <= 0.020001
    assert diagnostics.gross_exposure > 0.95
    assert abs(diagnostics.beta_exposure) <= 0.051


def test_allocator_falls_back_when_cvxpy_is_unavailable(monkeypatch) -> None:
    frame = pd.DataFrame(
        {
            "security_id": [f"s{index}" for index in range(60)],
            "score": np.linspace(-1, 1, 60),
            "beta": np.ones(60),
            "industry_code": [str(index % 10) for index in range(60)],
        }
    )
    original_import = builtins.__import__

    def without_cvxpy(name, *args, **kwargs):
        if name == "cvxpy":
            raise ImportError("simulated optional dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_cvxpy)
    weights, diagnostics = allocate_neutral(frame)

    assert diagnostics.solver == "heuristic"
    assert np.abs(weights).max() <= 0.020001
    assert abs(weights.sum()) <= 0.020001
