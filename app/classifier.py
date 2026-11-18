"""Classifier = black box that maps a prompt to a route.

Default: RuleClassifier (keywords + regex from routes.yaml, no ML dependencies).
Optional: MiniLMClassifier (app/minilm.py), selected with CLASSIFIER=minilm.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.config import RoutesConfig


@dataclass
class Decision:
    route: str
    target: dict
    score: float
    matched: list[str] = field(default_factory=list)   # evidence: which rules fired

    def as_dict(self) -> dict:
        return {"route": self.route, "score": self.score, "target": self.target, "matched": self.matched}


class Classifier(Protocol):
    def classify(self, prompt: str) -> Decision: ...


class RuleClassifier:
    """Score = number of distinct rules (keywords + patterns) of a route that match.

    Keywords are case-insensitive whole words; patterns are regex, case-sensitive.
    Highest score wins; ties go to the route listed first in routes.yaml.
    Score below `min_score` -> fall back to `default`.
    """

    def __init__(self, cfg: RoutesConfig):
        self.cfg = cfg
        self.rules: list[tuple[str, list[tuple[str, re.Pattern]]]] = []
        for r in cfg.routes:
            compiled = [(kw, re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.I)) for kw in r.keywords]
            compiled += [(p, re.compile(p)) for p in r.patterns]   # case-sensitive; use (?i) if needed
            self.rules.append((r.name, compiled))
        self.targets = {r.name: r.target for r in cfg.routes}

    def classify(self, prompt: str) -> Decision:
        best = Decision("default", self.cfg.default, 0.0)
        for name, rules in self.rules:
            matched = [src for src, rx in rules if rx.search(prompt)]
            if len(matched) > best.score:          # strict '>' keeps the first route on ties
                best = Decision(name, self.targets[name], float(len(matched)), matched)
        if best.score < self.cfg.min_score:
            return Decision("default", self.cfg.default, best.score)
        return best


def make_classifier(cfg: RoutesConfig, kind: str | None = None) -> Classifier:
    kind = (kind or os.getenv("CLASSIFIER", "rules")).lower()
    if kind == "rules":
        return RuleClassifier(cfg)
    if kind == "minilm":
        from app.minilm import MiniLMClassifier  # imported lazily: needs the [minilm] extra
        return MiniLMClassifier(cfg)
    raise ValueError(f"unknown CLASSIFIER={kind!r} (expected rules|minilm)")
