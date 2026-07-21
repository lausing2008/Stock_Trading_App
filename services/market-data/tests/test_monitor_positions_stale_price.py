"""Regression tests for BUG-MONITORPOS-STALEPRICE.

_monitor_positions()'s missing-live-quote fallback (T234-PT-MONITOR-MISSING-PRICE-FALLBACK)
previously fired silently forever — trade.current_price was unconditionally overwritten with
the SAME stale value every cycle (this loop runs every 5-10 min per this module's own
docstring), with no tracking of how many consecutive cycles a real quote hadn't arrived. A
genuinely bad multi-cycle data outage (feed issue, halt, delisting) could leave a position's
stop/target checks running against an increasingly frozen price for an unbounded time with
zero visibility — a single log.warning() per cycle looked identical whether this was cycle 1
or cycle 50.

_monitor_positions() itself is not exercised end-to-end here (200+ lines, heavy Signal/RSI/
regime dependencies that would need a large fixture harness disproportionate to this fix's
actual scope, which is an additive, self-contained staleness-tracking block) — matching
test_scheduler_static_names.py's established precedent for this exact risk class, these are
source-text regression checks on the specific new logic, not a full functional exercise.
"""
import pathlib

_PTE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_SOURCE = _PTE_PATH.read_text()


def _fallback_block() -> str:
    """The missing-live-quote fallback block inside _monitor_positions()'s per-trade loop —
    from the `if not live_price:` branch through the end of the `else` (real-quote) branch,
    just before `trade.current_price = live_price`."""
    start = _SOURCE.index("if not live_price:")
    end = _SOURCE.index("trade.current_price = live_price", start)
    return _SOURCE[start:end]


def test_stale_cycle_count_is_tracked_in_redis_not_just_logged():
    """The exact fix: a per-trade Redis counter (stockai:monitor_stale_price:{trade.id}) must
    be incremented on every cycle a real quote is missing, not just a repeated identical log
    line with no state carried between cycles."""
    body = _fallback_block()
    assert 'f"stockai:monitor_stale_price:{trade.id}"' in body
    assert ".incr(" in body


def test_stale_counter_has_a_ttl_so_it_cannot_leak_forever():
    """The counter must expire — it's transient diagnostic state, not something meant to
    survive indefinitely if a trade is later closed without ever recovering a live quote."""
    body = _fallback_block()
    assert ".expire(" in body


def test_stale_count_conversion_is_inside_the_same_try_except_as_the_redis_call():
    """int(_stale_redis.incr(...)) must be wrapped by the SAME try/except as the Redis call
    itself — a real Redis outage (connection error) OR an unexpected return type must both
    fail open to _stale_count = 0, never raise past this block and crash the whole monitoring
    cycle for every other open trade."""
    body = _fallback_block()
    try_idx = body.index("try:")
    except_idx = body.index("except Exception:", try_idx)
    incr_idx = body.index(".incr(", try_idx)
    assert try_idx < incr_idx < except_idx
    assert "_stale_count = int(_stale_redis.incr(" in body


def test_redis_failure_falls_back_to_zero_not_a_crash():
    """The except branch must explicitly reset _stale_count to 0 (fail-open), not silently
    leave a partially-assigned or stale value from a previous iteration."""
    body = _fallback_block()
    except_idx = body.index("except Exception:")
    except_block = body[except_idx:except_idx + 150]
    assert "_stale_count = 0" in except_block


def test_escalates_to_log_error_once_the_threshold_is_crossed():
    """Below the threshold: log.warning (as before this fix). At or above the threshold:
    log.error — genuinely different severity, so a stuck feed is visible/alertable
    differently than one normal missed tick."""
    body = _fallback_block()
    assert "_STALE_ESCALATION_THRESHOLD" in body
    assert 'log.error("paper.monitor_price_stale_escalation"' in body
    assert 'log.warning("paper.monitor_price_fallback"' in body
    escalation_idx = body.index("if _stale_count >= _STALE_ESCALATION_THRESHOLD:")
    error_idx = body.index('log.error("paper.monitor_price_stale_escalation"')
    warning_idx = body.index('log.warning("paper.monitor_price_fallback"')
    assert escalation_idx < error_idx < warning_idx


def test_both_the_error_and_warning_log_lines_include_the_stale_cycle_count():
    """The whole point of tracking stale_cycles is to make it visible in logs — both log
    lines must actually include the count, not just internally gate on it."""
    body = _fallback_block()
    error_line_start = body.index('log.error("paper.monitor_price_stale_escalation"')
    error_line = body[error_line_start:body.index(")", body.index("note=", error_line_start))]
    assert "stale_cycles=_stale_count" in error_line

    warning_line_start = body.index('log.warning("paper.monitor_price_fallback"')
    warning_line = body[warning_line_start:body.index(")", body.index("note=", warning_line_start))]
    assert "stale_cycles=_stale_count" in warning_line


def test_a_real_quote_arriving_clears_the_stale_streak():
    """A single missed tick followed by a healthy cycle must not carry a false streak into a
    LATER, unrelated gap — the real-quote branch (the `else` of `if not live_price:`) must
    delete the same Redis key the fallback branch increments."""
    start = _SOURCE.index("if not live_price:")
    else_idx = _SOURCE.index("\n        else:", start)
    end = _SOURCE.index("trade.current_price = live_price", else_idx)
    else_body = _SOURCE[else_idx:end]
    assert 'f"stockai:monitor_stale_price:{trade.id}"' in else_body
    assert ".delete(" in else_body


def test_staleness_tracking_never_changes_which_price_is_used_for_exit_math():
    """This fix is diagnostic-only by design — it must not alter the existing 3-tier fallback
    (live -> cached current_price -> entry_price) or the exit-check math that follows. The
    fallback price computation must appear BEFORE the staleness-tracking block, and
    trade.current_price must still be set to the same `live_price` variable regardless of
    whether escalation fired."""
    body = _fallback_block()
    fallback_price_idx = body.index("live_price = trade.current_price or trade.entry_price")
    stale_tracking_idx = body.index("_stale_count = 0")
    assert fallback_price_idx < stale_tracking_idx
