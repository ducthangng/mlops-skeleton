"""Load routes.yaml once. The file hash is the config version used in every cache key."""

import hashlib
import os
from dataclasses import dataclass, field

import yaml


@dataclass(frozen=True)
class Route:
    name: str
    target: dict                       # {model, temperature, max_tokens, fallback?}
    keywords: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    utterances: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RoutesConfig:
    version: str                       # first 12 hex chars of sha256(routes.yaml)
    default: dict                      # target used when nothing matches
    routes: list[Route]
    min_score: int                     # RuleClassifier: score below this -> default
    threshold: float                   # MiniLMClassifier: cosine below this -> default


def load_routes(path: str | None = None) -> RoutesConfig:
    path = path or os.getenv("ROUTES_PATH", "routes.yaml")
    with open(path, "rb") as f:
        raw = f.read()                 # read once: hash and parse the same bytes (lesson 10)
    cfg = yaml.safe_load(raw)
    routes = [
        Route(
            name=r["name"],
            target=r["target"],
            keywords=r.get("keywords", []),
            patterns=r.get("patterns", []),
            utterances=r.get("utterances", []),
        )
        for r in cfg["routes"]
    ]
    return RoutesConfig(
        version=hashlib.sha256(raw).hexdigest()[:12],
        default=cfg["default"]["target"],
        routes=routes,
        min_score=int(cfg.get("min_score", 1)),
        threshold=float(cfg.get("threshold", 0.40)),
    )
