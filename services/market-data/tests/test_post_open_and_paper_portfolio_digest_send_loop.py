"""Regression tests for AUD301-POSTOPENDIGEST-SENDLOOP and AUD301-PAPERPORTFOLIODIGEST-SENDLOOP.

Both send_post_open_digest() and send_paper_portfolio_digest() had the identical unguarded
send-loop pattern already found and fixed in send_premarket_brief()/send_morning_digest()
(AUD256, 2026-07-20c/2026-07-21d): no dedup (a restart within the job's own misfire-grace
window could re-email every recipient a second time) and no per-recipient error isolation (a
single bad send would propagate to the outer except, aborting the whole batch and silently
skipping every recipient still left in the loop).

send_paper_portfolio_digest() had a WORSE variant of the same bug: the per-portfolio METRICS
COMPUTATION (risk metrics, closed-trade queries, unrealized-P&L math) sat unguarded inside the
same nested loop, not just the send call — a single portfolio's data anomaly (e.g.
initial_capital == 0) could abort the digest for every other user/portfolio still left in the
loop. It also re-queried `portfolios` once per user for no reason (the query has no per-user
filter at all) — fixed to run once, before the outer loop.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler and other unstubbed modules) — covered via source-text regression checks, matching
test_morning_digest_send_loop.py's established pattern for this exact risk class.
"""
import pathlib

_SCHEDULER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
)
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _post_open_digest_body() -> str:
    start = _SCHEDULER_SOURCE.index("def send_post_open_digest(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def _paper_portfolio_digest_body() -> str:
    start = _SCHEDULER_SOURCE.index("def send_paper_portfolio_digest(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


# ── send_post_open_digest() ────────────────────────────────────────────────────

def test_post_open_digest_checks_a_redis_dedup_key_before_sending():
    body = _post_open_digest_body()
    assert 'redis_key = f"stockai:post_open_digest:{user.id}:{market}:{window}:{today_str}"' in body
    dedup_check_idx = body.index("_rc.exists(redis_key)")
    send_call_idx = body.index("send_post_open_digest_email(")
    assert dedup_check_idx < send_call_idx, "dedup check must happen BEFORE the send call"


def test_post_open_digest_sets_the_dedup_key_only_after_a_successful_send():
    body = _post_open_digest_body()
    setex_idx = body.index("_rc.setex(redis_key")
    if_ok_idx = body.rindex("if ok:", 0, setex_idx)
    send_call_idx = body.index("send_post_open_digest_email(")
    assert if_ok_idx > send_call_idx
    assert setex_idx > if_ok_idx


def test_post_open_digest_isolates_per_recipient_send_errors():
    body = _post_open_digest_body()
    send_call_idx = body.index("send_post_open_digest_email(")
    try_idx = body.rindex("try:", 0, send_call_idx)
    except_idx = body.index("except Exception as _send_exc:", send_call_idx)
    assert try_idx < send_call_idx < except_idx


def test_post_open_digest_logs_and_counts_per_recipient_errors_without_reraising():
    body = _post_open_digest_body()
    assert 'log.warning("post_open_digest.recipient_send_error"' in body
    assert "errors += 1" in body
    done_log_idx = body.index('log.info("post_open_digest.done"')
    done_log_line = body[done_log_idx:body.index("\n", done_log_idx + 200)]
    assert "errors=errors" in done_log_line


def test_post_open_digest_dedup_key_is_scoped_per_market_and_window():
    """send_post_open_digest(market, window) is called separately per (market, window)
    combination — the dedup key must include both so different windows/markets on the same
    day don't collide and suppress each other."""
    body = _post_open_digest_body()
    assert "{market}:{window}:{today_str}" in body


# ── send_paper_portfolio_digest() ──────────────────────────────────────────────

def test_paper_portfolio_digest_checks_a_redis_dedup_key_before_sending():
    """T372: the key is now scoped per (user, MARKET, date) — see the dedup-scope test below
    for why keeping the portfolio id would cause the very duplicate send it was meant to stop.
    The invariant asserted here is unchanged: check before send."""
    body = _paper_portfolio_digest_body()
    assert 'redis_key = f"stockai:paper_portfolio_digest:{user.id}:{_mkt}:{today_str}"' in body
    dedup_check_idx = body.index("_rc.exists(redis_key)")
    send_call_idx = body.index("send_paper_portfolio_digest_email(")
    assert dedup_check_idx < send_call_idx, "dedup check must happen BEFORE the send call"


def test_paper_portfolio_digest_sets_the_dedup_key_only_after_a_successful_send():
    body = _paper_portfolio_digest_body()
    setex_idx = body.index("_rc.setex(redis_key")
    if_ok_idx = body.rindex("if ok:", 0, setex_idx)
    send_call_idx = body.index("send_paper_portfolio_digest_email(")
    assert if_ok_idx > send_call_idx
    assert setex_idx > if_ok_idx


def test_paper_portfolio_digest_isolates_the_whole_per_portfolio_block_not_just_the_send():
    """Unlike the other digests, the try/except here must wrap the ENTIRE per-portfolio block
    (risk-metrics computation + trade queries + send), not just the send call — a single
    portfolio's data anomaly (e.g. a ZeroDivisionError computing total_return_pct) must not
    abort the digest for every other user/portfolio still left in the loop.

    T372-PORTFOLIO-DIGEST-CONSOLIDATE inverted the ORDER this test anchored on, without
    weakening the invariant it protects. The loop used to be `for user: for portfolio:
    dedup-check -> compute -> send`, so the dedup check came BEFORE the metrics. It is now
    `for portfolio: compute` (once, not once per user) followed by `for user: dedup-check ->
    send`, because the metrics do not depend on which user is being emailed — the old nesting
    recomputed every portfolio's risk metrics and trade queries for each recipient.

    The invariant is unchanged and still worth pinning: the per-portfolio computation must have
    its OWN try/except, so one portfolio's data anomaly (initial_capital == 0 ->
    ZeroDivisionError, a malformed equity-curve row) cannot abort the digest for every other
    portfolio in the market. Anchored now on that block directly.
    """
    body = _paper_portfolio_digest_body()
    # The per-portfolio computation sits inside its own try, closed by a calc-specific except.
    loop_idx = body.index("for p in portfolios:")
    try_idx = body.index("try:", loop_idx)
    metrics_call_idx = body.index("_portfolio_risk_metrics(curve_rows)")
    calc_except_idx = body.index("except Exception as _calc_exc:")
    assert loop_idx < try_idx < metrics_call_idx < calc_except_idx, (
        "the risk-metrics computation must sit inside a per-portfolio try/except, so one "
        "portfolio's bad data cannot abort the whole market's digest"
    )
    # And that except must NOT re-raise — it counts and continues.
    _tail = body[calc_except_idx:calc_except_idx + 400]
    assert "errors += 1" in _tail
    assert "raise" not in _tail
    # The send loop keeps its own separate isolation.
    send_call_idx = body.index("send_paper_portfolio_digest_email(")
    send_except_idx = body.index("except Exception as _send_exc:", send_call_idx)
    assert calc_except_idx < send_call_idx < send_except_idx, (
        "metrics are computed for every portfolio BEFORE any email is sent"
    )


def test_paper_portfolio_digest_logs_and_counts_per_recipient_errors_without_reraising():
    body = _paper_portfolio_digest_body()
    assert 'log.warning("paper_portfolio_digest.recipient_send_error"' in body
    assert "errors += 1" in body
    done_log_idx = body.index('log.info("scheduler.paper_portfolio_digest_done"')
    done_log_line = body[done_log_idx:body.index("\n", done_log_idx + 200)]
    assert "errors=errors" in done_log_line


def test_paper_portfolio_digest_dedup_key_is_scoped_per_user_and_market():
    """T372-PORTFOLIO-DIGEST-CONSOLIDATE inverted this test's premise, deliberately.

    It used to require the portfolio id in the dedup key, because there was ONE EMAIL PER
    PORTFOLIO and a user-scoped key would have suppressed every portfolio after the first. The
    user reported the consequence from their own inbox: ten emails at once, five of them
    reading "+0.0% / $0" for portfolios created the day before.

    There is now one email per MARKET carrying every portfolio as a row, so the key must be
    scoped per (user, market, date). Keeping the portfolio id would let a restart re-send the
    same consolidated email once for EVERY portfolio it contains — the exact duplicate-send
    failure the original key existed to prevent, just inverted.

    The invariant is unchanged: one digest per user per market per day, and no silent
    suppression. Only the unit of "a digest" moved.
    """
    body = _paper_portfolio_digest_body()
    assert "{user.id}:{_mkt}:{today_str}" in body
    assert "{user.id}:{p.id}:{today_str}" not in body, "per-portfolio scope would re-send"


def test_paper_portfolio_digest_queries_portfolios_exactly_once_not_per_user():
    """The original code re-executed the `portfolios` query once per user inside the outer
    loop, even though the query has no per-user filter at all (every user gets the identical
    active-portfolio list) — a pure O(n_users) redundant-query waste. The fixed version must
    query it exactly once, before the outer `for user in users:` loop begins."""
    body = _paper_portfolio_digest_body()
    portfolios_query_idx = body.index("PaperPortfolio.is_active.is_(True)")
    outer_loop_idx = body.index("for user in users:")
    assert portfolios_query_idx < outer_loop_idx, (
        "the portfolios query must run BEFORE the outer per-user loop, not inside it"
    )
    # Confirm there's exactly one such query in the whole function body (not one hoisted
    # copy plus a second, still-redundant one left behind by an incomplete fix).
    assert body.count("PaperPortfolio.is_active.is_(True)") == 1
