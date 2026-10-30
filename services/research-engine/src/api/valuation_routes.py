"""Background generation and immutable reads for US valuation reports."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import redis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from common.config import get_settings
from common.jwt_auth import get_current_username
from common.logging import get_logger
from common.redis_client import get_redis
from db import SessionLocal, IntelligenceReport
from ..intel_reports import store
from ..intel_reports import valuation_report as V
from ..intel_reports.candidate_qualification import REVIEWS, resolve_review
from ..intel_reports.filing_verification import collect as collect_filing_verification
from ..intel_reports.financial_verification import collect as collect_financial_verification
from ..intel_reports import valuation_jobs as durable_jobs
from ..intel_reports import valuation_outcome_store as outcomes
from ..intel_reports.quality_value import (
    business_quality_gate, durability_gate, valuation_gate, value_trap_gate,
    entry_condition_gate, compose, COMPETITIVE_DURABILITY, VALUATION, VALUE_TRAP_RISK,
)
from ..intel_reports.quality_value_store import latest_assessments

router = APIRouter(prefix="/intel/valuation", tags=["valuation"])
log = get_logger("research-engine.valuation")
LOCK = "stockai:valuation:generation"
JOB_TTL = 1800


def _cache():
    return get_redis()


def _job_key(job_id):
    return f"stockai:valuation:job:{job_id}"


def _data(row):
    return (row.payload or {}).get("fields", {}).get("valuation", {}).get("value") or {}


def _serialise(row):
    return {"id": row.id, "version": row.version, "supersedes_id": row.supersedes_id,
            "generated_at": row.generated_at.isoformat() + "Z", "status": row.status,
            "cutoff_at": row.cutoff_at.isoformat() + "Z", "coverage": row.coverage,
            "report_type": row.report_type, "valuation": _data(row)}


def _quality(session, raw, now):
    annuals = raw.get("annuals") or []
    latest = annuals[0] if annuals else {}
    prior = annuals[1] if len(annuals) > 1 else {}
    equity, debt, cash = (latest.get(k) for k in ("equity", "debt", "cash"))
    fin = {"annual_periods": len(annuals), "revenue": latest.get("revenue"),
           "free_cashflow": latest.get("free_cashflow"), "free_cashflow_prior": prior.get("free_cashflow"),
           "net_debt_to_equity": (debt - cash) / equity if equity and debt is not None and cash is not None else None,
           "industry": raw.get("industry"),
           "retrieval_age_days": (int(V.age_days(raw.get("fetched_at"), now))
                                  if raw.get("fetched_at") else None),
           "reported_year_age_days": ((now.date() - datetime.fromisoformat(latest["period_end"]).date()).days
                                      if latest.get("period_end") else None)}
    closes = [b["close"] for b in raw.get("bars", []) if V.finite(b.get("close")) is not None]
    px = {"recent_closes": closes[-21:][::-1], "sessions_read": len(closes),
          "sma20": sum(closes[-21:-1]) / 20 if len(closes) >= 21 else None,
          "session_complete": True, "latest_session": raw.get("bars", [{}])[-1].get("date") if raw.get("bars") else None}
    found = latest_assessments(session, raw["symbol"])
    review, review_blockers, _ = resolve_review(raw, now)
    if review and not review_blockers:
        # A fully verified code-reviewed assessment can supply a missing stored judgment.
        # Contrary existing assessments are retained, so conflicting evidence cannot be
        # erased merely by adding a registry entry.
        for dimension in (COMPETITIVE_DURABILITY, VALUATION, VALUE_TRAP_RISK):
            if dimension not in found:
                decision = review["gates"][dimension]
                found[dimension] = {"verdict": "supported", "summary": decision["rationale"],
                    "findings": [{"claim": decision["rationale"], "counterevidence": decision["counterevidence"],
                                  "source": "; ".join(s["url"] for s in review["sources"] if s["id"] in decision["source_ids"])}],
                    "evidence": {"review_version": review["version"], "reviewed_at": review["reviewed_at"],
                                 "source": "frozen issuer qualification registry"}, "not_assessed": []}
    gates = [business_quality_gate(fin), durability_gate(assessment=found.get(COMPETITIVE_DURABILITY)),
             valuation_gate(assessment=found.get(VALUATION)), entry_condition_gate(px),
             value_trap_gate(fin, assessment=found.get(VALUE_TRAP_RISK))]
    result = compose(raw["symbol"], gates)
    return result.as_dict()


def _run(job_id, authorization, record_status=None, before_save=None):
    cache = _cache()
    started = datetime.now(timezone.utc).isoformat()
    def status(state, **extra):
        if record_status:
            record_status(state, **extra)
        else:
            cache.setex(_job_key(job_id), JOB_TTL,
                        json.dumps({"job_id": job_id, "state": state, "started_at": started, **extra}))
    try:
        status("running", stage="Discovering US stocks and collecting Yahoo/UW inputs")
        with httpx.Client(timeout=httpx.Timeout(1200, connect=10)) as client:
            response = client.post(f"{get_settings().market_data_url}/stocks/valuation-inputs/refresh",
                                   headers={"Authorization": authorization},
                                   params={"priority_symbols": ",".join(list(REVIEWS)[:12])})
            response.raise_for_status()
            inputs = response.json()
        # A fully failed refresh must not supersede the last useful report with an empty one.
        checked_at = datetime.now(timezone.utc)
        def usable(row):
            bars = row.get("bars") or []
            age = V.age_days(row.get("fetched_at"), checked_at)
            price = V.finite(bars[-1].get("close")) if bars else None
            return bool(bars and bars[-1].get("date") == inputs.get("price_session")
                        and price is not None and price > 0 and age is not None and 0 <= age <= 1.5)
        if not any(usable(row) for row in inputs.get("rows", [])):
            raise ValueError("No current completed-session company inputs returned; last successful report retained")
        status("running", stage="Calculating scenarios and checking research evidence")
        now = datetime.now(timezone.utc)
        reviewed = {raw["symbol"]: REVIEWS[raw["symbol"]] for raw in inputs["rows"] if raw["symbol"] in REVIEWS}
        filing_checks = collect_filing_verification(cache, reviewed, now=now)
        financial_checks = collect_financial_verification(cache, reviewed, now=now)
        for raw in inputs["rows"]:
            if raw["symbol"] in filing_checks:
                raw["filing_verification"] = filing_checks[raw["symbol"]]
                raw["financial_verification"] = financial_checks[raw["symbol"]]
        with SessionLocal() as session:
            previous = store.latest(session, subject_key=V.SUBJECT, report_type=V.REPORT_TYPE)
            quality = {raw["symbol"]: _quality(session, raw, now) for raw in inputs["rows"]}
            built = V.build_report(inputs, now=now, quality=quality,
                                   previous=_data(previous) if previous else None)
            if before_save:
                before_save(session)
            row, created = store.save(session, *built)
            try:
                outcomes.register(SessionLocal, row)
                if previous:
                    outcomes.register(SessionLocal, previous)
            except Exception:
                log.warning("valuation.cohort_registration_failed")
            status("complete", report_id=row.id, created=created)
    except Exception as exc:
        log.warning("valuation.generate_failed", error=str(exc)[:200])
        # HTTP errors may contain request URLs; never expose headers/tokens/provider payloads.
        message = str(exc) if isinstance(exc, ValueError) else "Report refresh failed; last successful report retained. Retry after checking data-service availability."
        response = getattr(exc, "response", None)
        status("failed", error=message, contention=getattr(response, "status_code", None) == 409)
    finally:
        try:
            cache.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end",
                       1, LOCK, job_id)
        except redis.RedisError:
            pass


@router.post("/generate", status_code=202)
def generate(request: Request, background_tasks: BackgroundTasks,
             _user: str = Depends(get_current_username)):
    try:
        return durable_jobs.enqueue(SessionLocal, now=datetime.now(timezone.utc).replace(tzinfo=None))
    except durable_jobs.Cooldown:
        raise HTTPException(429, "A report was refreshed recently. Please wait five minutes.")
    except Exception:
        raise HTTPException(503, "Durable report job storage unavailable; retry later")


@router.get("/jobs/{job_id}")
def job(job_id: str, _user: str = Depends(get_current_username)):
    try:
        saved = durable_jobs.read(SessionLocal, job_id)
        if saved:
            return saved
    except Exception:
        raise HTTPException(503, "Durable report job storage unavailable")
    try:
        raw = _cache().get(_job_key(job_id))
    except redis.RedisError:
        raise HTTPException(503, "Report job storage unavailable")
    if raw is None:
        raise HTTPException(404, "Job expired or interrupted; read the latest report or retry generation")
    data = json.loads(raw)
    if data["state"] in ("queued", "running") and V.age_days(data.get("started_at"), datetime.now(timezone.utc)) * 86400 > JOB_TTL:
        return {**data, "state": "failed", "error": "Report job timed out; last successful report retained"}
    return data


@router.get("/latest")
def latest(_user: str = Depends(get_current_username)):
    with SessionLocal() as session:
        row = store.latest(session, subject_key=V.SUBJECT, report_type=V.REPORT_TYPE)
        return {"report": _serialise(row) if row else None}


@router.get("/status")
def worker_status(_user: str = Depends(get_current_username)):
    from ..intel_reports import valuation_worker
    with SessionLocal() as session:
        control = session.get(durable_jobs.Control, 1)
        if control is None:
            raise HTTPException(503, "Report job control is unavailable")
        return {"automatic_refresh": "active" if valuation_worker._thread and valuation_worker._thread.is_alive() else "disabled",
                "schedule": "After each completed US session plus 30 minutes",
                "active_job_id": control.active_id, "last_scheduled_session": control.scheduled_session}


@router.get("/history")
def history(limit: int = Query(20, ge=1, le=100), _user: str = Depends(get_current_username)):
    with SessionLocal() as session:
        rows = store.history(session, subject_key=V.SUBJECT, limit=limit)
        return {"reports": [{"id": r.id, "version": r.version, "generated_at": r.generated_at.isoformat() + "Z",
                             "price_session": _data(r).get("price_session")} for r in rows]}


@router.get("/reports/{report_id}")
def report(report_id: int, _user: str = Depends(get_current_username)):
    with SessionLocal() as session:
        row = session.get(IntelligenceReport, report_id)
        if row is None or row.report_type != V.REPORT_TYPE or row.subject_key != V.SUBJECT or row.user_id is not None:
            raise HTTPException(404, "Valuation report not found")
        return _serialise(row)


@router.get("/performance/{report_id}")
def performance(report_id: int, _user: str = Depends(get_current_username)):
    with SessionLocal() as session:
        row = session.get(IntelligenceReport, report_id)
        if row is None or row.report_type != V.REPORT_TYPE or row.subject_key != V.SUBJECT or row.user_id is not None:
            raise HTTPException(404, "Valuation report not found")
    return {"evaluation": outcomes.latest(SessionLocal, report_id), **outcomes.status(SessionLocal, report_id)}
