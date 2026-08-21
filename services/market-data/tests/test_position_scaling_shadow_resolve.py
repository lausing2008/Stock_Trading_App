"""Tests for AUD232-SILENT-SHADOW-DATALOSS — resolve_position_scaling_shadow_verdicts()'s
Redis write to ps:shadow:resolved previously had a silent `except Exception: pass` that let
execution fall through to `to_remove.append(raw)`/`resolved_count += 1` regardless of whether
the write actually succeeded — a real data-loss bug (not just a missing log): a transient
Redis failure would permanently drop a verdict into neither ps:shadow:pending NOR
ps:shadow:resolved, while ALSO inflating the reported hit_rate denominator with an outcome
that was never actually persisted. This directly feeds _retrain_position_scaling_gate's/
_check_position_scaling_gate_drift's own promotion decision for whether to let position-
scaling touch real trades.

Fixed: a resolved-write failure now logs and leaves the verdict in ps:shadow:pending (skipped
this run, retried next run) instead of silently discarding it — matching the DB-hiccup
branch's own pre-existing "try again on the next run, don't lose it" convention a few lines
above.

paper_trading_engine.py can't be imported directly in this test environment (conftest.py
stubs sqlalchemy itself as a MagicMock) — matches test_broker_position_sync.py's/
test_correlation_preentry.py's established technique: pop the stub, build ONE shared
in-memory engine + real models while real sqlalchemy is active, then restore the stub
immediately. resolve_position_scaling_shadow_verdicts()'s real source is extracted via exec()
and run against this real session + a fake Redis client, so these tests exercise the actual
logic, not a hand-copied duplicate.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import json
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_ps_shadow", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_ps_shadow"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.Stock.__table__, _models.Price.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Price = _models.Price
TimeFrame = _models.TimeFrame
Market = _models.Market
Exchange = _models.Exchange

_ENGINE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_ENGINE_SOURCE = _ENGINE_PATH.read_text()


class _FakeRedis:
    """A minimal fake matching the .lrange/.lpush/.ltrim/.lrem surface the real function
    calls — `resolved_writes_fail` makes every lpush to ps:shadow:resolved raise, simulating
    a transient Redis failure at exactly the write this fix guards."""
    def __init__(self, pending: list[str], resolved_writes_fail: bool = False):
        self._lists: dict[str, list[str]] = {"ps:shadow:pending": list(pending), "ps:shadow:resolved": []}
        self._resolved_writes_fail = resolved_writes_fail

    def lrange(self, key, start, end):
        return list(self._lists.get(key, []))

    def lpush(self, key, value):
        if key == "ps:shadow:resolved" and self._resolved_writes_fail:
            raise ConnectionError("simulated Redis failure")
        self._lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key, start, end):
        pass

    def lrem(self, key, count, value):
        lst = self._lists.setdefault(key, [])
        if value in lst:
            lst.remove(value)


def _extract_resolve_function():
    """Pulls resolve_position_scaling_shadow_verdicts()'s real source out of
    paper_trading_engine.py and exec()s it against real sqlalchemy/models, with
    common.redis_client.get_redis() stubbed to return a pre-built _FakeRedis instance instead
    of a real connection. The real function does `from common.redis_client import get_redis as
    _get_pool_redis` (a pooled-connection fix — see the "closing the loop" Redis-pooling audit
    documented elsewhere in this codebase) rather than a raw `import redis` — the injection
    point here must match that real import path, not `redis` itself, or the mock is silently
    never actually exercised by the real code path."""
    start = _ENGINE_SOURCE.index("def resolve_position_scaling_shadow_verdicts(")
    marker = '"hit_rate": round(correct_count / resolved_count, 4) if resolved_count else None,'
    marker_idx = _ENGINE_SOURCE.index(marker, start)
    end = _ENGINE_SOURCE.index("}", marker_idx) + 1
    func_source = _ENGINE_SOURCE[start:end]

    fake_redis_holder: dict = {}

    namespace = {
        "select": select,
        "Stock": Stock,
        "Price": Price,
        "TimeFrame": TimeFrame,
        "datetime": datetime,
        "timezone": timezone,
        "log": MagicMock(),
        "_PS_SHADOW_LIST_MAXLEN": 2000,  # real module-level constant the function reads
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source

    def _run(session, fake_redis):
        fake_redis_holder["client"] = fake_redis
        import types
        fake_redis_client_mod = types.ModuleType("common.redis_client")
        fake_redis_client_mod.get_redis = lambda: fake_redis_holder["client"]
        sys.modules.setdefault("common", types.ModuleType("common"))
        sys.modules["common.redis_client"] = fake_redis_client_mod

        return namespace["resolve_position_scaling_shadow_verdicts"](session)

    return _run


_run_resolve = _extract_resolve_function()


def _make_session():
    session = Session(_ENGINE)
    for table in (Price.__table__, Stock.__table__):
        session.execute(table.delete())
    session.commit()
    return session


def _make_stock(session, symbol="AAPL"):
    stock = Stock(symbol=symbol, name=symbol, market=Market.US, exchange=Exchange.NASDAQ, active=True)
    session.add(stock)
    session.commit()
    session.refresh(stock)
    return stock


def _make_price(session, stock_id, ts, close, price_id):
    session.add(Price(id=price_id, stock_id=stock_id, timeframe=TimeFrame.D1, ts=ts,
                       open=close, high=close, low=close, close=close, volume=1_000_000))
    session.commit()


def _resolvable_payload(symbol, price_at_verdict, would_act=True):
    now = datetime.now(timezone.utc)
    return json.dumps({
        "ts": (now - timedelta(days=25)).isoformat(),
        "resolve_after": (now - timedelta(days=1)).isoformat(),  # already past — resolvable now
        "symbol": symbol,
        "portfolio_id": 1,
        "act_probability": 0.7,
        "suggested_size_multiplier": 1.2,
        "would_act": would_act,
        "thesis_recommendation": "add",
        "thesis_broken_reasons": [],
        "price_at_verdict": price_at_verdict,
        "entry_price": price_at_verdict,
    })


def test_resolved_write_succeeds_moves_verdict_to_resolved_and_removes_from_pending():
    session = _make_session()
    stock = _make_stock(session)
    # The real function does Price.ts >= verdict_ts.replace(tzinfo=None) — Price.ts is stored
    # naive, so the fixture must build a naive datetime to reliably match/exceed it.
    verdict_ts_naive = (datetime.now(timezone.utc) - timedelta(days=25)).replace(tzinfo=None)
    _make_price(session, stock.id, verdict_ts_naive + timedelta(days=20), 110.0, 1)  # +10% -> would_act=True correct

    payload = _resolvable_payload("AAPL", price_at_verdict=100.0, would_act=True)
    fake_redis = _FakeRedis(pending=[payload], resolved_writes_fail=False)

    result = _run_resolve(session, fake_redis)

    assert result["resolved"] == 1
    assert result["still_pending"] == 0
    assert len(fake_redis._lists["ps:shadow:resolved"]) == 1
    assert len(fake_redis._lists["ps:shadow:pending"]) == 0  # removed — the real write succeeded


def test_resolved_write_failure_does_not_lose_the_verdict():
    """The exact bug this fix closes: a Redis failure writing to ps:shadow:resolved must
    NOT result in the verdict vanishing from both lists — it must stay in ps:shadow:pending
    for the next run to retry."""
    session = _make_session()
    stock = _make_stock(session)
    verdict_ts = datetime.now(timezone.utc) - timedelta(days=25)
    _make_price(session, stock.id, verdict_ts + timedelta(days=20), 110.0, 2)

    payload = _resolvable_payload("AAPL", price_at_verdict=100.0, would_act=True)
    fake_redis = _FakeRedis(pending=[payload], resolved_writes_fail=True)

    result = _run_resolve(session, fake_redis)

    assert result["resolved"] == 0, "must not count a verdict whose resolved-write failed"
    assert result["still_pending"] == 1
    assert len(fake_redis._lists["ps:shadow:resolved"]) == 0, "the failed write must not appear as resolved"
    assert payload in fake_redis._lists["ps:shadow:pending"], (
        "the verdict must remain in pending for the next run to retry — this is the exact "
        "data-loss bug: it must not vanish from both lists"
    )


def test_resolved_write_failure_does_not_inflate_hit_rate():
    """The failed write must not contribute to the reported hit_rate numerator/denominator —
    a corrupted hit_rate here has real downstream consequences for the position-scaling
    gate's own promotion decision."""
    session = _make_session()
    stock = _make_stock(session)
    verdict_ts = datetime.now(timezone.utc) - timedelta(days=25)
    _make_price(session, stock.id, verdict_ts + timedelta(days=20), 110.0, 3)

    payload = _resolvable_payload("AAPL", price_at_verdict=100.0, would_act=True)
    fake_redis = _FakeRedis(pending=[payload], resolved_writes_fail=True)

    result = _run_resolve(session, fake_redis)

    assert result["hit_rate"] is None, "a resolved_count of 0 must report hit_rate=None, not a fabricated ratio"


def test_a_genuinely_still_pending_verdict_is_left_alone():
    session = _make_session()
    now = datetime.now(timezone.utc)
    payload = json.dumps({
        "ts": now.isoformat(),
        "resolve_after": (now + timedelta(days=10)).isoformat(),  # not due yet
        "symbol": "AAPL", "portfolio_id": 1, "act_probability": 0.7,
        "suggested_size_multiplier": 1.2, "would_act": True,
        "thesis_recommendation": "add", "thesis_broken_reasons": [],
        "price_at_verdict": 100.0, "entry_price": 100.0,
    })
    fake_redis = _FakeRedis(pending=[payload], resolved_writes_fail=False)

    result = _run_resolve(session, fake_redis)

    assert result["resolved"] == 0
    assert result["still_pending"] == 1
    assert payload in fake_redis._lists["ps:shadow:pending"]
