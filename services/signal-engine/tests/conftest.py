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
