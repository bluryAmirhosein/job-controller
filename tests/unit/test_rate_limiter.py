from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from app.core.rate_limiter import RateLimiter, rate_limit


class TestRateLimiterCheck:
    async def test_allows_request_when_under_limit(self):
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=[3, 60])
        limiter = RateLimiter(redis, max_requests=10, window_seconds=60, key_prefix="test")

        allowed, current, retry_after = await limiter.check("user-1")

        assert allowed is True
        assert current == 3
        assert retry_after == 60

    async def test_blocks_request_when_over_limit(self):
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=[11, 45])
        limiter = RateLimiter(redis, max_requests=10, window_seconds=60, key_prefix="test")

        allowed, current, retry_after = await limiter.check("user-1")

        assert allowed is False
        assert current == 11
        assert retry_after == 45

    async def test_fails_open_when_redis_is_unavailable(self):
        redis = AsyncMock()
        redis.eval = AsyncMock(side_effect=ConnectionError("redis down"))
        limiter = RateLimiter(redis, max_requests=10, window_seconds=60, key_prefix="test")

        allowed, current, retry_after = await limiter.check("user-1")

        assert allowed is True
        assert current == 0
        assert retry_after == 0

    async def test_falls_back_to_window_seconds_when_ttl_is_negative(self):
        # TTL can come back as -1 if the key somehow has no expiry set;
        # the implementation should fall back to window_seconds in that case.
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=[1, -1])
        limiter = RateLimiter(redis, max_requests=10, window_seconds=60, key_prefix="test")

        _, _, retry_after = await limiter.check("user-1")

        assert retry_after == 60

    async def test_uses_the_configured_key_prefix_and_identifier(self):
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=[1, 60])
        limiter = RateLimiter(
            redis, max_requests=10, window_seconds=60, key_prefix="rate_limit:jobs:create"
        )

        await limiter.check("user-42")

        args, _ = redis.eval.call_args
        script, num_keys, key, window = args
        assert num_keys == 1
        assert key == "rate_limit:jobs:create:user-42"
        assert window == 60


class TestRateLimitDependency:
    async def test_sets_headers_and_passes_through_when_under_limit(self, make_user):
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=[3, 60])
        dependency = rate_limit(max_requests=10, window_seconds=60, key_prefix="test")
        response = Response()
        user = make_user()

        await dependency(response=response, current_user=user, redis_client=redis)

        assert response.headers["X-RateLimit-Limit"] == "10"
        assert response.headers["X-RateLimit-Remaining"] == "7"

    async def test_raises_429_with_retry_after_header_when_over_limit(self, make_user):
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=[11, 45])
        dependency = rate_limit(max_requests=10, window_seconds=60, key_prefix="test")
        response = Response()
        user = make_user()

        with pytest.raises(HTTPException) as exc_info:
            await dependency(response=response, current_user=user, redis_client=redis)

        assert exc_info.value.status_code == 429
        assert exc_info.value.headers["Retry-After"] == "45"

    async def test_remaining_count_never_goes_negative(self, make_user):
        redis = AsyncMock()
        redis.eval = AsyncMock(return_value=[15, 30])
        dependency = rate_limit(max_requests=10, window_seconds=60, key_prefix="test")
        response = Response()
        user = make_user()

        with pytest.raises(HTTPException):
            await dependency(response=response, current_user=user, redis_client=redis)

        assert response.headers["X-RateLimit-Remaining"] == "0"