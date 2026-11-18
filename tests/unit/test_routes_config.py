"""Static checks on routes.yaml (cheap; run on every commit)."""

import json
import re

import yaml

with open("routes.yaml") as f:
    CFG = yaml.safe_load(f)


def norm(s: str) -> str:
    return s.strip().lower()


def test_threshold_in_range():
    assert 0.0 <= CFG["threshold"] <= 1.0
    assert CFG["min_score"] >= 1


def test_each_route_has_rules_and_utterances():
    for r in CFG["routes"]:
        assert r.get("keywords") or r.get("patterns"), r["name"]
        assert len(r["utterances"]) >= 5, r["name"]


def test_keywords_are_strings_and_patterns_compile():
    # YAML turns bare `null`, `yes`, `no`, `on` into None/bool: that bit us once.
    for r in CFG["routes"]:
        for kw in r.get("keywords", []):
            assert isinstance(kw, str), f"{r['name']}: keyword {kw!r} is not a string (quote it)"
        for p in r.get("patterns", []):
            re.compile(p)


def test_no_duplicate_utterances():
    all_u = [norm(u) for r in CFG["routes"] for u in r["utterances"]]
    assert len(all_u) == len(set(all_u))


def test_no_eval_leakage():
    train = {norm(u) for r in CFG["routes"] for u in r["utterances"]}
    for split in ("evals/dev.jsonl", "evals/test.jsonl"):
        with open(split) as f:
            test = {norm(json.loads(line)["prompt"]) for line in f}
        assert not (train & test), f"leaked into {split}: {train & test}"


def test_dev_and_test_splits_are_disjoint():
    def prompts(p):
        with open(p) as f:
            return {norm(json.loads(line)["prompt"]) for line in f}
    assert not prompts("evals/dev.jsonl") & prompts("evals/test.jsonl")
