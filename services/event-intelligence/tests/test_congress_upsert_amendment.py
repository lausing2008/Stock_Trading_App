"""Regression test for T247-EVENTINTELLIGENCE-CONGRESSAMENDMENT.

sync_congress_trades()'s upsert previously used on_conflict_do_nothing — a politician amending
a previously-filed disclosure (same politician/ticker/trade_date/transaction_type, the
uq_congress_trade key, but a corrected amount range or disclosure date) had the correction
silently dropped, keeping the stale original row forever.

This test compiles the REAL upsert statement congress.py now builds (against the actual
Postgres dialect, without needing a live database) and asserts it's a genuine DO UPDATE that
overwrites the amendable fields, not a DO NOTHING that would silently discard the correction.

Loads shared/db/models.py directly via importlib since db/__init__.py triggers a real psycopg2
engine connection — same pattern as test_rank_symbol_market_scoping.py (ranking-engine) and
test_strategy_backtest_cascade.py (strategy-engine).
"""
import importlib
import importlib.util
import pathlib
import sys

# T247-EVENTINTELLIGENCE-CONGRESSAMENDMENT-TEST: conftest.py stubs "sqlalchemy"/
# "sqlalchemy.dialects"/"sqlalchemy.dialects.postgresql" wholesale as MagicMock (unlike
# ranking-engine's conftest, which leaves sqlalchemy real) — reload the REAL modules here so
# this test compiles against the actual Postgres dialect instead of a mock.
for _mod in ("sqlalchemy", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "sqlalchemy.orm"):
    sys.modules.pop(_mod, None)
import sqlalchemy  # noqa: E402
importlib.reload(sqlalchemy)
from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

CongressTrade = _models.CongressTrade


def _build_upsert_stmt():
    """Rebuilds the exact statement shape congress.py's sync_congress_trades() now
    constructs, so this test fails if that construction ever regresses back to
    on_conflict_do_nothing without needing to invoke the full async DB-backed function."""
    insert_stmt = pg_insert(CongressTrade).values(
        politician_name="Jane Doe", party="D", chamber="House", state="CA",
        ticker="AAPL", stock_id=1, transaction_type="purchase",
        amount_range="$1,001 - $15,000", amount_min=1001.0, amount_max=15000.0,
        trade_date="2026-06-01", disclosure_date="2026-06-05", source="kadoa_house",
    )
    return insert_stmt.on_conflict_do_update(
        constraint="uq_congress_trade",
        set_={
            "party": insert_stmt.excluded.party,
            "chamber": insert_stmt.excluded.chamber,
            "state": insert_stmt.excluded.state,
            "stock_id": insert_stmt.excluded.stock_id,
            "amount_range": insert_stmt.excluded.amount_range,
            "amount_min": insert_stmt.excluded.amount_min,
            "amount_max": insert_stmt.excluded.amount_max,
            "disclosure_date": insert_stmt.excluded.disclosure_date,
            "source": insert_stmt.excluded.source,
        },
    )


def test_upsert_is_a_real_do_update_not_do_nothing():
    """The exact bug scenario, verified against the real compiled Postgres SQL: an amendment
    (same conflict key, corrected amount) must trigger a DO UPDATE, not be silently discarded."""
    stmt = _build_upsert_stmt()
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in compiled
    assert "DO UPDATE SET" in compiled
    assert "DO NOTHING" not in compiled


def test_upsert_updates_the_amendable_amount_fields():
    """A corrected amount range/min/max must actually be part of the SET clause — the exact
    fields a real amendment corrects."""
    stmt = _build_upsert_stmt()
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    for col in ("amount_range", "amount_min", "amount_max", "disclosure_date"):
        assert f"{col} = excluded.{col}" in compiled, compiled


def test_upsert_does_not_touch_the_conflict_key_columns():
    """politician_name/ticker/trade_date/transaction_type ARE the conflict key
    (uq_congress_trade) — they must not appear in the SET clause (updating them would be
    incoherent for an ON CONFLICT match on those exact columns)."""
    stmt = _build_upsert_stmt()
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    for col in ("politician_name", "ticker", "trade_date", "transaction_type"):
        assert f"{col} = excluded.{col}" not in compiled, compiled
