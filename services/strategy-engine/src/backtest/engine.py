"""Vectorized backtester — single-asset, long-only, next-bar fill.

Signal detected at bar i-1 close, fill at bar i close (1-bar lag eliminates
same-bar look-ahead). Equity curve uses position.shift(1) so the fill bar's
return is excluded; first return captured is from fill close to next close.
Equity returns assume 100% allocation when entry rule fires and flat when exit
fires. This is intentional simplicity for MVP — portfolio-level and multi-asset
testing is a future extension documented in ARCHITECTURE.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..dsl import compute_features, evaluate_rule


@dataclass
class BacktestResult:
    total_return: float
    # T247-STRATEGYENGINE-CAGR-OVERFLOW: cagr can now be None for a degenerate (near-zero-day)
    # backtest range where the annualized value would otherwise overflow to inf.
    cagr: float | None
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    max_drawdown: float
    win_rate: float
    profit_factor: float
    n_trades: int
    equity_curve: list[dict]
    trades: list[dict]
    metrics_raw: dict = field(default_factory=dict)
    benchmark_cagr: float | None = None
    benchmark_total_return: float | None = None
    alpha: float | None = None
    benchmark_equity_curve: list[dict] = field(default_factory=list)


class BacktestEngine:
    def __init__(self, fee_bps: float = 5.0, slippage_bps: float = 2.0):
        self.fee = fee_bps / 1e4
        self.slippage = slippage_bps / 1e4

    def run(self, df: pd.DataFrame, entry_rule: dict, exit_rule: dict | None = None) -> BacktestResult:
        feat = compute_features(df).reset_index(drop=True)
        entries = evaluate_rule(entry_rule, feat).astype(bool)
        exits = evaluate_rule(exit_rule, feat).astype(bool) if exit_rule else None

        position = np.zeros(len(feat), dtype=int)
        entry_prices, exit_prices = [], []

        in_pos = False
        entry_p = 0.0
        trades = []
        # Detect signal at bar i-1, fill at bar i (1-bar look-ahead lag)
        for i in range(1, len(feat)):
            if not in_pos and entries.iloc[i - 1]:
                entry_p = feat["close"].iloc[i] * (1 + self.slippage + self.fee)
                entry_prices.append(entry_p)
                in_pos = True
                trades.append({"entry_ts": str(feat["ts"].iloc[i]), "entry": entry_p})
            elif in_pos and (exits is not None and exits.iloc[i - 1]):
                exit_p = feat["close"].iloc[i] * (1 - self.slippage - self.fee)
                exit_prices.append(exit_p)
                in_pos = False
                trades[-1].update({"exit_ts": str(feat["ts"].iloc[i]), "exit": exit_p, "ret": exit_p / entry_p - 1})
            position[i] = 1 if in_pos else 0

        # Close open position at last bar
        if in_pos:
            exit_p = feat["close"].iloc[-1] * (1 - self.slippage - self.fee)
            exit_prices.append(exit_p)
            trades[-1].update({"exit_ts": str(feat["ts"].iloc[-1]), "exit": exit_p, "ret": exit_p / entry_p - 1})

        # Shift position by 1: fill at bar i close → first return is bar i → bar i+1.
        # Adjust close at entry bars (pay fee) and exit bars (receive fee discount) so
        # the equity curve correctly reflects fee drag rather than using raw close prices.
        adj_close = feat["close"].copy().astype(float)
        for _i in range(1, len(feat)):
            if position[_i] == 1 and position[_i - 1] == 0:   # entry bar
                adj_close.iloc[_i] *= (1.0 + self.slippage + self.fee)
            elif position[_i] == 0 and position[_i - 1] == 1:  # exit bar
                adj_close.iloc[_i] *= (1.0 - self.slippage - self.fee)
        pos_shifted = pd.Series(position).shift(1, fill_value=0).values
        rets = adj_close.pct_change().fillna(0) * pos_shifted
        equity = (1 + rets).cumprod()
        dd = 1 - equity / equity.cummax()

        total_return = float(equity.iloc[-1] - 1) if len(equity) else 0.0
        # T247-STRATEGYENGINE-CAGR-OVERFLOW: years previously floored to 1e-6 for a
        # same-calendar-day (or otherwise near-zero-day) range, so `equity ** (1/years)` raised
        # to the power of up to 1,000,000 — any equity != 1.0 silently overflows to `inf` (a
        # numpy RuntimeWarning, not an exception). `inf`/`nan` is not valid JSON (stdlib
        # json.dumps emits the literal, non-spec-compliant token "Infinity"), breaking the
        # frontend backtest page and corrupting the stored Backtest.cagr row for all future
        # reads. Floor at 1 trading day (1/365.25 years) instead of 1e-6 — still produces a
        # large-but-finite annualized number for genuinely short backtests, and explicitly
        # guard the final result against inf/nan (same None-on-degenerate pattern already used
        # for sharpe/sortino/calmar below) rather than trusting the floor alone to prevent it.
        years = max((feat["ts"].iloc[-1] - feat["ts"].iloc[0]).days / 365.25, 1 / 365.25)
        cagr = (equity.iloc[-1]) ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1.0
        cagr = float(cagr) if np.isfinite(cagr) else None
        # `or 1e-9` does NOT catch NaN — NaN is truthy in Python, so it bypasses `or`.
        # Use explicit NaN + zero checks for all volatility denominators.
        # T237-SE1: the 1e-9 floor below used to feed straight into the sharpe/sortino division,
        # which turns "no variance" (zero trades, or no losing days at all) into an explosion to
        # +-10^7-10^9 instead of a meaningful ratio — e.g. an all-zero return series produced
        # sharpe=-50,000,000.0, and an all-positive-day series produced sortino=2,470,000,000.0.
        # A strict `> 0` check alone is not enough: an all-identical (but nonzero) return series
        # has std() that's floating-point noise (~1e-17), not exactly 0.0, so it still passes
        # `> 0` and still explodes — use a real epsilon threshold, matching the float-noise-bypass
        # fix already applied a few lines below for gross_loss. Return None in the near-zero-
        # variance case, same pattern as the existing calmar None-on-zero-drawdown a few lines
        # below, rather than silently corrupting the stored/displayed ratio.
        _VOL_EPS = 1e-9
        rf_annual = 0.05  # current T-bill rate; sharpe was overstated by ~1pt at rf=0
        _ann_vol_raw = rets.std() * np.sqrt(252)
        sharpe = (
            float((rets.mean() * 252 - rf_annual) / _ann_vol_raw)
            if (not np.isnan(_ann_vol_raw) and _ann_vol_raw > _VOL_EPS) else None
        )
        _sortino_vol_raw = rets[rets < 0].std() * np.sqrt(252)
        sortino = (
            float((rets.mean() * 252 - rf_annual) / _sortino_vol_raw)
            if (not np.isnan(_sortino_vol_raw) and _sortino_vol_raw > _VOL_EPS) else None
        )
        # Return None (not 0.0) for zero-drawdown — 0.0 is indistinguishable from a losing strategy.
        # cagr can be None (see T247-STRATEGYENGINE-CAGR-OVERFLOW above) — calmar is undefined
        # without a real cagr, same as the zero-drawdown case.
        calmar = float(cagr / dd.max()) if (cagr is not None and dd.max() > 0) else None

        wins = [t for t in trades if "ret" in t and t["ret"] > 0]
        losses = [t for t in trades if "ret" in t and t["ret"] <= 0]
        win_rate = len(wins) / len(trades) if trades else 0.0
        gross_win = sum(t["ret"] for t in wins)
        gross_loss = max(-sum(t["ret"] for t in losses), 1e-9)  # max() avoids float-noise bypass that `or` misses
        profit_factor = float(gross_win / gross_loss)

        equity_curve = [
            {"ts": str(t), "equity": float(e)} for t, e in zip(feat["ts"], equity, strict=False)
        ]

        return BacktestResult(
            total_return=round(total_return, 4),
            cagr=round(float(cagr), 4) if cagr is not None else None,
            sharpe=round(sharpe, 4) if sharpe is not None else None,
            sortino=round(sortino, 4) if sortino is not None else None,
            calmar=round(calmar, 4) if calmar is not None else None,
            max_drawdown=round(float(dd.max()), 4),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4),
            n_trades=len(trades),
            equity_curve=equity_curve,
            trades=trades,
            metrics_raw={"ann_vol": float(_ann_vol_raw), "rf_annual": rf_annual},
        )
