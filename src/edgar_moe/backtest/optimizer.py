from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class AllocationDiagnostics:
    gross_exposure: float
    net_exposure: float
    beta_exposure: float
    maximum_industry_exposure: float
    solver: str


def _candidate_target(
    candidates: pd.DataFrame,
    gross_exposure: float,
    long_quantile: float,
    short_quantile: float,
    maximum_name_weight: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    scores = candidates["score"].to_numpy(dtype=float)
    desired_tail = max(1, int(np.ceil(len(scores) * max(1 - long_quantile, short_quantile))))
    minimum_tail = int(np.ceil((gross_exposure / 2) / maximum_name_weight))
    tail_size = min(max(desired_tail, minimum_tail), len(scores) // 2)
    order = np.argsort(scores, kind="stable")
    short_indices = order[:tail_size]
    long_indices = order[-tail_size:]
    if not len(long_indices) or not len(short_indices):
        return np.zeros(len(scores)), long_indices, short_indices
    feasible_gross = min(gross_exposure, 2 * maximum_name_weight * tail_size)
    target = np.zeros(len(scores), dtype=float)
    long_cutoff = scores[long_indices].min()
    short_cutoff = scores[short_indices].max()
    long_strength = np.maximum(scores[long_indices] - long_cutoff, 1e-3)
    short_strength = np.maximum(short_cutoff - scores[short_indices], 1e-3)
    target[long_indices] = feasible_gross * 0.5 * long_strength / long_strength.sum()
    target[short_indices] = -feasible_gross * 0.5 * short_strength / short_strength.sum()
    return target, long_indices, short_indices


def allocate_neutral(
    candidates: pd.DataFrame,
    *,
    gross_exposure: float = 1.0,
    maximum_net_exposure: float = 0.02,
    maximum_beta_exposure: float = 0.05,
    maximum_industry_exposure: float = 0.05,
    maximum_name_weight: float = 0.02,
    long_quantile: float = 0.9,
    short_quantile: float = 0.1,
) -> tuple[pd.Series, AllocationDiagnostics]:
    """Project signal weights onto market-neutral portfolio constraints."""
    required = {"security_id", "score", "beta", "industry_code"}
    missing = required.difference(candidates.columns)
    if missing:
        raise ValueError(f"Missing candidate columns: {sorted(missing)}")
    if candidates["security_id"].duplicated().any():
        raise ValueError("Candidates must contain at most one row per security")
    target, long_indices, short_indices = _candidate_target(
        candidates, gross_exposure, long_quantile, short_quantile, maximum_name_weight
    )
    if not len(long_indices) or not len(short_indices):
        empty = pd.Series(np.zeros(len(candidates)), index=candidates.index, name="weight")
        return empty, _diagnostics(candidates, empty.to_numpy(), "empty")

    weights: np.ndarray
    solver_name = "heuristic"
    feasible_gross = min(
        gross_exposure, 2 * maximum_name_weight * min(len(long_indices), len(short_indices))
    )
    try:
        import cvxpy as cp
    except ImportError:
        weights = _heuristic_projection(
            candidates,
            target,
            long_indices,
            short_indices,
            feasible_gross,
            maximum_name_weight,
        )
    else:
        cvx: Any = cp
        try:
            variable = cvx.Variable(len(candidates))
            constraints: list[object] = [
                variable[long_indices] >= 0,
                variable[short_indices] <= 0,
                cvx.sum(variable[long_indices]) == feasible_gross / 2,
                cvx.sum(variable[short_indices]) == -feasible_gross / 2,
                variable <= maximum_name_weight,
                variable >= -maximum_name_weight,
                cvx.abs(cvx.sum(variable)) <= maximum_net_exposure,
                cvx.abs(variable @ candidates["beta"].to_numpy(dtype=float))
                <= maximum_beta_exposure,
            ]
            inactive = np.setdiff1d(np.arange(len(candidates)), np.r_[long_indices, short_indices])
            if len(inactive):
                constraints.append(variable[inactive] == 0)
            for industry in sorted(candidates["industry_code"].astype(str).unique()):
                mask = np.flatnonzero(candidates["industry_code"].astype(str).to_numpy() == industry)
                constraints.append(cvx.abs(cvx.sum(variable[mask])) <= maximum_industry_exposure)
            objective = cvx.Minimize(cvx.sum_squares(variable - target))
            problem = cvx.Problem(objective, constraints)
            problem.solve(solver=cvx.CLARABEL, verbose=False)
            if variable.value is None or problem.status not in {cvx.OPTIMAL, cvx.OPTIMAL_INACCURATE}:
                raise RuntimeError(f"Portfolio optimization failed: {problem.status}")
            weights = np.asarray(variable.value).reshape(-1)
            solver_name = "cvxpy-clarabel"
        except (RuntimeError, cvx.error.SolverError):
            weights = _heuristic_projection(
                candidates,
                target,
                long_indices,
                short_indices,
                feasible_gross,
                maximum_name_weight,
            )

    result = pd.Series(weights, index=candidates.index, name="weight")
    return result, _diagnostics(candidates, weights, solver_name)


def _heuristic_projection(
    candidates: pd.DataFrame,
    target: np.ndarray,
    long_indices: np.ndarray,
    short_indices: np.ndarray,
    gross_exposure: float,
    maximum_name_weight: float,
) -> np.ndarray:
    weights = np.clip(target, -maximum_name_weight, maximum_name_weight)
    beta = candidates["beta"].to_numpy(dtype=float)
    active = np.r_[long_indices, short_indices]
    if len(active) >= 2:
        centered_beta = beta[active] - beta[active].mean()
        denominator = float(np.dot(centered_beta, centered_beta))
        if denominator > 1e-12:
            correction = float(np.dot(weights[active], beta[active])) / denominator
            weights[active] -= correction * centered_beta
    weights[long_indices] = np.maximum(weights[long_indices], 0)
    weights[short_indices] = np.minimum(weights[short_indices], 0)
    long_sum = weights[long_indices].sum()
    short_sum = -weights[short_indices].sum()
    if long_sum:
        weights[long_indices] *= (gross_exposure / 2) / long_sum
    if short_sum:
        weights[short_indices] *= (gross_exposure / 2) / short_sum
    clipped = np.clip(weights, -maximum_name_weight, maximum_name_weight)
    return cast(np.ndarray, clipped)


def _diagnostics(
    candidates: pd.DataFrame, weights: np.ndarray, solver: str
) -> AllocationDiagnostics:
    industries = candidates["industry_code"].astype(str).to_numpy()
    industry_exposures = [
        abs(float(weights[industries == industry].sum())) for industry in np.unique(industries)
    ]
    return AllocationDiagnostics(
        gross_exposure=float(np.abs(weights).sum()),
        net_exposure=float(weights.sum()),
        beta_exposure=float(np.dot(weights, candidates["beta"].to_numpy(dtype=float))),
        maximum_industry_exposure=max(industry_exposures, default=0.0),
        solver=solver,
    )
