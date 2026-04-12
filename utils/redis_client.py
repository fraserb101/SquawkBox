"""Shared Redis client.

Provides lazily-initialized Redis connections using the REDIS_URL from config.
Two flavors:
  - get_redis(): decode_responses=True for string keys/values (rate limits,
    locks, idempotency flags — everything text-based).
  - get_redis_binary(): decode_responses=False for raw byte values (currently
    used to cache voice-note audio that Twilio fetches via /media/{token}).
"""

from typing import Optional

import redis

from utils.config import REDIS_URL

_client: Optional[redis.Redis] = None
_binary_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """Return a lazily-initialized Redis client with string decoding enabled.

    The connection is created on first call and reused across the process.
    """
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def get_redis_binary() -> redis.Redis:
    """Return a lazily-initialized Redis client that returns raw bytes.

    Use this for values that aren't text (e.g. audio bytes). Keeping a
    separate client avoids mixing string and binary decoding semantics on
    the same connection pool.
    """
    global _binary_client
    if _binary_client is None:
        _binary_client = redis.from_url(REDIS_URL, decode_responses=False)
    return _binary_client
