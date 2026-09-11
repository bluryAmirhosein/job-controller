# app/infrastructure/cache/job_list_cache.py
"""
Cache helpers for the paginated job list endpoint.

Strategy: versioned cache-aside.

Each "scope" (a specific owner id, or "admin" for the unfiltered admin
view) has a version counter stored in Redis. The cache key for a given
page of results embeds the current version and the page's cursor, so
bumping the version makes every previously cached page for that scope
unreachable immediately, without needing to know or delete every
cursor/limit combination that was ever cached.

Whenever a job is created or its status changes, the repository bumps
both the owning user's scope and the "admin" scope, since an admin's
unfiltered job list is affected by every owner's jobs.

A short TTL is still applied to cached pages as a safety net (e.g. in
case a version bump and a concurrent read race), not as the primary
invalidation mechanism.
"""

from redis.asyncio import Redis

JOB_LIST_CACHE_TTL_SECONDS = 60
ADMIN_SCOPE = "admin"


def _version_key(scope: str) -> str:
    return f"jobs:list:version:{scope}"


def _data_key(scope: str, version: int, limit: int, cursor: str) -> str:
    return f"jobs:list:data:{scope}:{version}:{limit}:{cursor}"


async def get_job_list_version(redis: Redis, scope: str) -> int:
    raw = await redis.get(_version_key(scope))
    return int(raw) if raw is not None else 0


async def bump_job_list_version(redis: Redis, scope: str) -> None:
    await redis.incr(_version_key(scope))


async def get_cached_job_list(
    redis: Redis, scope: str, version: int, limit: int, cursor: str
) -> str | None:
    return await redis.get(_data_key(scope, version, limit, cursor))


async def set_cached_job_list(
    redis: Redis, scope: str, version: int, limit: int, cursor: str, payload: str
) -> None:
    await redis.set(
        _data_key(scope, version, limit, cursor),
        payload,
        ex=JOB_LIST_CACHE_TTL_SECONDS,
    )