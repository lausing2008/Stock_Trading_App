"""Regression tests for BUG-MONITORPOS-STALEPRICE and its follow-up
AUD262-STALE-PRICE-COUNTER-GATES-NOTHING.

_monitor_positions()'s missing-live-quote fallback (T234-PT-MONITOR-MISSING-PRICE-FALLBACK)
previously fired silently forever — trade.current_price was unconditionally overwritten with
the SAME stale value every cycle (this loop runs every 5-10 min per this module's own
docstring), with no tracking of how many consecutive cycles a real quote hadn't arrived. A
genuinely bad multi-cycle data outage (feed issue, halt, delisting) could leave a position's
stop/target checks running against an increasingly frozen price for an unbounded time with
zero visibility — a single log.warning() per cycle looked identical whether this was cycle 1
or cycle 50. BUG-MONITORPOS-STALEPRICE (2026-07-21) added the escalating counter, but the
escalation itself GATED NOTHING — every stop/target/trailing-stop comparison still ran
unconditionally against the frozen price even past the escalation threshold, which could hold
a position through an entire decline (stop never fires against a frozen too-high price) or
fire a phantom exit at a price that never actually traded. AUD262-STALE-PRICE-COUNTER-GATES-
NOTHING (2026-08-07) closes this: past the threshold, the entire exit-evaluation chain is
skipped for that trade this cycle — hold, don't act on a price known to be stale.

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


def _exit_chain_block() -> str:
    """The full exit-evaluation if/elif chain inside _monitor_positions()'s per-trade loop —
    from the BUG-PAPERPOS-DELISTED-FROZEN check through the end of the WAIT-decay branch,
    just before the "Execute exit" comment."""
    start = _SOURCE.index("if trade.symbol in delisted_symbols:")
    end = _SOURCE.index("# ── Execute exit", start)
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
    """The staleness-tracking mechanism itself must not alter the existing 3-tier fallback
    (live -> cached current_price -> entry_price) — the fallback price computation must
    appear BEFORE the staleness-tracking block, and trade.current_price must still be set to
    the same `live_price` variable regardless of whether escalation fired. (Escalation DOES
    now change what happens LATER, in the exit-evaluation chain — see the
    AUD262-STALE-PRICE-COUNTER-GATES-NOTHING tests below — but not the fallback price itself.)"""
    body = _fallback_block()
    fallback_price_idx = body.index("live_price = trade.current_price or trade.entry_price")
    stale_tracking_idx = body.index("_stale_count = 0")
    assert fallback_price_idx < stale_tracking_idx


# ── AUD262-STALE-PRICE-COUNTER-GATES-NOTHING ─────────────────────────────────────────────

def test_escalation_flag_is_set_when_the_threshold_is_crossed():
    """The core new mechanism: crossing _STALE_ESCALATION_THRESHOLD must set
    _price_is_stale_escalated = True, the flag the exit-evaluation chain below checks."""
    body = _fallback_block()
    threshold_idx = body.index("if _stale_count >= _STALE_ESCALATION_THRESHOLD:")
    tail = body[threshold_idx:threshold_idx + 1600]
    assert "_price_is_stale_escalated = True" in tail


def test_escalation_flag_defaults_false_before_the_stale_check_runs():
    """_price_is_stale_escalated must be initialized to False before the `if not live_price:`
    branch — a trade with a genuinely fresh quote this cycle must never accidentally inherit
    a stale escalation state from a prior trade in the same loop iteration."""
    start = _SOURCE.index("_price_is_stale_escalated = False")
    not_live_idx = _SOURCE.index("if not live_price:")
    assert start < not_live_idx
    assert not_live_idx - start < 100


def test_exit_evaluation_chain_checks_the_escalation_flag_as_a_high_priority_branch():
    """The escalation-gate branch must be an `elif` positioned directly after the delisted
    check and BEFORE every price-based exit branch (stop/target/etc.) — Python's elif
    semantics mean this correctly short-circuits every subsequent branch in the chain once it
    matches, without needing to individually guard each one."""
    body = _exit_chain_block()
    delisted_idx = body.index("if trade.symbol in delisted_symbols:")
    escalation_idx = body.index("elif _price_is_stale_escalated:")
    stop_idx = body.index("elif live_price <= stop:")
    assert delisted_idx < escalation_idx < stop_idx


def test_escalated_branch_never_sets_an_exit_reason():
    """The whole point of this fix: past the threshold, the position must be HELD, not
    exited — the escalation branch's own body must never assign exit_reason, which would
    trigger a real position closure against a price known to be stale."""
    body = _exit_chain_block()
    escalation_idx = body.index("elif _price_is_stale_escalated:")
    next_elif_idx = body.index("elif live_price <= stop:")
    escalation_branch = body[escalation_idx:next_elif_idx]
    assert "exit_reason =" not in escalation_branch


def test_delisted_check_is_not_gated_by_the_stale_escalation():
    """A confirmed delisted stock has no real market left at all — this is a fundamentally
    different situation from a temporarily-blind feed, and must still exit even if the price
    escalation flag happens to also be set for that trade (both conditions could co-occur:
    the feed goes stale right as a stock is confirmed delisted). Only the delisted branch's
    OWN code body is checked here (up to its final log.warning call) — not the connective
    comment/prose between it and the next elif, which legitimately names the flag for
    documentation purposes without actually referencing it in a real condition."""
    body = _exit_chain_block()
    delisted_idx = body.index("if trade.symbol in delisted_symbols:")
    delisted_end_idx = body.index('exit_price=live_price, pnl_pct=round(pnl_pct * 100, 2))', delisted_idx)
    delisted_branch = body[delisted_idx:delisted_end_idx]
    assert "_price_is_stale_escalated" not in delisted_branch
