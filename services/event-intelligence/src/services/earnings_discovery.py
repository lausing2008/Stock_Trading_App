"""Finding earnings events the platform does not have — and saying why each one is missing.

WHY A SEPARATE DISCOVERY STEP. `sync_todays_earnings()` selects EXISTING recent pending rows, so
an event absent from the table can never enter its candidate set; polling it faster can never
find what was never created. And the history sync upserts whatever the provider returns without
ever comparing that against what is stored, so a row that fails to map is indistinguishable from
one the provider never sent.

Discovery inverts that: ask the provider what it holds, compare it against the event table, and
report every row that is present upstream and absent locally, with the stage it stopped at.

READ-ONLY BY DEFAULT, AND PREVIEW BEFORE WRITE. `discover()` never writes. `repair()` defaults
to a dry run and must be told explicitly to commit, because writing a historical earnings row is
not reversible by re-running a job — a frozen report may already cite the absence.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from common.logging import get_logger

log = get_logger("event-intelligence.discovery")


def _resolve_stock(session, symbol: str):
    """The stock row for a symbol, or None."""
    _EV, _SL, Stock = _db()
    return session.execute(select(Stock).where(Stock.symbol == symbol)).scalars().first()


def _stored_report_dates(session, stock_id: int) -> list[date]:
    """Every stored announcement date for this issuer, ascending.

    Separated from the comparison so the rule being tested — which provider periods have no
    stored event — can be exercised without a database or an ORM entity. The comparison is the
    part with the defect potential; the query is not.
    """
    EarningsEvent, _SL, _ST = _db()
    return list(session.execute(
        select(EarningsEvent.report_date).where(EarningsEvent.stock_id == stock_id)
        .order_by(EarningsEvent.report_date.asc())).scalars().all())


def _db():
    """Imported at CALL time, not import time.

    Binding `Stock` and `EarningsEvent` at module import makes this file's behaviour depend on
    what else has been imported first — which showed up immediately: the module loaded cleanly
    on its own and, inside the full suite, picked up another test's mocked `db` and handed
    SQLAlchemy a MagicMock. A lazy import removes the coupling instead of papering over the
    ordering.
    """
    from db import EarningsEvent, SessionLocal, Stock
    return EarningsEvent, SessionLocal, Stock

#: Mapping outcomes a provider row can reach. Each names the stage it stopped at, because
#: "it didn't arrive" sends the next reader to the wrong place.
PRESENT = "present"                       # already stored as an event
ABSENT = "absent"                         # upstream has it, the event table does not
UNMAPPABLE = "unmappable"                 # returned, but no usable date could be derived


def _provider_rows(symbol: str) -> tuple[list[dict], str | None]:
    """What the provider holds for this issuer, normalised. Never raises."""
    try:
        import pandas as pd
        import yfinance as yf
    except Exception as exc:                            # noqa: BLE001
        return [], f"provider import failed: {type(exc).__name__}"
    try:
        hist = yf.Ticker(symbol).earnings_history
    except Exception as exc:                            # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:300]
    if hist is None or getattr(hist, "empty", True):
        return [], None
    rows = []
    for idx, row in hist.iterrows():
        try:
            period_end = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
        except Exception:                               # noqa: BLE001
            rows.append({"period_end": None, "raw_index": str(idx)[:40],
                         "outcome": UNMAPPABLE,
                         "reason": "the provider's index is not a usable date"})
            continue
        def _num(key):
            v = row.get(key)
            try:
                return float(v) if pd.notna(v) else None
            except Exception:                           # noqa: BLE001
                return None
        rows.append({"period_end": period_end,
                     "eps_actual": _num("epsActual"),
                     "eps_estimate": _num("epsEstimate"),
                     "outcome": None, "reason": None})
    return rows, None


def discover(symbol: str) -> dict:
    """Compare the provider against the event table. Writes nothing.

    A provider row is matched to a stored event by report date rather than by period end,
    because the stored `report_date` is the announcement date while the provider indexes by
    PERIOD END — the two differ by the announcement lag, which is exactly the quantity that
    must not be guessed. So the match is a bounded search for ANY event between the period end
    and the next period, and anything it cannot resolve is reported as such rather than assumed
    absent.
    """
    sym = symbol.upper().strip()
    _EV, SessionLocal, _ST = _db()
    with SessionLocal() as s:
        stock = _resolve_stock(s, sym)
        if stock is None:
            return {"symbol": sym, "error": f"{sym} is not in the universe"}
        stored_dates = _stored_report_dates(s, stock.id)

        rows, error = _provider_rows(sym)
        if error:
            return {"symbol": sym, "provider_error": error, "rows_returned": None,
                    "stage": "retrieval", "absent": [], "stored_events": len(stored_dates)}

        # Sorted period ends give each row its own upper bound: the next period's end.
        periods = sorted(r["period_end"] for r in rows if r["period_end"])
        today = date.today()
        for r in rows:
            if r.get("outcome") == UNMAPPABLE:
                continue
            pe = r["period_end"]
            later = [p for p in periods if p > pe]
            upper = later[0] if later else date(9999, 12, 31)
            # A RELEASED RESULT CANNOT HAVE BEEN ANNOUNCED BY AN EVENT THAT HAS NOT HAPPENED.
            #
            # Found by running this against production: MU's newest provider period has no next
            # period, so its upper bound was open-ended and it matched the FUTURE scheduled
            # event three months away — reporting a quarter that is genuinely missing as
            # present. The bound that fixes it needs no lag constant, only the fact that a row
            # carrying a reported EPS describes something that already happened.
            candidates = stored_dates
            if r.get("eps_actual") is not None:
                candidates = [d for d in stored_dates if d <= today]
            match = [d for d in candidates if pe <= d < upper]
            if match:
                r["outcome"] = PRESENT
                r["matched_report_date"] = match[0].isoformat()
            else:
                r["outcome"] = ABSENT
                r["reason"] = ("the provider holds this period and no stored event falls "
                               "between it and the next period")
        absent = [r for r in rows if r.get("outcome") == ABSENT]
        return {
            "symbol": sym,
            "stage": "compared",
            "rows_returned": len(rows),
            "stored_events": len(stored_dates),
            "present": sum(1 for r in rows if r.get("outcome") == PRESENT),
            "absent": [{k: (v.isoformat() if isinstance(v, date) else v)
                        for k, v in r.items()} for r in absent],
            "unmappable": [r for r in rows if r.get("outcome") == UNMAPPABLE],
            "newest_stored": stored_dates[-1].isoformat() if stored_dates else None,
            "newest_provider_period": periods[-1].isoformat() if periods else None,
            "note": ("a provider row with no eps_actual is a scheduled period, not a released "
                     "result; repair records what the provider holds and nothing more"),
        }


def repair(symbol: str, *, commit: bool = False, actor: str = "discovery") -> dict:
    """Write the absent events discovered above. PREVIEW unless `commit=True`.

    WHAT THIS DELIBERATELY DOES NOT DO. It does not invent a report date: the provider gives a
    PERIOD END, and the announcement date is a different fact. A repaired row therefore carries
    the period end as its report date with that substitution recorded, so nothing downstream can
    mistake it for a sourced announcement date. It does not touch frozen reports, does not send
    anything, and does not fabricate figures the provider did not supply.
    """
    found = discover(symbol)
    if found.get("error") or found.get("provider_error"):
        return found | {"committed": False}

    planned = [r for r in found["absent"] if r.get("eps_actual") is not None]
    skipped = [r for r in found["absent"] if r.get("eps_actual") is None]
    plan = {
        "symbol": found["symbol"],
        "would_write": planned,
        "skipped_no_actual": skipped,
        "skip_reason": ("no reported EPS, so this is a scheduled period rather than a released "
                        "result; the calendar sync owns those"),
        "report_date_substitution": ("report_date is set to the provider's PERIOD END because no "
                                     "announcement date is available; it is not a sourced "
                                     "announcement date and must not be read as one"),
        "committed": False,
    }
    if not commit or not planned:
        return plan

    written, failed = [], []
    EarningsEvent, SessionLocal, _ST = _db()
    with SessionLocal() as s:
        stock = _resolve_stock(s, plan["symbol"])
        for r in planned:
            pe = date.fromisoformat(r["period_end"])
            try:
                # IDEMPOTENT: a second run finds the row present and plans nothing.
                exists = s.execute(
                    select(EarningsEvent).where(EarningsEvent.stock_id == stock.id,
                                                EarningsEvent.report_date == pe)
                    .limit(1)).scalars().first()
                if exists is not None:
                    continue
                s.add(EarningsEvent(
                    stock_id=stock.id, report_date=pe,
                    eps_estimate=r.get("eps_estimate"), eps_actual=r.get("eps_actual"),
                    # The inferred fiscal columns are deliberately left NULL rather than
                    # computed from the period month — that label is wrong for every
                    # non-calendar fiscal year and writing it would add a known-bad fact.
                    period=None, fiscal_year=None, fiscal_quarter=None,
                    fetched_at=datetime.utcnow()))
                s.commit()
                written.append(r["period_end"])
            except Exception as exc:                    # noqa: BLE001
                s.rollback()
                failed.append({"period_end": r["period_end"],
                               "error": f"{type(exc).__name__}: {exc}"[:200]})
    log.info("earnings.discovery_repair", symbol=plan["symbol"], actor=actor,
             written=len(written), failed=len(failed))
    return plan | {"committed": True, "written": written, "failed": failed}
