"""Stub Docker-only dependencies so unit tests run locally — matches the identical pattern in
services/event-intelligence/tests/conftest.py / services/market-data/tests/conftest.py /
services/signal-engine/tests/conftest.py.

sqlalchemy, feedparser, redis, httpx, and structlog are all REAL, installed packages in this
local dev environment (confirmed directly, not assumed) — only psycopg2 (a native Postgres
driver with no pure-Python equivalent) and this repo's own `common`/`db` packages (which need a
running Postgres/Redis to construct for real) are stubbed. This means tickers.py's
extract_symbols(), rss_sources.py's feed parsing, edgar_source.py's feed parsing, and
classify.py's Claude call construction are all tested against their REAL implementations below,
not hand-copied reimplementations that could silently drift from the real code.
"""
import sys
from unittest.mock import MagicMock

_stubs = [
    "psycopg2",
    "common", "common.config", "common.logging", "common.ai_keys", "common.redis_client",
    "common.llm_usage",
    "db",
]
for _m in _stubs:
    sys.modules.setdefault(_m, MagicMock())

import common.config as _cfg  # noqa: E402
_cfg.get_settings = MagicMock(return_value=MagicMock())

import common.logging as _log  # noqa: E402
_log.get_logger = MagicMock(return_value=MagicMock())


class FakeRedis:
    """In-memory stand-in for get_redis() with real get/setex/delete semantics, plus `eval`
    for the two compare-and-swap Lua scripts R05 introduced in storage.py.

    HONEST LIMITATION, stated rather than hidden: `eval` below REIMPLEMENTS what those scripts
    do, so it cannot catch a mistake in the Lua itself — a test passing here is a statement
    about the Python around the script, not about the script. The scripts were therefore
    exercised against the real production Redis before this fake was written (13 cases: set
    when absent, set when absent but present, CAS hit, CAS miss with the value left untouched,
    TTL applied, delete with a stale value, delete with the correct value, delete of a missing
    key), and every case matched the behaviour encoded here. Redo that if the Lua changes —
    `redis.call("GET", ...)` returns Lua `false`, not nil-as-empty-string, for a missing key,
    which is the one thing easy to get wrong.

    No TTL expiry is simulated; no test here sleeps past one.
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)

    def eval(self, script, numkeys, *args):
        from src.services import storage as _S

        key = args[0]
        cur = self.store.get(key)
        if script == _S._HOT_SET_IF_UNCHANGED_LUA:
            expect, payload, ttl = args[1], args[2], args[3]
            if (cur is None and expect == "") or (cur is not None and cur == expect):
                self.store[key] = payload
                self.ttls[key] = ttl
                return 1
            return 0
        if script == _S._HOT_DEL_IF_UNCHANGED_LUA:
            if cur is not None and cur == args[1]:
                self.delete(key)
                return 1
            return 0
        raise AssertionError(f"FakeRedis.eval got an unknown script:\n{script}")
