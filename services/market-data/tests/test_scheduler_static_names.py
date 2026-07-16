"""Regression guard for AUD-SCHED-MARKET-NAMEERROR / AUD-SCHED-DESC-NAMEERROR.

scheduler.py imports from sqlalchemy/apscheduler/db, all of which conftest.py stubs as
MagicMock() for local testing — a real import of this module in a test would silently
"succeed" even if it referenced an undefined name, since MagicMock() attribute access never
raises. That's exactly how these two bugs shipped undetected by the full 146-test pytest
suite: _run_watchlist_auto_rotation() used `Market.US` (no `Market` import anywhere in the
file) and `desc(Ranking.score)` (no `desc` import in scope) — both real NameErrors that would
fire on every run of the weekly watchlist-rotation job, caught only by a full-codebase audit
that actually read the function rather than executing it under stubs.

A general "does every name resolve" static analyzer was attempted here and abandoned — nested
closures/lambdas/comprehensions make correct scope resolution nontrivial enough that a
hand-rolled version produced more false positives than real signal. Targeted regression checks
for the two specific names involved are simpler, correct, and sufficient: they fail loudly if
either import is ever removed again, without pretending to catch every future instance of this
bug class.
"""
import pathlib

_SCHEDULER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
)
_SOURCE = _SCHEDULER_PATH.read_text()


def test_market_is_imported_at_module_scope():
    """_run_watchlist_auto_rotation() uses Market.US (market tie-break for dominant_market
    selection) — must be imported at module scope (the `from db import ...` line)."""
    import_line = next(line for line in _SOURCE.splitlines() if line.startswith("from db import"))
    names = {n.strip() for n in import_line.removeprefix("from db import").split(",")}
    assert "Market" in names, f"Market missing from module-level db import: {import_line!r}"


def test_desc_is_imported_where_used():
    """_run_watchlist_auto_rotation() uses desc(Ranking.score) inside a local
    `from sqlalchemy import ...` — must include `desc`, not just func/case/delete."""
    start = _SOURCE.index("def _run_watchlist_auto_rotation(")
    end = _SOURCE.index("\ndef ", start + 1)
    body = _SOURCE[start:end]
    assert "desc(Ranking.score)" in body, "expected usage not found — has the function changed?"
    local_import_line = next(
        line for line in body.splitlines() if line.strip().startswith("from sqlalchemy import")
    )
    names = {n.strip().split(" as ")[0] for n in local_import_line.split("import", 1)[1].split(",")}
    assert "desc" in names, f"desc missing from local sqlalchemy import: {local_import_line!r}"
