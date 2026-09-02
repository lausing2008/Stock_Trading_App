"""Stub Docker-only dependencies so unit tests run locally — matches the identical pattern in
services/market-data/tests/conftest.py and services/signal-engine/tests/conftest.py.

Every scoring function in this service calls its DB-backed getter (get_congress_for_symbol,
get_insider_for_symbol, get_institutional_for_symbol, get_days_to_earnings, get_beat_rate,
days_to_next_fomc) UNCONDITIONALLY — unlike decision-engine's hard_rejects.py, there is no
argument that lets a caller bypass the DB call. Tests monkeypatch/mock each getter at the
CONSUMING module's namespace (e.g. src.services.catalyst.get_beat_rate, not
src.services.earnings.get_beat_rate), since each getter is imported by name into the module
that calls it — the stubs below only need to make the module-level imports succeed.
"""
import sys
from unittest.mock import MagicMock

_stubs = [
    "structlog",
    "common", "common.config", "common.logging", "common.redis_client",
    "common.ai_keys", "common.uw_congress",
    "db", "db.session",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects",
    "sqlalchemy.dialects.postgresql",
    "psycopg2", "httpx", "pandas",
]
for _m in _stubs:
    sys.modules.setdefault(_m, MagicMock())

# AUD-EARNINGSFORECAST: sys.modules.setdefault(...) alone registers each submodule under its
# dotted key but does NOT link it as an attribute on the parent `common` mock — Python's real
# import machinery normally does this bookkeeping itself for a genuine package, but a bare
# MagicMock parent has no such behavior. Without this explicit link, `from common.redis_client
# import get_redis` (the exact statement earnings.py's own lazy imports use) resolves via
# getattr(sys.modules["common"], "redis_client") — which auto-vivifies a DIFFERENT, unlinked
# child mock, not the one registered in sys.modules["common.redis_client"] above. A test
# patching the sys.modules entry (or the dotted-string form pytest's monkeypatch.setattr()
# resolves via the identical getattr path) would then silently observe a mock the real import
# never reaches. Mirrors the identical explicit-link fix already applied for common.indicators
# in market-data/tests/conftest.py.
for _m in ("config", "logging", "redis_client", "ai_keys", "uw_congress"):
    setattr(sys.modules["common"], _m, sys.modules[f"common.{_m}"])

import common.config as _cfg  # noqa: E402
_cfg.get_settings = MagicMock(return_value=MagicMock())

import common.logging as _log  # noqa: E402
_log.get_logger = MagicMock(return_value=MagicMock())
