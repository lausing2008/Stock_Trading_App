"""SR-02 and SR-05 — two gates that silently did not apply.

SR-02. `check_hard_rejects` normalised naive `datetime` OBJECTS to UTC but not naive ISO
STRINGS. `datetime.fromisoformat("2026-09-20T15:00:00")` returns a naive value; subtracting it
from an aware `now` raises TypeError; the blanket `except Exception: pass` then skipped the
staleness gate entirely. The IDENTICAL instant written as `2026-09-20T15:00:00+00:00` was
rejected as 264 hours old. And `row.ts.isoformat()` over this platform's naive UTC columns
produces exactly the form that bypassed it — so the gate was off for the common case.

The decision route separately parsed the same value CORRECTLY for display while passing the
raw string to the gate, which is the worst shape this can take: the age shown to a reader was
right, and the age enforced was never computed.

SR-05. The same function's market-closed guard checked NYSE holidays for non-HK markets and
only weekday/session times for HK, so an otherwise eligible HK entry passed at 11:00 HKT on
2026-10-01 — an HKEX securities holiday. The shared calendar already carried HK_HOLIDAYS and
two other consumers already used it.

These tests execute the real shared module and the real extracted gate block; the behaviour is
never described in the test and then asserted against the description.
"""
import ast
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "shared"))

DE_SRC = (_ROOT / "services/decision-engine/src/api/core/hard_rejects.py").read_text()
DE_ROUTES = (_ROOT / "services/decision-engine/src/api/routes.py").read_text()

from common.market_calendar import HK_HOLIDAYS, is_trading_day  # noqa: E402
from common.signal_time import parse_signal_instant  # noqa: E402

_NOW = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


# ── SR-02: every serialisation of one instant must agree ────────────────────────────────

@pytest.mark.parametrize("raw", [
    "2026-09-20T15:00:00",                              # naive — the form that bypassed
    "2026-09-20T15:00:00+00:00",
    "2026-09-20T15:00:00Z",
    datetime(2026, 9, 20, 15, 0),                       # naive datetime
    datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc),
])
def test_one_instant_has_one_age_however_it_was_written(raw):
    """THE WITNESS. These five spellings are the same moment; the old code made two of them
    264 hours old and silently skipped the rest."""
    inst = parse_signal_instant(raw, now=_NOW)
    assert inst.state == "known"
    assert round(inst.age_hours(_NOW), 1) == 264.0


def test_an_offset_timestamp_is_converted_not_truncated():
    """A non-UTC offset must be respected, not read as if the digits were UTC."""
    inst = parse_signal_instant("2026-09-20T11:00:00-04:00", now=_NOW)
    assert round(inst.age_hours(_NOW), 1) == 264.0


def test_absent_and_invalid_are_different_facts():
    assert parse_signal_instant(None, now=_NOW).state == "absent"
    assert parse_signal_instant("", now=_NOW).state == "absent"
    assert parse_signal_instant("garbage", now=_NOW).state == "invalid"
    assert parse_signal_instant(object(), now=_NOW).state == "invalid"
    assert not parse_signal_instant(None, now=_NOW).evidence_supplied
    assert parse_signal_instant("garbage", now=_NOW).evidence_supplied


def test_a_future_timestamp_is_not_a_fresh_signal():
    """Its age is negative, so it passes any maximum-age test trivially."""
    assert parse_signal_instant(_NOW + timedelta(hours=5), now=_NOW).state == "future"


def test_small_clock_skew_is_tolerated_rather_than_flagged():
    assert parse_signal_instant(_NOW + timedelta(seconds=60), now=_NOW).state == "known"


def test_the_naive_utc_assumption_is_recorded_not_hidden():
    assert parse_signal_instant("2026-09-20T15:00:00", now=_NOW).assumed_utc is True
    assert parse_signal_instant("2026-09-20T15:00:00Z", now=_NOW).assumed_utc is False


def test_parsing_never_raises():
    for raw in [[], {}, 3.14, b"\xff", "2026-13-45T99:99:99"]:
        parse_signal_instant(raw, now=_NOW)


# ── SR-02: the gate and the route read the same value ───────────────────────────────────

def _code_only(block: str) -> str:
    """Strip comment lines before asserting on a block.

    These blocks DESCRIBE the defect they replaced, quoting the old code verbatim — so a test
    asserting "the old form is gone" matches the explanation instead of the code. That is the
    third time this exact self-match has bitten in this session; stripping comments is also a
    stronger assertion, because it checks what runs.
    """
    out = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  # ")[0] if "  # " in line else line)
    return "\n".join(out)


def _age_gate_block() -> str:
    i = DE_SRC.index("SR-02 (2026-10-02)")
    return _code_only(DE_SRC[i:DE_SRC.index("T232-DL-DUALSCORER-DEBT: K-Score floor", i)])


def test_the_gate_no_longer_swallows_a_parse_failure():
    block = _age_gate_block()
    assert "except Exception" not in block, \
        "a swallowed parse error is what turned this gate off"
    assert "parse_signal_instant" in block


def test_unreadable_freshness_evidence_blocks_rather_than_approves():
    block = _age_gate_block()
    for state in ('"invalid"', '"future"'):
        i = block.index(state)
        arm = block[i:block.index("\n", block.index("return", i))]
        assert "return" in arm, f"state {state} must produce a rejection"


def test_an_absent_timestamp_still_skips_the_gate():
    """The parameter is optional for callers that predate it; `absent` must stay fail-open, and
    it is a different fact from `invalid`."""
    block = _age_gate_block()
    assert "evidence_supplied" in block, \
        "the gate must distinguish 'not supplied' from 'supplied and unreadable'"


def test_the_route_passes_the_parsed_instant_to_the_gate():
    """The display path and the enforcement path must not re-interpret the value separately."""
    i = DE_ROUTES.index("SR-02 (2026-10-02)")
    block = DE_ROUTES[i:DE_ROUTES.index("# 5. Resolve research fields", i)]
    assert "parse_signal_instant" in block
    assert "sig_ts = sig_instant.at if sig_instant.usable else sig_ts_raw" in block
    tree = ast.parse(DE_ROUTES)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "check_hard_rejects"]
    assert calls, "the gate call was not found"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw["sig_ts"], ast.Name) and kw["sig_ts"].id == "sig_ts"


# ── SR-05: the HK holiday ───────────────────────────────────────────────────────────────

def test_the_hk_witness_date_is_a_holiday_the_calendar_knows():
    from datetime import date
    assert date(2026, 10, 1) in HK_HOLIDAYS
    assert is_trading_day("HK", datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)) is False


def test_hk_and_us_holidays_are_independent():
    """2026-10-01 closes HKEX and not the NYSE; a shared gate must not conflate them."""
    oct1 = datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc)
    assert is_trading_day("HK", oct1) is False
    assert is_trading_day("US", oct1) is True


def test_the_gate_dispatches_on_the_actual_venue():
    i = DE_SRC.index("SR-05 (2026-10-02)")
    block = _code_only(DE_SRC[i:DE_SRC.index("_mins = _local.hour", i)])
    assert "_is_trading_day(market" in block, "the venue must choose the calendar"
    assert 'market.upper() != "HK"' not in block, \
        "the holiday check must no longer exclude HK"


def test_no_second_holiday_table_survives_in_the_consumer():
    """The calendars drifted once already (AUD-HOLIDAY-2027GAP). One source of truth."""
    assert "_NYSE_HOLIDAYS" not in DE_SRC
    assert "from common.market_calendar import" in DE_SRC
