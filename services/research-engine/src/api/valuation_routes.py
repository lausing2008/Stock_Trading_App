"""Background generation and immutable reads for US valuation reports."""
from __future__ import annotations

import json
import uuid
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
    gates = [business_quality_gate(fin), durability_gate(assessment=found.get(COMPETITIVE_DURABILITY)),
             valuation_gate(assessment=found.get(VALUATION)), entry_condition_gate(px),
             value_trap_gate(fin, assessment=found.get(VALUE_TRAP_RISK))]
    result = compose(raw["symbol"], gates)
    return result.as_dict()


def _run(job_id, authorization):
    cache = _cache()
    started = datetime.now(timezone.utc).isoformat()
    def status(state, **extra):
        cache.setex(_job_key(job_id), JOB_TTL,
                    json.dumps({"job_id": job_id, "state": state, "started_at": started, **extra}))
    try:
        status("running", stage="Discovering US stocks and collecting Yahoo/UW inputs")
        with httpx.Client(timeout=httpx.Timeout(1200, connect=10)) as client:
            response = client.post(f"{get_settings().market_data_url}/stocks/valuation-inputs/refresh",
                                   headers={"Authorization": authorization})
            response.raise_for_status()
            inputs = response.json()
        # A fully failed refresh must not supersede the last useful report with an empty one.
        if not any(r.get("bars") and r.get("fetched_at") for r in inputs.get("rows", [])):
            raise ValueError("No usable company inputs returned; last successful report retained")
        status("running", stage="Calculating scenarios and checking research evidence")
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            previous = store.latest(session, subject_key=V.SUBJECT, report_type=V.REPORT_TYPE)
            quality = {raw["symbol"]: _quality(session, raw, now) for raw in inputs["rows"]}
            built = V.build_report(inputs, now=now, quality=quality,
                                   previous=_data(previous) if previous else None)
            row, created = store.save(session, *built)
            status("complete", report_id=row.id, created=created)
    except Exception as exc:
        log.warning("valuation.generate_failed", error=str(exc)[:200])
        # HTTP errors may contain request URLs; never expose headers/tokens/provider payloads.
        message = str(exc) if isinstance(exc, ValueError) else "Report refresh failed; last successful report retained. Retry after checking data-service availability."
        status("failed", error=message)
    finally:
        try:
            cache.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end",
                       1, LOCK, job_id)
        except redis.RedisError:
            pass


@router.post("/generate", status_code=202)
def generate(request: Request, background_tasks: BackgroundTasks,
             _user: str = Depends(get_current_username)):
    cache = _cache()
    job_id = uuid.uuid4().hex
    try:
        existing = cache.get(LOCK)
        if existing:
            return {"job_id": existing, "state": "running"}
        # Limit repeated completed refreshes as well as concurrent ones.
        if cache.get("stockai:valuation:cooldown"):
            raise HTTPException(429, "A report was refreshed recently. Please wait five minutes.")
        if not cache.set(LOCK, job_id, nx=True, ex=JOB_TTL):
            return {"job_id": cache.get(LOCK), "state": "running"}
        cache.setex(_job_key(job_id), JOB_TTL, json.dumps({"job_id": job_id, "state": "queued",
                    "started_at": datetime.now(timezone.utc).isoformat()}))
        cache.setex("stockai:valuation:cooldown", 300, "1")
    except redis.RedisError:
        raise HTTPException(503, "Report job storage unavailable; retry later")
    background_tasks.add_task(_run, job_id, request.headers.get("authorization", ""))
    return {"job_id": job_id, "state": "queued"}


@router.get("/jobs/{job_id}")
def job(job_id: str, _user: str = Depends(get_current_username)):
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
        if row is None or row.report_type != V.REPORT_TYPE or row.user_id is not None:
            raise HTTPException(404, "Valuation report not found")
        return _serialise(row)
