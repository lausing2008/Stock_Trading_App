"""Stub out all Docker-only dependencies so unit tests run locally."""
import sys
from unittest.mock import MagicMock

_stubs = [
    # shared/ modules
    "structlog",
    "common", "common.config", "common.logging", "common.ai_keys", "common.redis_client",
    "common.uw_congress", "common.llm_usage",
    # R07: auth.py imports revoke_user_tokens from here. Stubbed rather than real because the
    # real module resolves `_settings.jwt_secret` and a Redis client at IMPORT time; the
    # authorization behaviour itself is tested for real in
    # services/ml-prediction/tests/test_r07_account_state_authz.py, which loads the actual
    # file. What this stub buys is that a missing import is still caught here — the whole
    # suite errored out until it was added, which is exactly the signal wanted.
    "common.jwt_auth",
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

# R07: auth.py imports revoke_user_tokens / user_tokens_revoked from the stubbed common.jwt_auth.
# A bare MagicMock attribute returns a truthy Mock, so user_tokens_revoked() would report EVERY
# token revoked and 401 the entire auth suite — a stub failing open in the dangerous direction.
# Real callables with the real default (nothing revoked); the revocation behaviour itself is
# tested against the actual module in ml-prediction's test_r07_account_state_authz.py.
import common.jwt_auth as _jwtauth  # noqa: E402
_jwtauth.user_tokens_revoked = lambda _payload: False
_jwtauth.revoke_user_tokens = lambda _username: True

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

# SR-01/SR-02: common.signal_time and common.conviction_gate must be REAL for the same reason
# as the three above. Both are pure decision logic over values, and under the blanket "common"
# MagicMock every call returns a truthy Mock — so a test asserting that a STALE conviction
# record no longer vetoes an entry would pass against a module that still vetoed, and a test
# asserting that an unreadable timestamp blocks would pass against one that approved. Pure
# stdlib (datetime/json/dataclasses), so there is nothing to stub. Order matters:
# conviction_gate imports signal_time, so signal_time is registered first.
for _mod_name in ("signal_time", "conviction_gate"):
    _p = _pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / f"{_mod_name}.py"
    _sp = _ilu.spec_from_file_location(f"common.{_mod_name}", _p)
    _m = _ilu.module_from_spec(_sp)
    sys.modules[f"common.{_mod_name}"] = _m
    _sp.loader.exec_module(_m)
    setattr(sys.modules["common"], _mod_name, _m)

# AUD-ALERTPREFS: common.alert_prefs must be REAL for the same reason as the two above — it is
# all membership tests and an HMAC comparison. Under the blanket "common" MagicMock,
# `is_manageable("price_alert")` returns a truthy Mock, so a test asserting that ESSENTIAL mail
# can never be switched off would pass against a module that permitted exactly that. Likewise
# hmac.compare_digest against a Mock is meaningless, which would silently void every token test.
# Pure stdlib (hmac/hashlib), so there is nothing to stub.
_prefs_path = _pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "alert_prefs.py"
_prefs_spec = _ilu.spec_from_file_location("common.alert_prefs", _prefs_path)
_prefs_mod = _ilu.module_from_spec(_prefs_spec)
_prefs_spec.loader.exec_module(_prefs_mod)
sys.modules["common.alert_prefs"] = _prefs_mod
setattr(sys.modules["common"], "alert_prefs", _prefs_mod)
