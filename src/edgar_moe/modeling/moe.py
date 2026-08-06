from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import torch as torch
    from torch import nn
except ImportError:  # pragma: no cover - optional dependency path
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]


if nn is not None:

    class Expert(nn.Module):
        def __init__(
            self, input_dim: int, hidden_dim: int, expert_dim: int, dropout: float
        ) -> None:
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.LayerNorm(hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, expert_dim),
                nn.GELU(),
            )
            self.head = nn.Linear(expert_dim, 1)

        def forward(self, values: Any) -> tuple[Any, Any]:
            state = self.encoder(values)
            return self.head(state).squeeze(-1), state

    class RegimeGatedMoE(nn.Module):
        """Missing-aware learned mixture of text, fundamental, and market experts."""

        modality_names = ("text", "fundamental", "market")

        def __init__(
            self,
            text_dim: int,
            fundamental_dim: int,
            market_dim: int,
            regime_dim: int,
            hidden_dim: int = 64,
            expert_dim: int = 32,
            dropout: float = 0.15,
            gate_strength: float = 1.0,
        ) -> None:
            super().__init__()
            if gate_strength < 0:
                raise ValueError("gate_strength must be non-negative")
            self.dimensions = {
                "text": text_dim,
                "fundamental": fundamental_dim,
                "market": market_dim,
                "regime": regime_dim,
            }
            self.gate_strength = float(gate_strength)
            self.experts = nn.ModuleDict(
                {
                    "text": Expert(text_dim, hidden_dim, expert_dim, dropout),
                    "fundamental": Expert(fundamental_dim, hidden_dim, expert_dim, dropout),
                    "market": Expert(market_dim, hidden_dim, expert_dim, dropout),
                }
            )
            self.static_gate_logits = nn.Parameter(
                torch.zeros(len(self.modality_names), dtype=torch.float32)
            )
            self.regime_gate = nn.Sequential(
                nn.Linear(regime_dim + len(self.modality_names), hidden_dim),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, len(self.modality_names)),
            )

        def forward(
            self,
            text: Any,
            fundamental: Any,
            market: Any,
            regime: Any,
            missing_mask: Any,
        ) -> tuple[Any, Any, Any]:
            inputs = {"text": text, "fundamental": fundamental, "market": market}
            predictions = []
            states = []
            for name in self.modality_names:
                prediction, state = self.experts[name](inputs[name])
                predictions.append(prediction)
                states.append(state)
            expert_predictions = torch.stack(predictions, dim=1)
            residual_logits = self.regime_gate(torch.cat([regime, missing_mask], dim=1))
            gate_logits = self.static_gate_logits.unsqueeze(0) + (
                self.gate_strength * residual_logits
            )
            gate_logits = gate_logits.masked_fill(missing_mask.bool(), -1e9)
            all_missing = missing_mask.bool().all(dim=1)
            if all_missing.any():
                gate_logits = torch.where(
                    all_missing.unsqueeze(1), torch.zeros_like(gate_logits), gate_logits
                )
            weights = torch.softmax(gate_logits, dim=1)
            output = (expert_predictions * weights).sum(dim=1)
            return output, weights, expert_predictions

        def export_config(self) -> dict[str, Any]:
            return {**self.dimensions, "gate_strength": self.gate_strength}

else:

    class RegimeGatedMoE:  # type: ignore[no-redef]
        def __init__(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("Install research dependencies with `uv sync --extra research`")


@dataclass(frozen=True)
class MoEPrediction:
    scores: np.ndarray
    expert_weights: np.ndarray
    expert_predictions: np.ndarray
