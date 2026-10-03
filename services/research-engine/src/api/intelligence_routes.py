"""Intelligence report endpoints: generate, read, list history, compare, export.

GENERATION IS A POST AND READS ARE GETs, because generation can WRITE a new version. A GET that
silently creates a row would make "show me the report" and "produce a new one" the same action,
which is exactly wrong for a product whose value is a stable, citable snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from common.jwt_auth import get_current_username
from common.logging import get_logger
from db import SessionLocal, IntelligenceReport
from ..intel_reports import generators as G
from ..intel_reports import store as S
from ..intel_reports.markdown import to_markdown
from intelligence.report_contract import CONTRACT_VERSION, ReportType

log = get_logger("research-engine.intelligence")
router = APIRouter(prefix="/intel", tags=["intelligence"])

_GENERATORS = {
    ReportType.MARKET_OUTLOOK.value: "market",
    ReportType.STOCK_OUTLOOK.value: "stock",
    ReportType.PRE_EARNINGS.value: "pre_earnings",
    ReportType.POST_EARNINGS.value: "post_earnings",
}


class GenerateRequest(BaseModel):
    report_type: str
    symbol: str | None = None
    market: str | None = "US"
    event_id: int | None = None


def _serialise(r: IntelligenceReport, *, include_payload: bool = True) -> dict:
    out = {
        "id": r.id,
        "report_type": r.report_type,
        "subject_key": r.subject_key,
        "symbol": r.symbol,
        "market": r.market,
        "version": r.version,
        "supersedes_id": r.supersedes_id,
        "pre_report_id": r.pre_report_id,
        "status": r.status,
        "stage": r.stage,
        "contract_version": r.contract_version,
        "policy_version": r.policy_version,
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
        "cutoff_at": r.cutoff_at.isoformat() if r.cutoff_at else None,
        "input_fingerprint": r.input_fingerprint,
        "coverage": r.coverage,
    }
    if include_payload:
        out["payload"] = r.payload
    return out


@router.get("/contract")
def contract_version():
    """So a client can tell whether it is rendering a report written under today's rules."""
    return {"contract_version": CONTRACT_VERSION,
            "report_types": [t.value for t in ReportType]}


@router.post("/generate")
def generate(req: GenerateRequest, _: str = Depends(get_current_username)):
    if req.report_type not in _GENERATORS:
        raise HTTPException(400, f"unknown report_type {req.report_type!r}; "
                                 f"expected one of {sorted(_GENERATORS)}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        try:
            if req.report_type == ReportType.MARKET_OUTLOOK.value:
                built = G.market_outlook(session, market=(req.market or "US").upper(), now=now)
                pre = None
            else:
                if not req.symbol:
                    raise HTTPException(400, f"{req.report_type} requires a symbol")
                sym = req.symbol.upper().strip()
                if req.report_type == ReportType.STOCK_OUTLOOK.value:
                    built, pre = G.stock_outlook(session, symbol=sym, now=now), None
                elif req.report_type == ReportType.PRE_EARNINGS.value:
                    built, pre = G.pre_earnings(session, symbol=sym, now=now), None
                else:
                    # IR-01. The baseline must precede the RELEASE, not this report's own
                    # generation time. Passing `cutoff_at` here — which is `now` — asked only
                    # whether the baseline predates the moment we are writing the post-report,
                    # which every report written after a release trivially satisfies. A
                    # hindsight baseline sailed straight through the check meant to stop it.
                    #
                    # The generator returns the event's release boundary in its meta, derived
                    # from the stored release date, and that is what the lookup is bound to.
                    probe = G.post_earnings(session, symbol=sym, event_id=req.event_id, now=now)
                    boundary = probe[2]["release_boundary"]
                    if isinstance(boundary, str):
                        boundary = datetime.fromisoformat(boundary)
                    pre = S.frozen_pre_report(
                        session, subject_key=probe[2]["subject_key"], before=boundary)
                    built = G.post_earnings(session, symbol=sym, event_id=req.event_id,
                                            pre_report=pre, now=now)
        except LookupError as exc:
            raise HTTPException(404, str(exc))

        fields, evidence, meta, cov = built
        previous = S.latest(session, subject_key=meta["subject_key"],
                            report_type=meta["report_type"])
        report, created = S.save(session, fields, evidence, meta, cov)
        body = _serialise(report)
        body["created"] = created
        # WHAT "PREVIOUS" MEANS DEPENDS ON WHETHER WE JUST WROTE ANYTHING.
        #
        # When the inputs were unchanged, `save()` returns the EXISTING report — so `previous`
        # is that same row, `previous.id == report.id`, and comparing it with None produced
        # "first report" on a v2 whose own header said it superseded v1. A reused report's
        # predecessor is the one it supersedes, not nothing.
        if created:
            baseline = previous if (previous and previous.id != report.id) else None
        else:
            baseline = (session.get(IntelligenceReport, report.supersedes_id)
                        if report.supersedes_id else None)
        body["changes_since_previous"] = S.diff(baseline, report)
        body["reused_existing"] = not created
        log.info("intel.generated", report_type=report.report_type, subject=report.subject_key,
                 version=report.version, created=created, status=report.status)
        return body


@router.get("/events")
def list_events(symbol: str, limit: int = Query(16, ge=1, le=60),
                _: str = Depends(get_current_username)):
    """The earnings events ON FILE for an issuer, so a report can be generated against a chosen
    one instead of silently taking the newest.

    BUILT OVER KNOWN EVENTS AND SAYS SO. Coverage is not verified against the issuer's own
    calendar here, so this list may be missing releases — which is disclosed in the response
    rather than left for a reader to discover when the wrong quarter appears. Waiting for
    ingestion discovery before offering any choice at all would be worse: right now there is no
    way to ask for a different quarter even when the right one IS on file.
    """
    from datetime import date as _date
    from sqlalchemy import select as _select
    from db import EarningsEvent, Stock
    sym = symbol.upper().strip()
    with SessionLocal() as session:
        stock = session.execute(_select(Stock).where(Stock.symbol == sym)).scalars().first()
        if stock is None:
            raise HTTPException(404, f"{sym} is not in the universe")
        rows = session.execute(
            _select(EarningsEvent).where(EarningsEvent.stock_id == stock.id)
            .order_by(EarningsEvent.report_date.desc()).limit(limit)).scalars().all()
        today = _date.today()
        events = [{
            "event_id": r.id,
            "report_date": r.report_date.isoformat(),
            "released": r.report_date <= today,
            "has_actuals": r.eps_actual is not None or r.revenue_actual is not None,
            # Never presented as confirmed: the stored label is derived from the period-end
            # calendar month and is wrong for every non-calendar fiscal year.
            "stored_period_label": r.period,
            "period_label_is_inferred": True,
        } for r in rows]
        released = [e for e in events if e["released"]]
        return {
            "symbol": sym,
            "events": events,
            "coverage_note": (
                "these are the events stored on this platform. Coverage is NOT verified against "
                "the issuer's own release calendar, so a release may be missing from this list; "
                "the fiscal labels shown are inferred from the period-end month and are "
                "incorrect for non-calendar fiscal years."),
            "newest_released": released[0]["report_date"] if released else None,
        }


@router.get("/reports")
def list_reports(report_type: str | None = None, symbol: str | None = None,
                 limit: int = Query(25, ge=1, le=100),
                 _: str = Depends(get_current_username)):
    from sqlalchemy import select
    with SessionLocal() as session:
        q = select(IntelligenceReport).where(IntelligenceReport.user_id.is_(None))
        if report_type:
            q = q.where(IntelligenceReport.report_type == report_type)
        if symbol:
            q = q.where(IntelligenceReport.symbol == symbol.upper().strip())
        rows = session.execute(
            q.order_by(IntelligenceReport.generated_at.desc()).limit(limit)).scalars().all()
        return {"reports": [_serialise(r, include_payload=False) for r in rows]}


@router.get("/reports/{report_id}")
def get_report(report_id: int, _: str = Depends(get_current_username)):
    with SessionLocal() as session:
        r = session.get(IntelligenceReport, report_id)
        if r is None or r.user_id is not None:
            # A portfolio-bearing report is owner-only; this public path never serves one.
            raise HTTPException(404, "report not found")
        return _serialise(r)


@router.get("/reports/{report_id}/markdown")
def get_markdown(report_id: int, _: str = Depends(get_current_username)):
    with SessionLocal() as session:
        r = session.get(IntelligenceReport, report_id)
        if r is None or r.user_id is not None:
            raise HTTPException(404, "report not found")
        return {"report_id": r.id, "markdown": to_markdown(r)}


@router.get("/history")
def report_history(subject_key: str, limit: int = Query(20, ge=1, le=100),
                   _: str = Depends(get_current_username)):
    with SessionLocal() as session:
        rows = S.history(session, subject_key=subject_key, limit=limit)
        return {"subject_key": subject_key,
                "reports": [_serialise(r, include_payload=False) for r in rows]}


@router.get("/compare")
def compare(before_id: int, after_id: int, _: str = Depends(get_current_username)):
    with SessionLocal() as session:
        a = session.get(IntelligenceReport, before_id)
        b = session.get(IntelligenceReport, after_id)
        if a is None or b is None or a.user_id is not None or b.user_id is not None:
            raise HTTPException(404, "report not found")
        if a.subject_key != b.subject_key:
            raise HTTPException(400, "reports describe different subjects and are not comparable")
        return {"before": _serialise(a, include_payload=False),
                "after": _serialise(b, include_payload=False),
                "diff": S.diff(a, b)}
