"""Embedder = black box that maps text to an L2-normalised vector (used by the semantic cache).

Default: HashingEmbedder, dependency-free (numpy only). It hashes character n-grams
into a fixed-size vector ("feature hashing"). It captures *spelling* similarity,
not meaning: "fix my go deadlock" ~ "fix my Go deadlock?" but also
"derivative of x^2" ~ "derivative of x^3". That is why the threshold tau is
chosen on a near-miss eval set (evals/semantic_eval.py), not guessed.

Optional: MiniLMEmbedder (app/minilm.py), selected with EMBEDDER=minilm.
"""

import hashlib
import os
import re
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> np.ndarray: ...   # float32, shape (dim,), ||v|| = 1


class HashingEmbedder:
    def __init__(self, dim: int = 1024, ngram_range: tuple[int, int] = (3, 5)):
        self.dim = dim
        self.ngram_range = ngram_range

    @staticmethod
    def normalise(text: str) -> str:
        text = re.sub(r"[^\w^+*/=.-]+", " ", text.lower())   # keep math symbols, drop other punctuation
        return " " + " ".join(text.split()) + " "             # padding marks word boundaries

    def _features(self, text: str):
        t = self.normalise(text)
        lo, hi = self.ngram_range
        for n in range(lo, hi + 1):
            for i in range(len(t) - n + 1):
                yield t[i:i + n]
        yield from t.split()                                   # whole words as extra features

    def embed(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for feat in self._features(text):
            # blake2b, not hash(): Python's hash() is randomised per process,
            # so two pods would produce different vectors for the same text.
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
            sign = 1.0 if (h >> 63) & 1 else -1.0              # signed hashing reduces collision bias
            v[h % self.dim] += sign
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v


def make_embedder(kind: str | None = None) -> Embedder:
    kind = (kind or os.getenv("EMBEDDER", "hashing")).lower()
    if kind == "hashing":
        return HashingEmbedder(dim=int(os.getenv("EMBEDDER_DIM", "1024")))
    if kind == "minilm":
        from app.minilm import MiniLMEmbedder
        return MiniLMEmbedder()
    raise ValueError(f"unknown EMBEDDER={kind!r} (expected hashing|minilm)")
