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
    "common", "common.config", "common.logging",
    "db", "db.session",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects",
    "sqlalchemy.dialects.postgresql",
    "psycopg2", "httpx", "pandas",
]
for _m in _stubs:
    sys.modules.setdefault(_m, MagicMock())

import common.config as _cfg  # noqa: E402
_cfg.get_settings = MagicMock(return_value=MagicMock())

import common.logging as _log  # noqa: E402
_log.get_logger = MagicMock(return_value=MagicMock())
