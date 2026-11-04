"""Who holds market-data's database connections when the pool runs low (2026-10-30).

On 2026-10-30 market-data hit `QueuePool limit of size 5 overflow 10 reached` three times
(19:00-19:35 UTC). The error names the job that could NOT get a connection, not the ones holding
them, and the logs were then lost when a deploy recreated the container. The 2026-09-22 incident
(docs/incidents/db-connection-pool-exhaustion.md) needed a live py-spy dump to find the holder.

This records, for every checked-out connection, when it was taken, by which thread, and from which
lines of our own code. When a checkout leaves the pool within `MARGIN` of its limit, it logs
`db.pool_pressure` with every holder, oldest first, at most once per `INTERVAL_S`. Observation
only: it never refuses, delays or closes a connection.

AUD-IDLETX-LONGHOLD (2026-11-03): PostgreSQL ends any of our sessions idle in a transaction for
300 s (`ddl_guard.IDLE_IN_TX_TIMEOUT_MS`). On Nov 4 at 00:55 UTC it ended one that market-data
only noticed at 01:08:55, about 19 minutes after it was taken, with nothing naming the holder,
because the pool was nowhere near full. So a connection held past `LONG_HOLD_S` is logged once
while still held (`db.long_hold`, from the next checkout's sweep, before the server's kill) and
again when it finally returns (`db.long_hold_released`, with its total age), whatever the
pressure.
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
LONG_HOLD_S = 240.0
_MAX_FRAMES = 4

_LIBRARY_DIRS = tuple(os.path.realpath(p) for p in {sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["purelib"],
                                                     sysconfig.get_paths()["platlib"]} if p)
_ours: dict[str, bool] = {}   # filename -> is it our own code; realpath once per file, not per checkout
_holders: dict[int, tuple[float, str, list[str]]] = {}
_reported: set[int] = set()  # holders already logged as long holds while held
_lock = threading.Lock()
_last_log = [0.0]
_last_sweep = [0.0]
_clock = time.monotonic


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
    now = _clock() if now is None else now
    with _lock:
        items = list(_holders.values())
    return [dict(age_s=round(now - t, 1), thread=th, origin=o)
            for t, th, o in sorted(items, key=lambda x: x[0])]


def install(engine) -> None:
    from sqlalchemy import event
    pool = engine.pool
    limit = pool.size() + getattr(pool, "_max_overflow", 0)

    def _released(dbapi_conn, how: str) -> None:
        with _lock:
            held = _holders.pop(id(dbapi_conn), None)
            _reported.discard(id(dbapi_conn))
        if held is not None and _clock() - held[0] >= LONG_HOLD_S:
            log.warning("db.long_hold_released", how=how, age_s=round(_clock() - held[0], 1),
                        thread=held[1], origin=held[2])

    @event.listens_for(pool, "checkout")
    def _checkout(dbapi_conn, record, proxy):  # noqa: ARG001
        with _lock:
            _holders[id(dbapi_conn)] = (_clock(), threading.current_thread().name, _origin())
        try:
            used = pool.checkedout()
        except Exception:  # noqa: BLE001 - observation must never fail a checkout
            return
        now = _clock()
        if used >= limit - MARGIN and now - _last_log[0] >= INTERVAL_S:
            _last_log[0] = now
            log.warning("db.pool_pressure", checked_out=used, limit=limit, holders=snapshot(now))
        if now - _last_sweep[0] >= INTERVAL_S:
            _last_sweep[0] = now
            with _lock:
                long = [(k, v) for k, v in _holders.items()
                        if now - v[0] >= LONG_HOLD_S and k not in _reported]
                _reported.update(k for k, _ in long)
            for _, (t, th, o) in long:
                log.warning("db.long_hold", age_s=round(now - t, 1), thread=th, origin=o)

    @event.listens_for(pool, "checkin")
    def _checkin(dbapi_conn, record):  # noqa: ARG001
        _released(dbapi_conn, "checkin")

    @event.listens_for(pool, "close")
    def _close(dbapi_conn, record):  # noqa: ARG001
        _released(dbapi_conn, "close")


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
