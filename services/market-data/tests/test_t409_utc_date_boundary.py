"""AUD-T409-UTCDATEBOUNDARY — "today" must mean the US/Eastern trading date, not a truncation
of the current UTC instant.

FOUND: the user asked why the options-income staleness banner said the chain was "2 days old"
when the daily capture job had run normally and the archive held Thursday's settled close.
Checked live at 2026-09-18 20:40 ET (00:40 UTC, already 2026-09-19 in UTC): `datetime.now(
timezone.utc).date()` returns Sept 19, one calendar day ahead of the true US trading day,
because UTC crosses midnight at 8pm EDT / 7pm EST while the US trading day these dates are
meant to describe runs on New York wall-clock time. `(Sept 19 - Sept 17).days == 2` — the
banner was arithmetically correct on a wrong input.

THE MORE SERIOUS CONSEQUENCE, dormant until DST ends (~Nov 1): the scheduled options-income
step fires at 19:00 America/New_York — 00:00 UTC the FOLLOWING day during EST (winter). On a
true Friday, the naive UTC date at that exact moment is Saturday, so `today.weekday() >= 5`
would have silently skipped the entire Friday settlement/entry run as "the weekend", every
single EST-season Friday, with no error and no way to tell it apart from a genuine non-trading
day in the logs.

Six call sites across five files carried this exact bug, all reached today while building the
options-income and options-chain surface. Fixed with one canonical `_today_et()` in
options_income_engine.py; the other files reuse or inline the same conversion, matching how
`_is_us_trading_day()` already does it elsewhere in scheduler.py — this bug was never a missing
technique, only a set of call sites that never adopted it.
"""
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.services.options_income_engine import _today_et


class _FrozenDatetime(datetime):
    """A `datetime` subclass whose `now()` always returns a fixed instant — patched in as the
    module's own `datetime` so `_today_et()` exercises its REAL code path, not a re-implementation
    of it under test."""
    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


def _frozen_at(utc_iso: str):
    frozen = _FrozenDatetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc)
    _FrozenDatetime._frozen = frozen
    return patch("src.services.options_income_engine.datetime", _FrozenDatetime)


# ── The exact reported case ───────────────────────────────────────────────────────────────

def test_the_exact_reported_moment_reads_the_correct_et_date():
    """2026-09-18 20:40 ET == 2026-09-19 00:40 UTC. The naive version reads Sept 19; the
    correct answer — what a person in New York would call "today" — is Sept 18."""
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_et().isoformat() == "2026-09-18"


def test_naive_utc_truncation_would_have_gotten_this_wrong():
    """Not a strawman: this IS what every fixed call site used to compute."""
    with _frozen_at("2026-09-19T00:40:00"):
        naive = datetime.now(timezone.utc).date()
    assert naive.isoformat() == "2026-09-19", "confirms the bug this test module exists to prevent"


# ── The dormant EST-season Friday-skip consequence ──────────────────────────────────────────

def test_a_friday_evening_est_run_is_not_seen_as_saturday():
    """19:00 EST (winter) is 00:00 UTC the following day. A true Friday's scheduled run must
    still resolve to Friday, not the Saturday the naive version would have produced — which
    would have made `today.weekday() >= 5` silently skip the ENTIRE run as "the weekend"."""
    # 2026-01-16 is a Friday. 19:00 EST on that Friday == 00:00 UTC on 2026-01-17 (Saturday).
    with _frozen_at("2026-01-17T00:00:00"):
        today = _today_et()
    assert today.isoformat() == "2026-01-16"
    assert today.weekday() == 4, "Friday — must NOT read as Saturday (weekday 5)"


def test_naive_utc_would_have_misread_that_friday_as_saturday():
    with _frozen_at("2026-01-17T00:00:00"):
        naive = datetime.now(timezone.utc).date()
    assert naive.weekday() == 5, "confirms the dormant EST-season Friday-skip this replaces"


# ── EDT (summer) sanity — the fix must not overcorrect ─────────────────────────────────────

def test_daytime_edt_matches_the_naive_version_exactly():
    """During market hours the two must agree — this is purely an evening/timezone-boundary
    correction, not a general offset."""
    with _frozen_at("2026-09-18T18:00:00"):  # 14:00 EDT
        assert _today_et().isoformat() == "2026-09-18"


def test_far_from_any_boundary_both_versions_agree():
    with _frozen_at("2026-06-15T12:00:00"):
        et = _today_et()
        naive = _FrozenDatetime.now(timezone.utc).date()
    assert et == naive


# ── Every fixed call site actually uses the shared helper, not a re-inlined mistake ────────

import pathlib

_FILES = {
    "services/market-data/src/services/options_income_engine.py": ["_today_et()"] * 4,
    "services/market-data/src/api/options_income.py": ['_today_et()'],
    "services/market-data/src/services/options_strategies.py":
        ['ZoneInfo("America/New_York")'],
    "services/market-data/src/services/uw_option_chain.py":
        ['ZoneInfo("America/New_York")'],
    "services/market-data/src/services/options_game_plan_snapshot.py":
        ['ZoneInfo("America/New_York")', 'ZoneInfo("America/New_York")'],
}
_REPO = pathlib.Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("relpath,needles", _FILES.items())
def test_fixed_file_contains_no_live_naive_utc_date_call(relpath, needles):
    """Distinguishes real code from a docstring/comment MENTIONING the bug (several of these
    files now explain the fix in prose, which legitimately contains the banned substring)."""
    src = (_REPO / relpath).read_text()
    in_docstring = False
    for line in src.split("\n"):
        stripped = line.strip()
        # Track triple-quoted blocks: an ODD count of the marker on a line TOGGLES whether we
        # are inside one, rather than only skipping a line that carries the marker itself —
        # the original version missed docstring lines that don't happen to open/close on the
        # same line as the banned substring.
        toggles = stripped.count('"""') % 2 == 1
        was_in_docstring = in_docstring
        if toggles:
            in_docstring = not in_docstring
        if was_in_docstring or in_docstring or stripped.startswith("#"):
            continue
        assert "datetime.now(timezone.utc).date()" not in line, (
            f"{relpath}: live naive-UTC date call survived at: {line.strip()!r}"
        )
        assert "date.today()" not in line, (
            f"{relpath}: live naive local-date call survived at: {line.strip()!r}"
        )


@pytest.mark.parametrize("relpath,needles", _FILES.items())
def test_fixed_file_actually_uses_the_et_aware_replacement(relpath, needles):
    src = (_REPO / relpath).read_text()
    for needle in needles:
        assert needle in src, f"{relpath}: expected replacement {needle!r} not found"
