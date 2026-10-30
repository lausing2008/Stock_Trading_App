from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

from src.intel_reports import valuation_worker as W


def test_scheduler_uses_completed_exchange_sessions_and_skips_an_existing_same_day_snapshot(monkeypatch):
    now = datetime(2026, 10, 25, 17, tzinfo=timezone.utc)
    assert W.completed_session(now) == "2026-10-23"
    assert W.completed_session(datetime(2026, 11, 27, 18, 20, tzinfo=timezone.utc)) == "2026-11-25"
    assert W.completed_session(datetime(2026, 11, 27, 18, 35, tzinfo=timezone.utc)) == "2026-11-27"
    row = SimpleNamespace(payload={"fields": {"valuation": {"value": {"price_session": "2026-10-23"}}}})
    monkeypatch.setattr(W, "_latest_session", lambda *a: row.payload["fields"]["valuation"]["value"]["price_session"])
    calls = []
    monkeypatch.setattr(W.jobs, "enqueue", lambda *a, **k: calls.append(k) or {"job_id": "scheduled"})
    factory = lambda: nullcontext(None)
    assert W.schedule(factory, now=now) is None
    assert calls == []
    row.payload["fields"]["valuation"]["value"]["price_session"] = "2026-10-22"
    assert W.schedule(factory, now=now)["job_id"] == "scheduled"
    assert calls[0]["scheduled_session"] == "2026-10-23"


def test_worker_service_token_is_short_lived_and_has_no_user_or_admin_role(monkeypatch):
    from jose import jwt
    secret = "unit-test-secret-long-enough"
    monkeypatch.setattr(W, "get_settings", lambda: SimpleNamespace(jwt_secret=secret))
    encoded = W.service_authorization().removeprefix("Bearer ")
    claims = jwt.decode(encoded, secret, algorithms=["HS256"])
    assert claims["svc"] == "valuation-report" and claims["sub"] == "valuation-worker"
    assert claims["exp"] - claims["iat"] == 3600
    assert "role" not in claims
