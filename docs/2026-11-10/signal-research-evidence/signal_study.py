"""Which simple, well-known signal families carry information on daily US stock bars?

Research only. Input: the Yahoo daily bars fetched for the Little RZY study (993 symbols,
2005-01-03 to 2026-11-09, adjusted). Every definition below was fixed before any result was
computed; every signal and horizon is reported, nothing is selected.

Method
- A signal is known at the close of day t. The trade is entered at the OPEN of t+1 and closed at
  the close H sessions later (H = 5, 20, 60).
- Excess return = the stock's return over that window minus the equal-weight average return of
  every eligible stock over the same window. This removes the market's drift and most of the
  bias from using only stocks that exist today. Costs (0.10% round trip) are subtracted from the
  stock's side only.
- Eligible on day t: at least 260 prior bars and a 50-day median dollar volume of $5M or more.
- Event signals keep an event only if the same stock had none in the previous 5 sessions.
- Intervals: 95%, resampling whole calendar months (events in one month share the market).
- Periods: 2005-2015, 2016-2020, 2021-2026, reported separately as well as together.

Usage: signal_study.py <1d.jsonl.gz> <out.json>
"""
import gzip, json, sys
import numpy as np
import pandas as pd

COST = 0.0010
HORIZONS = (5, 20, 60)
PERIODS = (('2005-2015', '2005-01-01', '2015-12-31'), ('2016-2020', '2016-01-01', '2020-12-31'), ('2021-2026', '2021-01-01', '2026-12-31'))


def load(path):
    cols = {k: {} for k in ('open', 'high', 'low', 'close', 'volume')}
    with gzip.open(path, 'rt') as f:
        for line in f:
            row = json.loads(line)
            idx = pd.to_datetime([b[0][:10] for b in row['bars']])
            for k, j in (('open', 2), ('high', 3), ('low', 4), ('close', 5), ('volume', 6)):
                cols[k][row['symbol']] = pd.Series([b[j] for b in row['bars']], index=idx)
    return {k: pd.DataFrame(v).sort_index() for k, v in cols.items()}


def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def first_only(sig, gap=5):
    """Keep an event only when the stock had none in the previous `gap` sessions."""
    prior = sig.shift(1).rolling(gap, min_periods=1).max().fillna(0).astype(bool)
    return sig & ~prior


def signals(d):
    o, h, l, c, v = d['open'], d['high'], d['low'], d['close'], d['volume']
    ret1 = c.pct_change(fill_method=None)
    sma = lambda n: c.rolling(n).mean()
    sma50, sma200 = sma(50), sma(200)
    vol50 = v.rolling(50).mean()
    high252 = c.rolling(252).max(); low252 = c.rolling(252).min()
    eligible = (c.notna().rolling(260).sum() >= 260) & ((c * v).rolling(50).median() >= 5e6)
    month_end = pd.Series(c.index.to_period('M'), index=c.index); month_end = month_end != month_end.shift(-1)
    me = pd.DataFrame(np.repeat(month_end.values[:, None], c.shape[1], axis=1), index=c.index, columns=c.columns)
    mom = c.shift(21) / c.shift(252) - 1                      # 12-1 month momentum
    vol60 = ret1.rolling(60).std()
    rank = lambda x: x.where(eligible).rank(axis=1, pct=True)
    mom_rank, vol_rank, rev_rank = rank(mom), rank(vol60), rank(c / c.shift(5) - 1)
    up_trend = c > sma200
    gap = o / c.shift(1) - 1
    bb_mid, bb_sd = sma(20), c.rolling(20).std(ddof=0)
    new_high = (c >= high252) & (c.shift(1) < high252.shift(1))
    new_low = (c <= low252) & (c.shift(1) > low252.shift(1))
    s = {
        # Momentum and trend
        'momentum_top_decile (monthly, 12-1 month)': me & (mom_rank >= 0.9),
        'momentum_bottom_decile (monthly)': me & (mom_rank <= 0.1),
        'new_52_week_closing_high': first_only(new_high),
        'new_52_week_closing_low': first_only(new_low),
        'cross_above_200_day_average': first_only((c > sma200) & (c.shift(1) <= sma200.shift(1))),
        'golden_cross_50_over_200': first_only((sma50 > sma200) & (sma50.shift(1) <= sma200.shift(1))),
        'breakout_20_day_high_on_2x_volume': first_only((c > c.shift(1).rolling(20).max()) & (v > 2 * vol50.shift(1))),
        # Mean reversion
        'pullback_in_uptrend (RSI2 < 10, above 200-day)': first_only((rsi(c, 2) < 10) & up_trend),
        'oversold_below_200_day (RSI2 < 10, below 200-day)': first_only((rsi(c, 2) < 10) & ~up_trend),
        'five_day_losers_bottom_decile': first_only(rev_rank <= 0.1),
        'five_day_winners_top_decile': first_only(rev_rank >= 0.9),
        'close_below_lower_bollinger_in_uptrend': first_only((c < bb_mid - 2 * bb_sd) & up_trend),
        # News / earnings proxies from price and volume
        'gap_up_4pct_on_3x_volume_closing_strong': first_only((gap >= 0.04) & (v >= 3 * vol50.shift(1)) & (c > o)),
        'gap_down_4pct_on_3x_volume_closing_weak': first_only((gap <= -0.04) & (v >= 3 * vol50.shift(1)) & (c < o)),
        'gap_up_4pct_on_3x_volume_in_uptrend': first_only((gap >= 0.04) & (v >= 3 * vol50.shift(1)) & (c > o) & up_trend),
        # Risk
        'low_volatility_bottom_quintile (monthly)': me & (vol_rank <= 0.2),
        'high_volatility_top_quintile (monthly)': me & (vol_rank >= 0.8),
        # Combination named in advance: strong 12-1 momentum AND a short-term pullback
        'momentum_top_quintile_and_pullback (RSI2 < 10)': first_only((mom_rank >= 0.8) & (rsi(c, 2) < 10)),
    }
    return {k: (x & eligible).fillna(False) for k, x in s.items()}, eligible


def month_ci(values, months, n=2000, seed=11):
    codes, _ = pd.factorize(months); k = codes.max() + 1
    if k < 6:
        return [None, None]
    sums = np.bincount(codes, weights=values, minlength=k); counts = np.bincount(codes, minlength=k)
    pick = np.random.default_rng(seed).integers(0, k, size=(n, k))
    means = sums[pick].sum(1) / counts[pick].sum(1)
    return [float(x) for x in np.percentile(means, [2.5, 97.5])]


def main(path, out_path):
    d = load(path)
    sig, eligible = signals(d)
    o, c = d['open'], d['close']
    months = np.array(c.index.strftime('%Y-%m'))
    out = dict(symbols=int(c.shape[1]), first=str(c.index[0].date()), last=str(c.index[-1].date()), cost_round_trip=COST, results={})
    for H in HORIZONS:
        fwd = c.shift(-H) / o.shift(-1) - 1
        bench = fwd.where(eligible).mean(axis=1)
        excess = fwd.sub(bench, axis=0) - COST
        absolute = fwd - COST
        for name, s in sig.items():
            rows, cols = np.where(s.values)
            ex = excess.values[rows, cols]; ab = absolute.values[rows, cols]
            ok = ~np.isnan(ex); rows, ex, ab = rows[ok], ex[ok], ab[ok]
            dates = c.index[rows]
            for label, lo, hi in (('all', '2005-01-01', '2026-12-31'),) + PERIODS:
                m = (dates >= lo) & (dates <= hi)
                if m.sum() < 30:
                    continue
                e, a = ex[m], ab[m]
                out['results'].setdefault(name, {}).setdefault(str(H), {})[label] = dict(
                    events=int(m.sum()), excess_pct=float(100 * e.mean()), excess_ci_pct=[None if x is None else 100 * x for x in month_ci(e, months[rows[m]])],
                    beat_benchmark=float((e > 0).mean()), absolute_pct=float(100 * a.mean()), absolute_win=float((a > 0).mean()),
                    median_excess_pct=float(100 * np.median(e)))
    json.dump(out, open(out_path, 'w'), indent=1)
    print(f"{'signal':52s} H  period     events  excess%   95% interval      beat  abs%   abs win")
    for name, by_h in out['results'].items():
        for H, by_p in by_h.items():
            for label, r in by_p.items():
                ci = r['excess_ci_pct']
                print(f"{name[:52]:52s} {H:>2s} {label:10s} {r['events']:6d} {r['excess_pct']:+7.2f}  {ci[0]:+6.2f} to {ci[1]:+6.2f}  {100 * r['beat_benchmark']:4.1f}% {r['absolute_pct']:+6.2f} {100 * r['absolute_win']:5.1f}%")


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
