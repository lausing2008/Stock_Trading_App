"""Stub Docker-only dependencies so unit tests run locally.

methods.py now imports common.logging (T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT — added logging
for previously-silent SLSQP failures), which pulls in structlog/env config not available
outside the container. Matches the identical stubbing pattern in market-data/signal-engine/
ml-prediction's conftest.py files.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.logging", MagicMock())
sys.modules["common.logging"].get_logger = MagicMock(return_value=MagicMock())
