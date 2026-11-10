"""Post-earnings drift on the same 993-stock daily data. Definitions fixed before any result.

- Event: a Yahoo earnings announcement with a reported EPS and |EPS estimate| >= 0.10 (smaller
  estimates make the surprise percentage meaningless).
- Entry: the OPEN of the first session that opens after the announcement time (an after-close
  release is entered next morning; a pre-market release that same morning). The opening gap is
  therefore NOT captured: this measures only what is left after the news is public.
- Exit: the close H sessions later (H = 1, 5, 20, 60), counting the entry session as 1.
- Excess = stock return minus the equal-weight return of all eligible stocks over the same
  window, less 0.10% round-trip cost. Eligibility as in signal_study.py.
- Groups by surprise %: <= -10, -10..-2, -2..+2, +2..+10, > +10. For > +10 the group is also
  split by the sign of the entry gap (entry open vs previous close).
- Intervals: 95%, resampling calendar months. Periods reported separately.

Usage: earnings_study.py <1d.jsonl.gz> <earnings.json.gz> <out.json>
"""
import gzip, json, sys
import numpy as np
import pandas as pd
from signal_study import load, month_ci, COST, PERIODS

HORIZONS = (1, 5, 20, 60)
BINS = (('miss worse than -10%', -1e9, -10), ('miss -10% to -2%', -10, -2), ('in line -2% to +2%', -2, 2), ('beat +2% to +10%', 2, 10), ('beat above +10%', 10, 1e9))


def main(bars_path, earnings_path, out_path):
    d = load(bars_path); o, c, v = d['open'], d['close'], d['volume']
    eligible = (c.notna().rolling(260).sum() >= 260) & ((c * v).rolling(50).median() >= 5e6)
    sessions = c.index; col = {s: k for k, s in enumerate(c.columns)}
    opens = pd.DatetimeIndex([pd.Timestamp(f'{x.date()} 09:30', tz='America/New_York') for x in sessions])
    ev = []
    for symbol, rows in json.load(gzip.open(earnings_path, 'rt')).items():
        if symbol not in col:
            continue
        for ts, est, rep, sur in rows:
            if est is None or sur is None or abs(est) < 0.10:
                continue
            k = opens.searchsorted(pd.Timestamp(ts).tz_convert('America/New_York'), side='right')   # first open strictly after
            if 1 <= k < len(sessions) and bool(eligible.values[k - 1, col[symbol]]):
                ev.append((k, col[symbol], sur))
    ev = np.array(ev, dtype=float); rows = ev[:, 0].astype(int); cols = ev[:, 1].astype(int); sur = ev[:, 2]
    O, C = o.values, c.values
    gap = O[rows, cols] / C[rows - 1, cols] - 1
    months = np.array(sessions.strftime('%Y-%m'))[rows]; dates = sessions[rows]
    out = dict(events=int(len(ev)), cost_round_trip=COST, results={})
    groups = [(name, (sur > lo) & (sur <= hi)) for name, lo, hi in BINS]
    big = groups[-1][1]
    groups += [('beat above +10%, gap up at entry', big & (gap > 0)), ('beat above +10%, gap down at entry', big & (gap <= 0)),
               ('miss worse than -10%, gap down at entry', groups[0][1] & (gap <= 0))]
    for H in HORIZONS:
        fwd = pd.DataFrame(C, index=sessions).shift(-(H - 1)).values / O - 1      # entry open of row k to the close H-1 rows later
        bench = np.nanmean(np.where(eligible.shift(1).fillna(False).values, fwd, np.nan), axis=1)
        ex = fwd[rows, cols] - bench[rows] - COST; ab = fwd[rows, cols] - COST
        for name, mask in groups:
            for label, lo, hi in (('all', '2005-01-01', '2026-12-31'),) + PERIODS:
                m = mask & ~np.isnan(ex) & (dates >= lo) & (dates <= hi)
                if m.sum() < 30:
                    continue
                e, a = ex[m], ab[m]
                out['results'].setdefault(name, {}).setdefault(str(H), {})[label] = dict(
                    events=int(m.sum()), excess_pct=float(100 * e.mean()), excess_ci_pct=[100 * x for x in month_ci(e, months[m])],
                    median_excess_pct=float(100 * np.median(e)), beat_benchmark=float((e > 0).mean()),
                    absolute_pct=float(100 * a.mean()), absolute_win=float((a > 0).mean()), mean_entry_gap_pct=float(100 * gap[m].mean()))
    json.dump(out, open(out_path, 'w'), indent=1)
    print(f"{'group':42s}  H period     events excess%   95% interval    median  beat  abs%  abs win  gap%")
    for name, by_h in out['results'].items():
        for H, by_p in by_h.items():
            for label, r in by_p.items():
                ci = r['excess_ci_pct']
                print(f"{name[:42]:42s} {H:>2s} {label:10s} {r['events']:6d} {r['excess_pct']:+6.2f}  {ci[0]:+6.2f} to {ci[1]:+6.2f} {r['median_excess_pct']:+6.2f} {100 * r['beat_benchmark']:4.1f}% {r['absolute_pct']:+6.2f} {100 * r['absolute_win']:5.1f}% {r['mean_entry_gap_pct']:+5.2f}")


if __name__ == '__main__':
    main(*sys.argv[1:4])
