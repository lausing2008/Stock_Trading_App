"""Tests for T249-MARKETMOVER-P1's two earnings alert body builders.

_earnings_reminder_body() enriches the pre-existing T230-ALERTING-EARNINGS-PROXIMITY day-of
reminder with the estimate/beat-rate/surprise data /stocks/{symbol}/fundamentals already
computes (forward_eps, eps_beat_rate, eps_avg_surprise_pct) — previously a generic "review
your position" line even though that data was one field away in the same fundamentals_cache
dict the reminder already reads.

_earnings_reaction_body() is the genuinely new half: a post-release fast reaction once
eps_actual lands, using event-intelligence's already-computed surprise_pct/
earnings_strength_score — no LLM needed, both are already numeric and interpretable.

scheduler.py can't be imported directly in this test environment (see
test_price_alert_price_check.py's docstring for the same constraint) — both functions are
pure/dependency-free, so they're loaded directly from source via exec(), same technique.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_source = _scheduler_path.read_text()


def _load_function(name: str):
    start = _source.index(f"def {name}")
    end = _source.index("\n\n\n", start)
    namespace: dict = {}
    exec(_source[start:end], namespace)  # noqa: S102 — isolated eval of one pure function's source
    return namespace[name]


_earnings_reminder_body = _load_function("_earnings_reminder_body")
_earnings_reaction_body = _load_function("_earnings_reaction_body")


# ── _earnings_reminder_body() ──────────────────────────────────────────────────

def test_full_fundamentals_produces_the_tracker_example_format():
    """The exact example format from the T249-MARKETMOVER-P1 tracker entry: 'NVDA reports
    after close; street at $0.85; beat 7 of last 8, avg surprise +9%'."""
    body = _earnings_reminder_body("NVDA", 1, {
        "forward_eps": 0.85, "eps_beat_rate": 0.875, "eps_avg_surprise_pct": 9.2,
    })
    assert "NVDA reports earnings in 1 day(s)." in body
    assert "Street estimate: $0.85." in body
    assert "Beat 7 of last 8 quarters, avg surprise +9.2%." in body


def test_missing_fundamentals_falls_back_to_the_generic_reminder():
    """A symbol with no earnings_history yet (e.g. newly listed) must not crash — falls
    back to the pre-enrichment generic line."""
    body = _earnings_reminder_body("NEWCO", 3, {})
    assert body == "NEWCO reports earnings in 3 day(s). Review your position and manage risk before the print."


def test_partial_fundamentals_only_includes_available_fields():
    """forward_eps present but no beat-rate history yet — only the estimate line is added,
    not a beat-rate claim the data doesn't support."""
    body = _earnings_reminder_body("XYZ", 2, {"forward_eps": 1.20})
    assert "Street estimate: $1.20." in body
    assert "Beat" not in body


def test_negative_avg_surprise_is_formatted_with_a_minus_sign_not_double_negative():
    body = _earnings_reminder_body("BADCO", 1, {
        "forward_eps": 0.50, "eps_beat_rate": 0.25, "eps_avg_surprise_pct": -12.5,
    })
    assert "avg surprise -12.5%" in body
    assert "--" not in body


# ── _earnings_reaction_body() ──────────────────────────────────────────────────

def test_beat_produces_beat_verb_and_positive_sign():
    body = _earnings_reaction_body("AAPL", 1.50, 1.40, 7.14, 82.0)
    assert "AAPL beat EPS estimates" in body
    assert "actual $1.50 vs est $1.40" in body
    assert "(+7.1%)" in body
    assert "Earnings strength score: 82/100." in body


def test_miss_produces_missed_verb_and_negative_sign():
    body = _earnings_reaction_body("MISS", 0.80, 1.00, -20.0, 30.0)
    assert "MISS missed EPS estimates" in body
    assert "(-20.0%)" in body


def test_exact_meet_produces_met_verb_not_beat_or_missed():
    """surprise_pct of exactly 0 must read as 'met', not fall into the beat or missed
    branch — a falsy-zero check bug here would misclassify an exact meet as a miss."""
    body = _earnings_reaction_body("FLAT", 1.00, 1.00, 0.0, 50.0)
    assert "FLAT met EPS estimates" in body


def test_missing_estimate_and_strength_score_are_omitted_not_shown_as_none():
    body = _earnings_reaction_body("NOEST", 0.90, None, None, None)
    assert "vs est" not in body
    assert "strength score" not in body.lower() or "None" not in body
    assert "None" not in body


# ── wiring: check_signal_alerts() must actually call the enriched builder ─────

def test_reminder_wiring_calls_the_enriched_builder_not_a_generic_inline_string():
    """Source-text check: check_signal_alerts()'s day-of reminder must call
    _earnings_reminder_body(), not a hand-inlined generic f-string — otherwise the
    enrichment above exists but is never actually used by the real alert path."""
    assert "body_text = _earnings_reminder_body(sym, dte_int, fund)" in _source


def test_earnings_reaction_check_is_registered_as_a_scheduled_job():
    """Source-text check: check_earnings_reactions must actually be wired into
    start_scheduler()'s add_job calls, not just exist as an unused function."""
    assert 'check_earnings_reactions,' in _source
    assert 'id="earnings_reaction_check"' in _source
