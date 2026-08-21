"""Tests for T260-BEARISH-PUTS-WATCHLIST's check_squeeze_watch_reverts() (scheduler.py) and
send_squeeze_watch_revert_email() (email_service.py).

send_squeeze_watch_revert_email() is pure string composition (no DB/network dependency), so
it's tested directly with real inputs. check_squeeze_watch_reverts() itself can't be imported
in this test environment — scheduler.py's import chain pulls in apscheduler and other
unstubbed modules — so the scan logic/job registration is covered by source-text regression
checks instead, matching test_short_squeeze_alert.py's / test_gamma_unwind_alert.py's
established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_squeeze_watch_revert_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_squeeze_watch_reverts_body() -> str:
    start = _scheduler_source.index("def check_squeeze_watch_reverts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── send_squeeze_watch_revert_email() — pure composition, tested directly ───────────────────

def test_short_squeeze_watch_type_uses_short_squeeze_label():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_watch_revert_email(
            "user@example.com", "GME", "short_squeeze",
            "price recovered to $210.00 (was $180.00 when added)", 210.0, 8.0,
        )
    assert "Short Squeeze Watch" in calls[0]["subject"]
    assert "GME" in calls[0]["html"]
    assert "$210.00" in calls[0]["html"]
    assert "Short % of float" in calls[0]["html"]


def test_bearish_puts_watch_type_uses_bearish_puts_label():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_watch_revert_email(
            "user@example.com", "XYZ", "bearish_puts",
            "short-side options/interest pressure has faded", 42.5, 48.0,
        )
    assert "Bearish Puts Watch" in calls[0]["subject"]
    assert "Puts concentration" in calls[0]["html"]


def test_revert_reason_rendered_in_body():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_watch_revert_email(
            "user@example.com", "ABC", "bearish_puts",
            "price recovered to $50.00 (was $40.00 when added); short-side options/interest pressure has faded",
            50.0, 40.0,
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "price recovered to $50.00" in html
    assert "price recovered to $50.00" in text


def test_missing_price_or_metric_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_squeeze_watch_revert_email(
            "user@example.com", "XYZ", "bearish_puts", "setup rolled off the scan", None, None,
        )
    assert result is True
    assert "—" in calls[0]["html"]


def test_body_states_this_watch_will_not_alert_again():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_watch_revert_email(
            "user@example.com", "GME", "short_squeeze", "price recovered", 210.0, 8.0,
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "will not alert again" in html.lower()
    assert "will not alert again" in text.lower()


# ── check_squeeze_watch_reverts() — source-text regression checks ───────────────────────────

def test_only_un_reverted_watches_are_checked():
    body = _check_squeeze_watch_reverts_body()
    assert "SqueezeWatch.reverted.is_(False)" in body


def test_uses_a_redis_lock():
    body = _check_squeeze_watch_reverts_body()
    assert "_SQUEEZE_WATCH_LOCK_KEY" in body
    assert "nx=True" in body


def test_uses_live_prices_and_bearish_watch_caches_not_a_fresh_fetch():
    """MUST read the same caches other fast alerts read (stockai:live_prices,
    stockai:bearish_puts_watch, stockai:fundamentals:v2:*) — never a per-symbol yfinance call
    inside this 1-minute loop."""
    body = _check_squeeze_watch_reverts_body()
    assert '"stockai:live_prices"' in body
    assert '"stockai:bearish_puts_watch"' in body
    assert "import yfinance" not in body


def test_revert_condition_is_an_or_not_an_and():
    """Per the user's own explicit choice: EITHER price recovery OR the metric fading alone is
    enough to mark reverted — not both required together."""
    body = _check_squeeze_watch_reverts_body()
    assert "if not (price_recovered or metric_faded):" in body


def test_short_squeeze_metric_faded_uses_the_same_threshold_that_qualified_it():
    body = _check_squeeze_watch_reverts_body()
    assert "metric_faded = current_metric < _SQUEEZE_MIN_SHORT_FLOAT" in body


def test_bearish_puts_metric_faded_uses_the_gamma_unwind_concentration_threshold():
    body = _check_squeeze_watch_reverts_body()
    assert "_GAMMA_UNWIND_MIN_OI_CONCENTRATION" in body


def test_bearish_puts_watch_present_but_not_puts_dominant_counts_as_faded():
    """If a symbol IS present in a fresh scan but no longer puts-dominant, that counts as the
    setup having faded — a real, positive signal from a real scan result."""
    body = _check_squeeze_watch_reverts_body()
    assert 'if bp is not None and bp.get("dominant_side") != "puts":' in body


# ── AUD265-BEARISHPUTS-MASS-AUTOREVERT-ON-OUTAGE ────────────────────────────────────────────

def test_missing_bearish_watch_cache_does_not_count_as_faded():
    """The core fix: a MISSING/stale/unparseable stockai:bearish_puts_watch cache must NEVER be
    treated as proof a setup faded — only a symbol genuinely absent from a FRESH scan counts.
    Before this fix, `bp is None` alone (regardless of cache freshness) set metric_faded = True,
    so a gamma-job outage (all yfinance calls failing, or a zero-candidate cycle that used to
    skip the cache write entirely) would silently mass-revert every un-reverted bearish_puts
    watch and permanently consume the one-shot alert."""
    body = _check_squeeze_watch_reverts_body()
    assert "elif bp is None and _bearish_cache_fresh:" in body
    # The SECOND `elif bp is None:` branch (the stale/missing-cache case, distinct from the
    # first `elif bp is None and _bearish_cache_fresh:` branch above it) must set
    # metric_faded = False as the very next non-comment statement — not just "somewhere in
    # the function", which a sabotage that flips this ONE branch to True would not catch.
    second_bp_none_idx = body.rindex("elif bp is None:")
    tail_after_second_branch = body[second_bp_none_idx:second_bp_none_idx + 700]
    assert "metric_faded = False" in tail_after_second_branch
    assert "metric_faded = True" not in tail_after_second_branch


def test_cache_freshness_is_determined_before_parsing_not_assumed_true():
    """_bearish_cache_fresh must reflect whether the Redis GET returned real data (key exists),
    not just default to True — and must be set to False again if the JSON parse itself fails
    (a corrupted/truncated value is just as untrustworthy as a missing key)."""
    body = _check_squeeze_watch_reverts_body()
    assert "_bearish_cache_raw = _rc.get(" in body
    assert "_bearish_cache_fresh = _bearish_cache_raw is not None" in body
    except_idx = body.index("except Exception:\n                _bearish_by_symbol = {}")
    tail = body[except_idx:except_idx + 120]
    assert "_bearish_cache_fresh = False" in tail


def test_gamma_unwind_writes_the_cache_even_with_zero_candidates():
    """The OTHER half of the fix, in check_gamma_unwind_alerts(): the cache write must happen
    BEFORE the early-return-on-zero-candidates, not after — a genuinely completed scan that
    found nothing is real information the revert checker needs to see as a FRESH empty cache,
    not as a missing one."""
    _gamma_start = _scheduler_source.index("def check_gamma_unwind_alerts(")
    _gamma_end = _scheduler_source.index("\ndef ", _gamma_start + 1)
    gamma_body = _scheduler_source[_gamma_start:_gamma_end]
    write_idx = gamma_body.index('_rc.setex("stockai:bearish_puts_watch"')
    # The actual CODE statement, not the explanatory comment above it which legitimately
    # quotes "if not candidates: return" in prose while describing the bug this fixes.
    early_return_idx = gamma_body.index("if not candidates:\n                _record_job_status")
    assert write_idx < early_return_idx


def test_marks_reverted_only_after_a_successful_send_not_before():
    """A failed email send must not silently mark the watch reverted — the user would then
    never learn about a real revert that happened to hit a delivery failure."""
    body = _check_squeeze_watch_reverts_body()
    sent_idx = body.index("sent_ok = send_squeeze_watch_revert_email(")
    reverted_idx = body.index("w.reverted = True")
    assert sent_idx < reverted_idx
    assert "if sent_ok:" in body


def test_job_is_registered_at_one_minute_interval():
    assert 'id="squeeze_watch_revert_check"' in _scheduler_source
    idx = _scheduler_source.index('id="squeeze_watch_revert_check"')
    preceding = _scheduler_source[max(0, idx - 300):idx]
    assert "minutes=1" in preceding


# ── AUD265-REVERT-CHECKER-NO-MARKET-HOURS-GATE ──────────────────────────────────────────────

def test_uses_the_same_is_market_hours_helper_as_the_volume_anomaly_scan():
    """Must reuse _is_market_hours() (already established for check_volume_anomalies()'s own
    BUG-VOLANOM-STALEMARKET fix) rather than hand-rolling a second market-hours check."""
    body = _check_squeeze_watch_reverts_body()
    assert "from .paper_trading_engine import _is_market_hours" in body
    assert '_is_market_hours("US")' in body
    assert '_is_market_hours("HK")' in body


def test_whole_function_short_circuits_when_both_markets_are_closed():
    body = _check_squeeze_watch_reverts_body()
    assert "if not _us_market_open and not _hk_market_open:" in body


def test_gate_runs_before_the_session_local_block_not_after():
    """The whole-function short-circuit must happen before any DB work starts, matching
    check_volume_anomalies()'s own established ordering — not as an afterthought once watches
    have already been fetched."""
    body = _check_squeeze_watch_reverts_body()
    gate_idx = body.index("if not _us_market_open and not _hk_market_open:")
    session_idx = body.index("with SessionLocal() as session:")
    assert gate_idx < session_idx


def test_per_watch_gate_skips_hk_symbols_when_hk_is_closed_even_if_us_is_open():
    """Watches can be a mix of US and HK symbols in the SAME un-reverted set — a whole-function
    skip alone would be wrong whenever exactly one market is open. Matches
    check_volume_anomalies()'s own per-symbol .HK-suffix pattern exactly."""
    body = _check_squeeze_watch_reverts_body()
    assert '_is_hk_watch = w.symbol.upper().endswith(".HK")' in body
    assert "if _is_hk_watch and not _hk_market_open:" in body
    assert "if not _is_hk_watch and not _us_market_open:" in body


def test_per_watch_gate_sits_inside_the_for_loop_before_any_price_lookup():
    """The per-watch skip must run before live_by_symbol.get(w.symbol) is ever consulted — a
    frozen/absent-for-this-market price must never reach the revert-decision logic at all."""
    body = _check_squeeze_watch_reverts_body()
    loop_idx = body.index("for w in watches:")
    gate_idx = body.index('_is_hk_watch = w.symbol.upper().endswith(".HK")')
    live_lookup_idx = body.index("live = _live_by_symbol.get(w.symbol)")
    assert loop_idx < gate_idx < live_lookup_idx


# ── AUD292-SQUEEZEWATCH-REVERT-NOTOLERANCE — price_recovered now requires a real, meaningful
# margin (not any positive delta) before treating price alone as evidence the thesis resolved ──

def _price_recovered(current_price: float | None, price_at_add: float | None, min_pct: float = 2.0) -> bool:
    """Extracted, real arithmetic (not a hand-copied reimplementation) — pulls the exact
    price_recovered expression out of check_squeeze_watch_reverts()'s own source and evaluates
    it against real numeric inputs, isolating just this computation from the surrounding
    DB/Redis-dependent function body it can't be run inside directly."""
    body = _check_squeeze_watch_reverts_body()
    start = body.index("price_recovered = (")
    end = body.index("\n\n", start)
    expr = body[start:end]
    namespace = {"current_price": current_price, "w": type("W", (), {"price_at_add": price_at_add})(),
                 "_SQUEEZE_WATCH_PRICE_RECOVERY_MIN_PCT": min_pct}
    exec(f"result = {expr[len('price_recovered = '):]}", namespace)  # noqa: S102 — isolated eval of real source
    return namespace["result"]


def test_a_sub_2pct_wiggle_no_longer_counts_as_recovered():
    """The exact real-world regression case: DFNS $24.35 -> $24.40, a 0.2% move — must no
    longer trip price_recovered."""
    assert _price_recovered(current_price=24.40, price_at_add=24.35) is False


def test_a_genuine_2pct_or_greater_recovery_still_counts():
    assert _price_recovered(current_price=25.00, price_at_add=24.35) is True  # +2.67%


def test_exactly_at_the_threshold_counts_as_recovered():
    assert _price_recovered(current_price=102.0, price_at_add=100.0) is True  # exactly +2.0%


def test_just_below_the_threshold_does_not_count():
    assert _price_recovered(current_price=101.9, price_at_add=100.0) is False  # +1.9%


def test_a_price_drop_never_counts_as_recovered():
    assert _price_recovered(current_price=90.0, price_at_add=100.0) is False


def test_missing_current_price_fails_safe_to_not_recovered():
    assert _price_recovered(current_price=None, price_at_add=100.0) is False


def test_missing_price_at_add_fails_safe_to_not_recovered():
    assert _price_recovered(current_price=105.0, price_at_add=None) is False


def test_zero_price_at_add_does_not_crash_via_division_by_zero():
    """A degenerate price_at_add=0.0 (should never occur in real data, but must not crash the
    whole revert-check loop for every other watch if it somehow does)."""
    assert _price_recovered(current_price=10.0, price_at_add=0.0) is False


def test_the_real_function_uses_the_module_constant_not_a_hardcoded_literal():
    """Confirms the real source reads _SQUEEZE_WATCH_PRICE_RECOVERY_MIN_PCT rather than a
    hardcoded 2.0 literal inline — so a future change to the constant actually takes effect."""
    body = _check_squeeze_watch_reverts_body()
    assert "_SQUEEZE_WATCH_PRICE_RECOVERY_MIN_PCT" in body
    assert "_SQUEEZE_WATCH_PRICE_RECOVERY_MIN_PCT = 2.0" in _scheduler_source
