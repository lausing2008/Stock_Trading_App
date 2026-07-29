"""Regression tests for BUG-ALERTS-DELISTED-SILENT.

check_price_alerts()/check_signal_alerts() both go silent forever on a delisted symbol with
no notification — a delisted stock never has a usable live price (PriceAlert) or fresh
price bar (SignalAlert's existing freshness check), so the alert sits unfired/unchecked
indefinitely with zero indication to the user their subscription is dead.

PriceAlert self-terminates via `triggered=True` once fired — a confirmed delisting is
therefore a real terminal state, not just a notice. SignalAlert has no such lifecycle (a
persistent subscription, since a relisting is rare but not impossible), so it gets a
one-time Redis-deduped notice instead of being deleted outright.

scheduler.py can't be imported directly in this test environment — its import chain pulls
in apscheduler plus several unstubbed modules (see test_price_alert_price_check.py's
docstring for the same constraint). These are source-text regression checks.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SOURCE = _scheduler_path.read_text()


def _price_alerts_body() -> str:
    start = _SOURCE.index("def check_price_alerts(")
    end = _SOURCE.index("\ndef ", start + 10)
    return _SOURCE[start:end]


def _signal_alerts_body() -> str:
    start = _SOURCE.index("def check_signal_alerts(")
    end = _SOURCE.index("\ndef ", start + 10)
    return _SOURCE[start:end]


# ── check_price_alerts() ────────────────────────────────────────────────────────────

def test_price_alerts_bulk_fetches_delisted_symbols():
    body = _price_alerts_body()
    assert "delisted_symbols" in body
    assert "select(Stock.symbol, Stock.delisted)" in body


def test_price_alerts_delisted_fetch_happens_before_the_deactivation_loop():
    body = _price_alerts_body()
    fetch_idx = body.index("select(Stock.symbol, Stock.delisted)")
    deactivate_idx = body.index("if alert.symbol not in delisted_symbols:")
    assert fetch_idx < deactivate_idx


def test_price_alerts_delisted_fetch_fails_open_on_db_error():
    body = _price_alerts_body()
    fetch_start = body.index("select(Stock.symbol, Stock.delisted)")
    try_idx = body.rindex("try:", 0, fetch_start)
    except_idx = body.index("except Exception as exc:", fetch_start)
    assert try_idx < fetch_start < except_idx
    except_block = body[except_idx:except_idx + 150]
    assert "price_alert.delisted_check_failed" in except_block


def test_price_alerts_sets_triggered_true_on_confirmed_delisting():
    """A confirmed delisting must permanently deactivate the alert via the SAME
    triggered=True lifecycle every other fired alert already uses — not a separate flag."""
    body = _price_alerts_body()
    dl_start = body.index("delisted_fired = 0")
    dl_end = body.index("if delisted_fired:")
    block = body[dl_start:dl_end]
    assert "alert.triggered = True" in block
    assert "alert.triggered_at = datetime.now(timezone.utc)" in block


def test_price_alerts_commits_the_deactivation():
    body = _price_alerts_body()
    assert "if delisted_fired:" in body
    commit_idx = body.index("if delisted_fired:")
    commit_block = body[commit_idx:commit_idx + 60]
    assert "session.commit()" in commit_block


def test_price_alerts_main_loop_skips_already_deactivated_delisted_symbols():
    """Defense-in-depth: even though a delisted symbol never gets a usable price (so the
    pre-existing `if price is None: continue` already skips it), the main loop must
    explicitly skip delisted_symbols too, guarding against a transient stale cached price
    slipping through before the exception path fires."""
    body = _price_alerts_body()
    main_loop_idx = body.rindex("for alert in alerts:")
    guard_idx = body.index("if alert.symbol in delisted_symbols:", main_loop_idx)
    price_none_idx = body.index("if price is None:", main_loop_idx)
    assert main_loop_idx < guard_idx < price_none_idx


def test_price_alerts_sends_an_email_only_when_one_is_on_file():
    body = _price_alerts_body()
    dl_start = body.index("delisted_fired = 0")
    dl_end = body.index("if delisted_fired:")
    block = body[dl_start:dl_end]
    assert "if alert.email:" in block
    assert "send_email(" in block


# ── check_signal_alerts() ───────────────────────────────────────────────────────────

def test_signal_alerts_bulk_fetches_delisted_symbols():
    body = _signal_alerts_body()
    assert "delisted_symbols" in body
    assert "select(Stock.symbol, Stock.delisted)" in body


def test_signal_alerts_does_not_deactivate_the_subscription_itself():
    """Unlike PriceAlert, SignalAlert has no triggered/self-terminating lifecycle — a
    relisting is rare but not impossible, so the fix must be a notification only, never a
    delete/deactivate of the subscription row."""
    body = _signal_alerts_body()
    dl_start = body.index("delisted_symbols: set[str] = set()")
    dl_end = body.index("current_regime = _get_current_regime()")
    block = body[dl_start:dl_end]
    assert ".delete(" not in block
    assert "session.delete" not in block


def test_signal_alerts_notice_is_deduped_via_a_redis_key_per_alert():
    """A one-time notice must not re-fire every cycle (this job runs every minute) — a
    Redis SET NX EX keyed per alert id, matching the established one-time-notice
    convention already used elsewhere in this file (e.g. stockai:auto_research_sent:)."""
    body = _signal_alerts_body()
    dl_start = body.index("delisted_symbols: set[str] = set()")
    dl_end = body.index("current_regime = _get_current_regime()")
    block = body[dl_start:dl_end]
    assert 'f"stockai:alert_delisted_notice:{alert.id}"' in block
    assert ".set(" in block
    assert "nx=True" in block


def test_signal_alerts_only_notifies_alerts_with_an_email_on_file():
    body = _signal_alerts_body()
    dl_start = body.index("delisted_symbols: set[str] = set()")
    dl_end = body.index("current_regime = _get_current_regime()")
    block = body[dl_start:dl_end]
    assert "not alert.email" in block


def test_signal_alerts_delisted_fetch_fails_open_on_db_error():
    body = _signal_alerts_body()
    fetch_start = body.index("select(Stock.symbol, Stock.delisted)")
    try_idx = body.rindex("try:", 0, fetch_start)
    except_idx = body.index("except Exception as exc:", fetch_start)
    assert try_idx < fetch_start < except_idx
    except_block = body[except_idx:except_idx + 150]
    assert "signal_alert.delisted_check_failed" in except_block
