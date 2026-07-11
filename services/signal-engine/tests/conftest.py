"""Stub Docker-only dependencies so unit tests run locally."""
import sys
from unittest.mock import MagicMock

_stubs = [
    "structlog",
    "common", "common.config", "common.logging",
    "db", "db.session",
    "httpx", "redis",
]
for _m in _stubs:
    sys.modules.setdefault(_m, MagicMock())

import common.config as _cfg  # noqa: E402
_cfg.get_settings = MagicMock(return_value=MagicMock())

import common.logging as _log  # noqa: E402
_log.get_logger = MagicMock(return_value=MagicMock())

# common.indicators has no env/structlog dependencies (pure pandas/numpy) and signals.py
# needs the REAL implementation (not a MagicMock) to compute actual RSI/MACD values in
# tests — load it for real instead of leaving it under the blanket "common" stub above.
# Matches the identical pattern in services/market-data/tests/conftest.py.
import importlib.util as _ilu  # noqa: E402
import pathlib as _pathlib  # noqa: E402
_indicators_path = _pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "indicators.py"
_spec = _ilu.spec_from_file_location("common.indicators", _indicators_path)
_indicators_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_indicators_mod)
sys.modules["common.indicators"] = _indicators_mod
setattr(sys.modules["common"], "indicators", _indicators_mod)
