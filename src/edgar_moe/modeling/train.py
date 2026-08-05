from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any

import numpy as np

from edgar_moe.modeling.moe import MoEPrediction, RegimeGatedMoE, torch


@dataclass
class TrainingResult:
    model: RegimeGatedMoE
    train_losses: list[float]
    validation_losses: list[float]
    best_epoch: int


def set_deterministic_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    if torch is not None:
        torch.manual_seed(seed)
        if torch.backends.mps.is_available():
            torch.mps.manual_seed(seed)


def _tensor(values: np.ndarray, device: str) -> Any:
    if torch is None:
        raise RuntimeError("Install research dependencies with `uv sync --extra research`")
    return torch.as_tensor(values, dtype=torch.float32, device=device)


def train_moe(
    model: RegimeGatedMoE,
    train: dict[str, np.ndarray],
    validation: dict[str, np.ndarray],
    *,
    learning_rate: float = 1e-3,
    weight_decay: float = 1e-4,
    batch_size: int = 256,
    max_epochs: int = 80,
    patience: int = 10,
    seed: int = 42,
    device: str | None = None,
) -> TrainingResult:
    if torch is None:
        raise RuntimeError("Install research dependencies with `uv sync --extra research`")
    set_deterministic_seed(seed)
    if device is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = model.to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay
    )
    loss_function = torch.nn.HuberLoss(delta=1.0)
    row_count = len(train["target"])
    train_losses: list[float] = []
    validation_losses: list[float] = []
    best_loss = float("inf")
    best_epoch = 0
    best_state: dict[str, Any] | None = None
    stale_epochs = 0

    for epoch in range(max_epochs):
        model.train()
        generator = np.random.default_rng(seed + epoch)
        order = generator.permutation(row_count)
        epoch_losses: list[float] = []
        for start in range(0, row_count, batch_size):
            indices = order[start : start + batch_size]
            optimizer.zero_grad(set_to_none=True)
            output, weights, _ = model(
                _tensor(train["text"][indices], device),
                _tensor(train["fundamental"][indices], device),
                _tensor(train["market"][indices], device),
                _tensor(train["regime"][indices], device),
                _tensor(train["missing_mask"][indices], device),
            )
            target = _tensor(train["target"][indices], device)
            prediction_loss = loss_function(output, target)
            # Mild entropy regularization prevents gate collapse while preserving specialization.
            entropy = -(weights.clamp_min(1e-8) * weights.clamp_min(1e-8).log()).sum(dim=1).mean()
            loss = prediction_loss - 0.002 * entropy
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            optimizer.step()
            epoch_losses.append(float(prediction_loss.detach().cpu()))
        train_losses.append(float(np.mean(epoch_losses)))

        model.eval()
        with torch.inference_mode():
            validation_output, _, _ = model(
                _tensor(validation["text"], device),
                _tensor(validation["fundamental"], device),
                _tensor(validation["market"], device),
                _tensor(validation["regime"], device),
                _tensor(validation["missing_mask"], device),
            )
            validation_loss = float(
                loss_function(validation_output, _tensor(validation["target"], device)).cpu()
            )
        validation_losses.append(validation_loss)
        if validation_loss < best_loss - 1e-5:
            best_loss = validation_loss
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return TrainingResult(model, train_losses, validation_losses, best_epoch)


def predict_moe(
    model: RegimeGatedMoE, values: dict[str, np.ndarray], device: str | None = None
) -> MoEPrediction:
    if torch is None:
        raise RuntimeError("Install research dependencies with `uv sync --extra research`")
    if device is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = model.to(device).eval()
    with torch.inference_mode():
        output, weights, expert_predictions = model(
            _tensor(values["text"], device),
            _tensor(values["fundamental"], device),
            _tensor(values["market"], device),
            _tensor(values["regime"], device),
            _tensor(values["missing_mask"], device),
        )
    return MoEPrediction(
        scores=output.cpu().numpy(),
        expert_weights=weights.cpu().numpy(),
        expert_predictions=expert_predictions.cpu().numpy(),
    )
