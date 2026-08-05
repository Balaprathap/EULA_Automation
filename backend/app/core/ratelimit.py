"""Redis-backed sliding-window rate limiting, scoped per organization.

Falls open when Redis is unreachable: an outage in the limiter must not take
down the whole API. The failure is logged so it is visible rather than silent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RateLimiter:
    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client

    async def check(
        self, *, key: str, limit: int, window_seconds: int, cost: int = 1
    ) -> RateLimitDecision:
        if self.redis is None or limit <= 0:
            return RateLimitDecision(True, limit, limit, 0)

        now = time.time()
        bucket = f"ratelimit:{key}"
        cutoff = now - window_seconds

        try:
            pipeline = self.redis.pipeline()
            pipeline.zremrangebyscore(bucket, 0, cutoff)
            pipeline.zcard(bucket)
            results = await pipeline.execute()
            used = int(results[1] or 0)

            if used + cost > limit:
                oldest = await self.redis.zrange(bucket, 0, 0, withscores=True)
                retry_after = window_seconds
                if oldest:
                    retry_after = max(1, int(window_seconds - (now - oldest[0][1])))
                return RateLimitDecision(False, limit, 0, retry_after)

            pipeline = self.redis.pipeline()
            for i in range(cost):
                pipeline.zadd(bucket, {f"{now}:{i}": now})
            pipeline.expire(bucket, window_seconds + 1)
            await pipeline.execute()

            return RateLimitDecision(True, limit, max(0, limit - used - cost), 0)

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "rate limiter unavailable, failing open",
                extra={"error_type": type(exc).__name__},
            )
            return RateLimitDecision(True, limit, limit, 0)


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter


def set_rate_limiter(limiter: RateLimiter) -> None:
    global _limiter
    _limiter = limiter
