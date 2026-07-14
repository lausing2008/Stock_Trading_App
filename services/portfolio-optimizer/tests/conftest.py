"""Stub Docker-only dependencies so unit tests run locally.

methods.py imports common.logging (T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT — added logging for
previously-silent SLSQP failures), and routes.py imports common.jwt_auth/common.config —
none available outside the container. Matches the identical stubbing pattern in
market-data/signal-engine/ml-prediction's conftest.py files. fastapi/pydantic/httpx are real,
installed packages locally, so they are NOT stubbed — routes.py's real Pydantic models
(OptimizeRequest, OptimizeConstraints) are exercised for real in tests.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.logging", MagicMock())
sys.modules["common.logging"].get_logger = MagicMock(return_value=MagicMock())

sys.modules.setdefault("common.config", MagicMock())
sys.modules["common.config"].get_settings = MagicMock(return_value=MagicMock())

sys.modules.setdefault("common.jwt_auth", MagicMock())
