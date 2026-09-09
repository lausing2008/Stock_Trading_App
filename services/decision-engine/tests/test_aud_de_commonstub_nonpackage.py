"""AUD-DE-COMMONSTUB-NONPACKAGE — a bare `common` MagicMock is not a PACKAGE.

A DEFECT I INTRODUCED MYSELF, one day earlier, and only found while running the suite for an
unrelated change. `AUD-ENTRY-NYSEHOLIDAY-FOURTHCOPY` (commit 5796ebf, 2026-09-08) correctly
replaced hard_rejects.py's private `_NYSE_HOLIDAYS` frozenset with
`from common.market_calendar import NYSE_HOLIDAYS`. Four test files in this directory carry
their own `sys.modules.setdefault("common", MagicMock())` preamble, and a bare MagicMock is not
a package — so Python refuses to resolve any `common.<submodule>` import through it:

    test_hard_rejects.py  test_score_replay.py  test_entry_gate_params.py  test_entry_weights.py
    -> ModuleNotFoundError: No module named 'common.market_calendar'; 'common' is not a package

**144 + 15 + 15 + 14 = 188 tests stopped running**, including the entire hard-rejects gate suite
that guards real trading decisions.

WHY IT WENT UNNOTICED FOR A DAY: it surfaces as `4 errors during collection`, not as a test
FAILURE. A targeted run of the file you happen to be working on still passes, and the summary
line reads "errors" rather than "failed" — so the day's deploy shipped with four whole files
silently uncollectable. `pytest` exiting non-zero was the only signal, and it was attributed to
an unrelated in-progress change.

THE FIX, borrowed verbatim from market-data/tests/conftest.py which already solved this for the
same module: register the real `common.market_calendar` (and stubs for the submodules that reach
outside the process) BOTH in sys.modules AND as attributes on the parent. Both halves matter —
sys.modules alone satisfies `import common.market_calendar` but NOT
`from common.market_calendar import X` resolved through a mocked parent.

THE GENERALISABLE LESSON: `sys.modules` stubs are process-wide and order-dependent. Stubbing a
PACKAGE as a bare Mock silently forbids every real submodule import beneath it, and the breakage
lands at collection time in files you did not touch.
"""
import pathlib
import sys
from datetime import date

import pytest

CONFTEST = (pathlib.Path(__file__).resolve().parent / "conftest.py").read_text()


# ── The calendar must be REAL, not a truthy Mock ────────────────────────────────────────

def test_nyse_holidays_is_real_data():
    """THE VACUITY TRAP CLAUDE.md WARNS ABOUT. Under a blanket `common` MagicMock,
    `date(...) in NYSE_HOLIDAYS` returns a truthy Mock, so EVERY holiday assertion passes no
    matter what the calendar contains — or whether it contains anything at all."""
    from common.market_calendar import NYSE_HOLIDAYS
    assert isinstance(NYSE_HOLIDAYS, frozenset), "a Mock would not be a frozenset"
    assert len(NYSE_HOLIDAYS) >= 30, f"only {len(NYSE_HOLIDAYS)} dates — is this really loaded?"


def test_the_calendar_discriminates_both_ways():
    """A Mock returns truthy for BOTH, so asserting only the positive case proves nothing."""
    from common.market_calendar import NYSE_HOLIDAYS
    assert date(2026, 9, 7) in NYSE_HOLIDAYS, "Labor Day 2026 must be a holiday"
    assert date(2026, 9, 8) not in NYSE_HOLIDAYS, "an ordinary Tuesday must NOT be"


def test_hard_rejects_can_import_the_calendar():
    """The import that broke. If this fails, the four sibling files are uncollectable again."""
    from src.api.core import hard_rejects as hr
    assert hr._NYSE_HOLIDAYS is not None
    assert date(2026, 9, 7) in hr._NYSE_HOLIDAYS


# ── Both registration halves are present ────────────────────────────────────────────────

def test_the_submodule_is_registered_in_sys_modules():
    assert "common.market_calendar" in sys.modules


def test_the_submodule_is_also_an_attribute_of_the_parent():
    """THE HALF THAT IS EASY TO MISS. `from common.market_calendar import X` resolves the
    attribute on the parent, not just the sys.modules entry."""
    assert hasattr(sys.modules["common"], "market_calendar")


def test_the_conftest_registers_the_calendar_in_sys_modules():
    """The load-bearing half. The setattr alongside it is defensive only — deliberately NOT
    asserted, because a test that pins a non-load-bearing line fails on a harmless cleanup while
    telling you nothing about behaviour."""
    assert 'sys.modules["common.market_calendar"] = _calendar_mod' in CONFTEST


def test_the_mocked_submodules_are_registered_the_same_way():
    """common.config / redis_client are STUBBED (they reach outside the process) but still need
    both halves, or `from common.config import get_settings` fails through a mocked parent."""
    for name in ("config", "redis_client"):
        assert f"common.{name}" in sys.modules
        assert hasattr(sys.modules["common"], name)


# ── The four files that were broken now collect ─────────────────────────────────────────

@pytest.mark.parametrize("module", [
    "test_hard_rejects", "test_score_replay", "test_entry_gate_params", "test_entry_weights",
])
def test_the_previously_uncollectable_files_import(module):
    """THE REGRESSION GUARD. Each of these failed at COLLECTION — not as a test failure — so a
    green targeted run could hide 188 tests not running at all."""
    import importlib
    mod = importlib.import_module(module)
    assert mod is not None


def test_the_calendar_is_loaded_from_shared_not_copied():
    """A local copy would drift from the one source of truth — this codebase found FOUR copies
    of the NYSE holiday table before consolidating them."""
    assert '"shared" / "common" / "market_calendar.py"' in CONFTEST
    assert "date(20" not in CONFTEST, "conftest must not carry holiday literals of its own"


def test_the_conftest_tolerates_a_missing_shared_directory():
    """It must not hard-crash collection in an environment where shared/ is absent (e.g. a
    container that only ships the service) — that would be a worse failure than the one fixed."""
    assert "if _calendar_path.exists():" in CONFTEST


# ── Runtime proof, not source-text proof ────────────────────────────────────────────────
#
# TWO GAPS MY OWN SABOTAGE TESTING EXPOSED, both because the assertions above check the
# conftest's TEXT rather than its EFFECT:
#
#   * Deleting the mocked-submodule loop was caught by NOTHING at all — now covered by
#     test_every_common_submodule_the_core_imports_is_registered.
#   * Deleting the `setattr(...)` half failed only a source-text assertion. Investigating THAT
#     showed my explanation was simply wrong: setattr is not required for the from-import form,
#     so the honest fix was to stop asserting it rather than to pin it harder.
#
# A fix pinned only by a grep for its own source line is not pinned. These exercise the import
# forms that each half enables.

def test_the_from_import_form_works():
    """A CORRECTION TO MY OWN CLAIM. I first wrote that setattr was REQUIRED for this form and
    that sys.modules alone was insufficient. That is false — verified directly: with only the
    sys.modules entry, this from-import succeeds and returns the real frozenset, because CPython
    resolves `from X.Y import Z` against sys.modules["X.Y"] first.

    So this test pins the BEHAVIOUR (the import works) without asserting a mechanism that isn't
    load-bearing. The sys.modules registration IS load-bearing and is covered by
    test_every_common_submodule_the_core_imports_is_registered.
    """
    exec("from common.market_calendar import NYSE_HOLIDAYS", {})


def test_the_from_import_form_works_for_each_mocked_submodule():
    """WHAT THE SUBMODULE LOOP BUYS. hard_rejects.py and aggregator.py both do
    `from common.config import get_settings`; without the loop that fails and the decision-engine
    core is unimportable."""
    for stmt in (
        "from common.config import get_settings",
        "from common.redis_client import get_redis",
    ):
        exec(stmt, {})


def test_the_decision_engine_core_is_importable():
    """The end state that matters: the modules under test can actually be imported."""
    from src.api.core import aggregator, hard_rejects  # noqa: F401


def test_every_common_submodule_the_core_imports_is_registered():
    """PARITY. Derived from the real source rather than a hand-list, so a NEW `from common.X
    import ...` in the core cannot silently break collection again."""
    import re
    core = pathlib.Path(__file__).resolve().parents[1] / "src/api/core"
    needed = set()
    for py in core.glob("*.py"):
        needed |= set(re.findall(r"from common\.(\w+) import", py.read_text()))
    assert needed, "regex matched nothing — it would make this test vacuous"
    missing = [n for n in sorted(needed) if f"common.{n}" not in sys.modules]
    assert missing == [], (
        f"the core imports common.{{{','.join(missing)}}} but conftest does not register "
        f"them — those files will fail at COLLECTION, not as a test failure"
    )
