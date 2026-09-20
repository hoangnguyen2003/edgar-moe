from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DataConfig(BaseModel):
    start_date: str = "2016-01-01"
    universe_size: int = 1000
    minimum_price: float = 5.0
    minimum_history_sessions: int = 252
    liquidity_lookback_sessions: int = 60
    sec_forms: list[str] = Field(default_factory=lambda: ["10-K", "10-Q"])
    sec_requests_per_second: int = 8
    mapping_confidence_threshold: float = 0.85


class FeatureConfig(BaseModel):
    text_sections: list[str] = Field(
        default_factory=lambda: ["risk_factors", "management_discussion"]
    )
    momentum_windows: list[int] = Field(default_factory=lambda: [5, 21, 63, 126, 252])
    volatility_windows: list[int] = Field(default_factory=lambda: [21, 63])
    beta_window: int = 252
    embedding_model: str = "ProsusAI/finbert"
    embedding_chunk_tokens: int = 510
    embedding_max_chunks: int = 12


class ModelConfig(BaseModel):
    hidden_dim: int = 64
    expert_dim: int = 32
    dropout: float = 0.15
    gate_strength: float = 0.25
    candidate_hidden_dims: list[int] = Field(default_factory=lambda: [32, 64])
    candidate_dropouts: list[float] = Field(default_factory=lambda: [0.1, 0.2])
    candidate_gate_strengths: list[float] = Field(default_factory=lambda: [0.0, 0.25, 1.0])
    fundamental_anchor_weight: float = 0.75
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    entropy_regularization: float = 0.002
    expert_auxiliary_weight: float = 0.25
    correlation_regularization: float = 0.05
    batch_size: int = 256
    max_epochs: int = 80
    patience: int = 10


class EvaluationConfig(BaseModel):
    development_end: str = "2022-12-31"
    validation_start: str = "2023-01-01"
    validation_end: str = "2024-12-31"
    test_start: str = "2025-01-01"
    walk_forward_years: list[int] = Field(default_factory=lambda: [2023, 2024])
    horizon_sessions: int = 20
    embargo_sessions: int = 20
    minimum_split_events: int = 30
    bootstrap_samples: int = 1000


class PortfolioConfig(BaseModel):
    gross_exposure: float = 1.0
    maximum_net_exposure: float = 0.02
    maximum_beta_exposure: float = 0.05
    maximum_industry_exposure: float = 0.05
    maximum_name_weight: float = 0.02
    long_quantile: float = 0.9
    short_quantile: float = 0.1
    base_transaction_cost_bps: float = 10.0
    base_borrow_cost_annual: float = 0.02


class ProjectMetadata(BaseModel):
    name: str = "EDGAR-MoE"
    timezone: str = "America/New_York"
    random_seed: int = 42


class ResearchConfig(BaseModel):
    project: ProjectMetadata = Field(default_factory=ProjectMetadata)
    data: DataConfig = Field(default_factory=DataConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> ResearchConfig:
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream) or {}
        return cls.model_validate(payload)


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sec_user_agent: str = "EDGAR-MoE Research research@example.com"
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    fred_api_key: str = ""
    edgar_moe_data_dir: Path = Path("data")
    edgar_moe_demo_snapshot: Path = Path("data/demo/snapshot.json")
    edgar_moe_public_snapshot_lock: Path = Path("config/public_snapshot.lock.json")
    edgar_moe_log_level: str = "INFO"
    edgar_moe_registry_database_url: str = ""
    # The API prefers this SELECT-only connection; the writer URL remains for
    # the private forecast runner and migrations.
    edgar_moe_registry_read_database_url: str = ""
    # Keep each serverless API instance bounded to a small SELECT-only pool.
    # The private runner does not use these settings.
    edgar_moe_registry_api_pool_size: int = Field(default=1, ge=1, le=20)
    edgar_moe_registry_api_max_overflow: int = Field(default=0, ge=0, le=20)
    edgar_moe_registry_api_pool_timeout_seconds: float = Field(
        default=5.0, gt=0, le=60
    )
    edgar_moe_artifact_backend: str = "local"
    edgar_moe_artifact_mirror_backend: str = "none"
    edgar_moe_artifact_dir: Path = Path("data/forward/artifacts")
    edgar_moe_r2_endpoint_url: str = ""
    edgar_moe_r2_bucket: str = ""
    edgar_moe_r2_access_key_id: str = ""
    edgar_moe_r2_secret_access_key: str = ""
    # Optional operator-run LLM copilot. The public API never reads these fields.
    edgar_moe_copilot_api_key: SecretStr = SecretStr("")
    edgar_moe_copilot_endpoint: str = "https://api.openai.com/v1/chat/completions"
    edgar_moe_copilot_model: str = "gpt-4o-mini"
    edgar_moe_copilot_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    edgar_moe_copilot_max_tokens: int = Field(default=800, ge=1, le=8_000)
    edgar_moe_copilot_max_tool_calls: int = Field(default=4, ge=1, le=8)


@lru_cache
def runtime_settings() -> RuntimeSettings:
    return RuntimeSettings()
