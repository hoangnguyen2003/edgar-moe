from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from edgar_moe.data.storage import sha256_file


class FrozenModelSpec(BaseModel):
    """Reviewed identities for the one immutable model allowed in prospective scoring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    training_dataset_id: str
    training_dataset_dir: Path
    model_path: Path
    locked_result_path: Path
    artifact_sha256: str = Field(min_length=64, max_length=64)
    selection_hash: str = Field(min_length=64, max_length=64)
    locked_test_hash: str = Field(min_length=64, max_length=64)
    frozen_at: datetime

    @field_validator("frozen_at")
    @classmethod
    def normalize_frozen_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("frozen_at must be timezone-aware")
        return value.astimezone(UTC)

    @classmethod
    def from_yaml(cls, path: str | Path) -> FrozenModelSpec:
        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream) or {}
        return cls.model_validate(payload)

    @property
    def config_hash(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z)
        ).hexdigest()

    def verify_locked_evidence(self) -> dict[str, Any]:
        if sha256_file(self.model_path) != self.artifact_sha256:
            raise ValueError("Frozen model artifact SHA-256 does not match the reviewed spec")
        payload = orjson.loads(self.locked_result_path.read_bytes())
        if not isinstance(payload, dict):
            raise ValueError("Locked result must contain a JSON object")
        unsigned = dict(payload)
        stored_locked_hash = str(unsigned.pop("locked_test_hash", ""))
        computed_locked_hash = hashlib.sha256(
            orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)
        ).hexdigest()
        if stored_locked_hash != self.locked_test_hash or computed_locked_hash != self.locked_test_hash:
            raise ValueError("Locked-test hash verification failed")
        expected = {
            "dataset_id": self.training_dataset_id,
            "selection_hash": self.selection_hash,
            "model_sha256": self.artifact_sha256,
        }
        for name, value in expected.items():
            if str(payload.get(name, "")) != value:
                raise ValueError(f"Locked result has an unexpected {name}")
        return payload
