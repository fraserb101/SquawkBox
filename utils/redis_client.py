"""Shared Redis client.

Provides a single lazily-initialized Redis connection using the REDIS_URL
from config. Use this module wherever Redis is needed to avoid creating
multiple connection pools.
"""

from typing import Optional

import redis

from utils.config import REDIS_URL

_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """Return a lazily-initialized Redis client.

    The connection is created on first call and reused across the process.
    """
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client
