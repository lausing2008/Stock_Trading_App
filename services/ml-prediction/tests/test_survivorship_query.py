"""Regression test for T14-SURVIVORSHIP-REAPPLY (aud14-survivorship / t17-survivorship-bias).

The ML training-universe query in routes.py's train_all/tune_all/train_all_ensemble*/
train_all_horizons was originally fixed in e32f9bd (2026-06-19) to include delisted stocks
(or_(Stock.active.is_(True), Stock.delisted.is_(True))), reverted 2 days later in 399e34e
because the `delisted` column's migration hadn't reached production yet, and never
re-applied once it did — silently reintroducing survivorship bias into every training run.

Two complementary checks:
1. A source-text check on the real routes.py file — confirms the actual 5 call sites use
   the OR condition, not just a hand-copied re-implementation of the intended query (a
   re-implementation would pass even if routes.py itself regressed back to active-only,
   which is exactly what happened once already).
2. A behavioral check against a real in-memory SQLite session, proving the OR condition
   genuinely includes delisted stocks and excludes merely-inactive ones — same technique as
   ranking-engine's test_rank_symbol_market_scoping.py and strategy-engine's
   test_strategy_backtest_cascade.py, adapted here because ml-prediction's conftest.py stubs
   sqlalchemy wholesale (unlike those two services), so the real sqlalchemy package is
   swapped in only for the duration of a single function call, never left in sys.modules
   across pytest's collection phase (which imports every test file up front) or other test
   files' later executions.
"""
import importlib.util
import pathlib
import sys

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_SOURCE = _ROUTES_PATH.read_text()

_STUBBED = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql")


def _run_with_real_sqlalchemy(fn):
    """Swap in the real sqlalchemy modules, run fn(), then restore the original stubs —
    entirely within this call, so no other test file's collection or execution ever
    observes the swap (pytest imports every test module during collection, before running
    any test body, so a module-level swap would corrupt the shared sys.modules cache for
    files that haven't executed their tests yet)."""
    saved = {m: sys.modules.get(m) for m in _STUBBED}
    for m in _STUBBED:
        sys.modules.pop(m, None)
    try:
        from sqlalchemy import create_engine, or_, select
        from sqlalchemy.orm import Session

        models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
        spec = importlib.util.spec_from_file_location("db_models_under_test", models_path)
        models = importlib.util.module_from_spec(spec)
        sys.modules["db_models_under_test"] = models
        spec.loader.exec_module(models)

        return fn(create_engine, or_, select, Session, models)
    finally:
        for m, prev in saved.items():
            if prev is not None:
                sys.modules[m] = prev
            else:
                sys.modules.pop(m, None)
        sys.modules.pop("db_models_under_test", None)


def _training_universe_symbols(fixture_rows):
    def _body(create_engine, or_, select, Session, models):
        Stock, Market, Exchange, Base = models.Stock, models.Market, models.Exchange, models.Base
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine, tables=[Stock.__table__])
        session = Session(engine)
        for symbol, active, delisted in fixture_rows:
            session.add(Stock(
                symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ,
                name=f"{symbol} Co", active=active, delisted=delisted,
            ))
        session.commit()
        return list(session.execute(
            select(Stock.symbol).where(or_(Stock.active.is_(True), Stock.delisted.is_(True)))
        ).scalars())

    return _run_with_real_sqlalchemy(_body)


# ── Source-text check: routes.py itself must use the OR condition ────────────────

def test_all_five_training_universe_query_sites_include_delisted_stocks():
    """The exact regression scenario: routes.py's 5 call sites (train_all, tune_all,
    train_all_ensemble_three, train_all_ensemble, train_all_horizons) must query
    or_(Stock.active.is_(True), Stock.delisted.is_(True)), not bare Stock.active.is_(True) —
    reverting to active-only silently reintroduces survivorship bias, exactly what commit
    399e34e did to commit e32f9bd's fix."""
    or_sites = _SOURCE.count("or_(Stock.active.is_(True), Stock.delisted.is_(True))")
    assert or_sites == 5, (
        f"expected 5 call sites using the active-OR-delisted filter, found {or_sites}. "
        "Check services/ml-prediction/src/api/routes.py's train_all/tune_all/"
        "train_all_ensemble_three/train_all_ensemble/train_all_horizons functions."
    )


def test_no_call_site_regressed_to_bare_active_only_filter():
    """Belt-and-suspenders: explicitly assert the OLD (buggy) bare-active query string does
    NOT appear anywhere in the file — catches a partial revert of just 1-4 of the 5 sites,
    which the count-based check above would also catch, but this pinpoints the failure mode
    more directly if it ever regresses again."""
    assert "select(Stock.symbol).where(Stock.active.is_(True))" not in _SOURCE


# ── Behavioral check: the real query construction, run against real SQLite ───────

def test_delisted_stock_is_included_in_the_training_universe():
    """The exact bug scenario: a delisted stock (active=False, delisted=True) must still
    appear in the training universe, not be silently excluded."""
    symbols = _training_universe_symbols([
        ("ACTIVE1", True, False),
        ("DELISTED1", False, True),
    ])
    assert "ACTIVE1" in symbols
    assert "DELISTED1" in symbols


def test_inactive_non_delisted_stock_is_still_excluded():
    """A stock that's merely inactive (e.g. manually deactivated, not confirmed delisted)
    should NOT be included — only active OR genuinely-delisted stocks belong in training."""
    symbols = _training_universe_symbols([
        ("INACTIVE1", False, False),
    ])
    assert "INACTIVE1" not in symbols


def test_query_is_a_safe_noop_today_since_delisted_is_never_actually_set():
    """Documents the current real-world state: no data source in this codebase sets
    delisted=True on any row today, so this fix is a no-op in production until that changes —
    it must not silently break anything for the all-active, no-delisted-rows case."""
    symbols = _training_universe_symbols([
        ("A", True, False),
        ("B", True, False),
    ])
    assert set(symbols) == {"A", "B"}
