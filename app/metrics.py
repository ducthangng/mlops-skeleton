"""Prometheus metrics. Scraped from GET /metrics.

Cache hit rate (PromQL):
  sum(rate(gateway_cache_hits_total[5m])) by (type)
    / ignoring(type) group_left sum(rate(gateway_request_duration_seconds_count[5m]))
"""

from prometheus_client import Counter, Histogram

REQUEST_LATENCY = Histogram(
    "gateway_request_duration_seconds",
    "End-to-end latency of /v1/chat/completions",
    ["route", "cache"],                       # cache = exact | semantic | miss
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
CACHE_HITS = Counter("gateway_cache_hits_total", "Cache hits", ["type"])   # type = exact | semantic
CACHE_MISSES = Counter("gateway_cache_misses_total", "Requests that missed every cache layer")
CACHE_ERRORS = Counter("gateway_cache_errors_total", "Redis errors swallowed by fail-open", ["op"])
RATE_LIMITED = Counter("gateway_rate_limited_total", "Requests rejected with 429")
RATE_LIMIT_ERRORS = Counter("gateway_rate_limit_errors_total", "Rate limiter could not reach Redis")
UPSTREAM_CALLS = Counter("gateway_upstream_calls_total", "HTTP attempts to the LLM backend", ["model"])
UPSTREAM_ERRORS = Counter("gateway_upstream_errors_total", "Failed upstream attempts", ["model", "reason"])
UPSTREAM_FALLBACKS = Counter("gateway_upstream_fallbacks_total", "Requests served by the fallback model")
