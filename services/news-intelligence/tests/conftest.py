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
    "db",
]
for _m in _stubs:
    sys.modules.setdefault(_m, MagicMock())

import common.config as _cfg  # noqa: E402
_cfg.get_settings = MagicMock(return_value=MagicMock())

import common.logging as _log  # noqa: E402
_log.get_logger = MagicMock(return_value=MagicMock())
