"""AUD-A19-EQUITYMIXEDBASIS — the options equity curve must say WHICH equity it means.

Production had exactly two rows and they used two different definitions:

    2026-09-16 : 178,567 + 73,100          = 251,667   liability EXACTLY 0, 3 positions OPEN
    2026-09-17 :  66,467 + 186,600 - 3,087 = 249,980

Stored in the same column with nothing distinguishing them, so the apparent -1,687 move between
the only two points on the curve is mostly the liability term appearing for the first time, not
a loss. These tests pin the three things that keep that from recurring: the writer stamps a
basis, the migration derives each existing row's basis from its OWN arithmetic rather than a
cutoff date, and the constant is not changed casually.
"""
import pathlib
import re

_ENGINE = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "services" / "options_income_engine.py").read_text()
_MIGRATION = (pathlib.Path(__file__).resolve().parents[3]
              / "scripts" / "migrations"
              / "013_add_equity_basis_to_options_income_equity_curve.sql").read_text()


def _snapshot_fn_source() -> str:
    start = _ENGINE.index("def _snapshot_income_equity_curve(")
    return _ENGINE[start:_ENGINE.index("\ndef ", start)]


def test_every_write_path_stamps_the_basis():
    """Two write paths exist (update an existing row for today, insert a new one). A basis
    stamped on only one of them reproduces the bug for whichever path runs second."""
    body = _snapshot_fn_source()
    assert "existing.equity_basis = _EQUITY_BASIS" in body, "update path must stamp the basis"
    assert "equity_basis=_EQUITY_BASIS" in body, "insert path must stamp the basis"


def test_basis_constant_describes_the_actual_formula():
    """The constant is a claim about the arithmetic directly above it. If equity stops
    subtracting the liability, this name becomes a lie that no other test would catch."""
    assert '_EQUITY_BASIS = "cash_collateral_less_liability"' in _ENGINE
    body = _snapshot_fn_source()
    assert "+ collateral_committed - short_liability" in body, (
        "the basis name promises cash + collateral MINUS liability — keep them in step"
    )


def test_migration_is_idempotent():
    """run_migrations.sh re-runs every migration on every invocation."""
    assert "ADD COLUMN IF NOT EXISTS equity_basis" in _MIGRATION
    assert "WHERE equity_basis IS NULL" in _MIGRATION, "backfill must not overwrite known values"


def test_migration_is_registered_in_the_runner():
    """Migration 012 sat unregistered in this directory; an unregistered migration is one
    nobody runs."""
    runner = (pathlib.Path(__file__).resolve().parents[3]
              / "scripts" / "migrations" / "run_migrations.sh").read_text()
    assert "013_add_equity_basis_to_options_income_equity_curve.sql" in runner


# ── The migration's classification rule, executed against the REAL production rows ──────

def _classify(equity: float, cash: float, collateral: float, open_positions: int) -> str:
    """Python mirror of migration 013's CASE expression, kept deliberately literal so the two
    can be read side by side."""
    if open_positions == 0:
        return "cash_collateral_less_liability"
    if abs(equity - (cash + collateral)) < 0.005:
        return "cash_collateral"
    return "cash_collateral_less_liability"


def test_classifies_the_real_pre_fix_row_as_pre_fix():
    """The actual 2026-09-16 production row: three open positions and a liability of exactly
    zero — only possible under the old definition."""
    assert _classify(equity=251_667.0, cash=178_567.0, collateral=73_100.0, open_positions=3) \
        == "cash_collateral"


def test_classifies_the_real_post_fix_row_as_post_fix():
    """The actual 2026-09-17 row: 66,467 + 186,600 - 3,087 = 249,980."""
    assert _classify(equity=249_980.0, cash=66_467.0, collateral=186_600.0, open_positions=6) \
        == "cash_collateral_less_liability"


def test_a_row_with_no_open_positions_is_unambiguous():
    """With nothing short, the two definitions produce the identical number, so the row IS
    comparable to a current-basis row and is labelled as such — that is true of it, not a
    convenient default."""
    assert _classify(equity=250_000.0, cash=250_000.0, collateral=0.0, open_positions=0) \
        == "cash_collateral_less_liability"


def test_migration_does_not_classify_by_date():
    """A cutoff-date backfill is a guess: it mislabels any row written by an older deploy still
    running past the cutoff. The rule must read each row's own arithmetic."""
    assert not re.search(r"2026-09-1\d", _MIGRATION.split("UPDATE")[1]), (
        "the UPDATE must not key off a hardcoded date"
    )
    assert "ABS(equity - (cash + collateral_committed))" in _MIGRATION
