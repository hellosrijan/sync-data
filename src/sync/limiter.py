import math
import time
from typing import Callable, Optional

from django.conf import settings
import redis

# Atomic Lua token bucket:
# Evaluates rate, refilling tokens continuously, and checking pause.
# Returns:
#   1: token acquired
#  -1: paused (e.g. 429 slow down)
#  >1: wait milliseconds until token available
LUA_ACQUIRE = """
local rate_key = KEYS[1]
local tokens_key = KEYS[2]
local refill_key = KEYS[3]
local paused_key = KEYS[4]

local default_rate = tonumber(ARGV[1])
local now_ms = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])

-- 1. Check if throttled/paused
local paused_until = tonumber(redis.call('get', paused_key) or '0')
if now_ms < paused_until then
    return -1
end

-- 2. Current rate & capacity
local rate = tonumber(redis.call('get', rate_key) or default_rate)
local capacity = rate

-- 3. Token refill based on elapsed time
local tokens = tonumber(redis.call('get', tokens_key) or capacity)
local last_refill = tonumber(redis.call('get', refill_key) or now_ms)
local elapsed_ms = math.max(0, now_ms - last_refill)
local refill = (elapsed_ms / 1000.0) * rate
tokens = math.min(capacity, tokens + refill)
redis.call('set', refill_key, now_ms)

-- 4. Check availability
if tokens >= cost then
    tokens = tokens - cost
    redis.call('set', tokens_key, tokens)
    return 1
else
    redis.call('set', tokens_key, tokens)
    local needed = cost - tokens
    local wait_ms = math.ceil((needed / rate) * 1000)
    return math.max(1, wait_ms)
end
"""


class RateLimiter:
    """Atomic Redis token-bucket rate limiter shared by all workers (design §6, build-plan 5.2)."""

    def __init__(
        self,
        redis_client: Optional[redis.Redis] = None,
        default_rate: Optional[int] = None,
        key_prefix: str = "sync:limiter",
        clock: Optional[Callable[[], float]] = None,
    ):
        self.redis = redis_client or redis.Redis.from_url(settings.DRAMATIQ_BROKER["OPTIONS"]["url"])
        self.default_rate = default_rate or getattr(settings, "SALESFORCE_RATE_LIMIT", 90)
        self.prefix = key_prefix
        self.clock = clock or time.time
        self._script = self.redis.register_script(LUA_ACQUIRE)

    @property
    def rate_key(self) -> str:
        return f"{self.prefix}:rate"

    @property
    def tokens_key(self) -> str:
        return f"{self.prefix}:tokens"

    @property
    def refill_key(self) -> str:
        return f"{self.prefix}:last_refill"

    @property
    def paused_key(self) -> str:
        return f"{self.prefix}:paused_until"

    def get_current_rate(self) -> int:
        val = self.redis.get(self.rate_key)
        return int(val) if val is not None else self.default_rate

    def acquire(self, timeout: float = 5.0, cost: int = 1) -> bool:
        """Acquire `cost` tokens, blocking until available or `timeout` expires.

        Returns True on success, False if timeout exceeded.
        """
        start = time.monotonic()
        while True:
            now_ms = int(self.clock() * 1000)
            res = self._script(
                keys=[self.rate_key, self.tokens_key, self.refill_key, self.paused_key],
                args=[self.default_rate, now_ms, cost],
            )
            if res == 1:
                return True

            elapsed = time.monotonic() - start
            remaining = timeout - elapsed
            if remaining <= 0:
                return False

            if res == -1:  # paused
                sleep_dur = min(0.05, remaining)
            else:  # wait_ms
                sleep_dur = min(max(0.001, res / 1000.0), remaining, 0.05)

            time.sleep(sleep_dur)

    def throttle(self, retry_after: int = 1) -> int:
        """On HTTP 429: pause limiter for `retry_after` seconds and halve the current rate.

        Returns the new rate.
        """
        now_ms = int(self.clock() * 1000)
        paused_until = now_ms + int(max(1, retry_after) * 1000)
        self.redis.set(self.paused_key, paused_until)

        curr_rate = self.get_current_rate()
        new_rate = max(1, curr_rate // 2)
        self.redis.set(self.rate_key, new_rate)
        return new_rate

    def recover(self, step: int = 5) -> int:
        """Gradually recover rate by `step` req/s every 30s back up to default_rate."""
        curr_rate = self.get_current_rate()
        if curr_rate < self.default_rate:
            new_rate = min(self.default_rate, curr_rate + step)
            self.redis.set(self.rate_key, new_rate)
            return new_rate
        return curr_rate

    def reset(self) -> None:
        """Reset rate limiter state (useful in tests)."""
        self.redis.delete(self.rate_key, self.tokens_key, self.refill_key, self.paused_key)


_default_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter()
    return _default_limiter
