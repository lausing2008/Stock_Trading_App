"""Shared Redis client with a connection pool — import and use instead of redis.from_url().

All services that need Redis should call get_redis() rather than creating their
own connection per-request. redis-py's ConnectionPool is thread-safe and reuses
sockets, avoiding the overhead of a new TCP handshake on every call.

Usage:
    from common.redis_client import get_redis
    r = get_redis()
    r.set("key", "value")
"""
from .config import get_settings

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        import redis as redis_lib
        settings = get_settings()
        _pool = redis_lib.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=20,
            socket_connect_timeout=2,
            socket_timeout=5,
            retry_on_timeout=True,
        )
    return _pool


def get_redis():
    """Return a Redis client backed by the shared connection pool."""
    import redis as redis_lib
    return redis_lib.Redis(connection_pool=_get_pool())
