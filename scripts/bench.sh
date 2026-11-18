#!/usr/bin/env bash
# Replay benchmark, three cache configurations, same seeded workload:
#   no-cache  (REDIS_URL unset)       every request goes to the LLM
#   exact     (semantic cache off)
#   exact+semantic (tau = $TAU)
# Local processes: Redis 8 (docker), mock-llm (MOCK_LATENCY_MS), gateway (uvicorn, 1 worker).
#   N=300 TAU=0.95 MOCK_LATENCY_MS=200 scripts/bench.sh   -> reports/bench/*.json + summary.md
set -euo pipefail
cd "$(dirname "$0")/.."

N=${N:-300} SEED=${SEED:-7} TAU=${TAU:-0.95} MOCK_LATENCY_MS=${MOCK_LATENCY_MS:-200}
OUT=reports/bench
mkdir -p "$OUT"
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

for port in 8000 8001; do   # a stale server on these ports would silently answer instead of ours
  ! lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null || { echo "port $port is busy"; exit 1; }
done
# Redis on :6390, not :6379 - a host Redis on 127.0.0.1:6379 shadows Docker's port (it happened).
docker ps --format '{{.Names}}' | grep -qx gw-redis \
  || docker run -d --rm --name gw-redis -p 6390:6379 redis:8.10.3-alpine >/dev/null

MOCK_LATENCY_MS=$MOCK_LATENCY_MS MOCK_LATENCY_JITTER_MS=50 \
  .venv/bin/uvicorn mock_llm.main:app --port 8001 --log-level warning & PIDS+=($!)

wait_up() { for _ in $(seq 1 50); do curl -sf "$1/healthz" >/dev/null && return 0; sleep 0.2; done; return 1; }
wait_up http://localhost:8001

run_config() {  # label, then env assignments for the gateway
  local label=$1; shift
  docker exec gw-redis redis-cli FLUSHALL >/dev/null
  env "$@" UPSTREAM_BASE_URL=http://localhost:8001/v1 RATE_LIMIT_RATE=10000 RATE_LIMIT_BURST=10000 \
      SEMANTIC_CACHE_THRESHOLD="$TAU" \
      .venv/bin/uvicorn app.main:app --port 8000 --log-level warning 2>"$OUT/$label.gateway.log" & local pid=$!
  wait_up http://localhost:8000
  grep -h "semantic cache backend" "$OUT/$label.gateway.log" || true
  uv run python -m evals.bench --n "$N" --seed "$SEED" --label "$label" --out "$OUT" >/dev/null
  kill "$pid"; wait "$pid" 2>/dev/null || true
  echo "done: $label"
}

run_config no-cache        REDIS_URL=
run_config exact           REDIS_URL=redis://localhost:6390/0 SEMANTIC_CACHE_ENABLED=false
run_config exact+semantic  REDIS_URL=redis://localhost:6390/0 SEMANTIC_CACHE_ENABLED=true

uv run python - "$OUT" "$TAU" "$MOCK_LATENCY_MS" <<'PY'
import json, sys, pathlib, platform
out, tau, mock = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
rows = []
for label in ("no-cache", "exact", "exact+semantic"):
    r = json.loads((out / f"{label}.json").read_text())
    w = r["workload"]
    bc = r["by_cache"]
    def lat(k):
        return f'{bc[k]["p50_ms"]} / {bc[k]["p95_ms"]}' if k in bc else "-"
    rows.append(f'| {label} | {r["requests"]} | {r["upstream_calls"]} | {r["upstream_calls_avoided_pct"]}% '
                f'| {len(r["cross_prompt_hits"])} | {lat("miss")} | {lat("exact")} | {lat("semantic")} | {r["all"]["p50_ms"]} / {r["all"]["p95_ms"]} |')
md = [f'Workload: N={w["n"]} sequential requests, seed {w["seed"]}, mix {w["actual_mix"]} '
      f'(requested {w["requested_mix"]}); mock LLM latency {mock} ms + U(0, 50) ms; tau={tau}; '
      f'gateway 1 uvicorn worker, Redis 8 in Docker, client on the same laptop ({platform.machine()}).', "",
      "| config | requests | LLM calls | calls avoided | cross-prompt hits* | miss p50/p95 ms | exact hit p50/p95 ms | semantic hit p50/p95 ms | all p50/p95 ms |",
      "|---|---:|---:|---:|---:|---:|---:|---:|---:|"] + rows
review = json.loads((out / "exact+semantic.json").read_text())["cross_prompt_hits"]
md += ["", "*answer served was cached for a different prompt; review each one:"]
md += [f'- "{h["prompt"]}" got the answer cached for "{h["served_answer_for"]}"' for h in review] or ["- none"]
(out / "summary.md").write_text("\n".join(md) + "\n")
print("\n".join(md))
PY
