"""Tests for T286-EARNINGS-PLAYBOOK's _build_earnings_playbook() and its wiring into
check_earnings_impact_alerts() (both in scheduler.py).

_build_earnings_playbook() is a pure function (no DB/network dependency) — extracted via
exec() from the real source (matching this repo's established technique for pure functions in
scheduler.py, which can't be imported directly in this test environment since its import chain
pulls in apscheduler). The wiring into check_earnings_impact_alerts() is covered by source-text
regression checks, matching test_earnings_impact_delivery.py's own established pattern.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _func_body(name: str) -> str:
    start = _scheduler_source.index(f"def {name}(")
    end = _scheduler_source.index("\n\ndef ", start)
    return _scheduler_source[start:end]


def _extract_build_earnings_playbook():
    start = _scheduler_source.index("_PLAYBOOK_ACTION_LABEL = {")
    end = _scheduler_source.index("\n\ndef _earnings_reaction_body(")
    func_source = _scheduler_source[start:end]
    namespace = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_build_earnings_playbook"]


_build_earnings_playbook = _extract_build_earnings_playbook()


# ── _build_earnings_playbook() — behavioral tests ────────────────────────────────────────────

def test_no_position_is_always_watch_regardless_of_strength():
    result = _build_earnings_playbook(strength_score=95.0, surprise_pct=30.0, has_open_position=False, position_unrealized_pct=None)
    assert result["action"] == "WATCH"


def test_strong_beat_with_position_already_green_recommends_add():
    result = _build_earnings_playbook(strength_score=85.0, surprise_pct=25.0, has_open_position=True, position_unrealized_pct=5.0)
    assert result["action"] == "ADD"


def test_strong_beat_with_position_still_red_recommends_hold_not_add():
    """A strong beat while the position hasn't confirmed in price yet must NOT chase — this
    is the exact 'don't add into an unconfirmed move' property this function was designed for."""
    result = _build_earnings_playbook(strength_score=85.0, surprise_pct=25.0, has_open_position=True, position_unrealized_pct=-3.0)
    assert result["action"] == "HOLD"


def test_inline_print_recommends_hold():
    result = _build_earnings_playbook(strength_score=55.0, surprise_pct=1.0, has_open_position=True, position_unrealized_pct=2.0)
    assert result["action"] == "HOLD"


def test_weak_miss_recommends_reduce():
    result = _build_earnings_playbook(strength_score=30.0, surprise_pct=-7.0, has_open_position=True, position_unrealized_pct=-4.0)
    assert result["action"] == "REDUCE"


def test_severe_miss_recommends_exit():
    result = _build_earnings_playbook(strength_score=5.0, surprise_pct=-25.0, has_open_position=True, position_unrealized_pct=-15.0)
    assert result["action"] == "EXIT"


def test_missing_strength_score_defaults_to_neutral_50_not_a_crash():
    result = _build_earnings_playbook(strength_score=None, surprise_pct=None, has_open_position=True, position_unrealized_pct=1.0)
    assert result["action"] == "HOLD"  # 50.0 falls in the [40, 70) in-line band


def test_boundary_exactly_70_counts_as_strong_beat_tier():
    result = _build_earnings_playbook(strength_score=70.0, surprise_pct=15.0, has_open_position=True, position_unrealized_pct=1.0)
    assert result["action"] == "ADD"


def test_boundary_exactly_40_counts_as_inline_tier_not_reduce():
    result = _build_earnings_playbook(strength_score=40.0, surprise_pct=-4.0, has_open_position=True, position_unrealized_pct=0.0)
    assert result["action"] == "HOLD"


def test_boundary_exactly_20_counts_as_reduce_tier_not_exit():
    result = _build_earnings_playbook(strength_score=20.0, surprise_pct=-9.0, has_open_position=True, position_unrealized_pct=-2.0)
    assert result["action"] == "REDUCE"


def test_every_action_has_a_non_empty_rationale():
    for score in (95.0, 55.0, 30.0, 5.0):
        result = _build_earnings_playbook(strength_score=score, surprise_pct=0.0, has_open_position=True, position_unrealized_pct=1.0)
        assert result["rationale"]
        assert result["action_label"]


def test_does_not_fabricate_an_expected_move_pct():
    """This function must never claim an options-implied/historical expected-move number this
    app has no real data source for — the return dict must only contain action/action_label/
    rationale, nothing else."""
    result = _build_earnings_playbook(strength_score=50.0, surprise_pct=0.0, has_open_position=True, position_unrealized_pct=0.0)
    assert set(result.keys()) == {"action", "action_label", "rationale"}


# ── check_earnings_impact_alerts() wiring — source-text regression checks ───────────────────

def test_impact_alerts_builds_a_per_recipient_playbook_not_a_shared_one():
    """The playbook must be built per (event, symbol) using each recipient's OWN open
    position — never a single shared body_text reused across every recipient the way the
    plain impact_text portion is."""
    body = _func_body("check_earnings_impact_alerts")
    assert "_build_earnings_playbook(" in body
    assert "open_trade = _open_by_symbol.get(sym)" in body


def test_impact_alerts_position_lookup_is_one_bulk_query_not_per_recipient():
    """The open-position lookup must be ONE bulk query outside the per-event/per-recipient
    loops, not re-queried per recipient — matching this repo's established discipline against
    N+1 query patterns."""
    body = _func_body("check_earnings_impact_alerts")
    bulk_query_idx = body.index("_open_by_symbol: dict[str, PaperTrade] = {")
    pending_loop_idx = body.index("for ev, sym in pending:")
    assert bulk_query_idx < pending_loop_idx


def test_impact_alerts_position_pct_is_derived_from_current_vs_entry_price():
    body = _func_body("check_earnings_impact_alerts")
    assert "float(open_trade.current_price) / float(open_trade.entry_price) - 1) * 100" in body


def test_impact_alerts_playbook_html_is_appended_not_replacing_the_llm_impact_text():
    """The LLM-generated impact_text must still be the FIRST thing in the email body — the
    mechanical playbook is an ADDITION, never a replacement of the existing LLM section."""
    body = _func_body("check_earnings_impact_alerts")
    assert 'body_html = f"<p>{ev.impact_text}</p>{playbook_html}"' in body
