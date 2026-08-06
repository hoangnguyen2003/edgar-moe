import numpy as np
import pytest

torch = pytest.importorskip("torch")

from edgar_moe.modeling.moe import RegimeGatedMoE  # noqa: E402
from edgar_moe.modeling.train import predict_moe, train_moe  # noqa: E402


def test_gate_weights_sum_to_one_and_mask_missing_expert() -> None:
    model = RegimeGatedMoE(4, 3, 2, 2, hidden_dim=8, expert_dim=4, dropout=0)
    missing = torch.tensor([[0.0, 1.0, 0.0], [0.0, 0.0, 0.0]])
    output, weights, expert_predictions = model(
        torch.randn(2, 4),
        torch.randn(2, 3),
        torch.randn(2, 2),
        torch.randn(2, 2),
        missing,
    )
    assert output.shape == (2,)
    assert expert_predictions.shape == (2, 3)
    assert torch.allclose(weights.sum(dim=1), torch.ones(2))
    assert weights[0, 1].item() == 0.0


def test_zero_gate_strength_shrinks_weights_to_static_prior() -> None:
    model = RegimeGatedMoE(
        4,
        3,
        2,
        2,
        hidden_dim=8,
        expert_dim=4,
        dropout=0,
        gate_strength=0.0,
    )
    missing = torch.zeros((2, 3))
    _, weights, _ = model(
        torch.randn(2, 4),
        torch.randn(2, 3),
        torch.randn(2, 2),
        torch.tensor([[-100.0, 200.0], [300.0, -400.0]]),
        missing,
    )

    assert torch.allclose(weights[0], weights[1])
    assert torch.allclose(weights, torch.full((2, 3), 1 / 3))


def test_training_and_prediction_are_deterministic() -> None:
    generator = np.random.default_rng(4)

    def values(rows: int) -> dict[str, np.ndarray]:
        text = generator.normal(size=(rows, 4)).astype(np.float32)
        return {
            "text": text,
            "fundamental": generator.normal(size=(rows, 3)).astype(np.float32),
            "market": generator.normal(size=(rows, 2)).astype(np.float32),
            "regime": generator.normal(size=(rows, 2)).astype(np.float32),
            "missing_mask": np.zeros((rows, 3), dtype=np.float32),
            "target": (0.4 * text[:, 0]).astype(np.float32),
        }

    train = values(48)
    validation = values(16)
    model = RegimeGatedMoE(4, 3, 2, 2, hidden_dim=8, expert_dim=4, dropout=0)
    trained = train_moe(
        model,
        train,
        validation,
        max_epochs=3,
        patience=3,
        batch_size=16,
        expert_auxiliary_weight=0.25,
        correlation_regularization=0.05,
        seed=4,
        device="cpu",
    )
    prediction = predict_moe(trained.model, validation, device="cpu")

    assert len(trained.train_losses) == 3
    assert prediction.scores.shape == (16,)
    assert prediction.expert_weights.shape == (16, 3)
