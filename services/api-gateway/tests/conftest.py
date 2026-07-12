"""Stub Docker-only dependencies (Redis, common.config) so proxy.py's pure auth/routing
logic can be unit tested locally, matching the same pattern used by the other services'
conftest.py files (market-data, signal-engine, research-engine)."""
import sys
from unittest.mock import MagicMock

_settings_mock = MagicMock()
_settings_mock.jwt_secret = "test-secret-not-a-real-key"

_config_module = MagicMock()
_config_module.get_settings = MagicMock(return_value=_settings_mock)
sys.modules.setdefault("common", MagicMock())
sys.modules["common.config"] = _config_module
sys.modules.setdefault("redis", MagicMock())
