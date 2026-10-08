"""Behavioural request test for /quality-value/evaluations — the REAL handler, real SQL.

WHY THIS EXISTS. `classify_instrument` was used in `evaluations()` without being imported, so
every call raised NameError, and `make test`, `make test-integration` and `make test-all` were
all green over a route that could not run. A name-resolution check catches the missing import,
but it does not establish that the route WORKS — it never executes a line of it.

This calls the handler, against real SQLAlchemy models on SQLite, exercising the actual query
path: the universe scan, the per-symbol statement and price reads, the gate composition and the
summary. It is not a production/PostgreSQL test and does not claim to be; it establishes that
the endpoint executes end to end and that its three interesting cases behave differently.

Covered: MU (an operating company with statements), GLD (a fund by name, no statements, no
sector) and the default universe (no `symbols` argument at all).
"""
import importlib
import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import (Boolean, Column, Date, DateTime, Float, Integer, String, create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker


@pytest.fixture()
def routes(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "qv_req_calendar", root.parents[1] / "shared/common/market_calendar.py")
    calendar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(calendar)
    monkeypatch.setitem(sys.modules, "common.market_calendar", calendar)
    import fastapi
    router = SimpleNamespace(get=lambda *a, **k: lambda f: f,
                             post=lambda *a, **k: lambda f: f)
    monkeypatch.setattr(fastapi, "APIRouter", lambda **k: router)
    mod = importlib.import_module("src.api.quality_value_routes")

    Base = declarative_base()

    class Stock(Base):
        __tablename__ = "stocks"
        id = Column(Integer, primary_key=True)
        symbol = Column(String); market = Column(String); name = Column(String)
        sector = Column(String); industry = Column(String); currency = Column(String)
        active = Column(Boolean); delisted = Column(Boolean)

    class FinancialStatement(Base):
        """COLUMN NAMES TAKEN FROM THE REAL MODEL, not invented. The first version of this stub
        used `free_cash_flow` and `operating_cash_flow`; the real columns are `free_cashflow`
        and `operating_cashflow`, and the handler raised AttributeError. A stub whose shape
        drifts from the model tests the stub."""
        __tablename__ = "financial_statements"
        id = Column(Integer, primary_key=True)
        symbol = Column(String); period_end = Column(Date); period_type = Column(String)
        total_revenue = Column(Float); gross_profit = Column(Float)
        operating_income = Column(Float); ebit = Column(Float); net_income = Column(Float)
        tax_provision = Column(Float); pretax_income = Column(Float)
        total_assets = Column(Float); total_debt = Column(Float); total_equity = Column(Float)
        cash_and_equivalents = Column(Float); current_liabilities = Column(Float)
        operating_cashflow = Column(Float); capital_expenditure = Column(Float)
        free_cashflow = Column(Float); fetched_at = Column(DateTime)

    class Price(Base):
        __tablename__ = "prices"
        id = Column(Integer, primary_key=True)
        stock_id = Column(Integer); ts = Column(DateTime); timeframe = Column(String)
        high = Column(Float); low = Column(Float); close = Column(Float)
        volume = Column(Float); adj_close = Column(Float)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(mod, "SessionLocal", factory)
    monkeypatch.setattr(mod, "Stock", Stock)
    monkeypatch.setattr(mod, "Price", Price)
    monkeypatch.setattr(mod, "FinancialStatement", FinancialStatement)
    monkeypatch.setattr(mod, "TimeFrame", SimpleNamespace(D1="1d"))
    monkeypatch.setitem(sys.modules, "db",
                        SimpleNamespace(FinancialStatement=FinancialStatement,
                                        SessionLocal=factory, Stock=Stock, Price=Price))
    # Persistence writes to tables this SQLite base does not define; the route already treats a
    # persist failure as non-fatal and records it, which is the behaviour under test elsewhere.
    monkeypatch.setattr(mod, "record_evaluation",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no store here")))
    monkeypatch.setattr(mod, "latest_assessments", lambda *a, **k: {})

    now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    with factory() as s:
        # MU — an operating company: sector, industry AND stored annual statements.
        s.add(Stock(id=1, symbol="MU", market="US", name="Micron Technology, Inc.",
                    sector="Technology", industry="Semiconductors", currency="USD",
                    active=True, delisted=False))
        for i, yr in enumerate((2026, 2025, 2024)):
            s.add(FinancialStatement(
                symbol="MU", period_type="annual", period_end=date(yr, 8, 29),
                fetched_at=datetime(2026, 10, 1), total_revenue=5e10 - i * 1e10,
                gross_profit=2e10, operating_income=1.4e10, ebit=1.4e10, net_income=1e10,
                tax_provision=1e9, pretax_income=1.1e10, total_assets=9e10,
                total_debt=1.4e10, total_equity=6e10, cash_and_equivalents=9e9,
                current_liabilities=1e10, operating_cashflow=2e10,
                capital_expenditure=1.5e10, free_cashflow=5e9))
        # GLD — a fund BY NAME: no statements and, deliberately, no sector or industry, so the
        # classifier reaches the name rather than the metadata.
        s.add(Stock(id=2, symbol="GLD", market="US", name="SPDR Gold Shares ETF Trust",
                    sector=None, industry=None, currency="USD", active=True, delisted=False))
        for sid in (1, 2):
            day = datetime(2026, 10, 7)
            added = 0
            while added < 30:
                if calendar.is_trading_day("US", day.replace(hour=12, tzinfo=timezone.utc)):
                    s.add(Price(stock_id=sid, ts=day, timeframe="1d", high=110, low=90,
                                close=100, adj_close=100, volume=1000))
                    added += 1
                day -= timedelta(days=1)
        s.commit()
    yield mod, factory
    engine.dispose()


def test_the_endpoint_executes_for_a_single_operating_company(routes):
    mod, _ = routes
    body = mod.evaluations(symbols="MU", _user="test")
    assert body["evaluated"] == 1
    e = body["evaluations"][0]
    assert e["symbol"] == "MU"
    assert e["instrument"]["type"] == "operating_company"
    assert e["instrument"]["confidence"] == "inferred", "nothing verifies an instrument type"
    assert e["summary"]["applicability"]["status"] == "unverified"
    assert e["summary"]["applicability"]["company_research_applicable"] is True
    assert e["state"], "a state is always reached, even if it is insufficient_evidence"
    assert e["gates"], "gates were actually composed"


def test_the_endpoint_does_not_treat_a_fund_as_a_company_to_research(routes):
    """THE GLD CASE, end to end through the real handler rather than a hand-built summary."""
    mod, _ = routes
    body = mod.evaluations(symbols="GLD", _user="test")
    e = body["evaluations"][0]
    assert e["instrument"]["type"] == "fund"
    ap = e["summary"]["applicability"]
    assert ap["status"] == "unverified", "inferred, so not asserted as not-applicable either"
    assert ap["company_research_applicable"] is False
    assert ap["fund_analysis_required"], "what a fund would actually need is named"
    assert e["summary"]["next_research"] == [], "no company backlog on a pooled vehicle"


def test_the_endpoint_scans_the_default_universe_with_no_symbols_argument(routes):
    """The path a page load actually takes. The NameError shipped on exactly this call."""
    mod, _ = routes
    body = mod.evaluations(symbols=None, _user="test")
    assert body["evaluated"] == 2
    got = {e["symbol"]: e for e in body["evaluations"]}
    assert set(got) == {"MU", "GLD"}
    assert got["MU"]["instrument"]["type"] == "operating_company"
    assert got["GLD"]["instrument"]["type"] == "fund"
    # The two must not be described identically — that was the defect.
    assert (got["MU"]["summary"]["applicability"]["company_research_applicable"]
            != got["GLD"]["summary"]["applicability"]["company_research_applicable"])
    for key in ("status_catalog", "gate_coverage", "states", "notes", "required_for_entry"):
        assert key in body, f"the response is missing {key}, which the page renders"


def test_a_persistence_failure_is_reported_rather_than_failing_the_request(routes):
    """The fixture forces every store to raise. A read endpoint that 500s because a shadow
    write failed would take the screen down for a reason the reader does not care about."""
    mod, _ = routes
    body = mod.evaluations(symbols="MU", _user="test")
    assert body["persist_errors"], "the failure is surfaced, not swallowed"
    assert body["evaluations"], "and the read still answers"
