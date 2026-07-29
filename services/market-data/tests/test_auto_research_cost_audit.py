"""Tests for CLAUDE-API-COST-AUDIT (2026-07-28) — _auto_trigger_research() in scheduler.py.

A 2026-07-28 usage audit found 72 real full-Sonnet report generations/24h in production,
traced to two compounding bugs:

1. The symbol query had no dedup by symbol — a stock with multiple BUY-confidence horizon
   rows (SHORT/SWING/LONG/GROWTH) could occupy several of the top-5 slots at once (confirmed
   live: "[RXT, SMTC, RXT, MU, RXT]" — 3 of 5 slots were the same symbol).
2. The downstream /trigger endpoint's "6h cooldown" was a plain dict age-check with a TOCTOU
   race, not an actual lock (see test_trigger_research_inflight_check.py for that half).

Fixed by: (a) GROUP BY Stock.symbol so each symbol occupies exactly one top-5 slot, (b) a
per-symbol Redis SET NX EX lock so this function itself can never POST /trigger for the same
symbol more than once per cooldown window regardless of duplicate rows or overlapping cycles,
(c) a global admin feature flag (default OFF) gating the whole function, since this is the
most expensive Claude-calling feature in the app with no opt-in/opt-out anywhere before this.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler and other unstubbed modules — see test_price_alert_price_check.py's docstring for
the same constraint), so this is covered via source-text regression checks, matching
test_volume_anomaly_alert.py's/test_scheduler_static_names.py's established pattern.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _auto_research_body() -> str:
    start = _scheduler_source.index("def _auto_trigger_research(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


_body = _auto_research_body()


def test_feature_flag_gate_checked_before_any_query_or_http_call():
    """The feature-flag check must be the FIRST thing the function does — before the DB
    query or any /trigger HTTP call — so a disabled flag never even builds the candidate
    list, matching this repo's own established fail-closed convention."""
    flag_idx = _body.index("_AUTO_RESEARCH_ENABLED_KEY")
    session_idx = _body.index("with SessionLocal() as session:")
    assert flag_idx < session_idx


def test_feature_flag_fails_closed_on_redis_error():
    """An unreachable admin-flag store must not silently re-enable this expensive feature —
    a Redis exception during the flag check must return early, not fall through to the
    candidate query."""
    start = _body.index("_AUTO_RESEARCH_ENABLED_KEY")
    end = _body.index("with SessionLocal()", start)
    guard_block = _body[max(0, start - 60):end]
    assert "except Exception" in guard_block
    # the except branch must itself return, not pass/continue into the query
    except_idx = guard_block.index("except Exception")
    tail = guard_block[except_idx:]
    assert "return" in tail


def test_symbol_query_groups_by_symbol_to_prevent_duplicate_slots():
    """The exact fix for bug 1: without GROUP BY Stock.symbol, a stock with BUY-confidence
    signals across multiple horizons (SHORT/SWING/LONG/GROWTH) could occupy several of the
    top-N slots at once — confirmed live as [RXT, SMTC, RXT, MU, RXT] (3 of 5 slots the same
    symbol)."""
    assert "group_by(Stock.symbol)" in _body
    # must aggregate confidence across the grouped rows, not just re-select a raw column
    assert "func.max(Signal.confidence)" in _body


def test_query_orders_by_the_aggregated_confidence_not_a_raw_column():
    """The ORDER BY must reference the same aggregated func.max(...) expression the GROUP BY
    produces — ordering by a bare Signal.confidence column alongside a GROUP BY Stock.symbol
    would be invalid/ambiguous SQL."""
    order_idx = _body.index(".order_by(")
    order_line_end = _body.index(")", order_idx)
    order_expr = _body[order_idx:order_line_end + 1]
    assert "func.max(Signal.confidence)" in order_expr


def test_per_symbol_redis_lock_is_set_nx_ex_not_a_plain_set():
    """The exact fix for bug 2 (the local half): a per-symbol SET NX EX lock must gate every
    /trigger POST — NX ensures only the first caller within the cooldown window can proceed,
    EX bounds the lock lifetime to the same cooldown research-engine's own cache uses."""
    assert "nx=True" in _body
    assert "ex=_AUTO_RESEARCH_COOLDOWN_S" in _body
    assert '_lock_key = f"stockai:auto_research_sent:{sym}"' in _body


def test_lock_check_happens_before_the_http_post():
    """The lock must be acquired (or the loop iteration skipped) BEFORE the outbound
    httpx.Client POST — acquiring it after would defeat the whole point of the lock."""
    lock_idx = _body.index("_get_redis().set(_lock_key")
    post_idx = _body.index("client.post(")
    assert lock_idx < post_idx


def test_lock_failure_skips_this_symbol_without_aborting_the_whole_cycle():
    """A symbol that fails to acquire the lock (already sent this cooldown window) must
    `continue` to the next candidate, not raise or return — one already-sent symbol must
    never block the remaining candidates in the same cycle from being checked."""
    lock_check_idx = _body.index("if not _get_redis().set(_lock_key")
    block_end = _body.index("\n", lock_check_idx)
    # find the continue within a few lines after the if-guard
    nearby = _body[lock_check_idx:lock_check_idx + 300]
    assert "continue" in nearby


def test_cooldown_matches_research_engines_own_cache_window():
    """The lock's TTL must match research-engine's own 6h (21_600s) cache-freshness window,
    referenced explicitly so the two windows can't silently drift apart."""
    assert "_AUTO_RESEARCH_COOLDOWN_S = 21_600" in _scheduler_source
