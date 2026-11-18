"""KV cache plugin slot.

Write your own class with this shape and point GATEWAY_CACHE_CLASS at it, e.g.
    GATEWAY_CACHE_CLASS=my_cache.redis_kv:RedisKV

The gateway owns the key (see `cache_key`) and the value format (a JSON string).
Your cache only stores strings. Errors and slow calls are caught by the gateway and
treated as a miss (fail-open), so the cache can never take a chat down.
"""

import hashlib
import json
import time
from collections import OrderedDict
from typing import Protocol, runtime_checkable

KEY_VERSION = "v2"  # bump to invalidate every entry when the key format changes


@runtime_checkable
class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_s: int | None = None) -> None: ...

    # Optional lifecycle hooks; the gateway calls them if they exist:
    #   async def startup(self) -> None
    #   async def shutdown(self) -> None


class NullCache:
    """Used when no cache is configured: always a miss."""

    enabled = False

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_s: int | None = None) -> None:
        return None


class MemoryCache:
    """In-process LRU. A reference implementation and a stand-in until yours is ready.

    Not shared between gateway replicas. Enable with
    GATEWAY_CACHE_CLASS=gateway.plugins.cache:MemoryCache
    """

    def __init__(self, max_items: int = 1000):
        self.max_items = max_items
        self._data: OrderedDict[str, tuple[float | None, str]] = OrderedDict()

    async def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires is not None and expires < time.monotonic():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl_s: int | None = None) -> None:
        expires = time.monotonic() + ttl_s if ttl_s else None
        self._data[key] = (expires, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)


def cache_key(provider: str, model: str, params: dict, messages: list[dict]) -> str:
    """Exact-match key over everything that changes the answer.

    Two requests share a key only if they go to the same model with the same params,
    system prompt and full conversation.
    """
    body = json.dumps(
        {"provider": provider, "model": model, "params": params, "messages": messages},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"gw:{KEY_VERSION}:{provider}:{model}:{hashlib.sha256(body.encode()).hexdigest()}"
