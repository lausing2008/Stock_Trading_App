"""Stub out all Docker-only dependencies so unit tests run locally."""
import sys
from unittest.mock import MagicMock

_stubs = [
    # shared/ modules
    "structlog",
    "common", "common.config", "common.logging",
    "db", "db.session",
    # DB / cache drivers
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects",
    "sqlalchemy.dialects.postgresql",
    "psycopg2", "redis",
    # Third-party data adapters not installed locally
    "yfinance", "tenacity",
    "alpha_vantage", "alpha_vantage.timeseries",
    "polygon", "polygon.rest",
    "httpx",
]
for _m in _stubs:
    sys.modules.setdefault(_m, MagicMock())

# get_settings() must return an object — called at module level in ingestion.py
import common.config as _cfg  # noqa: E402
_cfg.get_settings = MagicMock(return_value=MagicMock())

import common.logging as _log  # noqa: E402
_log.get_logger = MagicMock(return_value=MagicMock())
