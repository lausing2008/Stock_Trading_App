"""Config-driven signal thresholds — loaded from signal_thresholds.json.

Hot-reloadable via POST /signals/admin/reload_config (admin JWT required).
All access goes through get_thresholds() so callers automatically see reloads.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger()

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "signal_thresholds.json"
_lock = threading.Lock()
_thresholds: dict = {}
_loaded_at: datetime | None = None


def _load() -> dict:
    with open(_CONFIG_PATH) as f:
        data = json.load(f)
    keys = [k for k in data if not k.startswith("_")]
    log.info("signal_thresholds.loaded", path=str(_CONFIG_PATH), sections=keys)
    return data


def _ensure_loaded() -> None:
    global _thresholds, _loaded_at
    if _thresholds:
        return
    with _lock:
        if _thresholds:
            return
        try:
            _thresholds = _load()
            _loaded_at = datetime.now(timezone.utc)
        except Exception as exc:
            log.warning("signal_thresholds.load_failed", error=str(exc))
            _thresholds = {}


def get_thresholds() -> dict:
    _ensure_loaded()
    return _thresholds


def reload() -> dict:
    global _thresholds, _loaded_at
    with _lock:
        _thresholds = _load()
        _loaded_at = datetime.now(timezone.utc)
    return {"loaded_at": _loaded_at.isoformat(), "sections": [k for k in _thresholds if not k.startswith("_")]}


def loaded_at() -> str | None:
    return _loaded_at.isoformat() if _loaded_at else None
