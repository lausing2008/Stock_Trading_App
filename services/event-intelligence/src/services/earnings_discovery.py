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


def _stored_events(session, stock_id: int) -> list[dict]:
    """Every stored event for this issuer with the facts the comparison needs.

    CARRIES WHETHER A RESULT IS RECORDED, not just a date. A date range alone let a PENDING
    placeholder — an event row with no reported figures — mark a released provider result as
    "present", so a quarter whose results nobody had could look covered because a scheduled
    entry happened to sit in the window.

    Separated from the comparison so the rule being tested can be exercised without a database
    or an ORM entity. The comparison is the part with defect potential; the query is not.
    """
    EarningsEvent, _SL, _ST = _db()
    rows = session.execute(
        select(EarningsEvent.id, EarningsEvent.report_date, EarningsEvent.eps_actual,
               EarningsEvent.revenue_actual, EarningsEvent.report_date_source)
        .where(EarningsEvent.stock_id == stock_id)
        .order_by(EarningsEvent.report_date.asc())).all()
    return [{"id": r[0], "report_date": r[1],
             "has_result": r[2] is not None or r[3] is not None,
             "report_date_source": r[4]} for r in rows]


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
PENDING_PLACEHOLDER = "pending_placeholder"   # an event row exists but records no result

#: Written to `EarningsEvent.report_date_source` when the provider gave only a period end.
SUBSTITUTED_PERIOD_END = "substituted_period_end"


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
        stored = _stored_events(s, stock.id)
        stored_dates = [e["report_date"] for e in stored]

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
            released = r.get("eps_actual") is not None
            candidates = [e for e in stored if pe <= e["report_date"] < upper]
            if released:
                # A released result cannot have been announced by an event that has not
                # happened yet.
                candidates = [e for e in candidates if e["report_date"] <= today]
            in_window = candidates
            # A RELEASED RESULT IS ONLY "PRESENT" IF A RESULT IS ACTUALLY RECORDED. An event row
            # with no reported figures is a scheduled placeholder, and a date range alone let
            # one of those mark a quarter covered that nobody holds the results for.
            if released:
                candidates = [e for e in candidates if e["has_result"]]
            if candidates:
                r["outcome"] = PRESENT
                r["matched_report_date"] = candidates[0]["report_date"].isoformat()
                r["matched_event_id"] = candidates[0]["id"]
                src = candidates[0].get("report_date_source")
                if src == SUBSTITUTED_PERIOD_END:
                    r["matched_date_is_substituted"] = True
            elif in_window:
                # A row exists in the window but records nothing. Distinct from absent, because
                # the repair should UPDATE it rather than insert a second event for the quarter.
                r["outcome"] = PENDING_PLACEHOLDER
                r["matched_event_id"] = in_window[0]["id"]
                r["matched_report_date"] = in_window[0]["report_date"].isoformat()
                r["reason"] = ("an event row covers this period but records no reported result, "
                               "so the quarter is scheduled rather than captured")
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
            "pending_placeholder": [{k: (v.isoformat() if isinstance(v, date) else v)
                                     for k, v in r.items()}
                                    for r in rows if r.get("outcome") == PENDING_PLACEHOLDER],
            "absent": [{k: (v.isoformat() if isinstance(v, date) else v)
                        for k, v in r.items()} for r in absent],
            "unmappable": [r for r in rows if r.get("outcome") == UNMAPPABLE],
            "newest_stored": stored_dates[-1].isoformat() if stored_dates else None,
            "newest_provider_period": periods[-1].isoformat() if periods else None,
            "note": ("a provider row with no eps_actual is a scheduled period, not a released "
                     "result; repair records what the provider holds and nothing more"),
        }


def repair(symbol: str, *, commit: bool = False, actor: str = "discovery") -> dict:
    """Record the absent results discovered above. PREVIEW unless `commit=True`.

    WHAT IT WRITES, AND WHAT IT REFUSES TO CLAIM. The provider supplies a PERIOD END; the
    announcement date is a different fact, weeks later — MU's fiscal Q4 ended 2026-08-31 and was
    announced 2026-09-30. So the period end goes in `period_end`, where it belongs, and
    `report_date` carries it only as a STAND-IN with `report_date_source` recording that on the
    row. The first version put the substitution warning in this function's return value, which
    protects nobody: every consumer reads `report_date` as the announcement date, and the next
    reader is a SQL query. The return-window calculation anchors on exactly that date, so an
    unmarked substitution would have published pre-release prices as the post-earnings reaction.

    NOTIFICATION REPLAY IS SUPPRESSED EXPLICITLY rather than left to window arithmetic. A
    repaired row is stamped as already-notified so a historical result can never enter a
    delivery path — "the window happens not to reach it" is a property of today's constants, not
    a guarantee, and those constants change.

    A PENDING PLACEHOLDER IS UPDATED, NOT DUPLICATED. An event row that covers the period but
    records no result is the same quarter; inserting a second event for it would create exactly
    the ambiguity discovery exists to remove.
    """
    found = discover(symbol)
    if found.get("error") or found.get("provider_error"):
        return found | {"committed": False}

    planned = [r for r in found["absent"] if r.get("eps_actual") is not None]
    fill = [r for r in found.get("pending_placeholder", []) if r.get("eps_actual") is not None]
    skipped = [r for r in found["absent"] if r.get("eps_actual") is None]
    plan = {
        "symbol": found["symbol"],
        "would_insert": planned,
        "would_fill_placeholder": fill,
        "skipped_no_actual": skipped,
        "skip_reason": ("no reported EPS, so this is a scheduled period rather than a released "
                        "result; the calendar sync owns those"),
        "report_date_substitution": (
            "the provider gives a PERIOD END, not an announcement date. The period end is stored "
            "in `period_end`; `report_date` carries it as a stand-in and `report_date_source` is "
            "set to 'substituted_period_end' ON THE ROW, so the return calculation and every "
            "other consumer can refuse to treat it as an announcement date."),
        "notification_suppression": (
            "repaired rows are stamped as already-notified, so a historical result cannot enter "
            "any delivery path regardless of what the notification windows are set to."),
        "committed": False,
    }
    if not commit or not (planned or fill):
        return plan

    EarningsEvent, SessionLocal, _ST = _db()
    inserted, filled, failed = [], [], []
    now = datetime.utcnow()
    with SessionLocal() as s:
        stock = _resolve_stock(s, plan["symbol"])
        for r in fill:
            try:
                row = s.get(EarningsEvent, r["matched_event_id"])
                if row is None:
                    continue
                row.eps_actual = r.get("eps_actual")
                if row.eps_estimate is None:
                    row.eps_estimate = r.get("eps_estimate")
                row.period_end = date.fromisoformat(r["period_end"])
                row.fetched_at = now
                row.impact_sent_at = row.impact_sent_at or now
                s.commit()
                filled.append({"event_id": row.id, "period_end": r["period_end"]})
            except Exception as exc:                    # noqa: BLE001
                s.rollback()
                failed.append({"period_end": r["period_end"],
                               "error": f"{type(exc).__name__}: {exc}"[:200]})
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
                    stock_id=stock.id,
                    report_date=pe,                     # a STAND-IN, marked as such below
                    period_end=pe,                      # where this date actually belongs
                    report_date_source=SUBSTITUTED_PERIOD_END,
                    eps_estimate=r.get("eps_estimate"), eps_actual=r.get("eps_actual"),
                    # The inferred fiscal columns are left NULL rather than computed from the
                    # period month — that label is wrong for every non-calendar fiscal year.
                    period=None, fiscal_year=None, fiscal_quarter=None,
                    # Suppression, stated rather than inferred from a window.
                    impact_sent_at=now,
                    fetched_at=now))
                s.commit()
                inserted.append(r["period_end"])
            except Exception as exc:                    # noqa: BLE001
                s.rollback()
                failed.append({"period_end": r["period_end"],
                               "error": f"{type(exc).__name__}: {exc}"[:200]})
    log.info("earnings.discovery_repair", symbol=plan["symbol"], actor=actor,
             inserted=len(inserted), filled=len(filled), failed=len(failed))
    return plan | {"committed": True, "inserted": inserted, "filled": filled, "failed": failed}
