"""AUD-E12-SIGNALEXPLANATIONS — send_signal_alert_email()'s direction/regime vocabulary and
boolean rendering had several actionable inconsistencies:

1. `None -> BUY` (a stock's first-ever signal) fell through to the SAME ("neutral", "unchanged")
   fallback every genuinely unmapped transition also hits — a brand-new BUY read as "unchanged."
2. The regime map recognized only bull/bear; the real, valid neutral/choppy/risk_off states
   (this codebase's own regime classifier produces them) rendered the SAME "Unknown" text a
   genuinely missing regime would — indistinguishable from "no regime data."
3. The regime note hardcoded "S&P" as the benchmark even for HK symbols, where the regime
   classifier actually uses HSI.
4. `_yn()` rendered a missing (None) boolean measurement as "No" — a stronger, false claim than
   "this wasn't measured."
5. The game-plan entry table listed three independently-labelled "50%" rows with no indication
   they are alternative tranches against ONE position budget, not three additive allocations.

email_service.py can't be imported directly in this test environment — source-scanned, matching
this repo's established convention for this file.
"""
import pathlib

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "email_service.py"
).read_text()


def _send_signal_alert_email_body() -> str:
    start = _SOURCE.index("def send_signal_alert_email(")
    end = _SOURCE.index("\ndef ", start + 10)
    return _SOURCE[start:end]


_BODY = _send_signal_alert_email_body()


def test_none_to_buy_is_mapped_as_an_initial_signal_not_unchanged():
    assert '(None, "BUY"):    ("bullish",   "initial BUY signal")' in _BODY


def test_none_to_sell_is_mapped_as_an_initial_signal():
    assert '(None, "SELL"):   ("bearish",   "initial SELL signal")' in _BODY


def test_regime_map_covers_neutral_choppy_and_risk_off_distinctly_from_unknown():
    for state in ("neutral", "choppy", "risk_off"):
        assert f'"{state}":' in _BODY, f"regime state {state!r} must have its own mapped entry"
    # The genuinely-unrecognized fallback must read differently from any of the real states.
    assert "Unknown — no regime data recorded for this signal" in _BODY


def test_regime_benchmark_is_market_aware_not_hardcoded_sp():
    assert '_benchmark = "HSI" if symbol.upper().endswith(".HK") else "S&P"' in _BODY
    assert 'f"Bull ({_benchmark} above 200MA)' in _BODY
    assert 'f"Bear ({_benchmark} below 200MA)' in _BODY


def test_yn_helper_renders_none_as_unknown_not_a_false_no():
    section = _BODY[_BODY.index("def _yn(v) -> str:"):_BODY.index("def _fmt(v")]
    assert "return \"Unknown\"" in section
    assert "if v is None:" in section


def test_entry_table_html_explains_the_tranches_share_one_budget():
    assert "alternative entry tranches against ONE total position budget" in _BODY


def test_entry_table_text_explains_the_tranches_share_one_budget():
    assert "Alternative entry tranches against ONE total position budget" in _BODY
