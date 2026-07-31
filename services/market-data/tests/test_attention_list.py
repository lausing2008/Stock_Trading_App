"""Tests for T257-OVERNIGHT-FLOW-BRIEF Phase 3 — _build_attention_list()'s scoring logic and
its send_premarket_brief()/send_premarket_brief_email() wiring.

_build_attention_list() is a pure function (no DB/network dependency), but scheduler.py's
import chain pulls in apscheduler, so it can't be imported directly in this test environment
(matching every other scheduler.py test file's documented constraint) — extracted via exec()
from the real source instead.
"""
import pathlib

from unittest.mock import patch

from src.services.email_service import send_premarket_brief_email

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _premarket_brief_body() -> str:
    start = _SCHEDULER_SOURCE.index("def send_premarket_brief(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def _extract_build_attention_list():
    start = _SCHEDULER_SOURCE.index("_ATTENTION_GAP_THRESHOLD_PCT")
    end = _SCHEDULER_SOURCE.index("\ndef send_premarket_brief(")
    func_source = _SCHEDULER_SOURCE[start:end]
    namespace: dict = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_build_attention_list"]


_build_attention_list = _extract_build_attention_list()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


# ── _build_attention_list() — pure scoring logic ────────────────────────────────────────────

def test_symbol_with_only_one_signal_does_not_qualify():
    result = _build_attention_list(
        symbols={"AAPL"},
        earnings_by_symbol={},
        premarket_movers=[{"symbol": "AAPL", "change_pct": 3.0}],
        recent_options_flow=[],
        macro_has_high_impact=False,
    )
    assert result == []


def test_symbol_with_two_signals_qualifies_with_both_reasons():
    result = _build_attention_list(
        symbols={"AAPL"},
        earnings_by_symbol={"AAPL": object()},
        premarket_movers=[{"symbol": "AAPL", "change_pct": 3.0}],
        recent_options_flow=[],
        macro_has_high_impact=False,
    )
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"
    assert len(result[0]["reasons"]) == 2
    assert any("Premarket gap" in r for r in result[0]["reasons"])
    assert any("earnings" in r.lower() for r in result[0]["reasons"])


def test_premarket_gap_below_threshold_does_not_count_as_a_reason():
    result = _build_attention_list(
        symbols={"AAPL"},
        earnings_by_symbol={"AAPL": object()},
        premarket_movers=[{"symbol": "AAPL", "change_pct": 0.5}],  # below 2.0% threshold
        recent_options_flow=[],
        macro_has_high_impact=False,
    )
    # only 1 real reason (earnings) since the gap is too small to count
    assert result == []


def test_negative_premarket_gap_beyond_threshold_counts_via_abs_value():
    result = _build_attention_list(
        symbols={"AAPL"},
        earnings_by_symbol={"AAPL": object()},
        premarket_movers=[{"symbol": "AAPL", "change_pct": -4.5}],
        recent_options_flow=[],
        macro_has_high_impact=False,
    )
    assert len(result) == 1
    assert any("-4.5" in r for r in result[0]["reasons"])


def test_neutral_options_sentiment_with_no_whale_does_not_count_as_a_reason():
    result = _build_attention_list(
        symbols={"MSFT"},
        earnings_by_symbol={"MSFT": object()},
        premarket_movers=[],
        recent_options_flow=[{"symbol": "MSFT", "sentiment": "neutral", "whale_count": 0}],
        macro_has_high_impact=False,
    )
    assert result == []


def test_slightly_bearish_sentiment_with_no_whale_does_not_count_as_a_reason():
    result = _build_attention_list(
        symbols={"MSFT"},
        earnings_by_symbol={"MSFT": object()},
        premarket_movers=[],
        recent_options_flow=[{"symbol": "MSFT", "sentiment": "slightly_bearish", "whale_count": 0}],
        macro_has_high_impact=False,
    )
    assert result == []


def test_strongly_bullish_sentiment_counts_as_a_reason():
    result = _build_attention_list(
        symbols={"MSFT"},
        earnings_by_symbol={"MSFT": object()},
        premarket_movers=[],
        recent_options_flow=[{"symbol": "MSFT", "sentiment": "strongly_bullish", "whale_count": 0}],
        macro_has_high_impact=False,
    )
    assert len(result) == 1
    assert any("bullish" in r.lower() for r in result[0]["reasons"])


def test_neutral_sentiment_with_a_real_whale_still_counts_as_a_reason():
    """A whale trade is notable on its own even at a neutral cp_ratio."""
    result = _build_attention_list(
        symbols={"MSFT"},
        earnings_by_symbol={"MSFT": object()},
        premarket_movers=[],
        recent_options_flow=[{"symbol": "MSFT", "sentiment": "neutral", "whale_count": 2,
                               "top_whale_premium": 750_000.0}],
        macro_has_high_impact=False,
    )
    assert len(result) == 1
    assert any("whale" in r.lower() for r in result[0]["reasons"])


def test_market_wide_macro_flag_applies_to_every_symbol_when_combined_with_one_other_signal():
    result = _build_attention_list(
        symbols={"AAPL", "MSFT"},
        earnings_by_symbol={"AAPL": object()},
        premarket_movers=[{"symbol": "MSFT", "change_pct": 5.0}],
        recent_options_flow=[],
        macro_has_high_impact=True,
    )
    syms = {r["symbol"] for r in result}
    assert syms == {"AAPL", "MSFT"}
    for r in result:
        assert any("macro" in reason.lower() for reason in r["reasons"])


def test_macro_flag_alone_never_qualifies_a_symbol_with_no_other_signal():
    result = _build_attention_list(
        symbols={"AAPL"},
        earnings_by_symbol={},
        premarket_movers=[],
        recent_options_flow=[],
        macro_has_high_impact=True,
    )
    assert result == []


def test_three_signals_all_appear_in_reasons():
    result = _build_attention_list(
        symbols={"AAPL"},
        earnings_by_symbol={"AAPL": object()},
        premarket_movers=[{"symbol": "AAPL", "change_pct": 3.5}],
        recent_options_flow=[{"symbol": "AAPL", "sentiment": "bearish", "whale_count": 0}],
        macro_has_high_impact=True,
    )
    assert len(result) == 1
    assert len(result[0]["reasons"]) == 4


def test_result_is_sorted_alphabetically_by_symbol():
    result = _build_attention_list(
        symbols={"ZETA", "AAPL"},
        earnings_by_symbol={"ZETA": object(), "AAPL": object()},
        premarket_movers=[{"symbol": "ZETA", "change_pct": 3.0}, {"symbol": "AAPL", "change_pct": 3.0}],
        recent_options_flow=[],
        macro_has_high_impact=False,
    )
    assert [r["symbol"] for r in result] == ["AAPL", "ZETA"]


def test_symbol_not_in_any_input_never_appears():
    result = _build_attention_list(
        symbols={"UNRELATED"},
        earnings_by_symbol={"AAPL": object()},
        premarket_movers=[{"symbol": "AAPL", "change_pct": 5.0}],
        recent_options_flow=[],
        macro_has_high_impact=False,
    )
    assert result == []


def test_empty_symbols_returns_empty_list():
    result = _build_attention_list(
        symbols=set(),
        earnings_by_symbol={"AAPL": object()},
        premarket_movers=[{"symbol": "AAPL", "change_pct": 5.0}],
        recent_options_flow=[],
        macro_has_high_impact=True,
    )
    assert result == []


# ── send_premarket_brief() wiring — source-text regression checks ──────────────────────────

def test_attention_list_computed_per_recipient_inside_the_send_loop():
    body = _premarket_brief_body()
    assert "attention_list = _build_attention_list(" in body
    call_idx = body.index("attention_list = _build_attention_list(")
    loop_idx = body.index("for uid, user in recipients.items():")
    assert call_idx > loop_idx


def test_macro_has_high_impact_computed_once_before_the_send_loop_not_per_recipient():
    body = _premarket_brief_body()
    assert "macro_has_high_impact = bool(macro_today)" in body
    flag_idx = body.index("macro_has_high_impact = bool(macro_today)")
    loop_idx = body.index("for uid, user in recipients.items():")
    assert flag_idx < loop_idx


def test_attention_list_passed_into_the_email_call():
    body = _premarket_brief_body()
    assert "attention_list=attention_list" in body


def test_attention_symbols_total_included_in_the_done_log():
    body = _premarket_brief_body()
    done_log_idx = body.index('log.info("premarket_brief.done"')
    done_log_line = body[done_log_idx:body.index("\n\n", done_log_idx)]
    assert "attention_symbols_total=attention_symbols_total" in done_log_line


def test_attention_list_deliberately_not_in_the_nothing_to_report_guard():
    """The attention list is derived entirely from the other 4 already-guarded inputs
    (macro/earnings/reactions/futures/movers/options_flow) — a symbol can only ever qualify
    when at least 2 of those are non-empty, so the existing guard already covers it. Adding
    attention_list to the guard would be redundant, not a bug — this test documents that
    choice rather than accidentally reintroducing a duplicate, always-true guard clause."""
    body = _premarket_brief_body()
    guard_idx = body.index('log.info("premarket_brief.nothing_to_report"')
    guard_line_start = body.rindex("if not macro_today", 0, guard_idx)
    guard_line = body[guard_line_start:guard_idx]
    assert "attention_list" not in guard_line


# ── send_premarket_brief_email()'s new attention-list section — pure composition ────────────

def test_attention_list_section_renders_symbol_and_reasons():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            attention_list=[{"symbol": "AAPL", "reasons": ["Premarket gap +3.5%", "Reports earnings today"]}],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "AAPL" in html
    assert "Premarket gap +3.5%" in html
    assert "Reports earnings today" in html
    assert "AAPL: Premarket gap +3.5%; Reports earnings today" in text


def test_attention_list_section_has_explicit_empty_state():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[], attention_list=[],
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "No symbols currently qualify (need 2+ independent signals)." in html
    assert "No symbols currently qualify (need 2+ independent signals)." in text


def test_attention_list_param_defaults_to_none_and_is_treated_as_empty():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
        )
    assert ok is True
    assert "No symbols currently qualify" in calls[0]["html"]


def test_attention_list_with_multiple_reasons_renders_all_of_them():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_premarket_brief_email(
            to="user@example.com", date_str="d", market="US",
            macro_events=[], my_earnings=[], recent_reactions=[],
            attention_list=[{"symbol": "NVDA", "reasons": ["reason one", "reason two", "reason three"]}],
        )
    html = calls[0]["html"]
    assert "reason one" in html
    assert "reason two" in html
    assert "reason three" in html
