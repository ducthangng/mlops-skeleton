"""Optional ML implementations. Needs the extra: `uv sync --extra minilm` (pulls PyTorch).

Not part of the default image. Kept so the black-box interfaces can be swapped
back to a real embedding model without touching the gateway.
"""

import os

import numpy as np
from sentence_transformers import SentenceTransformer

from app.classifier import Decision
from app.config import RoutesConfig

MODEL_NAME = os.getenv("MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")


class MiniLMEmbedder:
    dim = 384

    def __init__(self, model_name: str = MODEL_NAME):
        self.model = SentenceTransformer(model_name, device="cpu")

    def embed(self, text: str) -> np.ndarray:
        return self.model.encode(text, normalize_embeddings=True).astype(np.float32)


class MiniLMClassifier:
    """The original semantic router: max cosine similarity against each route's utterances."""

    def __init__(self, cfg: RoutesConfig, embedder: MiniLMEmbedder | None = None):
        self.cfg = cfg
        self.embedder = embedder or MiniLMEmbedder()
        self.routes = [r for r in cfg.routes if r.utterances]
        self.utt_emb = [
            self.embedder.model.encode(r.utterances, normalize_embeddings=True) for r in self.routes
        ]

    def classify(self, prompt: str) -> Decision:
        q = self.embedder.embed(prompt)
        scores = np.array([float((E @ q).max()) for E in self.utt_emb])
        i = int(scores.argmax())
        if scores[i] < self.cfg.threshold:
            return Decision("default", self.cfg.default, float(scores[i]))
        r = self.routes[i]
        return Decision(r.name, r.target, float(scores[i]))
