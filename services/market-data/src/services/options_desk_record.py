"""Options candidate outcome study, Part B: an append-only record of what the desk served.

Design: docs/features/2026-10-29-options-candidate-outcome-study-design.md, "Part B". Part A
replays the builder from the archive with inputs it can reconstruct; it cannot say what the desk
showed with the inputs a user really gave it (stop, target, holding period, preferences) under
the code running that day. This records exactly that, from the response the game-plan route has
already built: no extra provider request and no extra archive read.

RULES
  * Append-only. A row is never updated or deleted here. Its key is (ET date, symbol, record
    hash); the hash covers the code fingerprint, the inputs and the shortlist, so a rule change or
    a different input files a new row BESIDE the old one, and a repeat of the same request the
    same day files nothing.
  * Recording never affects the response. Any failure is logged and swallowed by `record()`.
  * No user identity is stored: the inputs describe the request, not who made it.
  * The research lead is recomputed with the frontend's rules (option_research_lead, golden-pinned)
    only when the desk would have computed it without user preferences, with an integer holding
    period. Otherwise the reason it was not recorded is stored instead of a guess.
  * Outcomes are not computed here. They come later from Part A's marking code, so both parts use
    one outcome definition.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import JSON, Column, Date, DateTime, Index, MetaData, String, Table

log = structlog.get_logger()

RECORD_VERSION = "options_desk_record_v1"
STRATEGIES = ("long_call", "protective_put", "covered_call", "cash_secured_put")

metadata = MetaData()
records = Table(
    "options_desk_records_v1", metadata,
    Column("record_date", Date, primary_key=True),          # the route's ET session date
    Column("symbol", String(32), primary_key=True),
    Column("record_hash", String(64), primary_key=True),
    Column("record_version", String(40), nullable=False),
    Column("code_fingerprint", String(64), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("chain_session", String(10)),
    Column("inputs", JSON, nullable=False),
    Column("shortlist", JSON, nullable=False),
    Column("recorded_at", DateTime, nullable=False),
)
Index("ix_options_desk_records_v1_symbol_date", records.c.symbol, records.c.record_date)


def ensure_schema():
    """A NEW table only: create_all creates it and never alters an existing one."""
    from db import SessionLocal
    with SessionLocal() as session:
        metadata.create_all(session.get_bind())


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str,
                                     allow_nan=False).encode()).hexdigest()


_FINGERPRINT: dict | None = None


def code_fingerprint() -> dict:
    """SHA-256 of the code that decides what the desk shows, computed once per process.

    Whole files for the candidate modules; for routes.py only the functions on this path, so an
    unrelated route edit does not look like a rule change."""
    global _FINGERPRINT
    if _FINGERPRINT is None:
        from . import (option_research_lead, option_search_preferences, options_contract,
                       options_scope, options_strategies, uw_option_chain)
        from ..api import routes
        parts = {m.__name__.rsplit(".", 1)[-1]: hashlib.sha256(inspect.getsource(m).encode()).hexdigest()
                 for m in (options_strategies, uw_option_chain, options_contract, options_scope,
                           option_search_preferences, option_research_lead)}
        for fn in (routes.compute_options_game_plan, routes.get_options_game_plan,
                   routes._nearest_expiry_in_dte_window, routes._nearest_strike):
            parts["routes." + fn.__name__] = hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()
        _FINGERPRINT = {"parts": parts, "sha256": _digest(parts)}
    return _FINGERPRINT


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _leg_summary(strategy: dict | None) -> dict | None:
    legs = (strategy or {}).get("legs") or []
    if len(legs) != 1:
        return None
    leg = legs[0]
    return {"option_symbol": leg.get("option_symbol"), "right": leg.get("right"), "action": leg.get("action"),
            "strike": _num(leg.get("strike")), "expiry": leg.get("expiry"),
            "price_per_share": _num(leg.get("price_per_share")), "price_source": leg.get("price_source"),
            "spread_pct": _num(leg.get("spread_pct")), "oi": _num(leg.get("oi")),
            "iv": _num(leg.get("iv")), "delta": _num(leg.get("delta"))}


def _lead(key, matrix, review, today, holding_days, preferences) -> dict:
    from .option_research_lead import research_lead
    if any(v is not None for v in (preferences or {}).values()):
        return {"status": "not_recorded_with_preferences"}
    if not (isinstance(holding_days, int) and not isinstance(holding_days, bool)):
        return {"status": "not_recorded_without_holding_period"}
    lead = research_lead(key, matrix, review, today, holding_days)
    if lead is None:
        return {"status": "no_qualifying_candidate"}
    return {"status": "recomputed", "index": lead[1], "contract": _leg_summary(lead[0])}


def build_record(*, symbol: str, plan: dict, today: date, stop_loss, take_profit, shares,
                 holding_days, preferences, chain_session: str | None) -> dict:
    """Pure: the stored row's content from an already-built game plan."""
    matrix = (plan or {}).get("strategy_matrix") or {}
    reviews = matrix.get("candidate_review") or {}
    shortlist = {}
    for key in STRATEGIES:
        review = reviews.get(key)
        preset = (matrix.get("singles") or {}).get(key)
        if review is None:
            shortlist[key] = {"review": None, "preset": _leg_summary(preset)}
            continue
        shortlist[key] = {
            "method_version": review.get("method_version"),
            "scope": review.get("scope"),
            "anchor": _num(review.get("anchor")),
            "expiry": review.get("expiry"),
            "searched_expiries": review.get("searched_expiries"),
            "failed_expiries": review.get("failed_expiries"),
            "evaluated_count": review.get("evaluated_count"),
            "screened_count": review.get("screened_count"),
            "rejected_counts": review.get("rejected_counts") or {},
            "original": review.get("original"),
            "preset": _leg_summary(preset),
            "alternatives": [_leg_summary(s) for s in review.get("alternatives") or []],
            "lead": _lead(key, matrix, review, today, holding_days, preferences),
        }
    inputs = {"stop_loss": _num(stop_loss), "take_profit": _num(take_profit), "shares": _num(shares),
              "holding_days": holding_days if isinstance(holding_days, int) and not isinstance(holding_days, bool) else None,
              "preferences": preferences or {}, "current_price": _num((plan or {}).get("current_price")),
              "chain_session": chain_session, "chain_as_of": (plan or {}).get("chain_as_of"),
              "contract_terms_refused": (plan or {}).get("contract_terms_refused")}
    fingerprint = code_fingerprint()["sha256"]
    return {"record_date": today, "symbol": symbol, "record_version": RECORD_VERSION,
            "code_fingerprint": fingerprint, "input_hash": _digest(inputs), "chain_session": chain_session,
            "inputs": inputs, "shortlist": shortlist,
            "record_hash": _digest({"v": RECORD_VERSION, "code": fingerprint, "inputs": inputs, "shortlist": shortlist})}


def persist(session, row: dict) -> bool:
    """Insert-or-nothing inside a SAVEPOINT; True when a new row was filed.

    Uses the caller's session, never a second connection: a nested session per request while the
    caller holds one is how the pool was exhausted in AUD-CONNPOOL-NESTEDSESSION."""
    if session.get_bind().dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    with session.begin_nested():
        filed = session.execute(insert(records).values(
            **row, recorded_at=datetime.now(timezone.utc).replace(tzinfo=None))
            .on_conflict_do_nothing().returning(records.c.record_hash)).scalar()
    session.commit()
    return filed is not None


def record(session, **kwargs) -> None:
    """Called by the game-plan route after the response is built. Never raises."""
    try:
        if not kwargs.get("plan") or not (kwargs["plan"].get("strategy_matrix") or {}).get("candidate_review"):
            return
        persist(session, build_record(**kwargs))
    except Exception as exc:
        log.warning("options_desk_record.failed", symbol=kwargs.get("symbol"), error=type(exc).__name__)
        try:
            session.rollback()
        except Exception:
            pass
