"""Regression tests for BUG-PAPERPOS-DELISTED-FROZEN.

Stock.delisted (aud14-survivorship) is populated with a real, conservative signal but was
never consumed inside _monitor_positions() — a delisted stock's live quote goes stale, and
the pre-existing staleness-escalation logic (BUG-MONITORPOS-STALEPRICE) only LOGS the
condition, never closes the position. Left alone, the position freezes open at an
increasingly stale price forever, distorting reported equity/P&L indefinitely.

_monitor_positions() itself is not exercised end-to-end here — same rationale as
test_monitor_positions_stale_price.py (200+ lines, heavy Signal/RSI/regime dependencies
disproportionate to this fix's actual scope). These are source-text regression checks on the
specific new logic, matching that file's established precedent for this exact risk class.
"""
import pathlib

_PTE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_SOURCE = _PTE_PATH.read_text()


def _monitor_positions_body() -> str:
    start = _SOURCE.index("def _monitor_positions(")
    end = _SOURCE.index("\ndef ", start + 10)
    return _SOURCE[start:end]


def test_delisted_symbols_are_bulk_fetched_once_per_cycle():
    """Must batch-fetch Stock.delisted for all open symbols in ONE query, matching the
    established batch-fetch pattern every other per-symbol lookup in this function already
    uses (signals/kscores/OBV) — not a per-trade N+1 query."""
    body = _monitor_positions_body()
    assert "delisted_symbols" in body
    assert "Stock.delisted" in body
    fetch_idx = body.index("select(Stock.symbol, Stock.delisted)")
    loop_idx = body.index("for trade in open_trades:")
    assert fetch_idx < loop_idx, "the delisted-symbols fetch must happen BEFORE the per-trade loop, not inside it"


def test_delisted_fetch_fails_open_on_a_db_error():
    """A DB hiccup on this lookup must never crash the whole monitoring cycle for every
    other open trade — matching the fail-open convention every other batch fetch in this
    function already follows."""
    body = _monitor_positions_body()
    fetch_start = body.index("select(Stock.symbol, Stock.delisted)")
    try_idx = body.rindex("try:", 0, fetch_start)
    except_idx = body.index("except Exception", fetch_start)
    assert try_idx < fetch_start < except_idx


def test_delisted_exit_is_checked_before_stop_target_and_signal_logic():
    """A delisted stock has no real market left to compute a meaningful stop/target breach
    against — this must be the FIRST hard-exit condition checked, preempting stop_hit/
    breakeven_stop/target_reached/signal_exit, not competing with them via a later elif."""
    body = _monitor_positions_body()
    delisted_idx = body.index('if trade.symbol in delisted_symbols:')
    stop_check_idx = body.index("elif live_price <= stop:")
    assert delisted_idx < stop_check_idx
    # confirm it's genuinely the first condition in the hard-exit chain, not just present
    # somewhere before it — no other `if`/`elif` for exit_reason appears between them
    between = body[delisted_idx:stop_check_idx]
    assert between.count("exit_reason =") == 1


def test_delisted_exit_sets_the_expected_exit_reason_and_notes():
    body = _monitor_positions_body()
    delisted_block_start = body.index('if trade.symbol in delisted_symbols:')
    delisted_block_end = body.index("elif live_price <= stop:")
    block = body[delisted_block_start:delisted_block_end]
    assert 'exit_reason = "delisted"' in block
    assert '"message":' in block
    assert '"pnl_pct":' in block


def test_delisted_exit_is_logged_at_warning_level_for_visibility():
    body = _monitor_positions_body()
    delisted_block_start = body.index('if trade.symbol in delisted_symbols:')
    delisted_block_end = body.index("elif live_price <= stop:")
    block = body[delisted_block_start:delisted_block_end]
    assert 'log.warning("paper.delisted_auto_exit"' in block
    assert "symbol=trade.symbol" in block
    assert "trade_id=trade.id" in block


def test_delisted_exit_reuses_the_shared_execute_exit_block_not_a_separate_code_path():
    """exit_reason must flow through the SAME "Execute exit" block every other hard exit
    uses (fills, commission, cash credit, signal_outcomes write-back, broker exit routing)
    — not a bespoke shortcut that skips any of that bookkeeping."""
    body = _monitor_positions_body()
    delisted_idx = body.index('if trade.symbol in delisted_symbols:')
    execute_idx = body.index("if exit_reason:", delisted_idx)
    assert delisted_idx < execute_idx
    # the exit_reason variable set inside the delisted branch must be the same one read here —
    # confirm no reassignment/shadowing of exit_reason occurs between the branch and the check
    between = body[delisted_idx:execute_idx]
    assert between.count("exit_reason = None") == 0
