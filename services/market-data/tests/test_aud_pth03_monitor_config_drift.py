"""Regression tests for AUD-PTH03-MONITORCONFIGDRIFT (PT-H03, docs/audits/2026-09-19-paper-
trading-horizon-threshold-audit.md).

`_monitor_positions()` built its own portfolio-level config every cycle with the pre-AUD-DE1-
CONFIGMERGE (2026-09-07) merge — no `_HK_MARKET_OVERRIDES` applied at all, and a style override
silently defeated by a stored value that merely echoes the generic default. The entry path
(`resolve_entry_config()`) was fixed for exactly this defect over two weeks earlier; the
monitoring path never was — confirmed via source read and independently re-derived in this
session's own audit review (docs/audits/2026-09-19-paper-trading-horizon-audit-review.md).

Simply swapping `_monitor_positions()` onto `resolve_entry_config()` was deliberately rejected:
every currently OPEN position's trailing-stop distance, partial-TP triggers, and hold-day exit
are recomputed from that config on EVERY monitoring cycle (not frozen at entry), so a same-pass
resolver swap would silently move real stops out from under open positions the instant the fix
deployed (the audit's own example: an HK trailing ATR multiplier moving 2.0x -> 1.5x mid-trade).

The fix instead: `PaperTrade.exit_config_snapshot` freezes `resolve_entry_config()`'s output at
entry time (the same config `_open_paper_trade()` already sized/priced the trade against).
`_monitor_positions()` reads a new per-trade `_trade_cfg = trade.exit_config_snapshot or cfg`
for every exit-relevant decision — `cfg` (the untouched OLD merge) is kept ONLY as the fallback
for trades with no snapshot (i.e. every trade already open before this deploy, whose monitoring
behavior must stay byte-identical) and for the one pre-loop, portfolio-level ATR-prefetch
heuristic that doesn't itself determine any stop/target level.

`_monitor_positions()` is not exercised end-to-end here — matching this file's own established
precedent (test_monitor_positions_stale_price.py, test_monitor_positions_naive_aware_datetime.py):
200+ lines, heavy Signal/RSI/regime dependencies disproportionate to this fix's actual scope.
These are source-text regression checks on the specific new logic and its call sites.
"""
import pathlib

_PTE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_SOURCE = _PTE_PATH.read_text()

_MODELS_PATH = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_MODELS_SOURCE = _MODELS_PATH.read_text()

# Every _trade_cfg.get(...)/_trade_cfg[...] key that must have moved off the stale portfolio-
# level `cfg` — one entry per real exit-relevant read this fix touches.
_TRADE_CFG_KEYS = (
    "max_hold_days", "hold_stall_days", "hold_stall_max_gain",
    "momentum_exit_enabled", "momentum_exit_min_days", "wait_exit_days",
    "entry_slippage_pct", "size_aware_slippage_enabled", "commission_per_share",
    "partial_tp_pct", "partial_tp2_pct", "trail_trigger_pct", "breakeven_trigger_pct",
    "trail_atr_mult",
)


def _monitor_positions_body() -> str:
    start = _SOURCE.index("def _monitor_positions(")
    end = _SOURCE.index("\ndef ", start + 1)
    return _SOURCE[start:end]


def _per_trade_loop_body() -> str:
    """From `for trade in open_trades:` through the PA-D1 sector-cap monitor comment that
    marks the end of the per-trade loop (confirmed by direct read: this is a second,
    unindented block after the loop, not part of it)."""
    body = _monitor_positions_body()
    start = body.index("for trade in open_trades:")
    end = body.index("# PA-D1: sector cap monitor", start)
    return body[start:end]


def test_trade_cfg_falls_back_to_the_old_portfolio_level_cfg():
    """The exact fallback that keeps every already-open trade's monitoring behavior
    byte-identical to before this fix: no snapshot -> the old (still-buggy) merge, never a
    silently different value."""
    loop = _per_trade_loop_body()
    assert "_trade_cfg = trade.exit_config_snapshot or cfg" in loop
    # Must be the FIRST statement in the loop body, before any exit logic runs.
    loop_line_idx = loop.index("for trade in open_trades:")
    trade_cfg_idx = loop.index("_trade_cfg = trade.exit_config_snapshot or cfg")
    first_hold_days_read = loop.index('_trade_cfg.get("max_hold_days"')
    assert loop_line_idx < trade_cfg_idx < first_hold_days_read


def test_every_exit_relevant_read_inside_the_loop_uses_trade_cfg_not_cfg():
    """Regression guard: every one of these keys must be read via `_trade_cfg.get(`, and NOT
    via a bare `cfg.get(` left over from before the fix, anywhere inside the per-trade loop."""
    loop = _per_trade_loop_body()
    for key in _TRADE_CFG_KEYS:
        assert f'_trade_cfg.get("{key}"' in loop, f"{key} must be read from _trade_cfg"
        assert f'cfg.get("{key}"' not in loop.replace(f'_trade_cfg.get("{key}"', ""), (
            f"{key} must not still be read from the stale portfolio-level cfg"
        )


def test_pre_loop_armed_symbols_heuristic_still_uses_portfolio_level_cfg():
    """The ATR-prefetch heuristic runs BEFORE the loop (no trade to snapshot from yet) and
    only decides which symbols to batch-fetch ATR for — not an actual stop/target level, so it
    is correctly left on the portfolio-level `cfg`, not migrated to a nonexistent `_trade_cfg`."""
    body = _monitor_positions_body()
    pre_loop = body[:body.index("for trade in open_trades:")]
    assert 'trail_trigger = cfg.get("trail_trigger_pct", 0.05)' in pre_loop


def test_post_loop_sector_cap_monitor_still_uses_portfolio_level_cfg():
    """PA-D1's sector-cap monitor runs once per portfolio after the per-trade loop, not
    per-trade — correctly left on `cfg`, matching its own pre-existing, unrelated semantics."""
    body = _monitor_positions_body()
    post_loop = body[body.index("# PA-D1: sector cap monitor"):]
    assert 'max_sector_pct = cfg.get("max_sector_pct"' in post_loop


def test_open_paper_trade_snapshots_the_resolved_config_at_entry():
    """_open_paper_trade() must persist the SAME cfg it already sized/priced the trade
    against, so exit_config_snapshot is never a value the entry decision didn't actually see."""
    start = _SOURCE.index("def _open_paper_trade(")
    end = _SOURCE.index("\ndef ", start + 1)
    body = _SOURCE[start:end]
    assert "exit_config_snapshot  = dict(cfg)," in body
    # Must be inside the same PaperTrade(...) constructor call as entry_reasons, not a
    # separate later assignment that could be skipped by an early return.
    ctor_idx = body.index("trade = PaperTrade(")
    reasons_idx = body.index("entry_reasons         = sig.reasons,", ctor_idx)
    snapshot_idx = body.index("exit_config_snapshot  = dict(cfg),", ctor_idx)
    session_add_idx = body.index("session.add(trade)", ctor_idx)
    assert ctor_idx < reasons_idx < snapshot_idx < session_add_idx


def test_exit_config_snapshot_column_exists_on_papertrade():
    start = _MODELS_SOURCE.index("class PaperTrade(Base):")
    end = _MODELS_SOURCE.index("\n\nclass ", start)
    body = _MODELS_SOURCE[start:end]
    assert "exit_config_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)" in body


def test_migration_017_adds_the_column_and_is_registered():
    mig_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "scripts" / "migrations" / "017_add_exit_config_snapshot_to_paper_trades.sql"
    )
    assert mig_path.exists()
    assert "ADD COLUMN IF NOT EXISTS exit_config_snapshot JSON" in mig_path.read_text()

    run_migrations_path = (
        pathlib.Path(__file__).resolve().parents[3] / "scripts" / "migrations" / "run_migrations.sh"
    )
    run_source = run_migrations_path.read_text()
    idx_016 = run_source.index('run_migration "016_create_paper_entry_scan_logs.sql"')
    idx_017 = run_source.index('run_migration "017_add_exit_config_snapshot_to_paper_trades.sql"')
    assert idx_016 < idx_017, "017 must run after 016, matching this file's ascending order"
