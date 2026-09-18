"""T325-FIXEFFECTIVENESS: "did this fix actually work" tracking.

Direct user request (2026-09-02), after the AI Signal deep audit found and fixed
AUD-SIGNAL3-EVALSELECTIONBIAS: "I would like to have a dashboard to show the performance after
we applied the fix so that we can compare later and see if the fix really works." See
FixRecord's own docstring (shared/db/models.py) for the full design rationale — a general
mechanism, not a one-off AI-Signal-only table, so any future significant fix from a later audit
domain registers here the same way.

Metric computation for the AI Signal fix specifically mirrors the exact grounding query the
audit itself ran against production (win_rate_5d/avg_return_5d per horizon+direction) — the
same numbers already published in docs/audits/2026-09-02-six-part-platform-audit-1-ai-signal.md,
so a later comparison is apples-to-apples against a real, already-verified baseline, not a
differently-computed number that merely looks similar.
"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.jwt_auth import get_current_username
from db import FixRecord, FixSnapshot, SignalOutcome, get_session

from .signals_shared import log

router = APIRouter(prefix="/fix-effectiveness", tags=["fix-effectiveness"])


class RegisterFixRequest(BaseModel):
    fix_id: str
    domain: str
    title: str
    audit_doc_path: str | None = None
    baseline_metrics: dict
    success_criteria: str | None = None
    recheck_after_days: int = 14


@router.post("/register")
def register_fix(req: RegisterFixRequest, session: Session = Depends(get_session), _: str = Depends(get_current_username)):
    """Register a new fix for effectiveness tracking — any future significant fix from this or
    a later audit domain calls this once, at fix time, with its own already-measured baseline
    (per FixRecord's own docstring: reuse the exact numbers already gathered/published for the
    audit, never re-derive a differently-computed "baseline" after the fact).
    """
    existing = session.execute(
        select(FixRecord).where(FixRecord.fix_id == req.fix_id)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"fix_id={req.fix_id!r} is already registered (fixed_at={existing.fixed_at.isoformat()})")

    record = FixRecord(
        fix_id=req.fix_id,
        domain=req.domain,
        title=req.title,
        audit_doc_path=req.audit_doc_path,
        baseline_metrics_json=req.baseline_metrics,
        success_criteria=req.success_criteria,
        recheck_after_days=req.recheck_after_days,
    )
    session.add(record)
    session.commit()
    log.info("fix_effectiveness.registered", fix_id=req.fix_id, domain=req.domain)
    return {"fix_id": req.fix_id, "id": record.id, "fixed_at": record.fixed_at.isoformat()}


def _compute_ai_signal_win_rate_metrics(session: Session, since: date | None = None) -> dict:
    """The exact metric shape used by AUD-SIGNAL3-EVALSELECTIONBIAS's own baseline — win_rate_5d
    and avg_return_5d per (horizon, signal_direction), plus the same figures for is_correct
    (the horizon-native window) — matching the audit's own grounding query verbatim so a later
    snapshot is directly comparable to the published baseline, not a similar-but-different
    computation.

    `since` filters to signal_date >= since when given — used by later snapshots to compare
    only NEW, post-fix data against the baseline, never silently blending pre-fix and post-fix
    rows into one figure (which would understate any real improvement by diluting it with the
    exact biased population the fix corrected).
    """
    q = select(
        SignalOutcome.horizon, SignalOutcome.signal_direction,
        func.count().label("total"),
        func.count().filter(SignalOutcome.is_correct_5d.is_not(None)).label("resolved_5d"),
        func.count().filter(SignalOutcome.is_correct_5d.is_(True)).label("wins_5d"),
        func.avg(SignalOutcome.return_5d).label("avg_return_5d"),
        func.count().filter(SignalOutcome.is_correct.is_not(None)).label("resolved_base"),
        func.count().filter(SignalOutcome.is_correct.is_(True)).label("wins_base"),
        func.avg(SignalOutcome.pct_return).label("avg_pct_return"),
    ).group_by(SignalOutcome.horizon, SignalOutcome.signal_direction)
    if since is not None:
        q = q.where(SignalOutcome.signal_date >= since)
    rows = session.execute(q).all()

    by_bucket: dict[str, dict] = {}
    total_resolved_5d = 0
    for r in rows:
        horizon = r.horizon.value if hasattr(r.horizon, "value") else r.horizon
        key = f"{horizon}|{r.signal_direction}"
        win_rate_5d = round(r.wins_5d / r.resolved_5d, 3) if r.resolved_5d else None
        win_rate_base = round(r.wins_base / r.resolved_base, 3) if r.resolved_base else None
        by_bucket[key] = {
            "total": r.total,
            "resolved_5d": r.resolved_5d,
            "win_rate_5d": win_rate_5d,
            "avg_return_5d_pct": round(r.avg_return_5d * 100, 2) if r.avg_return_5d is not None else None,
            "resolved_base": r.resolved_base,
            "win_rate_base": win_rate_base,
            "avg_pct_return_base": round(r.avg_pct_return * 100, 2) if r.avg_pct_return is not None else None,
        }
        total_resolved_5d += r.resolved_5d

    return {"by_bucket": by_bucket, "total_resolved_5d": total_resolved_5d}


# AUD-C01-FIXSNAPSHOTCUTOFF: which domains can actually be measured. A dict rather than an
# inline `!= "ai_signal"` so adding a domain is a one-line registration, and so both the snapshot
# route and the dashboard read the SAME source of truth for "is this measurable".
_SNAPSHOT_METRIC_FNS = {
    "ai_signal": _compute_ai_signal_win_rate_metrics,
}


@router.get("")
def list_fix_records(session: Session = Depends(get_session), _: str = Depends(get_current_username)):
    """All tracked fixes with their baseline + every snapshot taken so far — the data behind
    the fix-effectiveness dashboard. Ordered newest-fix-first."""
    records = session.execute(
        select(FixRecord).order_by(FixRecord.fixed_at.desc())
    ).scalars().all()
    return [
        {
            "fix_id": r.fix_id,
            "domain": r.domain,
            "title": r.title,
            "fixed_at": r.fixed_at.isoformat(),
            "audit_doc_path": r.audit_doc_path,
            "baseline_metrics": r.baseline_metrics_json,
            "success_criteria": r.success_criteria,
            "recheck_after_days": r.recheck_after_days,
            # AUD-C01: a registered fix whose domain has no metric function is NOT silently
            # equivalent to one that simply has no snapshots yet. Say which it is.
            "snapshot_supported": r.domain in _SNAPSHOT_METRIC_FNS,
            "snapshots": [
                {
                    "taken_at": s.taken_at.isoformat(),
                    "metrics": s.metrics_json,
                    "sample_size": s.sample_size,
                    "note": s.note,
                }
                for s in sorted(r.snapshots, key=lambda s: s.taken_at)
            ],
        }
        for r in records
    ]


@router.post("/{fix_id}/snapshot")
def take_fix_snapshot(fix_id: str, session: Session = Depends(get_session), _: str = Depends(get_current_username)):
    """Re-measure and record a new FixSnapshot for an already-registered FixRecord. Safe to
    call anytime (e.g. on-demand from the dashboard, or from a scheduled job on
    FixRecord.recheck_after_days cadence) — each call is a genuine new timestamped snapshot,
    never an update to a prior one, matching FixSnapshot's own append-only design.

    Dispatch is via _SNAPSHOT_METRIC_FNS — a future domain's fix registers its own metric
    function there. There is deliberately no generic "compute something" fallback, which would
    silently produce a meaningless snapshot for a domain with no real metric definition; an
    unsupported domain records an EXPLICIT unsupported snapshot instead.

    Metrics are computed over the POST-FIX cohort only (signal_date >= the record's own
    fixed_at). See AUD-C01-FIXSNAPSHOTCUTOFF below for why that is the whole point.
    """
    record = session.execute(
        select(FixRecord).where(FixRecord.fix_id == fix_id)
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, f"No FixRecord registered for fix_id={fix_id!r}")

    metric_fn = _SNAPSHOT_METRIC_FNS.get(record.domain)
    if metric_fn is None:
        # AUD-C01: previously a bare HTTP 400. That made the scheduled recheck log a failure for
        # this record EVERY DAY FOREVER while still reporting the job itself as "ok", and left no
        # durable trace that the fix is unmeasurable. Record an explicit unsupported snapshot
        # instead: it advances the recheck clock, it is visible on the dashboard, and it says
        # "could not measure" rather than the much worse "measured nothing".
        unsupported = {
            "status": "unsupported",
            "domain": record.domain,
            "reason": f"no snapshot metric function is implemented for domain={record.domain!r}",
            "supported_domains": sorted(_SNAPSHOT_METRIC_FNS),
        }
        snapshot = FixSnapshot(
            fix_record_id=record.id,
            metrics_json=unsupported,
            sample_size=None,
            note=f"UNSUPPORTED DOMAIN — {record.domain!r} has no metric function; nothing was measured.",
        )
        session.add(snapshot)
        session.commit()
        log.warning("fix_effectiveness.snapshot_unsupported_domain", fix_id=fix_id, domain=record.domain)
        return {"fix_id": fix_id, "status": "unsupported", **unsupported}

    # AUD-C01-FIXSNAPSHOTCUTOFF: the `since` cutoff. This argument existed, was documented in
    # _compute_ai_signal_win_rate_metrics' own docstring, was spelled out in this record's stored
    # success_criteria ("compare only NEW rows... mixing them would understate any real
    # improvement"), and had its own passing unit test — and this, its only call site, omitted it.
    # A snapshot without it measures ALL history, including the exact pre-fix population the fix
    # was meant to correct, and so dilutes the very improvement it is supposed to detect.
    since = record.fixed_at.date()
    metrics = metric_fn(session, since=since)
    metrics["since"] = since.isoformat()
    # Labelled, per the audit: signal_date is a DATE, so signals raised EARLIER on the fix's own
    # deployment day are included. That is a deliberate, disclosed inclusion, not a silent one.
    metrics["cutoff_note"] = (
        f"signal_date >= {since.isoformat()} (date-granular; includes same-day signals raised "
        f"before the fix landed at {record.fixed_at.isoformat()})"
    )
    snapshot = FixSnapshot(
        fix_record_id=record.id,
        metrics_json=metrics,
        sample_size=metrics["total_resolved_5d"],
        note=f"post-fix cohort only: signal_date >= {since.isoformat()}",
    )
    session.add(snapshot)
    session.commit()
    log.info("fix_effectiveness.snapshot_taken", fix_id=fix_id,
             sample_size=metrics["total_resolved_5d"], since=since.isoformat())
    return {
        "fix_id": fix_id,
        "status": "ok",
        "taken_at": snapshot.taken_at.isoformat() if snapshot.taken_at else datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }
