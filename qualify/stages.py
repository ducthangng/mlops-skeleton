"""The five qualification stages. Each returns a Result; qualify.run handles caching, golden
comparison and writing <stage>.log / <stage>.json.

Cost order (fail fast): config_lint (<1 s) -> contract (~1 s) -> routing (~1 s) -> cache (~1 s)
-> load (LOAD_SECONDS, default 30 s, starts real processes).
"""

import asyncio
import hashlib
import json
import os
import random
import re
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Result:
    metrics: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)     # [{id, message, evidence, line}]
    evidence: dict = field(default_factory=dict)     # metric -> evidence line
    lines: list = field(default_factory=list)

    def log(self, msg: str) -> int:
        self.lines.append(msg)
        return len(self.lines)

    def fail(self, fid: str, message: str) -> None:
        line = self.log(f"FAIL {message}")
        self.failures.append({"id": fid, "message": message, "evidence": f"FAIL {message}", "line": line})


@dataclass
class Ctx:
    routes_path: str
    env: dict                    # scenario env overrides, applied to child processes and lookups
    golden_dir: Path

    def getenv(self, key: str, default: str) -> str:
        return self.env.get(key, os.getenv(key, default))


def _hid(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- 1. config_lint
REQUIRED_TARGET = {"model"}


def config_lint(ctx: Ctx) -> Result:
    res = Result()
    try:
        cfg = yaml.safe_load(Path(ctx.routes_path).read_text())
    except yaml.YAMLError as e:
        res.fail("yaml", f"routes.yaml is not valid YAML: {e}")
        return res
    for key in ("default", "routes"):
        if key not in cfg:
            res.fail(f"missing:{key}", f"top-level key '{key}' missing")
    if not isinstance(cfg.get("min_score", 1), int) or cfg.get("min_score", 1) < 1:
        res.fail("min_score", f"min_score must be an int >= 1, got {cfg.get('min_score')!r}")
    names, models = set(), {cfg.get("default", {}).get("target", {}).get("model")}
    for r in cfg.get("routes", []):
        name = r.get("name", "?")
        if name in names:
            res.fail(f"dup-route:{name}", f"route '{name}' defined twice")
        names.add(name)
        tgt = r.get("target", {})
        if not REQUIRED_TARGET <= tgt.keys():
            res.fail(f"target:{name}", f"route '{name}': target needs {sorted(REQUIRED_TARGET)}")
        models.add(tgt.get("model"))
        if not (r.get("keywords") or r.get("patterns")):
            res.fail(f"norules:{name}", f"route '{name}' has no keywords or patterns")
        for kw in r.get("keywords", []):
            if not isinstance(kw, str):
                res.fail(f"kwtype:{name}:{kw!r}", f"route '{name}': keyword {kw!r} is not a string (quote it)")
        for p in r.get("patterns", []):
            try:
                re.compile(p)
            except (re.error, TypeError) as e:
                res.fail(f"regex:{name}:{_hid(str(p))}", f"route '{name}': pattern {p!r} does not compile: {e}")
    for r in cfg.get("routes", []):
        fb = r.get("target", {}).get("fallback")
        if fb and fb not in models:
            name = r.get("name")
            res.fail(f"fallback:{name}", f"route '{name}': fallback model '{fb}' is not a known model")
    res.metrics = {"routes": len(names), "errors": len(res.failures)}
    res.log(f"checked {len(names)} routes, {len(res.failures)} errors")
    return res


# ---------------------------------------------------------------- 2. contract
def _schema_fields(openapi: dict) -> dict:
    """Flatten: 'POST /v1/chat/completions' -> required request fields; schemas -> properties."""
    out = {"paths": sorted(f"{m.upper()} {p}" for p, ops in openapi["paths"].items() for m in ops),
           "schemas": {n: sorted(s.get("properties", {})) for n, s in openapi["components"]["schemas"].items()},
           "required": {n: sorted(s.get("required", [])) for n, s in openapi["components"]["schemas"].items()}}
    return out


def contract(ctx: Ctx) -> Result:
    from fakeredis import FakeAsyncRedis
    from fastapi.testclient import TestClient

    from app.main import Settings, create_app
    from tests.api.conftest import CountingTransport

    res = Result()
    os.environ.update({"MOCK_LATENCY_MS": "0", "MOCK_LATENCY_JITTER_MS": "0", "MOCK_ERROR_RATE": "0"})
    app = create_app(Settings(routes_path=ctx.routes_path, upstream_url="http://mock/v1"),
                     redis_client=FakeAsyncRedis(), upstream_transport=CountingTransport())
    now = _schema_fields(app.openapi())
    golden = json.loads((ctx.golden_dir / "openapi.json").read_text())
    was = _schema_fields(golden)
    for p in sorted(set(was["paths"]) - set(now["paths"])):
        res.fail(f"path:{p}", f"endpoint removed: {p}")
    for name, props in was["schemas"].items():
        for prop in sorted(set(props) - set(now["schemas"].get(name, []))):
            res.fail(f"prop:{name}.{prop}", f"schema {name}: property '{prop}' removed")
    for name, req in now["required"].items():
        for prop in sorted(set(req) - set(was["required"].get(name, []))):
            res.fail(f"req:{name}.{prop}", f"schema {name}: '{prop}' became required (breaks old clients)")

    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "python TypeError"}]})
        checks = {
            "status 200": r.status_code == 200,
            "header x-route": "x-route" in r.headers, "header x-cache": "x-cache" in r.headers,
            "header x-model": "x-model" in r.headers,
            "body choices[0].message.content": bool(r.json().get("choices", [{}])[0].get("message", {}).get("content")),
        }
        for k, v in checks.items():
            res.log(f"{'ok  ' if v else 'FAIL'} live: {k}") if v else res.fail(f"live:{k}", f"live call: {k}")
        r422 = c.post("/v1/chat/completions", json={"messages": []})
        if r422.status_code != 422:
            res.fail("live:422", f"empty messages should be 422, got {r422.status_code}")
    res.metrics = {"paths": len(now["paths"]), "breaking_changes": len(res.failures)}
    res.log(f"{len(now['paths'])} paths, {len(res.failures)} breaking changes")
    return res


# ---------------------------------------------------------------- 3. routing
def routing(ctx: Ctx) -> Result:
    from app.classifier import make_classifier
    from app.config import load_routes

    res = Result()
    clf = make_classifier(load_routes(ctx.routes_path))
    prompts = [json.loads(line) for f in ("evals/dev.jsonl", "evals/test.jsonl") for line in open(f) if line.strip()]
    golden = json.loads((ctx.golden_dir / "routing_decisions.json").read_text())
    correct = changed = improved = 0
    decisions = {}
    for d in prompts:
        pred = clf.classify(d["prompt"]).route
        decisions[d["prompt"]] = pred
        correct += pred == d["label"]
        was = golden.get(d["prompt"])
        if was is not None and was != pred:
            changed += 1
            if pred == d["label"]:
                improved += 1
                res.log(f"improved: {d['prompt']!r} {was} -> {pred}")
            else:
                res.fail(f"route:{_hid(d['prompt'])}",
                         f"{d['prompt']!r}: was {was}, now {pred} (label {d['label']})")
    res.metrics = {"accuracy": round(correct / len(prompts), 4), "changed": changed, "improved": improved}
    res.evidence["accuracy"] = f"accuracy {res.metrics['accuracy']:.1%} on {len(prompts)} fixed prompts"
    res.log(res.evidence["accuracy"] + f", {changed} decisions changed vs golden ({improved} improved)")
    res.decisions = decisions                        # used by --write-golden
    return res


# ---------------------------------------------------------------- 4. cache
def cache(ctx: Ctx) -> Result:
    from evals.semantic_eval import load_pairs, score_pairs

    res = Result()
    tau = float(ctx.getenv("SEMANTIC_CACHE_THRESHOLD", "0.95"))
    scored = score_pairs(load_pairs(), routes=ctx.routes_path)
    para = [s for s in scored if s["kind"] == "paraphrase"]
    near = [s for s in scored if s["kind"] == "near_miss"]
    hit = lambda s: s["sim"] >= tau and s["same_route"]     # noqa: E731
    for s in near:
        if hit(s):
            res.fail(f"falsehit:{_hid(s['a'] + s['b'])}",
                     f"false hit at tau={tau}: {s['a']!r} ~ {s['b']!r} (cosine {s['sim']:.3f}, {s['type']})")
    res.metrics = {"tau": tau, "paraphrase_hit_rate": round(sum(map(hit, para)) / len(para), 4),
                   "false_hit_rate": round(sum(map(hit, near)) / len(near), 4)}
    res.evidence["paraphrase_hit_rate"] = f"{res.metrics['paraphrase_hit_rate']:.1%} of {len(para)} paraphrases hit"
    res.log(f"tau={tau}: paraphrase hit rate {res.metrics['paraphrase_hit_rate']:.1%}, "
            f"false-hit rate {res.metrics['false_hit_rate']:.1%}")
    return res


# ---------------------------------------------------------------- 5. load
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout_s: float = 15) -> None:
    import httpx
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not come up")


async def _drive(url: str, seconds: float, concurrency: int, rps: float,
                 prompts: list[str]) -> list[tuple[str, int, float]]:
    """Paced (open-ish loop): each worker sends one request every concurrency/rps seconds, so the
    offered load is fixed and the 429 rate measures the limiter, not how fast this laptop is."""
    import httpx
    out: list[tuple[str, int, float]] = []
    rng = random.Random(42)
    start = time.perf_counter()
    stop = start + seconds
    interval = concurrency / rps

    async def worker(i: int):
        nxt = start + i * interval / concurrency          # stagger the workers
        async with httpx.AsyncClient(base_url=url, timeout=10) as c:
            while (now := time.perf_counter()) < stop:
                if nxt > now:
                    await asyncio.sleep(nxt - now)
                nxt += interval
                client = "noisy" if rng.random() < 0.2 else f"client-{i % 8}"
                t0 = time.perf_counter()
                try:
                    r = await c.post("/v1/chat/completions", headers={"X-API-Key": client},
                                     json={"messages": [{"role": "user", "content": rng.choice(prompts)}]})
                    code = r.status_code
                except httpx.HTTPError:
                    code = 0
                out.append((client, code, (time.perf_counter() - t0) * 1000))
    await asyncio.gather(*(worker(i) for i in range(concurrency)))
    return out


def load(ctx: Ctx) -> Result:
    res = Result()
    seconds = float(ctx.getenv("LOAD_SECONDS", "30"))
    concurrency = int(ctx.getenv("LOAD_CONCURRENCY", "16"))
    rps = float(ctx.getenv("LOAD_RPS", "100"))
    redis_url = ctx.getenv("REDIS_URL", "redis://localhost:6379/0")
    mport, gport = _free_port(), _free_port()
    env = {**os.environ, **ctx.env, "MOCK_LATENCY_MS": ctx.getenv("MOCK_LATENCY_MS", "50"),
           "MOCK_LATENCY_JITTER_MS": "10", "REDIS_URL": redis_url, "LOG_LEVEL": "WARNING",
           "ROUTES_PATH": ctx.routes_path,
           "UPSTREAM_BASE_URL": f"http://127.0.0.1:{mport}/v1",
           "RATE_LIMIT_RATE": "50", "RATE_LIMIT_BURST": "100", "RATE_LIMIT_CLIENTS": "noisy=2:5"}
    procs = [subprocess.Popen([sys.executable, "-m", "uvicorn", mod, "--port", str(port), "--log-level", "warning"],
                              env=env) for mod, port in (("mock_llm.main:app", mport), ("app.main:app", gport))]
    try:
        _wait(f"http://127.0.0.1:{mport}/healthz")
        _wait(f"http://127.0.0.1:{gport}/healthz")
        import redis
        redis.Redis.from_url(redis_url).flushdb()
        prompts = [json.loads(line)["prompt"] for line in open("evals/dev.jsonl")][:30]
        rows = asyncio.run(_drive(f"http://127.0.0.1:{gport}", seconds, concurrency, rps, prompts))
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)
    ok = [ms for _, code, ms in rows if code == 200]
    lat = sorted(ok)
    p = lambda q: round(lat[min(len(lat) - 1, int(q * (len(lat) - 1)))], 2) if lat else None   # noqa: E731
    n = len(rows)
    res.metrics = {
        "requests": n, "rps": round(n / seconds, 1), "p50_ms": p(0.5), "p95_ms": p(0.95),
        "mean_ms": round(statistics.fmean(lat), 2) if lat else None,
        "error_rate": round(sum(code not in (200, 429) for _, code, _ in rows) / n, 4),
        "rate_429_noisy": round(sum(code == 429 for c, code, _ in rows if c == "noisy")
                                / max(1, sum(c == "noisy" for c, _, _ in rows)), 4),
        "rate_429_others": round(sum(code == 429 for c, code, _ in rows if c != "noisy")
                                 / max(1, sum(c != "noisy" for c, _, _ in rows)), 4),
    }
    for k in ("p95_ms", "error_rate", "rate_429_noisy", "rate_429_others"):
        res.evidence[k] = f"{k}={res.metrics[k]} ({n} requests, offered {rps:g} rps, {seconds:g}s)"
    res.log(f"{n} requests in {seconds:g}s, offered {rps:g} rps by {concurrency} workers; 20% from client "
            "'noisy' (limit 2/s, burst 5), the rest from 8 clients (limit 50/s each)")
    res.log(json.dumps(res.metrics))
    return res


STAGES = {"config_lint": config_lint, "contract": contract, "routing": routing, "cache": cache, "load": load}

# Tolerances written into golden/<stage>.json at --write-golden time.
COMPARE = {
    "routing": {"accuracy": {"better": "higher", "abs": 0.02}},
    "cache": {"false_hit_rate": {"better": "lower", "abs": 0.0},
              "paraphrase_hit_rate": {"better": "higher", "abs": 0.05}},
    # laptop vs CI pod timings differ: generous on latency, strict on correctness.
    "load": {"p95_ms": {"better": "lower", "rel": 1.0, "abs": 25}, "error_rate": {"better": "lower", "abs": 0.01},
             "rate_429_others": {"better": "lower", "abs": 0.02},
             "rate_429_noisy": {"better": "higher", "abs": 0.2}},
}

# Files whose content decides the result. Unchanged inputs -> reuse the cached passing result.
INPUTS = {
    "config_lint": ["{routes}", "qualify/stages.py"],
    "contract": ["app/*.py", "mock_llm/*.py", "{golden}/openapi.json"],
    "routing": ["{routes}", "app/classifier.py", "app/config.py", "evals/dev.jsonl", "evals/test.jsonl",
                "{golden}/routing_decisions.json"],
    "cache": ["{routes}", "app/embedder.py", "app/classifier.py", "evals/semantic_pairs.jsonl",
              "evals/semantic_eval.py"],
    "load": ["{routes}", "app/*.py", "mock_llm/*.py"],
}
