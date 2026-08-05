from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.preprocessing import StandardScaler


@dataclass
class ModalityTransform:
    medians: np.ndarray
    scaler: StandardScaler


class MultimodalPreprocessor:
    """Training-fold-only median imputation and standardization."""

    def __init__(self) -> None:
        self.transforms: dict[str, ModalityTransform] = {}

    def fit(self, modalities: dict[str, np.ndarray]) -> MultimodalPreprocessor:
        self.transforms = {}
        for name, values in modalities.items():
            values = np.asarray(values, dtype=np.float64)
            medians = np.nanmedian(values, axis=0)
            medians = np.where(np.isfinite(medians), medians, 0.0)
            imputed = np.where(np.isnan(values), medians, values)
            scaler = StandardScaler().fit(imputed)
            self.transforms[name] = ModalityTransform(medians=medians, scaler=scaler)
        return self

    def transform(
        self, modalities: dict[str, np.ndarray]
    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
        if set(modalities) != set(self.transforms):
            raise ValueError("Transform modalities do not match fitted modalities")
        transformed: dict[str, np.ndarray] = {}
        masks: list[np.ndarray] = []
        for name, values in modalities.items():
            transform = self.transforms[name]
            values = np.asarray(values, dtype=np.float64)
            row_missing = np.isnan(values).all(axis=1)
            imputed = np.where(np.isnan(values), transform.medians, values)
            transformed[name] = transform.scaler.transform(imputed).astype(np.float32)
            masks.append(row_missing.astype(np.float32))
        return transformed, np.column_stack(masks).astype(np.float32)

    def fit_transform(
        self, modalities: dict[str, np.ndarray]
    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
        return self.fit(modalities).transform(modalities)
