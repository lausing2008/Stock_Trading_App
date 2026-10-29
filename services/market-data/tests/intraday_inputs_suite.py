"""Intraday snapshot reader on real models (design §4; review CDR-01 acceptance list).

Run by test_intraday_store.py in a subprocess with --noconftest (SQLite, and PostgreSQL 16 when
provided). No case may acquire a first-availability claim; source identity is known only when
the provenance read in the same snapshot matches the price row exactly and tracing did not fail.
"""
import importlib.util
import os
import pathlib
import sys
import types
import uuid
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services" / "market-data")]
db = types.ModuleType("db")
db.__path__ = [str(ROOT / "shared/db")]
sys.modules["db"] = db
_spec = importlib.util.spec_from_file_location("db.models", ROOT / "shared/db/models.py")
models = importlib.util.module_from_spec(_spec)
sys.modules["db.models"] = models
_spec.loader.exec_module(models)
for _k in vars(models):
    setattr(db, _k, getattr(models, _k))

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.services.bar_provenance import BarProvenance, BarSessionAuthority  # noqa: E402
from src.services.intraday import contract as c  # noqa: E402
from src.services.intraday.inputs import read_snapshot  # noqa: E402

URL = os.environ.get("INTRADAY_SUITE_URL", "sqlite://")
PG = URL.startswith("postgresql")
UTC = timezone.utc
D = date(2026, 10, 28)
OPEN = datetime(2026, 10, 28, 13, 30)          # naive UTC, as stored


@pytest.fixture
def engine():
    if PG:
        name = "i_" + uuid.uuid4().hex[:12]
        admin = create_engine(URL)
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {name}"))
        eng = create_engine(URL, connect_args={"options": f"-csearch_path={name}"})
    else:
        from sqlalchemy.pool import StaticPool
        eng = create_engine(URL, poolclass=StaticPool, connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(eng, tables=[models.Stock.__table__, models.Price.__table__,
                                                 BarProvenance.__table__, BarSessionAuthority.__table__])
    with Session(eng) as s:
        s.add(models.Stock(id=1, symbol="TEST", market=models.Market.US, exchange=models.Exchange.NASDAQ,
                           name="t", active=True, delisted=False))
        for i in range(12):                                  # 13:30 .. 14:25
            ts = OPEN + timedelta(minutes=5 * i)
            s.add(models.Price(id=i + 1, stock_id=1, ts=ts, timeframe=models.TimeFrame.M5, open=100.0 + i,
                               high=101.0 + i, low=99.0 + i, close=100.5 + i, volume=1000.0, adj_close=None,
                               session="REGULAR"))
            s.add(BarProvenance(stock_id=1, timeframe="5m", ts=ts, provider="unusual_whales", first_written_at=ts,
                                last_written_at=ts, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.5 + i,
                                volume=1000.0, ohlc_revisions=0, volume_only_revisions=0,
                                provider_switches_same_values=0, off_grid=False))
        s.commit()
    yield eng
    eng.dispose()
    if PG:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {name} CASCADE"))
        admin.dispose()


CUTOFF = datetime(2026, 10, 28, 14, 32, tzinfo=UTC)     # 14:25 bar closes 14:30, readable 14:32


def snap(engine, **kw):
    return read_snapshot(engine, stock_id=1, symbol="TEST", market="US", session_date=D, data_cutoff=CUTOFF,
                         clock=lambda: CUTOFF + timedelta(seconds=3), **kw)


def _exec(engine, sql, **p):
    with engine.begin() as conn:
        conn.execute(text(sql), p)


PV = BarProvenance.__table__


def _core(engine, stmt):
    """Core statements, not text(): SQLite stores DateTime in SQLAlchemy's own format, so a raw
    datetime parameter would silently match nothing."""
    with engine.begin() as conn:
        assert conn.execute(stmt).rowcount >= 1


def by_open(s, h, m):
    return next(r for r in s.bars_5m if r.bar_open == datetime(2026, 10, 28, h, m, tzinfo=UTC))


def test_baseline_snapshot(engine):
    s = snap(engine)
    assert s.status == "ok" and len(s.bars_5m) == 12 and len(s.bars_15m) == 4
    for r in s.bars_5m + s.bars_15m:
        assert r.first_available_at is None and r.availability_basis == "unknown_exact_version"
        assert r.provider_published_at is None
        assert r.readable_at <= r.input_read_at == CUTOFF + timedelta(seconds=3)
    assert by_open(s, 13, 30).extra["provider"] == "unusual_whales"
    assert all(r.source == "derived_from_5m" and r.derived_hash for r in s.bars_15m)


def test_a_bar_not_yet_settled_is_not_read(engine):
    s = read_snapshot(engine, stock_id=1, symbol="TEST", market="US", session_date=D,
                      data_cutoff=CUTOFF - timedelta(minutes=1), clock=lambda: CUTOFF)
    assert len(s.bars_5m) == 11                                        # 14:25 bar readable only at 14:32


def test_volume_only_revision_after_ohlc_revision_changes_raw_hash_and_claims_nothing(engine):
    base = snap(engine)
    _exec(engine, "UPDATE prices SET volume = 2000 WHERE id = 1")
    _core(engine, PV.update().where(PV.c.ts == OPEN).values(volume=2000.0))
    after = snap(engine)
    assert by_open(after, 13, 30).raw_hash != by_open(base, 13, 30).raw_hash
    assert by_open(after, 13, 30).first_available_at is None


def test_source_only_switch_changes_source_hash_not_raw_hash(engine):
    base = snap(engine)
    _core(engine, PV.update().where(PV.c.ts == OPEN).values(provider="yfinance"))
    after = snap(engine)
    b0, b1 = by_open(base, 13, 30), by_open(after, 13, 30)
    assert b0.raw_hash == b1.raw_hash and b0.source_hash != b1.source_hash
    assert b1.first_available_at is None


def test_sub_tolerance_price_revision_is_a_different_version(engine):
    base = snap(engine)
    _exec(engine, "UPDATE prices SET close = close + 0.0000001 WHERE id = 1")
    after = snap(engine)
    assert by_open(after, 13, 30).raw_hash != by_open(base, 13, 30).raw_hash
    assert by_open(after, 13, 30).extra["provider_basis"] == "provenance_mismatch"   # trace kept old values


def test_adjustment_basis_change_changes_input_hash(engine):
    base = snap(engine)
    _exec(engine, "UPDATE prices SET adj_close = close * 0.5")
    after = snap(engine)
    assert by_open(after, 13, 30).input_hash != by_open(base, 13, 30).input_hash
    assert by_open(after, 13, 30).values["price_basis"] == "adjusted"


def test_failed_trace_and_missing_provenance_keep_source_unknown(engine):
    _core(engine, PV.delete().where(PV.c.ts == OPEN))
    s = snap(engine)
    assert by_open(s, 13, 30).extra["provider_basis"] == "provenance_missing"
    assert by_open(s, 13, 30).source_hash == c.source_hash(by_open(s, 13, 30).raw_hash, None)
    with Session(engine) as ses:
        ses.add(BarSessionAuthority(stock_id=1, timeframe="5m", session_date=datetime(2026, 10, 28),
                                    authority="unusual_whales", recorded_at=OPEN, protected=0, replaced=0,
                                    trace_failed=True))
        ses.commit()
    s = snap(engine)
    assert all(r.extra["provider"] == "unknown" for r in s.bars_5m)


def test_old_row_first_traced_today_gains_no_availability(engine):
    _core(engine, PV.update().where(PV.c.ts == OPEN).values(first_written_at=datetime(2026, 10, 29, 9, 0)))
    assert by_open(snap(engine), 13, 30).first_available_at is None


def test_derived_input_with_one_unknown_constituent(engine):
    _core(engine, PV.delete().where(PV.c.ts == OPEN + timedelta(minutes=5)))
    s = snap(engine)
    first = s.bars_15m[0]
    assert first.extra["constituents_unknown_source"] == 1 and first.first_available_at is None


def test_replay_reads_at_the_logical_cutoff_and_hk_is_refused(engine):
    s = snap(engine, mode="replay")
    assert all(r.input_read_at == CUTOFF and r.read_clock_basis == "replay_logical" for r in s.bars_5m)
    from common.horizons import UnsupportedMarket
    with pytest.raises(UnsupportedMarket):
        read_snapshot(engine, stock_id=1, symbol="TEST", market="HK", session_date=D, data_cutoff=CUTOFF)
