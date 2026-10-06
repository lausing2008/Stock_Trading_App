"""ONE DEFERRED ARTICLE, actually resumed — against a real PostgreSQL.

The source-text tests establish shape. This establishes behaviour: a row stored without a
classification because a ceiling refused it is picked up, labelled, and left as ONE row with
its original URL. A stale one is labelled without arming the risk gate.

    docker run -d --name stockai-budget-pg -e POSTGRES_PASSWORD=probe \\
        -e POSTGRES_DB=budgetprobe -p 55433:5432 postgres:15
    BUDGET_PG_URL=postgresql+psycopg2://postgres:probe@localhost:55433/budgetprobe \\
        python -m pytest services/news-intelligence/tests/test_deferred_retry_integration.py
"""
import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Lives under shared/tests because the news-intelligence conftest stubs the database driver
# service-wide — a real connection cannot be opened under it, and this test is about the real
# database.
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "services" / "news-intelligence" / "src"))
sys.path.insert(0, str(_ROOT / "shared"))

_PG = os.environ.get("BUDGET_PG_URL")
pytestmark = pytest.mark.skipif(not _PG, reason="set BUDGET_PG_URL to run")

if _PG:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    _ENGINE = create_engine(_PG)
    _Session = sessionmaker(bind=_ENGINE)

    DDL = """
    CREATE TABLE IF NOT EXISTS realtime_news_items (
        id BIGSERIAL PRIMARY KEY, symbol VARCHAR(32), headline TEXT NOT NULL,
        source VARCHAR(32) NOT NULL, url TEXT,
        sentiment_score DOUBLE PRECISION, sentiment_label VARCHAR(16),
        is_material BOOLEAN NOT NULL DEFAULT FALSE, category VARCHAR(32),
        published_at TIMESTAMP NOT NULL, ingested_at TIMESTAMP DEFAULT now(),
        classification_deferred_reason VARCHAR(128), classification_attempts INT DEFAULT 0);
    """


@pytest.fixture
def db(monkeypatch):
    with _ENGINE.begin() as c:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.execute(text("TRUNCATE realtime_news_items"))

    from sqlalchemy.orm import declarative_base
    from sqlalchemy import Column, BigInteger, String, Text, Float, Boolean, DateTime, Integer
    Base = declarative_base()

    class RealtimeNewsItem(Base):
        __tablename__ = "realtime_news_items"
        id = Column(BigInteger, primary_key=True)
        symbol = Column(String(32))
        headline = Column(Text)
        source = Column(String(32))
        url = Column(Text)
        sentiment_score = Column(Float)
        sentiment_label = Column(String(16))
        is_material = Column(Boolean, default=False)
        category = Column(String(32))
        published_at = Column(DateTime)
        ingested_at = Column(DateTime)
        classification_deferred_reason = Column(String(128))
        classification_attempts = Column(Integer, default=0)

    fake_db = types.ModuleType("db")
    fake_db.SessionLocal = _Session
    fake_db.RealtimeNewsItem = RealtimeNewsItem
    monkeypatch.setitem(sys.modules, "db", fake_db)

    keys = types.ModuleType("common.ai_keys")
    keys.get_admin_ai_key = lambda _p: "test-key"
    monkeypatch.setitem(sys.modules, "common.ai_keys", keys)
    return RealtimeNewsItem


def _insert(now, *, minutes_old, url, material=False):
    with _Session() as s:
        s.execute(text(
            "INSERT INTO realtime_news_items"
            " (symbol, headline, source, url, published_at, ingested_at,"
            "  classification_deferred_reason, is_material)"
            " VALUES ('MU', 'Micron cuts guidance', 'alpaca', :u, :p, :i,"
            "         'budget_exhausted', :m)"),
            {"u": url, "p": now - timedelta(minutes=minutes_old), "i": now, "m": material})
        s.commit()


def _stub_classifier(monkeypatch, result):
    import services.deferred_retry as DR
    import services.classify as C
    monkeypatch.setattr(C, "classify_in_batches",
                        lambda h, k, **kw: [result] * len(h))
    return DR


LABEL = {"sentiment_score": 12.0, "sentiment_label": "negative",
         "is_material": True, "category": "earnings"}


def test_a_fresh_deferred_article_is_classified_and_arms_the_gate(db, monkeypatch):
    now = datetime.utcnow()
    _insert(now, minutes_old=5, url="https://x/fresh")
    DR = _stub_classifier(monkeypatch, LABEL)

    out = DR.retry_deferred()
    assert out["attempted"] == 1 and out["classified_fresh"] == 1

    with _Session() as s:
        rows = s.execute(text("SELECT * FROM realtime_news_items")).mappings().all()
    assert len(rows) == 1, "updated in place — a second insert would duplicate the article"
    r = rows[0]
    assert r["url"] == "https://x/fresh", "the original URL is untouched"
    assert r["sentiment_label"] == "negative" and r["category"] == "earnings"
    assert r["is_material"] is True, "a fresh item may arm the gate"
    assert r["classification_deferred_reason"] is None, "otherwise it retries forever"
    assert r["classification_attempts"] == 1


def test_a_stale_deferred_article_is_labelled_without_arming_the_gate(db, monkeypatch):
    """The gate suppresses a BUY on news the market has not absorbed. A day-old story is not
    that, however correct its label."""
    now = datetime.utcnow()
    _insert(now, minutes_old=60 * 20, url="https://x/stale")
    DR = _stub_classifier(monkeypatch, LABEL)

    out = DR.retry_deferred()
    assert out["classified_stale_no_gate"] == 1 and out["classified_fresh"] == 0

    with _Session() as s:
        r = s.execute(text("SELECT * FROM realtime_news_items")).mappings().one()
    assert r["sentiment_label"] == "negative", "the label is still recorded"
    assert r["is_material"] is False, "a stale headline must not arm today's risk gate"
    assert r["classification_deferred_reason"] is None


def test_an_existing_risk_flag_is_never_cleared_by_the_retry(db, monkeypatch):
    now = datetime.utcnow()
    _insert(now, minutes_old=60 * 20, url="https://x/flagged", material=True)
    DR = _stub_classifier(monkeypatch, {**LABEL, "is_material": False})

    DR.retry_deferred()
    with _Session() as s:
        r = s.execute(text("SELECT * FROM realtime_news_items")).mappings().one()
    assert r["is_material"] is True, \
        "a stale re-classification must not clear a flag that was already set"


def test_an_article_older_than_the_window_is_left_alone(db, monkeypatch):
    now = datetime.utcnow()
    with _Session() as s:
        s.execute(text(
            "INSERT INTO realtime_news_items (symbol, headline, source, url, published_at,"
            " ingested_at, classification_deferred_reason)"
            " VALUES ('MU','old','alpaca','https://x/old', :p, :i, 'budget_exhausted')"),
            {"p": now - timedelta(days=9), "i": now - timedelta(days=9)})
        s.commit()
    DR = _stub_classifier(monkeypatch, LABEL)
    out = DR.retry_deferred()
    assert out["attempted"] == 0, "spending on a nine-day-old headline buys no decision"


def test_an_out_of_scope_row_is_never_picked_up(db, monkeypatch):
    """"Never eligible" is not "deferred" — retrying those would undo the scope saving."""
    now = datetime.utcnow()
    with _Session() as s:
        s.execute(text(
            "INSERT INTO realtime_news_items (symbol, headline, source, url, published_at,"
            " ingested_at, classification_deferred_reason)"
            " VALUES (NULL,'unrelated','alpaca','https://x/oos', :p, :i, 'out_of_scope')"),
            {"p": now, "i": now})
        s.commit()
    DR = _stub_classifier(monkeypatch, LABEL)
    assert DR.retry_deferred()["attempted"] == 0


def test_an_item_still_over_budget_stays_deferred_and_counts_an_attempt(db, monkeypatch):
    now = datetime.utcnow()
    _insert(now, minutes_old=5, url="https://x/again")
    import services.deferred_retry as DR
    import services.classify as C

    def _still_broke(h, k, **kw):
        if kw.get("deferred_out") is not None:
            kw["deferred_out"].update(range(len(h)))
        return [None] * len(h)

    monkeypatch.setattr(C, "classify_in_batches", _still_broke)
    out = DR.retry_deferred()
    assert out["still_deferred"] == 1
    with _Session() as s:
        r = s.execute(text("SELECT * FROM realtime_news_items")).mappings().one()
    assert r["classification_deferred_reason"] == "budget_exhausted", "it stays retryable"
    assert r["classification_attempts"] == 1, "a stuck row is visible"


def test_two_workers_cannot_both_pay_for_the_same_article(db, monkeypatch):
    """The duplicate spend this budget exists to prevent. Both workers run against the same
    deferred row; exactly one may reach the provider."""
    import threading
    now = datetime.utcnow()
    _insert(now, minutes_old=5, url="https://x/contended")

    import services.deferred_retry as DR
    import services.classify as C
    paid, lock = [], threading.Lock()

    def _charging_classifier(h, k, **kw):
        with lock:
            paid.append(len(h))
        return [LABEL] * len(h)

    monkeypatch.setattr(C, "classify_in_batches", _charging_classifier)

    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        try:
            DR.retry_deferred()
        except Exception:
            pass

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert sum(paid) == 1, f"the provider was called for {sum(paid)} copies of one article"
    with _Session() as s:
        rows = s.execute(text("SELECT * FROM realtime_news_items")).mappings().all()
    assert len(rows) == 1 and rows[0]["classification_deferred_reason"] is None


def test_a_claimed_row_that_stays_over_budget_returns_to_the_queue(db, monkeypatch):
    """Otherwise a claim leaves it stranded in 'retrying' and it is never seen again."""
    now = datetime.utcnow()
    _insert(now, minutes_old=5, url="https://x/stuck")
    import services.deferred_retry as DR
    import services.classify as C

    def _still_broke(h, k, **kw):
        if kw.get("deferred_out") is not None:
            kw["deferred_out"].update(range(len(h)))
        return [None] * len(h)

    monkeypatch.setattr(C, "classify_in_batches", _still_broke)
    DR.retry_deferred()
    with _Session() as s:
        r = s.execute(text("SELECT * FROM realtime_news_items")).mappings().one()
    assert r["classification_deferred_reason"] == "budget_exhausted", \
        "a claimed row must return to the queue, not stay 'retrying'"
