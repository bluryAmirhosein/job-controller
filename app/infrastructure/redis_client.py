from redis import asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()

redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)


def get_redis_client() -> aioredis.Redis:
    return redis_client


async def check_redis_health() -> bool:
    try:
        return await redis_client.ping()
    except Exception:
        return False