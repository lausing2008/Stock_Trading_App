"""Stub Docker-only dependencies so unit tests run locally — matches the identical pattern in
market-data/signal-engine/ml-prediction/portfolio-optimizer's conftest.py files.

Pre-existing gap: test_kscore.py already needed this (kscore.py imports common.indicators) but
no conftest.py existed yet, so `pytest tests/` failed at collection before any test ran. Added
while touching this service for T247-RANKINGENGINE-CROSSMARKET.
"""
import importlib.util as _ilu
import pathlib as _pathlib
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.logging", MagicMock())
sys.modules["common.logging"].get_logger = MagicMock(return_value=MagicMock())

# common.indicators is pure pandas/numpy (no env/structlog deps) and kscore.py needs the REAL
# implementation to compute actual RSI/ATR values in tests.
_indicators_path = _pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "indicators.py"
_spec = _ilu.spec_from_file_location("common.indicators", _indicators_path)
_indicators_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_indicators_mod)
sys.modules["common.indicators"] = _indicators_mod
setattr(sys.modules["common"], "indicators", _indicators_mod)
