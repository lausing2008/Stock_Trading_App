"""F03 observation phase — record what each generation published, without changing anything.

Design: docs/features/2026-10-27-f03-signal-occurrence-and-delivery-attribution-design.md §11
and §12. The historical rows keep only the first fire and the final state, so they cannot say how
often a (stock, horizon) changes direction or strength within a session; this measures it from
now on. It writes three tables beside the existing `signals` columns and is read by nothing that
decides, sends or scores — the sender, eligibility and outcome paths are untouched.

Called after each `signals` upsert, inside a SAVEPOINT: a failure here rolls back only the
observation and is logged, never the signal.

Rules (§11 decision 1):
  * an occurrence is one continuous BUY or SELL run for a (stock, horizon) in one VENUE session;
  * HOLD and WAIT close it (`to_hold` / `to_wait`) and never open one;
  * the opposite direction closes it (`direction_change`) and opens the next `seq`;
  * a later session closes it (`session_change`), whatever the signal;
  * confidence changes stay inside the occurrence and are counted, not stored per generation.

The session is the one the generation's newest daily bar belongs to (F02 `input_contract`), and
only when that bar is `forming` or `completed`. A generation with no session identity is not
observed, and a generation reading an OLDER session than the open occurrence is ignored.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import JSON, bindparam, text

log = structlog.get_logger()

CONTRACT_VERSION = "f03-observe-v1"
ACTIONABLE = ("BUY", "SELL")
_LEVEL_KEYS = ("last_price", "entry_price", "stop_loss", "target_price", "take_profit")
_HORIZONS = ("SHORT", "SWING", "LONG", "GROWTH")
_LOCK_NAMESPACE = 0x0F03


def enabled() -> bool:
    return os.environ.get("F03_OBSERVATION", "1") != "0"


def _canonical(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def decision_content(signal: str, horizon: str, confidence, bullish_probability, reasons) -> dict:
    """Everything that is the decision. Only the generation's own clock is removed (§11)."""
    reasons = _canonical(dict(reasons or {}))
    contract = reasons.get("input_contract")
    if isinstance(contract, dict):
        reasons["input_contract"] = {k: v for k, v in contract.items() if k != "generated_at"}
    return {"signal": signal, "horizon": horizon, "confidence": _canonical(confidence),
            "bullish_probability": _canonical(bullish_probability), "reasons": reasons}


def content_hash(content: dict) -> str:
    blob = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def session_identity(reasons) -> tuple[date, str, bool] | None:
    """(session_date, market, session_closed) of the newest bar, or None if not a session bar."""
    contract = (reasons or {}).get("input_contract") or {}
    if contract.get("last_bar_status") not in ("forming", "completed") or not contract.get("last_bar_date"):
        return None
    try:
        d = date.fromisoformat(contract["last_bar_date"])
    except (TypeError, ValueError):
        return None
    closed = contract.get("last_bar_status") == "completed" and contract.get("basis") == "session_closed"
    return d, str(contract.get("market") or "US"), closed


def _lock(s, stock_id: int, horizon: str) -> None:
    # Serialises every generation for one (stock, horizon) until the caller commits. A row lock on
    # `signals` would not do: one session can span two UTC daily rows (US evening runs).
    if s.get_bind().dialect.name != "postgresql":
        return
    key = int(stock_id) * 8 + (_HORIZONS.index(horizon) if horizon in _HORIZONS else 7)
    s.execute(text("SELECT pg_advisory_xact_lock(:ns, :k)"), {"ns": _LOCK_NAMESPACE, "k": key})


# Typed binds, not CAST(:x AS json): SQLite gives an unknown type name NUMERIC affinity and would
# turn the JSON text into 0. The JSON type serialises the dict on both dialects.
_SNAPSHOT_INSERT = text("""
    INSERT INTO signal_snapshots
        (occurrence_id, signal_id, stock_id, horizon, session_date, kind, signal, captured_at,
         confidence, bullish_probability, levels, reasons, input_contract, content_hash,
         contract_version)
    VALUES (:occ, :sid, :stock, :hor, :sd, :kind, :sig, :now, :conf, :bp, :levels, :reasons,
            :ic, :hash, :ver)
    RETURNING id
""").bindparams(bindparam("levels", type_=JSON(none_as_null=True)),
                bindparam("reasons", type_=JSON(none_as_null=True)),
                bindparam("ic", type_=JSON(none_as_null=True)))


def _snapshot(s, *, occurrence_id, signal_id, stock_id, horizon, session_date, kind, content,
              digest, input_contract, now) -> int:
    reasons = content["reasons"]
    levels = {k: reasons[k] for k in _LEVEL_KEYS if reasons.get(k) is not None} or None
    row = s.execute(_SNAPSHOT_INSERT, dict(
        occ=occurrence_id, sid=signal_id, stock=stock_id, hor=horizon, sd=session_date, kind=kind,
        sig=content["signal"], now=now, conf=content["confidence"],
        bp=content["bullish_probability"], levels=levels, reasons=reasons,
        ic=_canonical(input_contract) if input_contract else None, hash=digest,
        ver=CONTRACT_VERSION)).first()
    return int(row[0])


def observe_generation(s, *, stock_id: int, horizon: str, signal: str, confidence,
                       bullish_probability, reasons, signal_id: int | None = None,
                       now: datetime | None = None) -> dict:
    """Record one published generation. Returns what was done, for logs and tests."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    ident = session_identity(reasons)
    if ident is None:
        return {"observed": False, "reason": "no_session_identity"}
    session_date, market, session_closed = ident
    content = decision_content(signal, horizon, confidence, bullish_probability, reasons)
    digest = content_hash(content)
    direction = signal if signal in ACTIONABLE else None
    out: dict = {"observed": True, "session_date": session_date.isoformat(), "closed": None,
                 "opened": None, "continued": None, "completed_close": None}

    _lock(s, stock_id, horizon)
    open_occ = s.execute(text("""
        SELECT id, direction, session_date, last_content_hash FROM signal_occurrences
        WHERE stock_id = :stock AND horizon = :hor AND closed_at IS NULL
    """), {"stock": stock_id, "hor": horizon}).first()
    open_session = None
    if open_occ is not None:
        open_session = open_occ[2] if isinstance(open_occ[2], date) else date.fromisoformat(str(open_occ[2]))
        if session_date < open_session:
            return {"observed": False, "reason": "older_session_than_open_occurrence"}

    if open_occ is not None:
        if session_date > open_session:
            reason = "session_change"
        elif direction is None:
            reason = "to_wait" if signal == "WAIT" else "to_hold"
        elif direction != open_occ[1]:
            reason = "direction_change"
        else:
            reason = None
        if reason is None:
            s.execute(text("""
                UPDATE signal_occurrences
                SET generations = generations + 1,
                    content_changes = content_changes + CASE WHEN last_content_hash = :hash THEN 0 ELSE 1 END,
                    last_content_hash = :hash, last_seen_at = :now
                WHERE id = :id
            """), {"hash": digest, "now": now, "id": open_occ[0]})
            out["continued"] = int(open_occ[0])
        else:
            s.execute(text("""
                UPDATE signal_occurrences SET closed_at = :now, close_reason = :reason WHERE id = :id
            """), {"now": now, "reason": reason, "id": open_occ[0]})
            out["closed"] = {"id": int(open_occ[0]), "reason": reason}
            open_occ = None

    occurrence_id = out["continued"]
    if direction is not None and open_occ is None:
        seq = s.execute(text("""
            SELECT COALESCE(MAX(seq), 0) + 1 FROM signal_occurrences
            WHERE stock_id = :stock AND horizon = :hor AND session_date = :sd
        """), {"stock": stock_id, "hor": horizon, "sd": session_date}).scalar()
        occurrence_id = int(s.execute(text("""
            INSERT INTO signal_occurrences
                (stock_id, horizon, direction, market, session_date, seq, opened_at, opened_signal_id,
                 last_seen_at, generations, content_changes, last_content_hash, contract_version)
            VALUES (:stock, :hor, :dir, :mkt, :sd, :seq, :now, :sid, :now, 1, 0, :hash, :ver)
            RETURNING id
        """), dict(stock=stock_id, hor=horizon, dir=direction, mkt=market, sd=session_date,
                   seq=seq, now=now, sid=signal_id, hash=digest, ver=CONTRACT_VERSION)).first()[0])
        snap = _snapshot(s, occurrence_id=occurrence_id, signal_id=signal_id, stock_id=stock_id,
                         horizon=horizon, session_date=session_date, kind="opened",
                         content=content, digest=digest,
                         input_contract=(reasons or {}).get("input_contract"), now=now)
        out["opened"] = {"id": occurrence_id, "seq": int(seq), "snapshot_id": snap}

    # The target session is the newest bar's own session, so yesterday's completed bar can only
    # ever freeze yesterday's record — never today's (§11 correction).
    if session_closed:
        exists = s.execute(text("""
            SELECT 1 FROM signal_session_closes
            WHERE stock_id = :stock AND horizon = :hor AND session_date = :sd
        """), {"stock": stock_id, "hor": horizon, "sd": session_date}).first()
        if exists is None:
            snap = _snapshot(s, occurrence_id=occurrence_id if direction else None,
                             signal_id=signal_id, stock_id=stock_id, horizon=horizon,
                             session_date=session_date, kind="completed_close", content=content,
                             digest=digest, input_contract=(reasons or {}).get("input_contract"),
                             now=now)
            s.execute(text("""
                INSERT INTO signal_session_closes
                    (stock_id, horizon, session_date, snapshot_id, captured_at, close_ingest_verified)
                VALUES (:stock, :hor, :sd, :snap, :now, NULL)
            """), {"stock": stock_id, "hor": horizon, "sd": session_date, "snap": snap, "now": now})
            out["completed_close"] = snap
    return out


def observe_safely(s, **kwargs) -> dict | None:
    """The call site's entry point: a SAVEPOINT, so a failure never touches the signal upsert."""
    if not enabled():
        return None
    try:
        with s.begin_nested():
            return observe_generation(s, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("signal.occurrence_observe_failed", stock_id=kwargs.get("stock_id"),
                    horizon=kwargs.get("horizon"), error=str(exc)[:300])
        return None


def observation_report(s, since: date | None = None) -> dict:
    """§12 read-only parity report: the first real measurements of within-session churn."""
    where = "WHERE session_date >= :since" if since else ""
    p = {"since": since} if since else {}
    per_session = s.execute(text(f"""
        SELECT session_date, market, horizon, COUNT(*) AS occurrences,
               COUNT(DISTINCT (stock_id)) AS stocks,
               SUM(CASE WHEN seq > 1 THEN 1 ELSE 0 END) AS later_runs,
               SUM(generations) AS generations, SUM(content_changes) AS content_changes,
               SUM(CASE WHEN content_changes > 0 THEN 1 ELSE 0 END) AS occurrences_with_changes
        FROM signal_occurrences {where}
        GROUP BY session_date, market, horizon ORDER BY session_date, market, horizon
    """), p).mappings().all()
    reasons = s.execute(text(f"""
        SELECT COALESCE(close_reason, 'open') AS reason, COUNT(*) AS n
        FROM signal_occurrences {where} GROUP BY 1 ORDER BY 1
    """), p).mappings().all()
    closes = s.execute(text(f"""
        SELECT c.session_date, sn.signal, COUNT(*) AS n
        FROM signal_session_closes c JOIN signal_snapshots sn ON sn.id = c.snapshot_id
        {where.replace('session_date', 'c.session_date')}
        GROUP BY c.session_date, sn.signal ORDER BY c.session_date, sn.signal
    """), p).mappings().all()
    out = {
        "contract_version": CONTRACT_VERSION,
        "per_session": [dict(r) for r in per_session],
        "close_reasons": {r["reason"]: int(r["n"]) for r in reasons},
        "completed_close_by_signal": [dict(r) for r in closes],
        "note": ("Counts are generations OBSERVED by this process from deployment onward; "
                 "a session with no post-close generation has no completed-close record."),
    }
    if s.get_bind().dialect.name == "postgresql":
        out["table_bytes"] = {t: int(s.execute(text("SELECT pg_total_relation_size(:t)"), {"t": t}).scalar())
                              for t in ("signal_occurrences", "signal_snapshots", "signal_session_closes")}
    return out
