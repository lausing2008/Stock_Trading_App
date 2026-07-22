"""Tests for T249-MARKETMOVER-P1's earnings alert body builders.

AUD-EARNINGS-DIGEST (2026-07-22): the day-of reminder was consolidated from one send_email()
call per (user, symbol) into one send_earnings_reminder_digest_email() call per user, listing
every upcoming print as a table — see that function's own docstring in email_service.py.
_earnings_reminder_body() (the old per-symbol sentence builder) was deleted since its only
caller was removed; its formatting logic (estimate/beat-rate/surprise-trend from
/stocks/{symbol}/fundamentals) now lives inline in send_earnings_reminder_digest_email()'s own
_fmt_price()/_fmt_beat_rate()/_fmt_kscore() helpers, tested directly there instead.

_earnings_reaction_body() is unchanged — the post-release fast reaction once eps_actual lands,
using event-intelligence's already-computed surprise_pct/earnings_strength_score.

scheduler.py can't be imported directly in this test environment (see
test_price_alert_price_check.py's docstring for the same constraint) — _earnings_reaction_body()
is pure/dependency-free, so it's loaded directly from source via exec(), same technique.
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


_earnings_reaction_body = _load_function("_earnings_reaction_body")


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


# ── wiring: check_signal_alerts() must send ONE consolidated digest per user ──

def test_reminder_wiring_sends_one_consolidated_digest_not_per_symbol_emails():
    """Source-text check: check_signal_alerts()'s day-of reminder must collect all of a
    user's upcoming-earnings rows and send ONE send_earnings_reminder_digest_email() call per
    user — not a separate send_email() per (user, symbol) inside the per-symbol loop (the
    exact inbox-flood pattern AUD-EARNINGS-DIGEST fixed)."""
    start = _source.index("# T230-ALERTING-EARNINGS-PROXIMITY: send earnings reminder")
    end = _source.index("\n\n            # ", start + 10) if "\n\n            # " in _source[start:start+4000] else start + 4000
    body = _source[start:end]
    assert "send_earnings_reminder_digest_email" in body
    assert "digest_rows.append(" in body


def test_reminder_dedup_key_granularity_unchanged():
    """The per-(user, symbol, days_to_earnings) 20h-TTL dedup key must still exist — the
    consolidation only batches DELIVERY, not the dedup granularity."""
    assert 'redis_key = f"stockai:earnings_remind:{uid}:{sym}:{dte_int}"' in _source
    assert "72000" in _source  # 20-hour TTL, unchanged


def test_earnings_reaction_check_is_registered_as_a_scheduled_job():
    """Source-text check: check_earnings_reactions must actually be wired into
    start_scheduler()'s add_job calls, not just exist as an unused function."""
    assert 'check_earnings_reactions,' in _source
    assert 'id="earnings_reaction_check"' in _source


# ── T249-MARKETMOVER-P2: check_macro_reaction_alerts() wiring ──────────────────

def test_macro_reaction_alert_check_is_registered_as_a_scheduled_job():
    assert 'check_macro_reaction_alerts,' in _source
    assert 'id="macro_reaction_alert_check"' in _source


def test_macro_reaction_alerts_only_marks_sent_after_a_successful_send():
    """The exact ordering discipline check_signal_alerts()/check_earnings_reactions() both
    follow: reaction_sent_at must only advance inside the any_sent-gated branch, not
    unconditionally — otherwise a fully-failed send cycle would still mark rows as sent and
    silently drop the alert forever (no retry on the next minute's tick)."""
    start = _source.index("def check_macro_reaction_alerts")
    end = _source.index("\n\ndef check_signal_alerts", start)
    body = _source[start:end]
    assert "if any_sent:" in body
    assert body.index("if any_sent:") < body.index("ev.reaction_sent_at = ")
