import logging

from fastapi import Depends, HTTPException, Response, status
from redis.asyncio import Redis

from app.infrastructure.redis_client import get_redis_client
from app.models.user import User
from app.core.dependencies import get_current_user

logger = logging.getLogger("rate_limiter")


_RATE_LIMIT_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if tonumber(current) == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
local ttl = redis.call("TTL", KEYS[1])
return {current, ttl}
"""


class RateLimiter:
    def __init__(
        self,
        redis_client: Redis,
        *,
        max_requests: int,
        window_seconds: int,
        key_prefix: str,
    ):
        self._redis = redis_client
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._key_prefix = key_prefix

    async def check(self, identifier: str) -> tuple[bool, int, int]:
        """Returns (allowed, current_count, retry_after_seconds)."""
        key = f"{self._key_prefix}:{identifier}"
        try:
            current, ttl = await self._redis.eval(
                _RATE_LIMIT_SCRIPT, 1, key, self._window_seconds
            )
        except Exception:
            # Redis در دسترس نیست: fail-open. ترجیح دادم رِیت لیمیتر پایین بودنش
            # کل create_job رو غیرقابل‌استفاده نکنه؛ فقط لاگ می‌کنیم که مطلع باشیم.
            logger.warning("Rate limiter backend unavailable, allowing request", exc_info=True)
            return True, 0, 0

        current = int(current)
        ttl = int(ttl) if int(ttl) > 0 else self._window_seconds
        allowed = current <= self._max_requests
        return allowed, current, ttl


def rate_limit(*, max_requests: int, window_seconds: int, key_prefix: str):
    """Dependency factory: هر endpoint که بهش نیاز داره جداگانه صداش می‌زنه
    با محدودیت‌های خودش (مثلاً create_job: 10/60s، یه endpoint دیگه: 100/60s)."""

    async def _dependency(
        response: Response,
        current_user: User = Depends(get_current_user),
        redis_client: Redis = Depends(get_redis_client),
    ) -> None:
        limiter = RateLimiter(
            redis_client,
            max_requests=max_requests,
            window_seconds=window_seconds,
            key_prefix=key_prefix,
        )
        allowed, current, retry_after = await limiter.check(str(current_user.id))

        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(max(max_requests - current, 0))

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: max {max_requests} requests per {window_seconds}s.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency