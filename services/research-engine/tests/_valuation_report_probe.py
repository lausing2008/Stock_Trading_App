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
    from intelligence.report_contract import Field

    engine = create_engine(config.get_settings().database_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
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
        def post(self, url, headers):
            assert url.endswith("/stocks/valuation-inputs/refresh")
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
        for task in tasks.tasks:
            task.func(*task.args, **task.kwargs)
        return result
    assert R.latest(_user="tester") == {"report": None}
    with patch.object(R.httpx, "Client", ProviderClient):
        response = generate()
        job = R.job(response["job_id"], _user="tester")
        assert job["state"] == "complete", job
        first = R.latest(_user="tester")["report"]
        assert first["valuation"]["rows"][0]["symbol"] == "NEW"
        assert not first["valuation"]["rows"][0]["tracked"]
        original = copy.deepcopy(first["valuation"])
        try:
            generate()
            raise AssertionError("cooldown was not enforced")
        except HTTPException as exc:
            assert exc.status_code == 429
        cache.items.pop("stockai:valuation:cooldown")
        second_job_id = generate()["job_id"]
        second_job = R.job(second_job_id, _user="tester")
        assert second_job["state"] == "complete", second_job
        assert second_job["created"] is False
        assert second_job["report_id"] == first["id"]
        cache.items.pop("stockai:valuation:cooldown")
        data["rows"][0]["bars"][-1]["close"] = 60
        generate()
        second = R.latest(_user="tester")["report"]
        assert second["version"] == 2 and second["supersedes_id"] == first["id"]
        old = R.report(first["id"], _user="tester")
        assert old["valuation"] == original
        assert len(R.history(limit=20, _user="tester")["reports"]) == 2
        # A completely failed refresh does not replace a successful snapshot.
        cache.items.pop("stockai:valuation:cooldown")
        data["rows"][0]["bars"] = []
        failed_id = generate()["job_id"]
        assert R.job(failed_id, _user="tester")["state"] == "failed"
        assert R.latest(_user="tester")["report"]["id"] == second["id"]
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
