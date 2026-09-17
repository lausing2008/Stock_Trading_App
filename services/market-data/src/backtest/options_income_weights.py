"""T399-WEIGHTDERIV — re-derive quality_score's weights from settled outcomes.

WHY. The 40/40/20 yield/cushion/liquidity split was a documented judgement call, not evidence.
The first real backtest (417 settled trades) showed it does NOT order win rate: 85+ scored
67.9% while 70-85 scored 71.7% and 50-70 scored 66.2% — no ordering at all. Its components do
carry signal individually (cushion predicts assignment monotonically, 66.7% -> 19.4%; open
interest predicts win rate, 58.1% vs 70.5%), which suggests the weights combine them badly
rather than the inputs being worthless.

HOW, and the discipline that makes the answer trustworthy rather than fitted:

  * CHRONOLOGICAL split, never random. Time-series data split at random leaks the future into
    training. Older `train_frac` of ENTRY DATES trains, the newer remainder is held out.
  * The objective is what the engine actually does: re-rank each date's candidates under a
    candidate weight set, take the top `max_per_date` (the live daily cap), and measure the
    realised mean return on collateral of exactly those trades. Optimising correlation instead
    would reward ordering trades the engine never takes.
  * The incumbent 40/40/20 is evaluated on the SAME held-out slice, so the comparison is like
    for like.
  * A new weight set is only recommended if it beats the incumbent OUT OF SAMPLE by a real
    margin — mirroring `_passes_promotion_margin`'s own rule in gate_harness.py, which exists
    because BUG233-BACKTESTHARNESS-COINFLIP promoted a coin flip once already.
  * Saturation points (_Q_FULL_*) are deliberately NOT searched. Fitting those too would
    multiply the search space and is exactly how a study this size starts fitting noise; only
    the three weights move.

KNOWN RESIDUAL BIAS, stated rather than buried: the candidate pool comes from the archive's own
captured symbols, and greeks are only ~47.6% populated in that archive (UW computes them only
for contracts that traded), so the pool skews toward contracts that saw real volume. The
per-symbol reduction bias IS removed — the study runs with `all_contracts=True` precisely so
the pool is not pre-filtered by the incumbent weights it is being compared against.
"""
from __future__ import annotations

from datetime import date
from statistics import mean

import structlog
from sqlalchemy.orm import Session

from ..services.options_income_engine import (
    _Q_FULL_YIELD_PCT, _Q_FULL_CUSHION_PCT, _Q_FULL_OPEN_INTEREST,
    _Q_WEIGHT_YIELD, _Q_WEIGHT_CUSHION, _Q_WEIGHT_LIQUIDITY,
    leverage_factor,
)
from .options_income_backtest import backtest_options_income

log = structlog.get_logger()

# Minimum out-of-sample edge before a new weight set is worth adopting, in percentage points of
# mean return on collateral per trade. Below this the difference is not distinguishable from
# which half-year happened to land in the test slice.
_MIN_OOS_MARGIN_PP = 0.10


def _score_with(w_yield: float, w_cushion: float, w_liq: float, t: dict) -> float:
    """quality_score() recomputed under candidate weights, using the trade's OWN recorded
    inputs. Mirrors the live formula exactly — same saturation points, same leverage penalty —
    so the only thing varying across the search is the three weights."""
    y = min(max(t["annualized_yield_pct"], 0.0) / _Q_FULL_YIELD_PCT, 1.0)
    c = min(max(t["otm_cushion_pct"], 0.0) / _Q_FULL_CUSHION_PCT, 1.0)
    liq = min(max(t["open_interest"] or 0, 0) / _Q_FULL_OPEN_INTEREST, 1.0)
    raw = 100.0 * (w_yield * y + w_cushion * c + w_liq * liq)
    return raw * leverage_factor(t["symbol"])


def _evaluate(trades: list[dict], weights: tuple[float, float, float], max_per_date: int) -> dict:
    """Mean return on collateral of the trades these weights would actually have selected.

    Groups by entry date and takes the top N under the candidate ranking — the same shape as a
    real run, where only the day's best few are ever opened.
    """
    by_date: dict[str, list[dict]] = {}
    for t in trades:
        by_date.setdefault(t["entry_date"], []).append(t)

    picked: list[dict] = []
    for _d, day in by_date.items():
        ranked = sorted(day, key=lambda t: _score_with(*weights, t), reverse=True)
        picked.extend(ranked[:max_per_date])

    if not picked:
        return {"n": 0, "mean_return_pct": None, "win_rate_pct": None,
                "assignment_rate_pct": None, "total_pnl": 0.0}
    rets = [t["return_on_collateral_pct"] for t in picked if t["return_on_collateral_pct"] is not None]
    return {
        "n": len(picked),
        "mean_return_pct": round(mean(rets), 4) if rets else None,
        "win_rate_pct": round(sum(1 for t in picked if t["pnl"] > 0) / len(picked) * 100, 1),
        "assignment_rate_pct": round(sum(1 for t in picked if t["assigned"]) / len(picked) * 100, 1),
        "total_pnl": round(sum(t["pnl"] for t in picked), 2),
    }


def _weight_grid(step: float = 0.05) -> list[tuple[float, float, float]]:
    """All (yield, cushion, liquidity) triples on a `step` grid summing to 1.0."""
    n = int(round(1.0 / step))
    out = []
    for i in range(n + 1):
        for j in range(n + 1 - i):
            k = n - i - j
            out.append((round(i * step, 4), round(j * step, 4), round(k * step, 4)))
    return out


def derive_quality_weights(
    session: Session,
    start: date,
    end: date,
    symbols: list[str] | None = None,
    step_days: int = 7,
    max_per_date: int = 3,
    train_frac: float = 0.70,
    grid_step: float = 0.05,
) -> dict:
    """Search weights on the older `train_frac` of entry dates, then report the winner and the
    incumbent side by side on the held-out remainder. Returns a recommendation, never applies
    one — adopting new weights changes live trading behaviour and stays an explicit decision.
    """
    bt = backtest_options_income(
        session, start=start, end=end, symbols=symbols, step_days=step_days,
        max_per_date=10_000,   # settle EVERY qualifying candidate; the ranking is applied later
        all_contracts=True,    # and do not let the incumbent score pre-filter the pool
    )
    trades = bt.get("trades", [])
    if len(trades) < 50:
        return {"error": f"only {len(trades)} settled trades — too few to derive weights from",
                "trades_settled": len(trades)}

    dates = sorted({t["entry_date"] for t in trades})
    split_at = dates[int(len(dates) * train_frac)]
    train = [t for t in trades if t["entry_date"] < split_at]
    test = [t for t in trades if t["entry_date"] >= split_at]
    if not train or not test:
        return {"error": "chronological split produced an empty slice", "trades_settled": len(trades)}

    incumbent = (_Q_WEIGHT_YIELD, _Q_WEIGHT_CUSHION, _Q_WEIGHT_LIQUIDITY)

    scored = []
    for w in _weight_grid(grid_step):
        r = _evaluate(train, w, max_per_date)
        if r["mean_return_pct"] is not None:
            scored.append((r["mean_return_pct"], w, r))
    if not scored:
        return {"error": "no weight set produced evaluable trades on the train slice"}
    scored.sort(key=lambda x: x[0], reverse=True)
    best_ret, best_w, best_train = scored[0]

    best_test = _evaluate(test, best_w, max_per_date)
    incumbent_train = _evaluate(train, incumbent, max_per_date)
    incumbent_test = _evaluate(test, incumbent, max_per_date)

    oos_margin = None
    if best_test["mean_return_pct"] is not None and incumbent_test["mean_return_pct"] is not None:
        oos_margin = round(best_test["mean_return_pct"] - incumbent_test["mean_return_pct"], 4)

    recommend = bool(oos_margin is not None and oos_margin >= _MIN_OOS_MARGIN_PP)

    return {
        "window": [start.isoformat(), end.isoformat()],
        "pool_size": len(trades),
        "train_dates": [dates[0], split_at], "test_dates": [split_at, dates[-1]],
        "train_trades": len(train), "test_trades": len(test),
        "incumbent_weights": {"yield": incumbent[0], "cushion": incumbent[1], "liquidity": incumbent[2]},
        "best_weights": {"yield": best_w[0], "cushion": best_w[1], "liquidity": best_w[2]},
        "incumbent_train": incumbent_train, "incumbent_test": incumbent_test,
        "best_train": best_train, "best_test": best_test,
        "oos_margin_pp": oos_margin,
        "min_oos_margin_pp": _MIN_OOS_MARGIN_PP,
        "recommend_change": recommend,
        "verdict": (
            f"ADOPT {best_w}: beats incumbent out of sample by {oos_margin}pp"
            if recommend else
            f"KEEP incumbent: best train weights {best_w} beat it out of sample by "
            f"{oos_margin}pp, under the {_MIN_OOS_MARGIN_PP}pp margin required — "
            f"an in-sample win that does not survive the held-out slice is a fit, not an edge"
        ),
        # Top train results, to show whether the objective surface is flat (many near-equal
        # weight sets = the weights barely matter) or genuinely peaked.
        "train_leaderboard": [
            {"weights": {"yield": w[0], "cushion": w[1], "liquidity": w[2]},
             "mean_return_pct": r, "n": s["n"]}
            for r, w, s in scored[:8]
        ],
    }
