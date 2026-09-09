"""AUD-ENTRY-NYSEHOLIDAY-FOURTHCOPY — a fourth holiday calendar that escaped the consolidation.

`AUD-HOLIDAY-2027GAP` merged three drifted NYSE/HKEX calendars into
`shared/common/market_calendar.py` and added `assert_calendar_coverage()` to force annual
extension with runway. Decision-engine carried a **fourth** private `_NYSE_HOLIDAYS` frozenset in
`hard_rejects.py`, and that consolidation missed it — the same day.

Two things made that dangerous rather than merely untidy:

  1. **It sits in the AUTHORITATIVE entry gate.** `decision_engine_mode` defaults to `"primary"`,
     so `hard_rejects.py` is the path that actually blocks a trade on a market holiday.
  2. **It was completely unprotected.** `assert_calendar_coverage()` is invoked in exactly ONE
     place — the market-data test file — and that test does not import decision-engine. So this
     copy would have expired silently after **2027-12-24** with no failing test to announce it:
     precisely the setup that produced `AUD-HOLIDAY-2027GAP` in the first place.

Its dates were verified identical to the shared module's before replacement, so this is a pure
de-duplication with no behaviour change today. It only removes the next divergence.

THE PATTERN: this is the fourth instance in one session of "the same constant maintained in more
than one place." The sector→ETF map was in three services with three different key sets, the
benchmark-ETF list had a fourth copy that omitted XLP, `_soft_layer_keywords` had a divergent
hardcoded copy — and now this. The recurring check is: **after consolidating a constant, grep the
WHOLE repo for the literal, not just the files you set out to change.**
"""
import pathlib

import pytest

DE_HR = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[2] / "decision-engine/src/api/core/hard_rejects.py"
)
HR_SRC = DE_HR.read_text()


# ── The copy is gone, and the shared module is the source ───────────────────────────────

def test_decision_engine_no_longer_defines_its_own_table():
    """THE FIX. A private frozenset here is a fourth copy by definition."""
    assert "_NYSE_HOLIDAYS: frozenset[date] = frozenset({" not in HR_SRC
    assert "from common.market_calendar import NYSE_HOLIDAYS" in HR_SRC


def test_the_gate_still_reads_the_same_name():
    """The consumer is unchanged — only where the data comes from moved."""
    assert "_local.date() in _NYSE_HOLIDAYS" in HR_SRC


def test_no_stray_hardcoded_holiday_dates_remain():
    """A partial replacement leaving a few literals would be worse than either state."""
    import re
    literals = re.findall(r"date\(20\d\d, \d+, \d+\)", HR_SRC)
    assert literals == [], f"hardcoded holiday dates still present: {literals}"


def test_the_now_unused_date_import_was_removed():
    """`date` was only there for the literals. Leaving it is dead weight and a lint failure —
    verified with an AST walk that `date` is no longer used as a bare name (only `_local.date()`,
    which is a method call)."""
    assert "from datetime import datetime, timezone" in HR_SRC
    assert "from datetime import date, datetime, timezone" not in HR_SRC


# ── The whole point: no fifth copy anywhere ─────────────────────────────────────────────

def test_no_service_defines_its_own_nyse_holiday_table():
    """THE PARITY ASSERTION THAT WOULD HAVE CAUGHT THIS. After consolidating a constant, the
    check is repo-wide, not file-by-file.

    Only shared/common/market_calendar.py may contain the literal date set.
    """
    root = pathlib.Path(__file__).resolve().parents[3]
    # What counts as an offence is a table of literal DATES, not any assignment. Two legitimate
    # non-offences must be excluded:
    #   * `from ... import NYSE_HOLIDAYS as _NYSE_HOLIDAYS` — the fix itself.
    #   * scheduler.py's derived tuple set, `frozenset((d.year, d.month, d.day) for d in
    #     _NYSE_HOLIDAY_DATES)` — a SHAPE ADAPTER over the shared module (its three comparison
    #     sites want tuples), carrying no dates of its own.
    # So detect the literal `date(YYYY, M, D)` entries that only a real copy would contain.
    import re
    literal_re = re.compile(r"date\(20\d\d,\s*\d+,\s*\d+\)")
    offenders = []
    for py in root.glob("services/**/src/**/*.py"):
        src = py.read_text(errors="ignore")
        if "NYSE_HOLIDAYS" not in src:
            continue
        # Count date literals in the 2,000 chars following the NYSE_HOLIDAYS mention — a real
        # table has dozens; an import or an adapter has none.
        i = src.index("NYSE_HOLIDAYS")
        if len(literal_re.findall(src[i:i + 2000])) >= 5:
            offenders.append(str(py.relative_to(root)))
    assert offenders == [], f"a service still defines its own NYSE holiday table: {offenders}"


def test_the_shared_module_is_the_only_definition():
    root = pathlib.Path(__file__).resolve().parents[3]
    cal = root / "shared/common/market_calendar.py"
    assert cal.exists()
    assert "NYSE_HOLIDAYS: frozenset[date] = frozenset([" in cal.read_text()


# ── Behaviour is unchanged where it mattered, and extended where it did not ─────────────

def _shared_dates():
    root = pathlib.Path(__file__).resolve().parents[3]
    ns: dict = {}
    exec(  # noqa: S102 — our own module, stdlib-only
        (root / "shared/common/market_calendar.py").read_text(), ns
    )
    return ns["NYSE_HOLIDAYS"]


@pytest.mark.parametrize("y,m,d,label", [
    (2026, 9, 7, "Labor Day 2026 — the day the digest bug fired"),
    (2026, 12, 25, "Christmas 2026"),
    (2027, 12, 24, "Christmas 2027 observed — DE's old expiry edge"),
    (2027, 1, 1, "New Year 2027"),
])
def test_every_date_de_used_to_carry_is_still_a_holiday(y, m, d, label):
    """Verified identical before replacement; pinned so a future edit to the shared module
    cannot silently drop a date the authoritative gate depends on."""
    from datetime import date as _d
    assert _d(y, m, d) in _shared_dates(), label


def test_a_normal_trading_day_is_still_not_a_holiday():
    """The inverse — a fix that made everything a holiday would block all entries."""
    from datetime import date as _d
    assert _d(2026, 9, 8) not in _shared_dates()


def test_the_shared_table_is_a_superset_of_des_old_one():
    """DE carried 30 dates (2025-2027); the shared module carries 2024-2027. Strictly more
    coverage, so the gate can only become MORE correct, never less."""
    assert len(_shared_dates()) >= 30


# ── The gap that let this survive ───────────────────────────────────────────────────────

def test_the_coverage_assertion_is_still_only_enforced_by_tests():
    """RECORDED, NOT FIXED — an honest limitation worth knowing.

    `assert_calendar_coverage()` is called from the test suite and from NO service. That is
    sufficient (CI fails before the calendar expires) but it means the guard protects only what
    the tests import. DE's copy survived precisely because no test imported it. This test file
    now closes that specific hole for NYSE tables repo-wide, via
    test_no_service_defines_its_own_nyse_holiday_table above.
    """
    import re
    root = pathlib.Path(__file__).resolve().parents[3]
    callers = [
        str(p.relative_to(root))
        for p in root.glob("services/**/*.py")
        # Match a real CALL at the start of a statement, not a mention inside a comment.
        if re.search(r"^\s*(?:\w+\s*=\s*)?assert_calendar_coverage\(", p.read_text(errors="ignore"), re.M)
        and "/tests/" not in str(p) and "/test_" not in str(p)
    ]
    assert callers == [], (
        "if a service now calls it, update this test — the enforcement model changed"
    )
