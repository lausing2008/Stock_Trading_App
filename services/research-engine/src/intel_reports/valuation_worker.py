"""Restart-safe report worker and completed-US-session scheduling."""
from datetime import datetime, timedelta, timezone
import os
import threading
import uuid
from zoneinfo import ZoneInfo

from common.config import get_settings
from common.logging import get_logger
from common.market_calendar import MIN_COVERAGE_YEAR, session_close
from db import SessionLocal, IntelligenceReport
from sqlalchemy import select

from . import valuation_jobs as jobs, valuation_report as V

log = get_logger("research-engine.valuation-worker")
_thread = None


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def completed_session(now):
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    if day.year > MIN_COVERAGE_YEAR:
        raise ValueError("US calendar requires extension")
    for _ in range(12):
        close = session_close("US", day)
        if close and close + timedelta(minutes=30) <= now:
            return day.isoformat()
        day -= timedelta(days=1)
    raise ValueError("No verified completed US session")


def service_authorization():
    from jose import jwt
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": "valuation-worker", "svc": "valuation-report", "iat": now,
                        "exp": now + timedelta(hours=1), "jti": uuid.uuid4().hex},
                       get_settings().jwt_secret, algorithm="HS256")
    return "Bearer " + token


def _latest_session(factory):
    with factory() as session:
        return session.execute(select(IntelligenceReport.payload["fields"]["valuation"]["value"]["price_session"].as_string())
            .where(IntelligenceReport.subject_key == V.SUBJECT, IntelligenceReport.report_type == V.REPORT_TYPE,
                   IntelligenceReport.user_id.is_(None)).order_by(IntelligenceReport.version.desc()).limit(1)).scalar_one_or_none()


def schedule(factory=SessionLocal, *, now=None):
    now = now or datetime.now(timezone.utc)
    day = completed_session(now)
    previous_day = _latest_session(factory)
    if previous_day == day:
        return None
    try:
        return jobs.enqueue(factory, now=now.replace(tzinfo=None), scheduled_session=day)
    except jobs.Cooldown:
        return None


def process_once(factory=SessionLocal, runner=None, authorization=None):
    from ..api.valuation_routes import _run
    claimed = jobs.claim(factory, now=utcnow())
    if not claimed:
        return False
    job_id, token = claimed["job_id"], claimed["lease_token"]
    stopped = threading.Event()
    outcome = {}

    def heartbeat():
        while not stopped.wait(30):
            try:
                jobs.progress(factory, job_id, token, now=utcnow())
            except jobs.LeaseLost:
                return
            except Exception:
                log.warning("valuation.heartbeat_unavailable")

    def status(state, **extra):
        if state in {"complete", "failed"}:
            outcome.update(state=state, **extra)
        else:
            jobs.progress(factory, job_id, token, now=utcnow(), stage=extra.get("stage"))

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        (runner or _run)(job_id, authorization or service_authorization(), record_status=status,
                        before_save=lambda session: jobs.assert_owner(session, job_id, token, now=utcnow()))
        jobs.finish(factory, job_id, token, now=utcnow(), result=outcome or {"state": "failed"})
    except jobs.LeaseLost:
        log.warning("valuation.lease_lost", job_id=job_id)
    except Exception:
        log.warning("valuation.worker_failed", job_id=job_id)
        try:
            jobs.finish(factory, job_id, token, now=utcnow(), result={"state": "failed"})
        except Exception:
            pass  # Uncommitted work is recovered after lease expiry, not acknowledged away.
    finally:
        stopped.set()
        thread.join(timeout=1)
    return True


def start():
    global _thread
    if os.environ.get("VALUATION_WORKER_ENABLED", "true").lower() not in {"true", "1", "yes"}:
        return
    if _thread and _thread.is_alive():
        return
    def loop():
        while True:
            try:
                schedule()
                processed = process_once()
                if not processed:
                    from . import valuation_outcome_store as outcomes
                    def prices(symbols):
                        import httpx
                        with httpx.Client(timeout=httpx.Timeout(120, connect=10)) as client:
                            response = client.post(f"{get_settings().market_data_url}/stocks/valuation-outcome-prices",
                                params={"symbols": ",".join(symbols)}, headers={"Authorization": service_authorization()})
                            response.raise_for_status()
                            return response.json()
                    outcomes.refresh(SessionLocal, prices, now=datetime.now(timezone.utc), limit=1)
            except Exception:
                log.warning("valuation.worker_cycle_failed")
            threading.Event().wait(10)
    _thread = threading.Thread(target=loop, name="valuation-report-worker", daemon=True)
    _thread.start()
