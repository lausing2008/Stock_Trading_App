"""Tests for the AUD266 alert-reliability trio (Deep Audit #6, Tier 266):

- AUD266-DEDUP-KEY-SET-BEFORE-SEND: 4 alert jobs (check_top3_conviction, check_
  value_area_breakdown, check_volume_anomalies, check_short_squeeze_alerts) wrote their
  dedup/active-set state BEFORE attempting the send, so a single transient send failure
  permanently suppressed that alert for the full TTL with no retry. Fixed by moving every
  write inside the successful-send branch, matching check_gamma_unwind_alerts' established
  correct pattern (sadd only inside `if send(...):`).
- AUD266-ANY-SENT-GLOBAL-FLAG-CROSS-USER-SUPPRESSION: check_macro_reaction_alerts and
  check_earnings_impact_alerts used a single any_sent flag shared across the whole recipient
  loop to gate a PER-EVENT timestamp (reaction_sent_at/impact_sent_at) — so the moment ANY ONE
  recipient's send succeeded, the event was marked delivered for every OTHER recipient too.
  Fixed via a per-(event, user) Redis dedup key plus an all_recipients_notified flag that also
  gates the per-event timestamp.
- AUD266-PER-RECIPIENT-ISOLATION-NEVER-PROPAGATED: 9 alert send loops called send_X_email()
  directly with no per-recipient try/except, so an uncaught exception from inside the email
  builder (a malformed dict, a None formatting target) would propagate to the function's
  outer except, aborting the whole remaining recipient loop. Fixed by porting the same
  try/except-log-continue pattern send_premarket_brief()/send_morning_digest() already use.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler and other unstubbed modules) — covered by source-text regression checks, matching
this repo's established convention for this class of function.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _function_body(name: str) -> str:
    start = _scheduler_source.index(f"\ndef {name}(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── AUD266-DEDUP-KEY-SET-BEFORE-SEND ─────────────────────────────────────────────────────

def test_top3_conviction_dedup_key_written_only_after_successful_send():
    body = _function_body("check_top3_conviction")
    send_idx = body.index("top3_ok = send_top3_conviction_email(user.email, top3)")
    setex_idx = body.index('_rc.setex(dedup_key, 6 * 3600, "1")')
    if_top3_ok_idx = body.rindex("if top3_ok:")
    assert send_idx < if_top3_ok_idx < setex_idx


def test_value_area_breakdown_dedup_keys_written_only_after_successful_send():
    body = _function_body("check_value_area_breakdown")
    send_idx = body.index("va_ok = send_value_area_breakdown_email(u_obj.email, my_alerts)")
    if_ok_idx = body.index("if va_ok:")
    setex_idx = body.index('_rc.setex(dedup_key, 26 * 3600, "1")')
    assert send_idx < if_ok_idx < setex_idx


def test_volume_anomalies_dedup_keys_written_only_after_successful_send():
    body = _function_body("check_volume_anomalies")
    send_idx = body.index("vol_anomaly_ok = send_volume_anomaly_email(user.email, my_alerts)")
    if_ok_idx = body.index("if vol_anomaly_ok:")
    setex_idx = body.index('_rc.setex(dedup_key, 20 * 3600, "1")')
    assert send_idx < if_ok_idx < setex_idx


def test_short_squeeze_active_set_never_includes_failed_send_symbols():
    """The trickiest of the 4 — state_key tracks BOTH dedup AND "still active" (for a symbol
    that legitimately drops out to correctly re-alert later), so a bare `if send(...): resync`
    fix would be wrong: a dropped-out symbol must still be removed regardless of send outcome.
    The real fix excludes only the FAILED newly_qualifying symbols from the resync, not the
    whole set."""
    body = _function_body("check_short_squeeze_alerts")
    assert "resync_set = current_active if send_ok else (current_active - set(newly_qualifying))" in body
    # the resync must happen unconditionally (a real drop-out is a fact independent of send
    # outcome) — confirm there's no `if send_ok:` gating the delete/sadd block itself
    resync_idx = body.index("resync_set = current_active if send_ok")
    tail = body[resync_idx:resync_idx + 300]
    assert "_rc.delete(state_key)" in tail
    assert "if resync_set:" in tail


def test_gamma_unwind_is_the_untouched_reference_pattern():
    """Confirms this fix's own reference implementation is unchanged — sadd only inside the
    successful-send branch, exactly as it always was."""
    body = _function_body("check_gamma_unwind_alerts")
    send_idx = body.index("gamma_ok = send_gamma_unwind_email(user.email, new_candidates)")
    if_ok_idx = body.index("if gamma_ok:")
    sadd_idx = body.index("_rc.sadd(state_key, *[f\"{c['symbol']}:{c['expiry']}\" for c in new_candidates])")
    assert send_idx < if_ok_idx < sadd_idx


# ── AUD266-ANY-SENT-GLOBAL-FLAG-CROSS-USER-SUPPRESSION ───────────────────────────────────

def test_macro_reaction_alerts_tracks_delivery_per_event_and_user():
    body = _function_body("check_macro_reaction_alerts")
    assert 'redis_key = f"stockai:macro_reaction_sent:{uid}:{ev.id}"' in body
    assert "all_recipients_notified" in body


def test_macro_reaction_alerts_only_stamps_sent_at_when_every_recipient_notified():
    body = _function_body("check_macro_reaction_alerts")
    assert "if any_sent and all_recipients_notified:" in body
    stamp_idx = body.index("if any_sent and all_recipients_notified:")
    tail = body[stamp_idx:stamp_idx + 150]
    assert "ev.reaction_sent_at = datetime.now(timezone.utc)" in tail


def test_macro_reaction_alerts_marks_all_recipients_notified_false_on_failed_send():
    """A single failed send within the loop must flip all_recipients_notified to False —
    otherwise a partial-delivery cycle would still stamp reaction_sent_at."""
    body = _function_body("check_macro_reaction_alerts")
    assert "all_recipients_notified = False" in body


def test_earnings_impact_alerts_tracks_delivery_per_event_and_user():
    body = _function_body("check_earnings_impact_alerts")
    assert 'redis_key = f"stockai:earnings_impact_sent:{uid}:{ev.id}"' in body
    assert "all_recipients_notified" in body


def test_earnings_impact_alerts_only_stamps_sent_at_when_every_recipient_notified():
    body = _function_body("check_earnings_impact_alerts")
    assert "if any_sent and all_recipients_notified:" in body
    stamp_idx = body.rindex("if any_sent and all_recipients_notified:")
    tail = body[stamp_idx:stamp_idx + 150]
    assert "ev.impact_sent_at = datetime.now(timezone.utc)" in tail


def test_earnings_impact_alerts_marks_all_recipients_notified_false_on_failed_send():
    body = _function_body("check_earnings_impact_alerts")
    assert "all_recipients_notified = False" in body


# ── AUD266-PER-RECIPIENT-ISOLATION-NEVER-PROPAGATED ──────────────────────────────────────

def test_top3_conviction_send_call_is_isolated_per_recipient():
    body = _function_body("check_top3_conviction")
    try_idx = body.index("try:\n                    top3_ok = send_top3_conviction_email")
    tail = body[try_idx:try_idx + 250]
    assert "except Exception as _send_exc:" in tail
    assert "top3_ok = False" in tail


def test_volume_anomalies_send_call_is_isolated_per_recipient():
    body = _function_body("check_volume_anomalies")
    try_idx = body.index("try:\n                    vol_anomaly_ok = send_volume_anomaly_email")
    tail = body[try_idx:try_idx + 250]
    assert "except Exception as _send_exc:" in tail


def test_short_squeeze_send_call_is_isolated_per_recipient():
    body = _function_body("check_short_squeeze_alerts")
    try_idx = body.index("try:\n                        send_ok = send_short_squeeze_email")
    tail = body[try_idx:try_idx + 250]
    assert "except Exception as _send_exc:" in tail
    assert "send_ok = False" in tail


def test_gamma_unwind_send_call_is_isolated_per_recipient():
    body = _function_body("check_gamma_unwind_alerts")
    try_idx = body.index("try:\n                    gamma_ok = send_gamma_unwind_email")
    tail = body[try_idx:try_idx + 250]
    assert "except Exception as _send_exc:" in tail


def test_value_area_breakdown_send_call_is_isolated_per_recipient():
    body = _function_body("check_value_area_breakdown")
    try_idx = body.index("try:\n                    va_ok = send_value_area_breakdown_email")
    tail = body[try_idx:try_idx + 250]
    assert "except Exception as _send_exc:" in tail


def test_signal_alerts_conviction_email_send_is_isolated_per_recipient():
    body = _function_body("check_signal_alerts")
    try_idx = body.index("try:\n                    email_ok = send_signal_alert_email(")
    tail = body[try_idx:try_idx + 900]
    assert "except Exception as _send_exc:" in tail
    assert "email_ok = False" in tail


def test_signal_alerts_earnings_reminder_digest_send_is_isolated_per_recipient():
    body = _function_body("check_signal_alerts")
    try_idx = body.index("try:\n                        digest_ok = send_earnings_reminder_digest_email")
    tail = body[try_idx:try_idx + 250]
    assert "except Exception as _send_exc:" in tail


def test_price_alerts_pending_emails_loop_is_isolated_per_recipient():
    body = _function_body("check_price_alerts")
    for_idx = body.index("for kwargs in pending_emails:")
    tail = body[for_idx:for_idx + 300]
    assert "try:" in tail
    assert "except Exception as _send_exc:" in tail


def test_price_alerts_drawdown_send_is_isolated_per_trade():
    """This one previously ALSO ignored its own return value entirely (a silent fire-and-
    forget) — the fix must both catch the exception AND check the boolean result."""
    body = _function_body("check_price_alerts")
    try_idx = body.index("try:\n                            drawdown_ok = send_price_alert_email(")
    tail = body[try_idx:try_idx + 900]
    assert "except Exception as _send_exc:" in tail
    assert "drawdown_ok = False" in tail
    assert "if drawdown_ok:" in tail


def test_price_alerts_drawdown_dedup_key_written_only_after_successful_send():
    """A second, previously-uncaught instance of the dedup-before-send bug class, found
    during this same fix pass."""
    body = _function_body("check_price_alerts")
    if_ok_idx = body.index("if drawdown_ok:")
    setex_idx = body.index('_rc and _rc.setex(redis_key, 86400, "1")')
    assert if_ok_idx < setex_idx


def test_technical_alerts_pending_emails_loop_is_isolated_per_recipient():
    body = _function_body("check_technical_alerts")
    for_idx = body.index("for kwargs in pending_emails:")
    tail = body[for_idx:for_idx + 300]
    assert "try:" in tail
    assert "except Exception as _send_exc:" in tail


def test_earnings_beat_screener_send_is_isolated_per_recipient():
    body = _function_body("check_earnings_beat_screener_alerts")
    try_idx = body.index("try:\n                    screener_ok = send_earnings_beat_screener_email")
    tail = body[try_idx:try_idx + 250]
    assert "except Exception as _send_exc:" in tail


def test_sector_rotation_send_is_isolated_per_recipient():
    body = _function_body("check_sector_rotation_alerts")
    try_idx = body.index("try:\n                    rotation_ok = send_sector_rotation_email")
    tail = body[try_idx:try_idx + 250]
    assert "except Exception as _send_exc:" in tail


def test_squeeze_watch_reverts_already_had_per_watch_isolation_untouched():
    """Confirms this function needed NO fix — its per-watch try/except (squeeze_watch.
    symbol_error) already wraps the send_squeeze_watch_revert_email call, isolating one
    watch's exception from the rest of the loop."""
    body = _function_body("check_squeeze_watch_reverts")
    assert "send_squeeze_watch_revert_email(" in body
    assert 'except Exception as exc:\n                    log.warning("squeeze_watch.symbol_error"' in body
