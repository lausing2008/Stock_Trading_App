# What actually finds trading signals: tests and evidence (2026-11-10)

> **Update 2026-11-10:** the four experiments in section 7d were run as one test
> (`combined-signal-results.md`). The combined score failed, momentum's and pullback's results in
> section 3 come almost entirely from using today's stock list, and the +4.19% earnings figure in
> section 5 is a measurement error. Read that document before acting on sections 3, 5 or 7.

Written for the platform owner. The question was: what is the most effective and accurate way
to find a trading signal? This answers it with tests on 993 US stocks from 2005 to 2026, the
platform's own measured history, and the published research. Evidence and scripts:
`signal-research-evidence/`.

**Terms.** *Excess return* is a stock's return over a holding window minus the average return of
all eligible stocks over the same window, after a 0.10% round-trip cost. A signal with no
information therefore reads about −0.10%. *Beat rate* is the share of signals whose stock did
better than that average. Bold figures have a 95% interval that excludes zero.

---

## 1. Bottom line

1. **No simple signal gives a large or "accurate" edge in liquid US stocks.** Across 18
   price-and-volume signals and five earnings-surprise groups, the best honest edges are a few
   tenths of a percent per trade after costs. Every signal's beat rate sits between 45% and 50%.
2. **Chart-pattern signals do not work.** New 52-week highs, volume breakouts, crossing above
   the 200-day average and the golden cross all came out slightly *negative* after costs, on
   tens of thousands of events each. This agrees with the Little RZY result.
3. **What carries information, in order of how well it held up:**
   - **Earnings surprise.** Results line up in order from big miss to big beat. But most of the
     move is in the opening gap (+2.1% on average after a big beat), which cannot be captured.
     What is left after the open is +0.14% over 5 days.
   - **Short-term reversal.** The past week's biggest winners lag over the next week (−0.21%)
     in every period tested. The past week's losers recover over 20 to 60 days (+0.22%, +0.53%).
   - **Momentum, held for months.** The top tenth by 12-month momentum gained +1.25% over
     60 days, but all of it since 2016. Buying a short-term dip inside strong momentum gained
     +0.37% over 20 days, again mostly since 2021.
4. **The platform's own best number did not replicate.** The platform measured +4.19% over
   5 days after a big earnings beat (250 events). On 17,768 events here it is +0.14%, and
   +0.77% on the platform's own symbols since 2024 with an interval that spans zero. That figure
   should be re-checked before anything is built on it (section 5).
5. **The effective way to find signals is a process, not a pattern.** Small, real effects exist.
   They pay only when several are combined into a ranking, traded across many stocks at low
   cost, and each is proven on data it was not designed on. Section 7 sets out that process and
   the next experiments.

---

## 2. How each signal was tested

- **Data:** the Yahoo daily bars fetched for the Little RZY study: 993 symbols (platform
  symbols plus today's S&P 500 and S&P 400), 2005-01-03 to 2026-11-09, split- and
  dividend-adjusted. Earnings: 73,578 announcements for 944 symbols, back to 2002.
- **Timing:** a signal is known at the close of day *t*. The trade is entered at the next open
  and closed at the close 5, 20 or 60 sessions later. An earnings trade is entered at the first
  open after the announcement.
- **Benchmark:** the equal-weight average of every eligible stock over the same window. This
  removes the market's own rise and most of the bias from using only stocks that exist today.
- **Eligibility:** at least 260 prior bars and a 50-day median dollar volume of $5M.
- **Intervals:** 95%, resampling whole calendar months. Three periods are also shown separately
  (2005–2015, 2016–2020, 2021–2026), because a pooled figure can hide a result that has stopped.
- **No selection:** the definitions were committed before any result was computed (f1ea0951 for
  price signals, 69358733 for earnings), and every signal and horizon is reported.

---

## 3. Price and volume signals

Excess return in %, after costs. Events are signal occurrences on eligible stocks.

| Signal | Events | 5 days | 20 days | 60 days | 60 days: 2005–15 / 2016–20 / 2021–26 | Beat rate (20d) |
|---|---|---|---|---|---|---|
| **Momentum and trend** | | | | | | |
| Top tenth by 12-month momentum (monthly) | 19,061 | −0.14 | +0.38 | **+1.25** | −0.44 / +1.19 / **+3.34** | 48.9% |
| Bottom tenth by 12-month momentum (monthly) | 18,812 | +0.19 | +0.18 | **+2.07** † | +0.47 / **+5.93** / +0.97 | 47.6% |
| New 52-week closing high | 68,576 | **−0.14** | **−0.18** | **−0.43** | −0.16 / −0.73 / −0.51 | 47.5% |
| New 52-week closing low | 19,134 | −0.10 | +0.20 | +1.40 † | +0.84 / +3.95 / +0.41 | 49.7% |
| Close crosses above the 200-day average | 49,591 | **−0.11** | **−0.20** | **−0.59** | **−0.61** / **−0.97** / −0.28 | 47.3% |
| Golden cross (50-day over 200-day) | 11,482 | **−0.12** | −0.12 | −0.37 | −0.56 / −0.16 / −0.35 | 47.5% |
| 20-day high on twice normal volume | 29,418 | **−0.19** | **−0.21** | −0.15 | −0.13 / −0.47 / +0.10 | 46.6% |
| **Mean reversion** | | | | | | |
| Dip in an uptrend (RSI(2) under 10, above 200-day) | 102,347 | −0.04 | −0.00 | −0.19 | −0.21 / −0.44 / +0.01 | 47.9% |
| Dip in a downtrend (RSI(2) under 10, below 200-day) | 88,756 | −0.06 | +0.06 | +0.22 | +0.19 / +0.42 / +0.11 | 48.2% |
| Past week's losers (bottom tenth) | 116,499 | +0.07 | **+0.22** | **+0.53** | +0.33 / +0.58 / **+0.74** | 48.7% |
| Past week's winners (top tenth) | 117,785 | **−0.21** | −0.12 | −0.01 | **−0.28** / +0.03 / +0.30 | 47.3% |
| Close below the lower Bollinger band in an uptrend | 38,255 | −0.02 | +0.05 | −0.10 | −0.15 / −0.71 / +0.44 | 48.1% |
| **News proxies (gap on heavy volume)** | | | | | | |
| Gap up 4% on 3× volume, closing strong | 4,122 | −0.16 | +0.31 | **+3.03** † | +0.79 / **+8.89** / +1.14 | 48.9% |
| Gap down 4% on 3× volume, closing weak | 4,490 | +0.06 | +0.15 | **+4.94** † | −0.15 / **+16.83** / +0.76 | 46.9% |
| Gap up 4% on 3× volume, in an uptrend | 3,374 | −0.17 | +0.22 | **+1.33** | +0.42 / **+2.61** / +1.40 | 48.3% |
| **Risk** | | | | | | |
| Lowest fifth by volatility (monthly) | 37,740 | −0.22 | **−0.44** | **−1.31** † | −0.38 / **−2.43** / **−1.54** | 46.8% |
| Highest fifth by volatility (monthly) | 37,989 | +0.10 | **+0.61** | **+2.85** † | +0.99 / **+5.11** / **+3.33** | 48.9% |
| **Combination (named in advance)** | | | | | | |
| Top fifth by momentum and RSI(2) under 10 | 38,473 | +0.10 | **+0.37** | **+0.65** | −0.41 / +0.23 / **+2.22** | 49.5% |

† **Do not trust these.** The stock list holds only companies that exist today. Beaten-down,
gapped-down and highly volatile stocks that later failed are missing, so the survivors' rebounds
make distress look profitable. The 2016–2020 column is dominated by the 2020 crash and recovery.
The low-volatility row is the mirror image of the same bias.

**Reading the table.**

- **Breakout and trend-cross signals are below zero.** The new high, the 200-day cross, the
  golden cross and the volume breakout are each significantly negative at some horizon and
  never positive. Buying strength on the day it becomes visible pays slightly less than buying
  a random stock.
- **Short-term reversal is the most consistent effect.** Winners of the past week lag the next
  week in all three periods. It is small (−0.21%) and is an argument for *not chasing*, more
  than a trade in itself.
- **Momentum is real only at long holds, and only recently in this data.** At 5 days it is
  negative; at 60 days it is positive since 2016 and negative before. Part of the recent figure
  is the same survivor effect, since today's index members are the past decade's winners.
- **Beat rates are all under 50%.** A few large winners pull the average up, so the typical
  stock lags the average even when a signal's mean excess is positive. In absolute terms 53% to
  58% of trades were up over 20 days, which is the rising market, not the signal.

---

## 4. Earnings surprise

55,723 eligible announcements with an EPS estimate of at least $0.10 in size. Entry is the first
open after the announcement, so the opening gap is excluded.

| Surprise | Events | Average gap at entry | 1 day | 5 days | 20 days | 60 days | 5 days: 2005–15 / 2016–20 / 2021–26 |
|---|---|---|---|---|---|---|---|
| Miss worse than −10% | 5,282 | −2.5% | **−0.26** | **−0.31** | **−0.39** | −0.33 | **−0.61** / −0.18 / −0.01 |
| Miss −10% to −2% | 5,204 | | **−0.19** | **−0.27** | **−0.48** | **−1.00** | **−0.46** / −0.20 / −0.05 |
| In line (±2%) | 9,625 | | **−0.14** | **−0.18** | **−0.40** | **−0.95** | **−0.36** / −0.05 / −0.03 |
| Beat +2% to +10% | 17,614 | | +0.01 | −0.06 | −0.17 | **−0.66** | +0.08 / −0.07 / −0.20 |
| Beat above +10% | 17,768 | +2.1% | **+0.13** | **+0.14** | −0.04 | −0.02 | **+0.17** / −0.07 / **+0.26** |
| Beat above +10%, gapped up | 12,654 | | **+0.12** | **+0.17** | +0.05 | −0.02 | **+0.27** / −0.16 / **+0.30** |
| Beat above +10%, gapped down | 5,114 | | **+0.17** | +0.09 | −0.25 | −0.02 | −0.09 / +0.16 / +0.17 |

- **The ordering is clean.** Every step from big miss to big beat does better over 1 and 5
  days. A big beat outperforms a big miss by 0.45% over 5 days. This is the only signal family
  here where the direction of the effect matches the size of the news.
- **The tradeable part is small.** The market moves about 2% at the open. After that, a big
  beat adds +0.14% over 5 days and nothing by 20 days.
- **The short side has faded.** Misses kept falling after the open in 2005–2015 (−0.61% over
  5 days) and no longer do (−0.01% since 2021).
- **Limits:** Yahoo's estimate is the final consensus and its timestamps are sometimes a default
  16:00. A wrong time of day would shift the entry by a session.

---

## 5. The platform's own evidence

| Signal on the platform | What was measured | Source |
|---|---|---|
| AI signals (BUY / SELL) | BUY returned −1.22% and SELL +1.03% on 18,561 outcomes; confidence bands flat (36.5% to 43.9% wins) | `docs/features/signal-engine-pillars.md`, `docs/audits/2026-09-30-three-week-deferred-checkpoint.md` |
| Short Squeeze alert | 13.3% wins on 15 alerts; fires at intraday spike tops | `docs/features/squeeze-and-options-alerts.md` |
| Options flow, bearish arm | 29.1% wins; price rose 4.0% afterwards | `docs/audits/2026-09-30-three-week-deferred-checkpoint.md` |
| Little RZY | No timing edge on 9,000 independent trades | `docs/2026-11-10/rzy-deep-review-and-improvement-results.md` |
| Earnings beat above 10% | +4.19% over 5 days after the open, 250 events | `docs/features/earnings-data-and-forecasts.md` |

Four of the five are flat or inverted. The fifth, the earnings figure, is far larger than the
same test gives here:

| Sample | Events | 5 days after the open |
|---|---|---|
| Platform's figure (raw return) | 250 | +4.19% |
| This study, all stocks (excess, after cost) | 17,768 | **+0.14%** (+0.02 to +0.27) |
| This study, platform symbols | 1,644 | +0.19% (−0.24 to +0.62) |
| This study, platform symbols, 2024–2026 | 455 | +0.77% (−0.34 to +1.86) |

The last three rows are a post-hoc cut. The two measurements differ in ways that could explain
part of the gap: the platform's is a raw return, not net of the market, on a small sample from
a strong period. It may also differ in when it places the entry relative to the announcement.
Until that is checked, +4.19% should not be used as the size of the edge.

---

## 6. What the published research says

Cited from memory, not re-read for this document.

| Finding | Reference | Fits what was measured here? |
|---|---|---|
| Stocks that rose over 3 to 12 months keep outperforming for months | Jegadeesh and Titman (1993) | Yes at 60 days since 2016; not at 5 days |
| Prices drift after earnings surprises | Ball and Brown (1968); Bernard and Thomas (1989) | Direction yes; size much smaller, and gone on the short side |
| Last week's and last month's winners reverse | Jegadeesh (1990); Lehmann (1990) | Yes, the most stable effect here |
| Nearness to the 52-week high predicts returns | George and Hwang (2004) | No: a new high was followed by slightly lower returns |
| Trend following works across asset classes | Moskowitz, Ooi and Pedersen (2012) | Not tested (futures, not single stocks) |
| Published anomalies shrink by about half afterwards | McLean and Pontiff (2016) | Yes: most effects are weaker in the later periods |
| Most reported factors are false discoveries without a higher bar | Harvey, Liu and Zhu (2016) | The reason every test here was fixed in advance |
| Chart patterns carry a little information, not enough to trade after costs | Lo, Mamaysky and Wang (2000); Sullivan, Timmermann and White (1999) | Yes |
| Costs erase most high-turnover anomalies | Novy-Marx and Velikov (2016) | Yes: 0.10% is the size of most effects here |

---

## 7. The most effective way to find signals

### 7a. What to look for

| Source of signal | Why it can work | Evidence here | Priority |
|---|---|---|---|
| New information the market has not fully priced: earnings surprise, guidance, analyst revisions | Investors adjust slowly | Clean ordering, small size | **First.** Needs revision and guidance data, which the platform partly has |
| Slow trends: 6 to 12 month momentum, held for months | Under-reaction, flows | Positive since 2016 | Second, as a ranking input |
| Short-term over-reaction: do not chase the past week's winners; prefer dips inside strength | Liquidity, crowding | Most stable effect | Second, as an entry-timing rule |
| Market state (trend of the index, volatility) | Risk, not prediction | Did not rescue any signal | Use for position size, not for selection |
| Chart patterns, breakouts, moving-average crosses | Nothing the market has not already seen | Zero or negative | **Drop as signals** |

### 7b. How to turn weak effects into something usable

1. **Rank, do not alert.** Score every stock each day on a few independent inputs (surprise or
   revision, momentum, short-term pullback) and act on the top of the list. One input gives
   0.1% to 0.4%; inputs that are not correlated add.
2. **Trade breadth.** With a 49% beat rate, single trades are coin flips. An edge of 0.3% per
   trade shows up only across hundreds of trades.
3. **Hold longer, trade less.** Every effect that survived lives at 20 to 60 days. Costs take
   the same 0.10% whether the hold is an hour or a quarter.
4. **Size by risk, not conviction.** The platform's confidence scores did not separate winners
   from losers. Equal risk per position is the honest default.

### 7c. The standard every new signal should pass

| Rule | Why |
|---|---|
| 1. Write the definition and the pass mark before running it | Stops choosing the winner after the fact |
| 2. Compare with a control (random timing, or the market average) | A rising market makes everything look good |
| 3. Subtract costs | Most effects are the size of costs |
| 4. Test on data the idea was not found on | The Little RZY daily variant passed on design data and failed here |
| 5. Report by period, not pooled | Momentum, the earnings short side and the RZY variant all changed over time |
| 6. Use a universe that includes failed companies | Three "best" rows in section 3 are artefacts without it |
| 7. Run it forward in shadow before any alert | The only test that cannot be fitted |

### 7d. Next experiments, in order

| ID | Experiment | Size | Why first |
|---|---|---|---|
| E1 | Re-check the platform's +4.19% earnings figure: entry time relative to the announcement, and net of the market | Small | The platform treats it as its one real edge |
| E2 | Analyst estimate revisions and guidance changes as a signal, using the stored FMP and earnings data | Medium | The strongest documented information signal not yet tested here |
| E3 | A composite ranking (surprise, momentum, pullback) back-tested as a monthly top-20 portfolio, with a universe that includes delisted stocks | Medium, needs a data source for delisted stocks | Tests whether the small effects add up |
| E4 | Forward shadow run of whatever E3 produces, for three months, with no emails | Small once E3 exists | Rule 7 |

None of these has been started.

---

## 8. Limits

- **Survivors only.** The universe is today's index members and platform symbols. It flatters
  momentum, recovery and high-volatility results. Rows marked † are the worst affected.
- **Liquid US stocks only.** Effects are usually larger in small, illiquid names, where costs
  are also larger. Nothing here covers Hong Kong, options or intraday trading.
- **Costs are an assumption** (0.10% round trip). Results near zero would change sign with a
  different figure.
- **Equal-weight benchmark.** Against a value-weighted index the excess returns would differ.
- **Earnings data is Yahoo's**, with final estimates and imperfect timestamps.
- **Each signal was tested alone**, except one combination named in advance. Section 7b's claim
  that effects add is the standard view, not something measured here; E3 would measure it.

---

## 9. Reproduce

```bash
E=docs/2026-11-10/signal-research-evidence
B=<directory holding the 1d.jsonl.gz from the Little RZY study>
python $E/signal_study.py $B/1d.jsonl.gz $E/results.json
python $E/fetch_earnings.py $B/1d.jsonl.gz $B/earnings.json.gz
python $E/earnings_study.py $B/1d.jsonl.gz $B/earnings.json.gz $E/earnings-results.json
python $E/earnings_posthoc.py $B/1d.jsonl.gz $B/earnings.json.gz docs/2026-11-10/rzy-improvement-evidence/universe.json
```

Outputs are saved beside the scripts (`results.json`, `results.txt`, `earnings-results.json`,
`earnings-results.txt`, `earnings-posthoc.txt`). The earnings file's hash is
`c8083e56…e2c63`; the bars' hashes are in `rzy-improvement-evidence/data-manifest.json`.
