"""Stub out all Docker-only dependencies so unit tests run locally."""
import sys
from unittest.mock import MagicMock

_stubs = [
    # shared/ modules
    "structlog",
    "common", "common.config", "common.logging", "common.ai_keys",
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

# common.indicators has no env/structlog dependencies (pure pandas/numpy) and
# candidate_event_mining.py needs the REAL implementation (not a MagicMock) to compute
# actual ATR values in tests — load it for real instead of leaving it under the blanket
# "common" stub above.
import importlib.util as _ilu  # noqa: E402
import pathlib as _pathlib  # noqa: E402
_indicators_path = _pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "indicators.py"
_spec = _ilu.spec_from_file_location("common.indicators", _indicators_path)
_indicators_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_indicators_mod)
sys.modules["common.indicators"] = _indicators_mod
setattr(sys.modules["common"], "indicators", _indicators_mod)
