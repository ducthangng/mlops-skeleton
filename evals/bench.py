"""Replay benchmark against a running gateway (+ mock-llm). Run via scripts/bench.sh.

Workload (seeded, written into the report):
  unique      a prompt not sent before (eval prompts + both sides of near-miss pairs)
  repeat      byte-identical copy of an earlier prompt            -> exact-cache candidate
  paraphrase  the paraphrase 'b' of an earlier 'a' (semantic_pairs) -> semantic-cache candidate
Requests are sequential (concurrency 1) so latency is not queueing.

Cross-prompt hit = the answer served was generated for a different prompt than the one we
intended (the mock LLM echoes the prompt it answered). It is a FALSE hit if the intent differs;
they are listed in the report for manual review, because the pools can contain near-identical
prompts (e.g. the same greeting with and without punctuation).
"""

import argparse
import json
import random
import re
import statistics
import time
from pathlib import Path

import httpx

ANSWER_RX = re.compile(r"mock answer to: (.*)$", re.S)


def load_jsonl(p):
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]


def build_workload(n: int, mix: dict[str, float], seed: int) -> list[dict]:
    rng = random.Random(seed)
    para = [p for p in load_jsonl("evals/semantic_pairs.jsonl") if p["kind"] == "paraphrase"]
    near = [p for p in load_jsonl("evals/semantic_pairs.jsonl") if p["kind"] == "near_miss"]
    evals = load_jsonl("evals/dev.jsonl") + load_jsonl("evals/test.jsonl")
    pool = list(dict.fromkeys([d["prompt"] for d in evals] + [p["a"] for p in para]
                              + [x for p in near for x in (p["a"], p["b"])]))
    rng.shuffle(pool)
    para_of = {p["a"]: p["b"] for p in para}
    sent: list[str] = []
    work = []
    kinds, weights = zip(*mix.items(), strict=True)
    for _ in range(n):
        kind = rng.choices(kinds, weights)[0]
        eligible_para = [a for a in sent if a in para_of]
        if kind == "repeat" and sent:
            work.append({"kind": "repeat", "prompt": rng.choice(sent), "base": None})
            continue
        if kind == "paraphrase" and eligible_para:
            a = rng.choice(eligible_para)
            work.append({"kind": "paraphrase", "prompt": para_of[a], "base": a})
            continue
        if not pool:
            raise SystemExit(f"prompt pool exhausted after {len(sent)} unique prompts: lower --n")
        prompt = pool.pop()
        sent.append(prompt)
        work[len(work):] = [{"kind": "unique", "prompt": prompt, "base": None}]
    for w in work:
        w["base"] = w["base"] or w["prompt"]
    return work


def upstream_calls(client: httpx.Client) -> float:
    text = client.get("/metrics").text
    return sum(float(m) for m in re.findall(r'^gateway_upstream_calls_total\{[^}]*\} ([0-9.e+]+)$', text, re.M))


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 2)


def run(url: str, work: list[dict]) -> dict:
    c = httpx.Client(base_url=url, timeout=30)
    before = upstream_calls(c)
    rows = []
    for w in work:
        t0 = time.perf_counter()
        r = c.post("/v1/chat/completions", headers={"X-API-Key": "bench"},
                   json={"messages": [{"role": "user", "content": w["prompt"]}]})
        ms = (time.perf_counter() - t0) * 1000
        r.raise_for_status()
        m = ANSWER_RX.search(r.json()["choices"][0]["message"]["content"])
        answered = m.group(1) if m else ""
        ok = answered in (w["prompt"][:80], w["base"][:80])
        rows.append({**w, "cache": r.headers.get("x-cache", "?"), "ms": round(ms, 2), "correct": ok,
                     "answered": answered})
    calls = upstream_calls(c) - before
    out = {"requests": len(rows), "upstream_calls": int(calls),
           "upstream_calls_avoided_pct": round(100 * (1 - calls / len(rows)), 1),
           "cross_prompt_hits": [{"prompt": r["prompt"], "served_answer_for": r["answered"]}
                                 for r in rows if not r["correct"]],
           "by_cache": {}, "by_kind": {}}
    for cache in sorted({r["cache"] for r in rows}):
        lat = [r["ms"] for r in rows if r["cache"] == cache]
        out["by_cache"][cache] = {"n": len(lat), "p50_ms": pct(lat, 0.5), "p95_ms": pct(lat, 0.95)}
    for kind in sorted({r["kind"] for r in rows}):
        sel = [r for r in rows if r["kind"] == kind]
        out["by_kind"][kind] = {"n": len(sel), **{c: sum(r["cache"] == c for r in sel)
                                                 for c in ("exact", "semantic", "miss")}}
    out["all"] = {"p50_ms": pct([r["ms"] for r in rows], 0.5), "p95_ms": pct([r["ms"] for r in rows], 0.95),
                  "mean_ms": round(statistics.fmean(r["ms"] for r in rows), 2)}
    out["rows"] = rows
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--unique", type=float, default=0.5)
    ap.add_argument("--repeat", type=float, default=0.3)
    ap.add_argument("--paraphrase", type=float, default=0.2)
    ap.add_argument("--label", default="run")
    ap.add_argument("--out", default="reports/bench")
    args = ap.parse_args()
    mix = {"unique": args.unique, "repeat": args.repeat, "paraphrase": args.paraphrase}
    work = build_workload(args.n, mix, args.seed)
    res = run(args.url, work)
    res["workload"] = {"n": args.n, "seed": args.seed, "requested_mix": mix,
                       "actual_mix": {k: sum(w["kind"] == k for w in work) for k in mix}, "concurrency": 1}
    Path(args.out).mkdir(parents=True, exist_ok=True)
    Path(args.out, f"{args.label}.json").write_text(json.dumps(res, indent=2))
    summary = {k: v for k, v in res.items() if k != "rows"}
    print(json.dumps({"label": args.label, **summary}, indent=2))


if __name__ == "__main__":
    main()
