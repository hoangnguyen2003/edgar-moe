from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from edgar_moe.features.dataset import ResearchDataset, dataset_xbrl_fact_policy
from edgar_moe.features.point_in_time import audit_feature_availability
from edgar_moe.forward.domain import ForecastDraft, QualityCheckDraft
from edgar_moe.forward.metrics import percentile_ranks
from edgar_moe.modeling.experiment import ArrayTransform
from edgar_moe.modeling.moe import RegimeGatedMoE, torch
from edgar_moe.modeling.preprocess import ModalityTransform, MultimodalPreprocessor
from edgar_moe.modeling.train import predict_moe
from edgar_moe.settings import LEGACY_XBRL_FACT_POLICY

MODALITY_NAMES = ("text", "fundamental", "market")


class ForecastQualityError(ValueError):
    """A blocking quality gate failed; its checks are kept for the failed run."""

    def __init__(self, message: str, *, checks: list[QualityCheckDraft]) -> None:
        super().__init__(message)
        self.checks = checks


@dataclass(frozen=True)
class ForecastBatch:
    forecasts: list[ForecastDraft]
    checks: list[QualityCheckDraft]
    candidate_indices: np.ndarray


class FrozenPredictor:
    """Inference-only loader for a hash-pinned frozen EDGAR-MoE artifact."""

    def __init__(
        self,
        *,
        model: RegimeGatedMoE,
        preprocessor: MultimodalPreprocessor,
        regime_transform: ArrayTransform,
        target_mean: float,
        target_std: float,
        champion_family: str,
        champion_parameters: dict[str, float | int],
        fundamental_coef: np.ndarray | None,
        fundamental_intercept: float | None,
        selection_hash: str,
        artifact_sha256: str,
        xbrl_fact_policy: str = LEGACY_XBRL_FACT_POLICY,
    ) -> None:
        self.model = model
        self.preprocessor = preprocessor
        self.regime_transform = regime_transform
        self.target_mean = target_mean
        self.target_std = target_std
        self.champion_family = champion_family
        self.champion_parameters = champion_parameters
        self.fundamental_coef = fundamental_coef
        self.fundamental_intercept = fundamental_intercept
        self.selection_hash = selection_hash
        self.artifact_sha256 = artifact_sha256
        # The XBRL fact policy of the model's training data; artifacts that
        # predate the field (including frozen v1) were trained on legacy_v1.
        self.xbrl_fact_policy = xbrl_fact_policy

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        expected_sha256: str,
        expected_selection_hash: str,
        device: str = "cpu",
    ) -> FrozenPredictor:
        if torch is None:
            raise RuntimeError("Install research dependencies with `uv sync --extra research`")
        # Hash and unpickle the same in-memory bytes so the file cannot change
        # between verification and loading.
        content = Path(path).read_bytes()
        observed_hash = hashlib.sha256(content).hexdigest()
        if observed_hash != expected_sha256:
            raise ValueError("Refusing to load a frozen model with an unexpected SHA-256")
        # The reviewed payload stores numpy arrays and scikit-learn comparison
        # models, which weights_only=True cannot restore; the SHA-256 pin above
        # is the trust boundary.
        payload = torch.load(io.BytesIO(content), map_location=device, weights_only=False)
        if not isinstance(payload, dict):
            raise ValueError("Frozen model artifact must contain a mapping")
        if str(payload.get("selection_hash", "")) != expected_selection_hash:
            raise ValueError("Frozen model selection hash does not match the reviewed selection")

        state = _mapping(payload, "moe_state_dict")
        model_config = _mapping(payload, "moe_config")
        hidden_dim = int(_tensor_shape(state, "experts.text.encoder.0.weight")[0])
        expert_dim = int(_tensor_shape(state, "experts.text.encoder.4.weight")[0])
        model = RegimeGatedMoE(
            text_dim=int(model_config["text"]),
            fundamental_dim=int(model_config["fundamental"]),
            market_dim=int(model_config["market"]),
            regime_dim=int(model_config["regime"]),
            hidden_dim=hidden_dim,
            expert_dim=expert_dim,
            dropout=0.0,
            gate_strength=float(model_config["gate_strength"]),
        )
        model.load_state_dict(state)
        model.to(device).eval()

        preprocessor_payload = _mapping(payload, "preprocessor")
        preprocessor = MultimodalPreprocessor()
        preprocessor.transforms = {
            name: ModalityTransform(
                medians=_array(_mapping(preprocessor_payload, name)["medians"]),
                scaler=_restore_scaler(_mapping(preprocessor_payload, name)),
            )
            for name in MODALITY_NAMES
        }
        regime_payload = _mapping(payload, "regime")
        regime_transform = ArrayTransform(
            medians=_array(regime_payload["medians"]),
            scaler=_restore_scaler(regime_payload),
        )
        family = str(payload.get("champion_family", ""))
        if family not in {"anchored_multimodal", "multimodal"}:
            raise ValueError(f"Frozen predictor does not support champion family: {family}")
        raw_parameters = _mapping(payload, "champion_parameters")
        parameters = {
            str(name): value
            for name, value in raw_parameters.items()
            if isinstance(value, (int, float))
        }
        fundamental_payload = payload.get("fundamental_elastic_net")
        coef: np.ndarray | None = None
        intercept: float | None = None
        if isinstance(fundamental_payload, dict):
            coef = _array(fundamental_payload["coef"])
            intercept = float(fundamental_payload["intercept"])
        if family == "anchored_multimodal" and (coef is None or intercept is None):
            raise ValueError("Anchored frozen model is missing its fundamental expert")
        return cls(
            model=model,
            preprocessor=preprocessor,
            regime_transform=regime_transform,
            target_mean=float(payload["target_mean"]),
            target_std=float(payload["target_std"]),
            champion_family=family,
            champion_parameters=parameters,
            fundamental_coef=coef,
            fundamental_intercept=intercept,
            selection_hash=expected_selection_hash,
            artifact_sha256=observed_hash,
            xbrl_fact_policy=str(payload.get("xbrl_fact_policy", LEGACY_XBRL_FACT_POLICY)),
        )

    def forecast(
        self,
        dataset: ResearchDataset,
        *,
        as_of: datetime,
        device: str = "cpu",
    ) -> ForecastBatch:
        forecast_as_of = _aware_utc(as_of)
        self._require_feature_policy(dataset)
        candidate_indices = _candidate_indices(dataset, as_of=forecast_as_of)
        violations = audit_feature_availability(dataset.availability)
        checks = [
            QualityCheckDraft(
                name="point_in_time_availability",
                status="passed" if not violations else "failed",
                observed_value=float(len(violations)),
                threshold=0.0,
                details={"candidate_events": int(len(candidate_indices))},
            ),
            QualityCheckDraft(
                name="prospective_candidate_count",
                status="passed" if len(candidate_indices) else "warning",
                observed_value=float(len(candidate_indices)),
                threshold=1.0,
                details={
                    "rule": "accepted_at <= forecast_as_of < entry_at < horizon_at",
                },
            ),
        ]
        if violations:
            preview = ", ".join(f"{item.event_id}:{item.feature_name}" for item in violations[:5])
            # Carry the failed check so the failed run records why it failed.
            raise ForecastQualityError(
                f"Point-in-time availability audit failed ({len(violations)} rows): {preview}",
                checks=checks,
            )
        if not len(candidate_indices):
            return ForecastBatch(forecasts=[], checks=checks, candidate_indices=candidate_indices)

        modalities = {
            name: np.asarray(dataset.modalities[name][candidate_indices]) for name in MODALITY_NAMES
        }
        self._verify_dimensions(modalities, dataset.regime[candidate_indices])
        transformed, missing_mask = self.preprocessor.transform(modalities)
        values = {
            **transformed,
            "regime": self.regime_transform.transform(dataset.regime[candidate_indices]),
            "missing_mask": missing_mask,
        }
        prediction = predict_moe(self.model, values, device=device)
        moe_scores = prediction.scores * self.target_std + self.target_mean
        expert_predictions = prediction.expert_predictions * self.target_std + self.target_mean
        fundamental_scores: np.ndarray | None = None
        if self.fundamental_coef is not None and self.fundamental_intercept is not None:
            fundamental_scores = (
                values["fundamental"] @ self.fundamental_coef + self.fundamental_intercept
            )
        if self.champion_family == "anchored_multimodal":
            if fundamental_scores is None:
                raise AssertionError("Anchored model is missing fundamental predictions")
            anchor_weight = float(self.champion_parameters["fundamental_anchor_weight"])
            residual_weight = float(self.champion_parameters["moe_residual_weight"])
            if not np.isclose(anchor_weight + residual_weight, 1.0):
                raise ValueError("Frozen anchor and residual weights do not sum to one")
            scores = anchor_weight * fundamental_scores + residual_weight * moe_scores
        else:
            scores = moe_scores
        ranks = percentile_ranks(scores.tolist())
        rows = dataset.events.iloc[candidate_indices]
        forecasts = [
            ForecastDraft(
                event_id=str(row.event_id),
                accession_number=str(row.accession_number),
                security_id=str(row.security_id),
                ticker=str(row.ticker),
                company_name=str(row.company_name),
                form=str(row.form),
                accepted_at=_timestamp(row.accepted_at),
                entry_at=_timestamp(row.entry_at),
                entry_date=pd.Timestamp(row.entry_date).date(),
                horizon_at=_timestamp(row.horizon_at),
                industry_code=str(row.industry_code),
                score=float(scores[position]),
                rank=float(ranks[position]),
                fundamental_score=(
                    float(fundamental_scores[position]) if fundamental_scores is not None else None
                ),
                expert_weights={
                    name: float(prediction.expert_weights[position, expert_index])
                    for expert_index, name in enumerate(MODALITY_NAMES)
                },
                expert_predictions={
                    name: float(expert_predictions[position, expert_index])
                    for expert_index, name in enumerate(MODALITY_NAMES)
                },
            )
            for position, row in enumerate(rows.itertuples(index=False))
        ]
        return ForecastBatch(
            forecasts=forecasts,
            checks=checks,
            candidate_indices=candidate_indices,
        )

    def component_outputs(
        self,
        dataset: ResearchDataset,
        *,
        device: str = "cpu",
    ) -> dict[str, np.ndarray]:
        """Score every feature row for research-drift inspection only.

        This path is intentionally separate from :meth:`forecast`: it does not
        select tradable candidates, register a run, read targets, or write
        evidence. It reuses the hash-pinned v1 preprocessing and model so drift
        reports can compare component behavior without changing the frozen
        artifact or opening the locked test.
        """
        self._require_feature_policy(dataset)
        modalities = {name: np.asarray(dataset.modalities[name]) for name in MODALITY_NAMES}
        if any(len(values) != len(dataset.events) for values in modalities.values()) or len(
            dataset.regime
        ) != len(dataset.events):
            raise ValueError("Dataset feature rows must match the event row count")
        self._verify_dimensions(modalities, dataset.regime)
        violations = audit_feature_availability(dataset.availability)
        if violations:
            preview = ", ".join(f"{item.event_id}:{item.feature_name}" for item in violations[:5])
            raise ValueError(
                f"Point-in-time availability audit failed ({len(violations)} rows): {preview}"
            )
        transformed, missing_mask = self.preprocessor.transform(modalities)
        values = {
            **transformed,
            "regime": self.regime_transform.transform(dataset.regime),
            "missing_mask": missing_mask,
        }
        prediction = predict_moe(self.model, values, device=device)
        moe_scores = prediction.scores * self.target_std + self.target_mean
        outputs: dict[str, np.ndarray] = {"moe_score": np.asarray(moe_scores, dtype=np.float64)}
        for expert_index, name in enumerate(MODALITY_NAMES):
            outputs[f"expert_weight:{name}"] = np.asarray(
                prediction.expert_weights[:, expert_index], dtype=np.float64
            )
            outputs[f"expert_prediction:{name}"] = np.asarray(
                prediction.expert_predictions[:, expert_index] * self.target_std + self.target_mean,
                dtype=np.float64,
            )
        if self.fundamental_coef is not None and self.fundamental_intercept is not None:
            fundamental_scores = np.asarray(
                values["fundamental"] @ self.fundamental_coef + self.fundamental_intercept,
                dtype=np.float64,
            )
            outputs["fundamental_score"] = fundamental_scores
        else:
            fundamental_scores = None
        if self.champion_family == "anchored_multimodal":
            if fundamental_scores is None:
                raise AssertionError("Anchored model is missing fundamental predictions")
            anchor_weight = float(self.champion_parameters["fundamental_anchor_weight"])
            residual_weight = float(self.champion_parameters["moe_residual_weight"])
            if not np.isclose(anchor_weight + residual_weight, 1.0):
                raise ValueError("Frozen anchor and residual weights do not sum to one")
            final_scores = anchor_weight * fundamental_scores + residual_weight * moe_scores
        else:
            final_scores = moe_scores
        outputs["final_score"] = np.asarray(final_scores, dtype=np.float64)
        return outputs

    def _require_feature_policy(self, dataset: ResearchDataset) -> None:
        """Refuse features built differently from the model's training data."""
        observed = dataset_xbrl_fact_policy(dataset)
        if observed != self.xbrl_fact_policy:
            raise ValueError(
                f"Dataset XBRL fact policy {observed!r} does not match the frozen model's "
                f"{self.xbrl_fact_policy!r}; rebuild the dataset with the model's policy"
            )

    def _verify_dimensions(
        self,
        modalities: dict[str, np.ndarray],
        regime: np.ndarray,
    ) -> None:
        expected = self.model.dimensions
        observed = {name: int(values.shape[1]) for name, values in modalities.items()}
        observed["regime"] = int(regime.shape[1])
        if observed != expected:
            raise ValueError(
                f"Dataset/model feature dimensions differ: observed={observed}, expected={expected}"
            )


def _candidate_indices(dataset: ResearchDataset, *, as_of: datetime) -> np.ndarray:
    events = dataset.events
    required = {"accepted_at", "entry_at", "horizon_at"}
    missing = required.difference(events.columns)
    if missing:
        raise ValueError(f"Dataset events are missing prospective timestamps: {sorted(missing)}")
    dataset_end = datetime.combine(dataset.as_of + timedelta(days=1), time.min, tzinfo=UTC)
    available_through = min(as_of, dataset_end)
    accepted = pd.to_datetime(events["accepted_at"], utc=True)
    entry = pd.to_datetime(events["entry_at"], utc=True)
    horizon = pd.to_datetime(events["horizon_at"], utc=True)
    mask = (accepted <= available_through) & (entry > as_of) & (horizon > entry)
    return np.flatnonzero(mask.to_numpy())


def _restore_scaler(payload: dict[str, Any]) -> StandardScaler:
    mean = _array(payload["mean"])
    scale = _array(payload["scale"])
    if mean.shape != scale.shape:
        raise ValueError("Frozen scaler mean and scale have different shapes")
    scaler = StandardScaler()
    scaler.mean_ = mean
    scaler.scale_ = scale
    scaler.var_ = scale**2
    scaler.n_features_in_ = int(mean.shape[0])
    scaler.n_samples_seen_ = 1
    return scaler


def _mapping(payload: dict[str, Any], name: str) -> dict[str, Any]:
    value = payload.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"Frozen model is missing mapping: {name}")
    return value


def _tensor_shape(state: dict[str, Any], name: str) -> tuple[int, ...]:
    value = state.get(name)
    if value is None or not hasattr(value, "shape"):
        raise ValueError(f"Frozen model state is missing tensor: {name}")
    return tuple(int(size) for size in value.shape)


def _array(value: Any) -> np.ndarray:
    return np.asarray(value, dtype=np.float64)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Forecast as-of must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return cast(datetime, timestamp.tz_convert("UTC").to_pydatetime())
