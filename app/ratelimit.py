"""Per-client token bucket in Redis, one Lua script = one atomic round trip.

Why Lua: read-modify-write of (tokens, timestamp) must be atomic across all
router pods. Why redis TIME inside the script: one clock for every pod.

Failure policy (RATE_LIMIT_FAIL_MODE):
  open   (default) Redis down -> allow the request. Availability over protection;
         right for an internal gateway where the cache is also fail-open.
  closed Redis down -> reject with 503. Right when the limit protects a paid
         upstream budget and overspending is worse than downtime.
"""

import logging
from dataclasses import dataclass

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.metrics import RATE_LIMIT_ERRORS

log = logging.getLogger(__name__)

TOKEN_BUCKET_LUA = """
local rate  = tonumber(ARGV[1])            -- tokens per second
local burst = tonumber(ARGV[2])            -- bucket size
local t     = redis.call('TIME')
local now   = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)   -- ms
local b      = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1]) or burst
local ts     = tonumber(b[2]) or now
tokens = math.min(burst, tokens + (now - ts) / 1000 * rate)
local allowed, retry_ms = 0, 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry_ms = math.ceil((1 - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(burst / rate * 1000) + 1000)
return {allowed, retry_ms}
"""


@dataclass
class Verdict:
    allowed: bool
    retry_after_s: int = 0
    degraded: bool = False          # True = Redis unavailable, policy decided


class RateLimiter:
    def __init__(self, r: aioredis.Redis | None, rate: float, burst: int,
                 clients: dict[str, tuple[float, int]] | None = None, fail_mode: str = "open"):
        self.r = r
        self.rate, self.burst = rate, burst
        self.clients = clients or {}            # api key -> (rate, burst) overrides
        self.fail_open = fail_mode != "closed"
        self._script = r.register_script(TOKEN_BUCKET_LUA) if r is not None else None

    def limits_for(self, client: str) -> tuple[float, int]:
        return self.clients.get(client, (self.rate, self.burst))

    async def check(self, client: str) -> Verdict:
        if self._script is None:
            return Verdict(allowed=self.fail_open, degraded=True)
        rate, burst = self.limits_for(client)
        try:
            allowed, retry_ms = await self._script(keys=[f"rl:{client}"], args=[rate, burst])
        except (RedisError, OSError) as e:
            RATE_LIMIT_ERRORS.inc()
            log.warning("rate limiter degraded (%s): fail-%s", e, "open" if self.fail_open else "closed")
            return Verdict(allowed=self.fail_open, degraded=True)
        return Verdict(allowed=bool(allowed), retry_after_s=max(1, -(-int(retry_ms) // 1000)) if not allowed else 0)


def parse_clients(spec: str) -> dict[str, tuple[float, int]]:
    """'key1=50:100,key2=1:2' -> {'key1': (50.0, 100), 'key2': (1.0, 2)}"""
    out = {}
    for item in filter(None, (s.strip() for s in spec.split(","))):
        key, limits = item.split("=", 1)
        rate, burst = limits.split(":", 1)
        out[key] = (float(rate), int(burst))
    return out
