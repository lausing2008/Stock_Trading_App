"""AUD-UW01-ALERTDATEBOUNDARY — the exact T409 bug class, found again in scheduler.py's
flow/dark-pool/squeeze/prebreakout alert-outcome recorders and evaluators.

`date.today()` in a UTC container reads one calendar day ahead of the true US trading date for
~4-5 hours every evening (UTC crosses midnight at 8pm EDT / 7pm EST). Confirmed live: a
DarkPoolAlertOutcome row dated 2026-09-19 while New York was still on September 18. In the
evaluator functions specifically, `today` gates `if target > today: still pending` — a censoring
check identical in shape to signal-engine's evaluate_signal_outcomes(), which T409 already fixed.
A one-day-ahead `today` makes that check resolve a window ONE DAY TOO EARLY.

scheduler.py can't be imported directly in this test environment (only one existing test file
in this directory imports it, and that file stubs a large fake environment first) — `_today_et()`
is a small, standalone pure function (no DB/session access), so its source is extracted and
exec'd directly, matching test_t409_utc_date_boundary.py's own technique. The 6 call sites are
then verified by scanning the real source text, the same technique that test's own
`test_fixed_file_contains_no_live_naive_utc_date_call` uses.
"""
import pathlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
)
_SOURCE = _PATH.read_text()


def _extract_today_et():
    start = _SOURCE.index('def _today_et() -> date:')
    end = _SOURCE.index("\n\n\ndef _is_us_trading_day", start)
    func_source = _SOURCE[start:end]
    namespace = {}
    exec(  # noqa: S102 — isolated eval of one pure function's real source
        "from datetime import date, datetime, timezone\nfrom zoneinfo import ZoneInfo\n" + func_source,
        namespace,
    )
    return namespace["_today_et"]


_today_et = _extract_today_et()


class _FrozenDatetime(datetime):
    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


def _frozen_at(utc_iso: str):
    frozen = _FrozenDatetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc)
    _FrozenDatetime._frozen = frozen
    # _today_et was exec'd into its own namespace dict rather than a real module, so it has no
    # __module__ to patch.object() against — but that namespace dict IS _today_et.__globals__,
    # since exec() binds a function's globals to the dict it ran in. Patch that dict directly.
    return patch.dict(_today_et.__globals__, {"datetime": _FrozenDatetime})


def test_the_exact_reported_moment_reads_the_correct_et_date():
    """2026-09-18 20:40 ET == 2026-09-19 00:40 UTC — the naive version reads Sept 19; the
    correct answer is Sept 18, matching the exact reported DarkPoolAlertOutcome incident."""
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_et().isoformat() == "2026-09-18"


def test_naive_utc_truncation_would_have_gotten_this_wrong():
    with _frozen_at("2026-09-19T00:40:00"):
        naive = datetime.now(timezone.utc).date()
    assert naive.isoformat() == "2026-09-19", "confirms the bug this test module exists to prevent"


def test_daytime_edt_matches_the_naive_version_exactly():
    with _frozen_at("2026-09-18T18:00:00"):  # 14:00 EDT
        assert _today_et().isoformat() == "2026-09-18"


# ── Every one of the 6 real call sites actually uses the shared helper ─────────────────────

_CALL_SITES = [
    "_record_options_flow_alert_outcome",
    "_record_dark_pool_alert_outcome",
    "evaluate_squeeze_alert_outcomes",
    "evaluate_prebreakout_alert_outcomes",
    "evaluate_options_flow_alert_outcomes",
    "evaluate_dark_pool_alert_outcomes",
]


def _function_body(func_name: str) -> str:
    start = _SOURCE.index(f"def {func_name}(")
    next_def = _SOURCE.index("\ndef ", start + 1)
    return _SOURCE[start:next_def]


@pytest.mark.parametrize("func_name", _CALL_SITES)
def test_call_site_uses_today_et_not_a_live_naive_date_today(func_name):
    body = _function_body(func_name)
    assert "_today_et()" in body, f"{func_name}: expected to call the shared _today_et() helper"
    assert "date.today()" not in body, (
        f"{func_name}: a live naive date.today() call survived — this is the exact bug "
        f"AUD-UW01-ALERTDATEBOUNDARY exists to prevent"
    )
