"""Redis async client.

Use cases:
- Notification retry queue
- Distributed lock cho reconciliation job
- Cache (optional)
"""

import redis.asyncio as redis

from app.core.config import get_settings

settings = get_settings()

# Singleton client, auto manage connection pool
redis_client: redis.Redis = redis.from_url(
    str(settings.redis_url),
    decode_responses=True,  # auto decode bytes → str
    encoding="utf-8",
)


async def get_redis() -> redis.Redis:
    """FastAPI dependency cho redis client."""
    return redis_client


async def close_redis() -> None:
    """Close connection pool. Gọi ở app shutdown."""
    await redis_client.aclose()
