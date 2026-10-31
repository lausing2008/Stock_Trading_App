"""Who holds market-data's database connections when the pool runs low (2026-10-30).

On 2026-10-30 market-data hit `QueuePool limit of size 5 overflow 10 reached` three times
(19:00-19:35 UTC). The error names the job that could NOT get a connection, not the ones holding
them, and the logs were then lost when a deploy recreated the container. The 2026-09-22 incident
(docs/incidents/db-connection-pool-exhaustion.md) needed a live py-spy dump to find the holder.

This records, for every checked-out connection, when it was taken, by which thread, and from which
lines of our own code. When a checkout leaves the pool within `MARGIN` of its limit, it logs
`db.pool_pressure` with every holder, oldest first, at most once per `INTERVAL_S`. Observation
only: it never refuses, delays or closes a connection.
"""
from __future__ import annotations

import os
import sys
import sysconfig
import threading
import time

from common.logging import get_logger

log = get_logger(__name__)

MARGIN = 2
INTERVAL_S = 60.0
_MAX_FRAMES = 4

_LIBRARY_DIRS = tuple(os.path.realpath(p) for p in {sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["purelib"],
                                                     sysconfig.get_paths()["platlib"]} if p)
_ours: dict[str, bool] = {}   # filename -> is it our own code; realpath once per file, not per checkout
_holders: dict[int, tuple[float, str, list[str]]] = {}
_lock = threading.Lock()
_last_log = [0.0]


def _origin() -> list[str]:
    """Our own frames on the checkout stack, innermost first; stdlib and installed packages
    (SQLAlchemy, threading, APScheduler) are skipped."""
    out = []
    f = sys._getframe(2)
    while f is not None and len(out) < _MAX_FRAMES:
        name = f.f_code.co_filename
        ours = _ours.get(name)
        if ours is None:
            ours = _ours[name] = not (name.startswith("<") or "/site-packages/" in name
                                      or name.endswith("pool_watch.py")
                                      or os.path.realpath(name).startswith(_LIBRARY_DIRS))
        if ours:
            out.append(f"{name.rsplit('/src/', 1)[-1]}:{f.f_lineno} {f.f_code.co_name}")
        f = f.f_back
    return out


def snapshot(now: float | None = None) -> list[dict]:
    now = time.monotonic() if now is None else now
    with _lock:
        items = list(_holders.values())
    return [dict(age_s=round(now - t, 1), thread=th, origin=o)
            for t, th, o in sorted(items, key=lambda x: x[0])]


def install(engine) -> None:
    from sqlalchemy import event
    pool = engine.pool
    limit = pool.size() + getattr(pool, "_max_overflow", 0)

    @event.listens_for(pool, "checkout")
    def _checkout(dbapi_conn, record, proxy):  # noqa: ARG001
        with _lock:
            _holders[id(dbapi_conn)] = (time.monotonic(), threading.current_thread().name, _origin())
        try:
            used = pool.checkedout()
        except Exception:  # noqa: BLE001 - observation must never fail a checkout
            return
        now = time.monotonic()
        if used >= limit - MARGIN and now - _last_log[0] >= INTERVAL_S:
            _last_log[0] = now
            log.warning("db.pool_pressure", checked_out=used, limit=limit, holders=snapshot(now))

    @event.listens_for(pool, "checkin")
    def _checkin(dbapi_conn, record):  # noqa: ARG001
        with _lock:
            _holders.pop(id(dbapi_conn), None)

    @event.listens_for(pool, "close")
    def _close(dbapi_conn, record):  # noqa: ARG001
        with _lock:
            _holders.pop(id(dbapi_conn), None)


def install_safely() -> str:
    """Never fatal to startup."""
    try:
        from db.session import engine
        install(engine)
        return "ok"
    except Exception as exc:  # noqa: BLE001 - recorded and printed, never silent
        msg = f"failed: {type(exc).__name__}: {exc}"[:300]
        print(f"[pool_watch] not installed: {msg}")
        return msg
