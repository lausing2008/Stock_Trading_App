"""Real route functions + SQLite workflow; providers/Redis replaced at boundaries."""
import copy
import json
import pathlib
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services/research-engine")]
with tempfile.TemporaryDirectory() as temp:
    config = types.ModuleType("common.config")
    config.Settings = type("Settings", (), {})
    config.get_settings = lambda: types.SimpleNamespace(
        database_url=f"sqlite:///{temp}/valuation.db", jwt_secret="test", redis_url="redis://localhost:6379",
        market_data_url="http://market-data", admin_password=None, email_provider="", email_from="")
    sys.modules["common.config"] = config
    logs = types.ModuleType("common.logging"); logs.get_logger = lambda *_: MagicMock()
    logs.configure_logging = lambda *_: None
    sys.modules["common.logging"] = logs

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from db.models import Base, IntelligenceReport
    from fastapi import BackgroundTasks, HTTPException, Request
    from src.api import valuation_routes as R
    from src.intel_reports import valuation_report as V
    from src.intel_reports import valuation_jobs as J, valuation_worker as W
    from intelligence.report_contract import Field

    engine = create_engine(config.get_settings().database_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    J.init_store(engine)
    R.SessionLocal = Session
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    raw = {"symbol": "NEW", "name": "Discovered Company", "quote_type": "EQUITY", "exchange": "NYQ",
           "currency": "USD", "financial_currency": "USD", "sector": "Industrials", "industry": "Tools",
           "fetched_at": now.isoformat(), "source_url": "https://finance.yahoo.com/quote/NEW/", "tracked": False,
           "info": {"marketCap": 10e9, "sharesOutstanding": 100e6, "trailingPE": 15},
           "bars": [{"date": (now - timedelta(days=119-i)).date().isoformat(),
                     "close": 50, "adj_close": 50, "volume": 1e6} for i in range(120)],
           "annuals": [], "analyst_actions": [], "errors": []}
    data = {"rows": [raw], "price_session": day, "queries": ["test"], "queries_failed": [],
            "discovered": 1, "sampled": 1, "sample_limit": 60, "universe": "Test discovery sample"}

    class Cache:
        def __init__(self): self.items = {}
        def get(self, k): return self.items.get(k)
        def setex(self, k, _ttl, v): self.items[k] = v
        def set(self, k, v, nx=False, ex=None):
            if nx and k in self.items: return False
            self.items[k] = v; return True
        def eval(self, script, count, key, token):
            if self.get(key) == token: self.items.pop(key, None)
        def exists(self, key): return key in self.items
    cache = Cache(); R._cache = lambda: cache

    class ProviderClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, headers, params=None):
            assert url.endswith("/stocks/valuation-inputs/refresh")
            assert params and "priority_symbols" in params
            return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: copy.deepcopy(data))

    # Exercise handlers directly: the host's Starlette TestClient/AnyIO portal is incompatible.
    # Verify the actual registered dependencies and reject an unauthenticated call as well.
    for route in R.router.routes:
        assert any(d.call is R.get_current_username for d in route.dependant.dependencies)
    try:
        R.get_current_username(authorization=None)
        raise AssertionError("unauthenticated call accepted")
    except HTTPException as exc:
        assert exc.status_code == 401
    request = Request({"type": "http", "headers": [(b"authorization", b"Bearer fixture")]})
    def generate():
        tasks = BackgroundTasks()
        result = R.generate(request, tasks, _user="tester")
        assert not tasks.tasks  # Work is committed durably rather than attached to the response.
        W.process_once(factory=Session, authorization="Bearer fixture")
        return result
    def clear_cooldown():
        with Session() as session:
            session.get(J.Control, 1).cooldown_until = None
            session.commit()
    assert R.latest(_user="tester") == {"report": None}
    with patch.object(R.httpx, "Client", ProviderClient):
        response = generate()
        job = R.job(response["job_id"], _user="tester")
        assert job["state"] == "complete", job
        first = R.latest(_user="tester")["report"]
        assert W._latest_session(Session) == day
        assert first["valuation"]["rows"][0]["symbol"] == "NEW"
        assert not first["valuation"]["rows"][0]["tracked"]
        original = copy.deepcopy(first["valuation"])
        try:
            generate()
            raise AssertionError("cooldown was not enforced")
        except HTTPException as exc:
            assert exc.status_code == 429
        clear_cooldown()
        second_job_id = generate()["job_id"]
        second_job = R.job(second_job_id, _user="tester")
        assert second_job["state"] == "complete", second_job
        assert second_job["created"] is False
        assert second_job["report_id"] == first["id"]
        clear_cooldown()
        data["rows"][0]["bars"][-1]["close"] = 60
        generate()
        second = R.latest(_user="tester")["report"]
        assert second["version"] == 2 and second["supersedes_id"] == first["id"]
        old = R.report(first["id"], _user="tester")
        assert old["valuation"] == original
        assert len(R.history(limit=20, _user="tester")["reports"]) == 2
        # A completely failed refresh does not replace a successful snapshot.
        clear_cooldown()
        data["rows"][0]["bars"] = []
        failed_id = generate()["job_id"]
        assert R.job(failed_id, _user="tester")["state"] == "queued"
        for _ in range(2):
            with Session() as session:
                session.get(J.Job, failed_id).due_at = now.replace(tzinfo=None) - timedelta(seconds=1)
                session.commit()
            W.process_once(factory=Session, authorization="Bearer fixture")
        assert R.job(failed_id, _user="tester")["state"] == "failed"
        assert R.latest(_user="tester")["report"]["id"] == second["id"]
    # Prove that a fully sourced registry review reaches the real route's gates, rather
    # than only qualifying when a unit test injects a made-up all-pass quality dictionary.
    from test_candidate_qualification import approved_review
    from test_valuation_report import source as company_source
    from src.intel_reports import candidate_qualification as C
    qualified_raw = company_source("QUAL")
    qualified_raw["fetched_at"] = now.isoformat()
    for i, bar in enumerate(qualified_raw["bars"]):
        bar["date"] = (now - timedelta(days=119-i)).date().isoformat()
        bar["close"] = 11 if i >= 118 else 10
    approved = approved_review(qualified_raw)
    C.REVIEWS["QUAL"] = approved
    qualified_raw["filing_verification"] = {"state": "OK", "symbol": "QUAL", "cik": 123,
        "checked_at": now.isoformat(), "since": approved["reviewed_at"], "filings": [
            {"accession": a, "form": "10-K", "filed": approved["reviewed_at"]} for a in approved["filing_dispositions"]]}
    qualified_raw["financial_verification"] = {"state": "OK", "cik": 123, "checked_at": now.isoformat(),
        "latest_period_end": "2025-12-31", "latest_annual": {"revenue": {"value": 5e9}}}
    with Session() as session:
        actual_quality = R._quality(session, qualified_raw, now)
        actual_row = V.build_row(qualified_raw, day, now, actual_quality)
        assert actual_row["qualified"], actual_row["blockers"]
        with patch.object(R, "latest_assessments", return_value={"competitive_durability": {
            "verdict": "contradicted", "summary": "Contrary stored evidence", "findings": [], "evidence": {}}}):
            conflicting = R._quality(session, qualified_raw, now)
            assert not V.build_row(qualified_raw, day, now, conflicting)["qualified"]
    C.REVIEWS.pop("QUAL")
    # This read path must not expose owner-scoped reports even if their ID is guessed.
    with Session() as session:
        private, _ = R.store.save(session, {"private": Field(value="owner data")}, [],
            {"subject_key": "private:valuation", "report_type": V.REPORT_TYPE, "fingerprint": "private",
             "status": "partial", "cutoff_at": now.replace(tzinfo=None)}, {}, user_id=1)
        private_id = private.id
    try:
        R.report(private_id, _user="tester")
        raise AssertionError("private report was exposed")
    except HTTPException as exc:
        assert exc.status_code == 404
    print(json.dumps({"passes": True, "database": "SQLite", "versions": second["version"]}))
