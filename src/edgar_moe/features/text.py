from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TextFeatures:
    embedding: np.ndarray
    sentiment: np.ndarray
    token_count: int


class HashingTextEmbedder:
    """Deterministic offline embedder used only for tests and the synthetic demo."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def encode(self, text: str) -> TextFeatures:
        tokens = re.findall(r"[a-zA-Z][a-zA-Z'-]+", text.lower())
        vector = np.zeros(self.dimensions, dtype=np.float32)
        positive = {"growth", "improved", "strong", "gain", "opportunity", "profitable"}
        negative = {"risk", "decline", "loss", "weak", "uncertain", "adverse"}
        pos = neg = 0
        for token in tokens:
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimensions
            sign = 1.0 if (value >> 8) % 2 else -1.0
            vector[index] += sign
            pos += token in positive
            neg += token in negative
        norm = np.linalg.norm(vector)
        if norm:
            vector /= norm
        total = max(pos + neg, 1)
        sentiment = np.array([neg / total, (total - pos - neg) / total, pos / total], dtype=np.float32)
        return TextFeatures(vector, sentiment, len(tokens))


class FinBertEmbedder:
    """Frozen FinBERT encoder with chunked pooling for long filing sections."""

    def __init__(
        self,
        model_name: str = "ProsusAI/finbert",
        chunk_tokens: int = 384,
        device: str | None = None,
    ) -> None:
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as error:  # pragma: no cover - optional dependency path
            raise RuntimeError(
                "Install research dependencies with `uv sync --extra research`"
            ) from error
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)  # type: ignore[no-untyped-call]
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.model.eval()
        self.chunk_tokens = chunk_tokens
        if device is None:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.device = device
        self.model.to(device)

    def encode(self, text: str) -> TextFeatures:
        torch = self.torch
        token_ids = self.tokenizer.encode(text, add_special_tokens=False)
        chunks = [token_ids[i : i + self.chunk_tokens] for i in range(0, len(token_ids), self.chunk_tokens)]
        if not chunks:
            chunks = [[]]
        embeddings: list[np.ndarray] = []
        sentiments: list[np.ndarray] = []
        weights: list[int] = []
        with torch.inference_mode():
            for chunk in chunks:
                encoded = self.tokenizer.prepare_for_model(
                    chunk,
                    add_special_tokens=True,
                    return_tensors="pt",
                    truncation=True,
                    max_length=self.chunk_tokens + 2,
                )
                encoded = {key: value.to(self.device) for key, value in encoded.items()}
                output = self.model(**encoded, output_hidden_states=True)
                cls = output.hidden_states[-1][:, 0, :].squeeze(0)
                embeddings.append(cls.cpu().float().numpy())
                sentiments.append(torch.softmax(output.logits, dim=-1).squeeze(0).cpu().numpy())
                weights.append(max(len(chunk), 1))
        normalized_weights = np.asarray(weights, dtype=np.float32)
        normalized_weights /= normalized_weights.sum()
        embedding = np.average(np.stack(embeddings), axis=0, weights=normalized_weights)
        sentiment = np.average(np.stack(sentiments), axis=0, weights=normalized_weights)
        return TextFeatures(embedding.astype(np.float32), sentiment.astype(np.float32), len(token_ids))


def filing_change_features(current: TextFeatures, previous: TextFeatures | None) -> dict[str, float]:
    if previous is None:
        return {
            "embedding_cosine_change": 0.0,
            "sentiment_negative_delta": 0.0,
            "sentiment_positive_delta": 0.0,
            "token_count_change": 0.0,
            "has_prior_filing": 0.0,
        }
    denominator = np.linalg.norm(current.embedding) * np.linalg.norm(previous.embedding)
    similarity = float(np.dot(current.embedding, previous.embedding) / denominator) if denominator else 0.0
    return {
        "embedding_cosine_change": 1.0 - similarity,
        "sentiment_negative_delta": float(current.sentiment[0] - previous.sentiment[0]),
        "sentiment_positive_delta": float(current.sentiment[-1] - previous.sentiment[-1]),
        "token_count_change": float(np.log1p(current.token_count) - np.log1p(previous.token_count)),
        "has_prior_filing": 1.0,
    }
