"""Gateway release qualification against a committed golden baseline.

  python -m qualify.run --stage routing --scenario partial --out reports/qualify --cache-dir .qualify-cache
  python -m qualify.run --all --scenario clean               # all stages, stop at the first failure
  python -m qualify.run --all --write-golden                 # (re)record the golden baseline

Scenario = overlay in qualify/scenarios/<name>/: routes.yaml (optional) + scenario.env (optional).
Exit code 1 if the stage regressed vs golden (new failures or metrics outside tolerance).
"""

import argparse
import glob
import hashlib
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from qualify.compare import compare
from qualify.stages import COMPARE, INPUTS, STAGES, Ctx

GOLDEN = Path("qualify/golden")
# Env knobs that change stage results: part of the cache key even when set outside the scenario.
ENV_KEYS = ["SEMANTIC_CACHE_THRESHOLD", "LOAD_SECONDS", "LOAD_RPS", "LOAD_CONCURRENCY", "MOCK_LATENCY_MS", "CLASSIFIER"]
SCENARIOS = Path("qualify/scenarios")


def load_ctx(scenario: str, extra_env: list[str]) -> Ctx:
    d = SCENARIOS / scenario
    if scenario != "clean" and not d.is_dir():
        raise SystemExit(f"unknown scenario {scenario!r}")
    routes = d / "routes.yaml"
    env = {}
    if (d / "scenario.env").exists():
        for line in (d / "scenario.env").read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update(dict(e.split("=", 1) for e in extra_env))
    return Ctx(routes_path=str(routes if routes.exists() else "routes.yaml"), env=env, golden_dir=GOLDEN)


def inputs_key(stage: str, ctx: Ctx) -> str:
    h = hashlib.sha256()
    for pattern in INPUTS[stage] + [f"{{golden}}/{stage}.json"]:
        pattern = pattern.replace("{routes}", ctx.routes_path).replace("{golden}", str(ctx.golden_dir))
        for f in sorted(glob.glob(pattern)):
            h.update(f.encode() + b"\0" + Path(f).read_bytes())
    env = {k: ctx.getenv(k, "") for k in ENV_KEYS} | ctx.env
    h.update(json.dumps(env, sort_keys=True).encode())
    return h.hexdigest()[:16]


def run_stage(stage: str, ctx: Ctx, out: Path, cache_dir: Path | None, scenario: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    key = inputs_key(stage, ctx)
    cached = cache_dir / f"{stage}-{key}.json" if cache_dir else None
    if cached and cached.exists():
        summary = {**json.loads(cached.read_text()), "skipped": "cache", "scenario": scenario}
        (out / f"{stage}.log").write_text(f"SKIP {stage}: inputs unchanged (key {key}), reusing passing result\n")
        (out / f"{stage}.json").write_text(json.dumps(summary, indent=2))
        print(f"[{stage}] skipped: inputs unchanged (key {key})")
        return summary

    t0 = time.perf_counter()
    res = STAGES[stage](ctx)
    summary = {"stage": stage, "scenario": scenario, "inputs_key": key, "skipped": False,
               "duration_s": round(time.perf_counter() - t0, 2), "metrics": res.metrics,
               "failures": res.failures, "evidence": res.evidence, "log": f"{stage}.log"}
    golden_file = ctx.golden_dir / f"{stage}.json"
    diff = compare(summary, json.loads(golden_file.read_text())) if golden_file.exists() else \
        {"new_failures": res.failures, "regressed_metrics": []}
    summary["status"] = "fail" if diff["new_failures"] or diff["regressed_metrics"] else "pass"
    summary["diff"] = diff
    (out / f"{stage}.log").write_text("\n".join(res.lines) + "\n")
    (out / f"{stage}.json").write_text(json.dumps(summary, indent=2))
    if hasattr(res, "decisions"):
        (out / "routing_decisions.json").write_text(json.dumps(res.decisions, indent=2, sort_keys=True))
    print(f"[{stage}] {summary['status']} in {summary['duration_s']}s  metrics={res.metrics}")
    for f in diff["new_failures"][:10]:
        print(f"   {f['evidence']}  ({stage}.log:{f['line']})")
    for m in diff["regressed_metrics"]:
        print(f"   regressed {m['metric']}: {m['run']} vs golden {m['golden']} (limit {m['limit']})")
    if cached and summary["status"] == "pass":           # only passing results are reusable
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(summary, indent=2))
    return summary


def write_golden(out: Path) -> None:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for stage in STAGES:
        s = json.loads((out / f"{stage}.json").read_text())
        s = {k: s[k] for k in ("stage", "metrics", "failures", "evidence")}
        s["compare"] = COMPARE.get(stage, {})
        (GOLDEN / f"{stage}.json").write_text(json.dumps(s, indent=2) + "\n")
    shutil.copy(out / "routing_decisions.json", GOLDEN / "routing_decisions.json")
    print(f"golden written to {GOLDEN}/")


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stage", choices=list(STAGES))
    g.add_argument("--all", action="store_true")
    ap.add_argument("--scenario", default="clean")
    ap.add_argument("--env", action="append", default=[], help="extra KEY=VALUE overrides")
    ap.add_argument("--out", type=Path, default=Path("reports/qualify"))
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--write-golden", action="store_true")
    args = ap.parse_args()

    logging.getLogger("httpx").setLevel(logging.WARNING)   # one line per request is noise here
    ctx = load_ctx(args.scenario, args.env)
    if args.write_golden:
        from app.main import create_app
        GOLDEN.mkdir(parents=True, exist_ok=True)
        (GOLDEN / "openapi.json").write_text(json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n")
        (GOLDEN / "routing_decisions.json").write_text("{}")
        for stage in STAGES:
            (GOLDEN / f"{stage}.json").unlink(missing_ok=True)
        args.cache_dir = None
    stages = list(STAGES) if args.all else [args.stage]
    for stage in stages:
        if run_stage(stage, ctx, args.out, args.cache_dir, args.scenario)["status"] == "fail" \
                and not args.write_golden:
            return 1                                       # fail fast: later stages don't run
    if args.write_golden:
        write_golden(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
