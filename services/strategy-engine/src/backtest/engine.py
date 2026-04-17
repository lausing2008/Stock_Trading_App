"""Vectorized backtester — single-asset, long-only, next-bar fill.

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
    cagr: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    n_trades: int
    equity_curve: list[dict]
    trades: list[dict]
    metrics_raw: dict = field(default_factory=dict)


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
        for i in range(1, len(feat)):
            if not in_pos and entries.iloc[i]:
                entry_p = feat["close"].iloc[i] * (1 + self.slippage + self.fee)
                entry_prices.append(entry_p)
                in_pos = True
                trades.append({"entry_ts": str(feat["ts"].iloc[i]), "entry": entry_p})
            elif in_pos and (exits is not None and exits.iloc[i]):
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

        rets = feat["close"].pct_change().fillna(0) * position
        equity = (1 + rets).cumprod()
        dd = 1 - equity / equity.cummax()

        total_return = float(equity.iloc[-1] - 1) if len(equity) else 0.0
        years = max((feat["ts"].iloc[-1] - feat["ts"].iloc[0]).days / 365.25, 1e-6)
        cagr = (equity.iloc[-1]) ** (1 / years) - 1 if equity.iloc[-1] > 0 else -1.0
        ann_vol = rets.std() * np.sqrt(252) or 1e-9
        sharpe = float(rets.mean() * 252 / ann_vol)

        wins = [t for t in trades if "ret" in t and t["ret"] > 0]
        losses = [t for t in trades if "ret" in t and t["ret"] <= 0]
        win_rate = len(wins) / len(trades) if trades else 0.0
        gross_win = sum(t["ret"] for t in wins)
        gross_loss = -sum(t["ret"] for t in losses) or 1e-9
        profit_factor = float(gross_win / gross_loss)

        equity_curve = [
            {"ts": str(t), "equity": float(e)} for t, e in zip(feat["ts"], equity, strict=False)
        ]

        return BacktestResult(
            total_return=round(total_return, 4),
            cagr=round(float(cagr), 4),
            sharpe=round(sharpe, 4),
            max_drawdown=round(float(dd.max()), 4),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4),
            n_trades=len(trades),
            equity_curve=equity_curve,
            trades=trades,
            metrics_raw={"ann_vol": float(ann_vol)},
        )
