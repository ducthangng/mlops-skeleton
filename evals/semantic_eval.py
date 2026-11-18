"""Choose the semantic-cache threshold tau from labelled pairs.

evals/semantic_pairs.jsonl:
  paraphrase  same intent  -> the cache SHOULD return the cached answer (hit = good)
  near_miss   different answer, similar text -> a hit here is a FALSE HIT (wrong answer served)

The gateway only reuses an entry if (cosine >= tau) AND (same route). We report both the
embedder alone and the full gateway rule, because the route filter blocks some near misses.

  python -m evals.semantic_eval --out reports/semantic_eval.json --md reports/semantic_eval.md
Rule for picking tau: the lowest tau whose false-hit rate <= --max-false-hit (default 0).
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.classifier import make_classifier
from app.config import load_routes
from app.embedder import make_embedder

TAUS = [round(t, 2) for t in np.arange(0.50, 1.0001, 0.05)] + [0.92, 0.97, 0.98, 0.99]


def load_pairs(path: str = "evals/semantic_pairs.jsonl") -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def score_pairs(pairs: list[dict], embedder_kind: str | None = None, routes: str | None = None) -> list[dict]:
    emb, clf = make_embedder(embedder_kind), make_classifier(load_routes(routes))
    out = []
    for p in pairs:
        sim = float(emb.embed(p["a"]) @ emb.embed(p["b"]))
        same_route = clf.classify(p["a"]).route == clf.classify(p["b"]).route
        out.append({**p, "sim": round(sim, 4), "same_route": same_route})
    return out


def table(scored: list[dict], taus=None) -> list[dict]:
    para = [s for s in scored if s["kind"] == "paraphrase"]
    near = [s for s in scored if s["kind"] == "near_miss"]
    def rate(rows_, tau, use_route):
        hits = [s for s in rows_ if s["sim"] >= tau and (s["same_route"] or not use_route)]
        return len(hits) / len(rows_) if rows_ else 0.0

    return [{"tau": tau,
             "paraphrase_hit_rate": round(rate(para, tau, True), 4),
             "false_hit_rate": round(rate(near, tau, True), 4),
             "false_hit_rate_embedder_only": round(rate(near, tau, False), 4)}
            for tau in sorted(set(taus or TAUS))]


def pick_tau(rows: list[dict], max_false_hit: float) -> dict | None:
    ok = [r for r in rows if r["false_hit_rate"] <= max_false_hit + 1e-9]
    return min(ok, key=lambda r: r["tau"]) if ok else None


def by_type(scored: list[dict], tau: float) -> dict:
    agg = defaultdict(lambda: [0, 0])
    for s in scored:
        key = f'{s["kind"]}/{s["type"]}'
        agg[key][0] += s["sim"] >= tau and s["same_route"]
        agg[key][1] += 1
    return {k: f"{h}/{n}" for k, (h, n) in sorted(agg.items())}


def to_markdown(rows, chosen, scored, n_para, n_near, embedder) -> str:
    lines = [f"Embedder `{embedder}`, {n_para} paraphrase pairs, {n_near} near-miss pairs "
             "(evals/semantic_pairs.jsonl). Hit = cosine >= tau AND same route.", "",
             "| tau | paraphrase hit rate | false-hit rate | false-hit rate (embedder only, no route filter) |",
             "|---:|---:|---:|---:|"]
    for r in rows:
        mark = " **<- chosen**" if chosen and r["tau"] == chosen["tau"] else ""
        lines.append(f'| {r["tau"]:.2f} | {r["paraphrase_hit_rate"]:.1%} | {r["false_hit_rate"]:.1%}{mark} '
                     f'| {r["false_hit_rate_embedder_only"]:.1%} |')
    if chosen:
        lines += ["", f'Hits per pair type at tau={chosen["tau"]:.2f}:', ""]
        lines += [f"- {k}: {v}" for k, v in by_type(scored, chosen["tau"]).items()]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", default=None, help="hashing (default) | minilm")
    ap.add_argument("--routes", default=None)
    ap.add_argument("--max-false-hit", type=float, default=0.0)
    ap.add_argument("--out")
    ap.add_argument("--md")
    args = ap.parse_args()

    pairs = load_pairs()
    scored = score_pairs(pairs, args.embedder, args.routes)
    rows = table(scored)
    chosen = pick_tau(rows, args.max_false_hit)
    n_para = sum(p["kind"] == "paraphrase" for p in pairs)
    md = to_markdown(rows, chosen, scored, n_para, len(pairs) - n_para, args.embedder or "hashing")
    print(md)
    report = {"embedder": args.embedder or "hashing", "max_false_hit": args.max_false_hit,
              "chosen": chosen, "table": rows, "pairs": scored}
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))
    if args.md:
        Path(args.md).write_text(md + "\n")
    return 0 if chosen else 1


if __name__ == "__main__":
    sys.exit(main())
