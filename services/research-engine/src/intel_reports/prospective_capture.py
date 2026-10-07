"""Capturing what is expected BEFORE the result, from sources the platform already holds.

WHY THIS RUNS ON A SCHEDULE RATHER THAN ON DEMAND. `earnings_events.eps_estimate` is a current
value: every refresh overwrites it in place. So the consensus as it stood a week before a
release is not recoverable by any query — it is only recoverable by having copied it out on the
day. A capture job that runs daily turns a mutable column into an immutable series; running the
same code once, later, recovers nothing.

NOTHING HERE BACKFILLS. A snapshot for a date on which the job did not run cannot be created
afterwards, and the gap is the honest record: it says we do not know what was expected then,
which is true, rather than asserting today's figure was yesterday's.

CAPTURE IS NOT INGESTION. This reads rows another service already fetched and writes them to an
append-only table. It makes no provider call and spends no budget.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from common.logging import get_logger
from .quality_value import naive_utc
from .quality_value_store import capture_estimate

log = get_logger("research-engine.prospective_capture")

#: Only events still ahead of us. A consensus captured after the release has already been
#: contaminated by the result, which is the same rule the macro side enforces by raising.
HORIZON_DAYS = 120


#: What each metric is measured in, per the source. Declared rather than inferred: a number
#: whose units are not recorded cannot later be compared with anything, and this platform has
#: already come within one step of dividing an unlabelled estimate into an issuer figure.
METRIC_UNITS = {"eps": "USD_per_share", "revenue": "USD"}


def estimates_to_capture(event_rows, *, now: datetime, horizon_days: int = HORIZON_DAYS) -> dict:
    """Decide what to snapshot. PURE — takes (event, symbol) pairs, touches no database.

    Separated from the write so the selection rules are testable at all: the service conftest
    stubs `db`, so a function that imports ORM classes cannot be imported from a test, and
    stubbing the ORM well enough to exercise a query expression only tests the stub.

    Returns the rows to write plus the counts that explain what was left out and why. A metric
    with no estimate is SKIPPED, never written as zero — a zero consensus is a real and
    different claim from an absent one.
    """
    now = naive_utc(now)
    horizon = (now + timedelta(days=horizon_days)).date()
    to_write, skipped_no_estimate, out_of_horizon = [], 0, 0

    for ev, symbol in event_rows:
        # ALREADY RELEASED IS OUT. A consensus read after the print is contaminated by it, and
        # storing that as an expectation makes every surprise look smaller than it was.
        if ev.report_date <= now.date() or ev.report_date > horizon:
            out_of_horizon += 1
            continue
        for metric in ("eps", "revenue"):
            value = getattr(ev, f"{metric}_estimate" if metric == "eps" else "revenue_estimate")
            if value is None:
                skipped_no_estimate += 1
                continue
            to_write.append({
                "symbol": symbol,
                # THE PROVIDER'S LABEL, keyed to the report date. Not a fiscal quarter: the
                # platform's stored fiscal_quarter is derived from the calendar month and is
                # wrong for any non-calendar financial year, and this must not inherit it.
                "target_period": f"report_{ev.report_date.isoformat()}",
                "metric": metric,
                "units": METRIC_UNITS[metric],
                # NULL RECORDS THAT THE PROVIDER DID NOT SAY. Assuming GAAP would fabricate the
                # one field that decides whether this can ever be compared with an actual.
                "accounting_basis": None,
                "provider": "earnings_events",
                "value": float(value),
                "captured_at": now,
                "raw": {"report_date": ev.report_date.isoformat(), "event_id": ev.id},
            })

    return {"to_write": to_write, "skipped_no_estimate": skipped_no_estimate,
            "out_of_horizon": out_of_horizon, "horizon_days": horizon_days,
            "as_of": now.isoformat(),
            "note": "accounting_basis is NULL for every row: the upstream source does not "
                    "record one. These estimates therefore cannot be compared with an "
                    "issuer-reported actual without crossing an unverified basis boundary."}


def capture_earnings_estimates(session, *, now: datetime | None = None,
                               horizon_days: int = HORIZON_DAYS) -> dict:
    """Snapshot the current consensus for every scheduled, not-yet-released earnings event."""
    from db import EarningsEvent, Stock

    now = naive_utc(now or datetime.now(timezone.utc))
    horizon = (now + timedelta(days=horizon_days)).date()
    rows = list(session.execute(
        select(EarningsEvent, Stock.symbol)
        .join(Stock, Stock.id == EarningsEvent.stock_id)
        .where(EarningsEvent.report_date > now.date(),
               EarningsEvent.report_date <= horizon)).all())

    plan = estimates_to_capture(rows, now=now, horizon_days=horizon_days)
    captured = already = 0
    for row in plan["to_write"]:
        _id, created = capture_estimate(session, **row)
        if created:
            captured += 1
        else:
            already += 1

    out = {"events_in_horizon": len(rows), "captured": captured,
           "already_captured_this_moment": already,
           "skipped_no_estimate": plan["skipped_no_estimate"],
           "horizon_days": horizon_days, "as_of": plan["as_of"], "note": plan["note"]}
    log.info("prospective_capture.estimates", **{k: v for k, v in out.items() if k != "note"})
    return out
