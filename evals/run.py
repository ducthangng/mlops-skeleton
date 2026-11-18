"""Routing eval + CI gate.

  python -m evals.run                          # print accuracy on dev and test
  python -m evals.run --split test --gate evals/baseline.json --out reports/eval.json
      exit 1 if accuracy < baseline - tolerance (2 points)
  python -m evals.run --split test --write-baseline evals/baseline.json
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from app.classifier import make_classifier
from app.config import load_routes

SPLITS = {"dev": "evals/dev.jsonl", "test": "evals/test.jsonl"}


def load(split: str) -> list[dict]:
    with open(SPLITS[split]) as f:
        return [json.loads(line) for line in f if line.strip()]


def evaluate(rows: list[dict], routes_path: str | None = None) -> dict:
    cfg = load_routes(routes_path)
    clf = make_classifier(cfg)
    errors, wrong = Counter(), []
    for d in rows:
        pred = clf.classify(d["prompt"]).route
        if pred != d["label"]:
            errors[f'{d["label"]}->{pred}'] += 1
            wrong.append({"prompt": d["prompt"], "label": d["label"], "pred": pred})
    acc = 1 - len(wrong) / len(rows)
    return {"cfg_version": cfg.version, "n": len(rows), "accuracy": round(acc, 4),
            "errors": dict(errors.most_common()), "wrong": wrong}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=list(SPLITS), action="append")
    ap.add_argument("--routes", default=None)
    ap.add_argument("--gate", help="baseline json; fail if accuracy drops more than tolerance")
    ap.add_argument("--write-baseline")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args()

    report = {}
    for split in args.split or ["dev", "test"]:
        r = evaluate(load(split), args.routes)
        report[split] = r
        print(f"{split:5s} n={r['n']:3d} acc={r['accuracy']:.1%} errors={r['errors']}")

    status = 0
    if args.gate:
        base = json.loads(Path(args.gate).read_text())
        split = base["split"]
        floor = base["accuracy"] - base.get("tolerance", 0.02)
        acc = report[split]["accuracy"]
        ok = acc >= floor - 1e-9
        report["gate"] = {"split": split, "baseline": base["accuracy"], "floor": round(floor, 4),
                          "accuracy": acc, "passed": ok}
        print(f"gate: {split} acc={acc:.1%} floor={floor:.1%} (baseline {base['accuracy']:.1%} - "
              f"{base.get('tolerance', 0.02):.0%}) -> {'PASS' if ok else 'FAIL'}")
        status = 0 if ok else 1
    if args.write_baseline:
        split = (args.split or ["test"])[-1]
        Path(args.write_baseline).write_text(json.dumps(
            {"split": split, "accuracy": report[split]["accuracy"], "tolerance": 0.02,
             "cfg_version": report[split]["cfg_version"], "n": report[split]["n"]}, indent=2) + "\n")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
