import json

import yaml

# tests/test_routes_config.py
with open("routes.yaml") as f:
    CFG = yaml.safe_load(f)


def norm(s: str) -> str:
    return s.strip().lower()


def test_threshold_in_range():
    assert 0.0 <= CFG["threshold"] <= 1.0


def test_each_route_has_enough_utterances():
    for r in CFG["routes"]:
        assert len(r["utterances"]) >= 5, r["name"]


def test_no_duplicate_utterances():
    all_u = [norm(u) for r in CFG["routes"] for u in r["utterances"]]
    assert len(all_u) == len(set(all_u))

def test_no_eval_leakage():
    train = {norm(u) for r in CFG["routes"] for u in r["utterances"]}
    with open("evals/prompts.jsonl") as f:
        test = {norm(json.loads(line)["prompt"]) for line in f}
    assert not (train & test), f"leaked: {train & test}"