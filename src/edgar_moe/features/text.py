from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import numpy as np


@dataclass(frozen=True)
class TextFeatures:
    embedding: np.ndarray
    sentiment: np.ndarray
    token_count: int


class HashingTextEmbedder:
    """Deterministic offline embedder used only for tests and the synthetic demo."""

    TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z'-]+")
    POSITIVE = {"growth", "improved", "strong", "gain", "opportunity", "profitable"}
    NEGATIVE = {"risk", "decline", "loss", "weak", "uncertain", "adverse"}

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    @property
    def cache_identity(self) -> str:
        return f"hashing-blake2b-v1:dimensions={self.dimensions}"

    @staticmethod
    @lru_cache(maxsize=131_072)
    def _token_hash(token: str) -> int:
        return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")

    def encode(self, text: str) -> TextFeatures:
        token_counts = Counter(
            match.group(0).lower() for match in self.TOKEN_PATTERN.finditer(text)
        )
        vector = np.zeros(self.dimensions, dtype=np.float32)
        pos = neg = 0
        for token, count in token_counts.items():
            value = self._token_hash(token)
            index = value % self.dimensions
            sign = 1.0 if (value >> 8) % 2 else -1.0
            vector[index] += sign * count
            pos += count if token in self.POSITIVE else 0
            neg += count if token in self.NEGATIVE else 0
        norm = np.linalg.norm(vector)
        if norm:
            vector /= norm
        total = max(pos + neg, 1)
        sentiment = np.array(
            [neg / total, (total - pos - neg) / total, pos / total], dtype=np.float32
        )
        return TextFeatures(vector, sentiment, sum(token_counts.values()))


class FinBertEmbedder:
    """Frozen FinBERT encoder with chunked pooling for long filing sections."""

    def __init__(
        self,
        model_name: str = "ProsusAI/finbert",
        chunk_tokens: int = 510,
        max_chunks: int | None = 12,
        inference_batch_size: int = 16,
        device: str | None = None,
    ) -> None:
        if (
            chunk_tokens < 1
            or inference_batch_size < 1
            or (max_chunks is not None and max_chunks < 1)
        ):
            raise ValueError("chunk_tokens, max_chunks, and inference_batch_size must be positive")
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as error:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "Install research dependencies with `uv sync --extra research`"
            ) from error
        self.torch = torch
        self.model_name = model_name
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)  # type: ignore[no-untyped-call]
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.model_revision = str(getattr(self.model.config, "_commit_hash", None) or "unresolved")
        self.sentiment_indices = _canonical_sentiment_indices(dict(self.model.config.id2label))
        self.model.eval()
        self.chunk_tokens = chunk_tokens
        self.max_chunks = max_chunks
        self.inference_batch_size = inference_batch_size
        if device is None:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        self.model.to(device)

    @property
    def cache_identity(self) -> str:
        return (
            f"finbert:model={self.model_name}:revision={self.model_revision}:"
            f"chunk_tokens={self.chunk_tokens}:pooling=weighted-cls:"
            f"max_chunks={self.max_chunks}:sampling=uniform-v1:"
            "sentiment=negative-neutral-positive"
        )

    def encode(self, text: str) -> TextFeatures:
        torch = self.torch
        token_ids = self.tokenizer.encode(text, add_special_tokens=False, verbose=False)
        chunks = [
            token_ids[i : i + self.chunk_tokens]
            for i in range(0, len(token_ids), self.chunk_tokens)
        ]
        if not chunks:
            chunks = [[]]
        chunks = _uniform_chunk_sample(chunks, self.max_chunks)
        embeddings: list[np.ndarray] = []
        sentiments: list[np.ndarray] = []
        weights: list[int] = []
        with torch.inference_mode():
            for offset in range(0, len(chunks), self.inference_batch_size):
                batch_chunks = chunks[offset : offset + self.inference_batch_size]
                prepared = [
                    self.tokenizer.prepare_for_model(
                        chunk,
                        add_special_tokens=True,
                        truncation=True,
                        max_length=self.chunk_tokens + 2,
                    )
                    for chunk in batch_chunks
                ]
                encoded = self.tokenizer.pad(
                    prepared,
                    padding=True,
                    return_tensors="pt",
                )
                encoded = {key: value.to(self.device) for key, value in encoded.items()}
                base_output = self.model.base_model(**encoded, return_dict=True)
                pooled = base_output.pooler_output
                if pooled is None:
                    raise RuntimeError("FinBERT backbone did not return a pooled output")
                logits = self.model.classifier(self.model.dropout(pooled))
                cls = base_output.last_hidden_state[:, 0, :].cpu().float().numpy()
                probabilities = (
                    torch.softmax(logits, dim=-1)[:, list(self.sentiment_indices)]
                    .cpu()
                    .float()
                    .numpy()
                )
                embeddings.extend(cls)
                sentiments.extend(probabilities)
                weights.extend(max(len(chunk), 1) for chunk in batch_chunks)
        normalized_weights = np.asarray(weights, dtype=np.float32)
        normalized_weights /= normalized_weights.sum()
        embedding = np.average(np.stack(embeddings), axis=0, weights=normalized_weights)
        sentiment = np.average(np.stack(sentiments), axis=0, weights=normalized_weights)
        return TextFeatures(
            embedding.astype(np.float32), sentiment.astype(np.float32), len(token_ids)
        )


def _canonical_sentiment_indices(id2label: dict[object, object]) -> tuple[int, int, int]:
    by_label = {str(label).strip().lower(): int(str(index)) for index, label in id2label.items()}
    required = ("negative", "neutral", "positive")
    missing = [label for label in required if label not in by_label]
    if missing:
        raise ValueError(f"FinBERT model is missing sentiment labels: {missing}")
    return (by_label["negative"], by_label["neutral"], by_label["positive"])


def _uniform_chunk_sample(chunks: list[list[int]], maximum: int | None) -> list[list[int]]:
    if maximum is None or len(chunks) <= maximum:
        return chunks
    positions = np.linspace(0, len(chunks) - 1, num=maximum).round().astype(int)
    return [chunks[int(position)] for position in positions]


def filing_change_features(
    current: TextFeatures, previous: TextFeatures | None
) -> dict[str, float]:
    if previous is None:
        return {
            "embedding_cosine_change": 0.0,
            "sentiment_negative_delta": 0.0,
            "sentiment_positive_delta": 0.0,
            "token_count_change": 0.0,
            "has_prior_filing": 0.0,
        }
    denominator = np.linalg.norm(current.embedding) * np.linalg.norm(previous.embedding)
    similarity = (
        float(np.dot(current.embedding, previous.embedding) / denominator) if denominator else 0.0
    )
    return {
        "embedding_cosine_change": 1.0 - similarity,
        "sentiment_negative_delta": float(current.sentiment[0] - previous.sentiment[0]),
        "sentiment_positive_delta": float(current.sentiment[-1] - previous.sentiment[-1]),
        "token_count_change": float(np.log1p(current.token_count) - np.log1p(previous.token_count)),
        "has_prior_filing": 1.0,
    }
