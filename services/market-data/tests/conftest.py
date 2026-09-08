"""Stub out all Docker-only dependencies so unit tests run locally."""
import sys
from unittest.mock import MagicMock

_stubs = [
    # shared/ modules
    "structlog",
    "common", "common.config", "common.logging", "common.ai_keys", "common.redis_client",
    "common.uw_congress", "common.llm_usage",
    "db", "db.session", "db.models",
    # DB / cache drivers
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects",
    "sqlalchemy.dialects.postgresql",
    "psycopg2", "redis",
    # Third-party data adapters not installed locally
    "yfinance", "tenacity",
    "alpha_vantage", "alpha_vantage.timeseries",
    "polygon", "polygon.rest",
    "httpx",
    # apscheduler — declared prod dep, previously never stubbed, which meant scheduler.py
    # (28 check_*/send_* alert functions) could not be imported by any test at all.
    "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
    "apscheduler.triggers", "apscheduler.triggers.combining", "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
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

# AUD-HOLIDAY-2027GAP: common.market_calendar must also be REAL, for the same reason as
# common.indicators above — it carries actual holiday DATA, and every guard built on it is a
# membership test. Under the blanket "common" MagicMock, `date(...) in NYSE_HOLIDAYS` returns a
# truthy Mock, so a holiday test would pass no matter what the calendar contained (or whether it
# contained anything at all). Pure stdlib (datetime/zoneinfo), so there is nothing to stub.
_calendar_path = _pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "market_calendar.py"
_cal_spec = _ilu.spec_from_file_location("common.market_calendar", _calendar_path)
_calendar_mod = _ilu.module_from_spec(_cal_spec)
_cal_spec.loader.exec_module(_calendar_mod)
sys.modules["common.market_calendar"] = _calendar_mod
setattr(sys.modules["common"], "market_calendar", _calendar_mod)
