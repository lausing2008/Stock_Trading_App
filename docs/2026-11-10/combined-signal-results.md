# Combined signal test — results (2026-11-10)

> **Update 2026-11-10:** the delisted-company gap was partly filled from Unusual Whales
> (`uw-delisted-results.md`). The stock-picking conclusion is unchanged; the earnings drift after a
> big beat measures +0.42% over 5 days with exact announcement times (2022–2026).

Written for the platform owner. It answers whether the four experiments proposed in
`signal-research-what-finds-trading-signals.md` combine into one signal worth using. The plan
and pass criteria were committed before any result (`combined-signal-plan.md`, be03edf7).
Evidence: `combined-signal-evidence/`.

**Terms.** *Excess return* is a portfolio's return minus the equal-weight average of every
eligible stock over the same month, after trading costs. *Point-in-time* means using only the
stocks that were actually in the S&P 500 on that date, not today's list.

---

## 1. Bottom line

1. **The combined score failed all three predeclared checks.** It made +0.40% a month over the
   average stock (interval −0.32% to +1.34%), did worse than momentum on its own, and was below
   zero in 2006–2015 and 2016–2020.
2. **The single inputs that looked strong were produced by choosing stocks with hindsight.**
   Momentum alone looked like +1.52% a month. Using the S&P 500 as it actually stood each month,
   it is **+0.03%**. Restricted to today's S&P 500 members it comes back to +0.94%. The
   difference is the stocks that were picked because they later did well.
3. **No input survives point-in-time membership:** momentum +0.03%, pullback +0.11%, earnings
   surprise −0.07%, analyst revisions −0.12%, combined +0.05% a month. Every interval spans zero.
4. **The platform's +4.19% earnings figure is a measurement error.** For companies that report
   after the close it measures from before the news, so it includes the overnight jump. Measured
   from the first open after the news, the same events give +2.24% over the market (interval
   −0.62% to +5.24%), and +0.63% without five stocks.
5. **Recommendation:** do not build a combined signal, and do not start the forward run (the
   plan allows it only after a pass). More free data will not change the answer; only
   point-in-time data including delisted companies could. The one change worth making now is
   correcting the Earnings Impact figure (section 5).

---

## 2. Why delisted and point-in-time data matter

A backtest can only pick from the stocks in its list. If the list is "companies that exist and
are large today", it quietly knows the future in two ways:

- **It leaves out the failures.** In 2006, 48% of the S&P 500's actual members have no Yahoo
  price history at all: acquired, bankrupt or delisted. Strategies that buy beaten-down or
  volatile stocks look profitable because the ones that went to zero are missing.
- **It includes the future winners early.** A stock that joined the S&P 500 in 2023 because it
  rose tenfold is in today's list, so a 2019 backtest can "buy" it before it was large. Momentum
  ranks it near the top and collects the rise. The platform's own watchlist has the same effect:
  its symbols were chosen because they had done well.

| Universe | Momentum, excess per month | 95% interval |
|---|---|---|
| Today's S&P 500, S&P 400 and platform symbols (the plan's universe) | +1.52% | +0.45% to +2.91% |
| Today's S&P 500 only | +0.94% | +0.25% to +1.73% |
| **The S&P 500 as it stood each month** | **+0.03%** | −0.58% to +0.61% |

Point-in-time membership removes the second problem only. The first remains: former members
without prices are still missing (48% in 2006, 18% in 2020, 3% in 2026). Which way that last
gap pushes is not known, which is why a decision on real money needs a dataset that keeps
delisted companies (for example Norgate or Sharadar).

---

## 3. E1: the platform's earnings figure

The platform's Earnings Impact tab says a beat above 10% is followed by **+4.19% over 5 days
"after the open"** (250 events). Its code (`event-intelligence` `earnings.get_earnings_surprise_impact`)
measures from the close of the first bar **on or after** the report date.

Measured on the platform's own 216 US big-beat events (read from production 2026-11-10), with
the announcement time taken from Yahoo:

| Announcement time | Events | Platform's definition | From the first open after the news, raw | Same, net of the market |
|---|---|---|---|---|
| After the close | 103 | **+6.47%** | +2.42% | +1.99% |
| Before the open | 65 | +0.88% | +2.65% | +2.63% |
| Time unknown | 48 | +3.18% | — | — |
| **All** | 216 | **+4.06%** (reproduces +4.19%) | +2.51% (168 known) | **+2.24%** (−0.62% to +5.24%) |

- **After-close reports:** the "after open" start is the close *before* the news, so the
  figure includes the overnight jump. That is most of the +4%.
- **Before-open reports:** the start is that day's close, so the figure misses the day's move
  after the open; this one understates.
- **The corrected figure is not reliable:** 168 events over 15 months, interval −0.62% to
  +5.24%. Five stocks (VICR, FCEL, SNDK, AAOI, RXT) supply most of it; without them it is
  +0.63%. On 17,768 events across 944 stocks the same measure is +0.14%.

---

## 4. E2 and E3: the combined test

**E2 data.** EPS-estimate revision history is not available free (the platform's
`estimate_snapshots` starts 2026-10-07; Yahoo gives only today's figures). Yahoo's history of
analyst rating changes and price-target changes was used instead: 249,645 records for 963
stocks, from 2012.

**E3 design** (fixed in advance): every month-end, rank each stock on momentum (12 months
excluding the last), pullback (the past week's losers rank highest) and earnings surprise
(latest in 90 days). Average the three ranks, buy the top 20 at the next open, hold a month.
Measure against the equal-weight average of eligible stocks, after costs. 249 months, 2006-01
to 2026-10.

### Results

Excess return per month, after costs, in %. "Beat" is the share of months the portfolio beat
the average stock.

| Portfolio | Plan's universe | 95% interval | Beat | Point-in-time S&P 500 | 95% interval | Beat |
|---|---|---|---|---|---|---|
| **Combined (3 inputs), the tested score** | **+0.40** | −0.32 to +1.34 | 49% | **+0.05** | −0.33 to +0.43 | 50% |
| Momentum alone | +1.52 | +0.45 to +2.91 | 60% | +0.03 | −0.58 to +0.61 | 54% |
| Pullback alone | +1.24 | +0.20 to +2.61 | 51% | +0.11 | −0.38 to +0.60 | 49% |
| Earnings surprise alone | +0.27 | −0.10 to +0.66 | 52% | −0.07 | −0.32 to +0.23 | 45% |
| Combined plus analyst revisions (4 inputs) | +0.10 | −0.40 to +0.60 | 50% | +0.11 | −0.25 to +0.49 | 51% |
| Analyst revisions alone (2013 on) | −0.01 | −0.33 to +0.30 | 50% | −0.12 | −0.42 to +0.26 | 48% |
| Combined, top 50 instead of 20 | +0.29 | −0.15 to +0.81 | 53% | — | | |
| Combined, platform symbols only | +0.14 | −0.31 to +0.62 | 47% | — | | |

Combined score by period (plan's universe): 2006–2015 −0.08, 2016–2020 −0.02, 2021–2026 +1.59.

### The predeclared checks

| Check | Result | Pass? |
|---|---|---|
| 1. Interval of the combined score above zero | −0.32 to +1.34 | **No** |
| 2. Beats each input alone | Beats surprise; loses to momentum (by −1.12%, interval −1.93 to −0.44) and pullback | **No** |
| 3. Positive in every period | Negative in 2006–2015 and 2016–2020 | **No** |
| S1: adding analyst revisions helps (2013 on) | −0.44% a month (−1.61 to +0.30) | No |

**Why combining made it worse.** Momentum favours last year's winners; pullback favours last
week's losers. Averaging the two ranks selects stocks that are middling on both, which keeps
neither effect. Where both effects are themselves mostly hindsight (section 2), this does not
matter.

---

## 5. What to do

| ID | Action | Recommendation |
|---|---|---|
| A1 | Correct the Earnings Impact figure: measure "after open" from the first open after the announcement time, and net of the market | **Do.** The page currently shows a number that includes the overnight jump it claims to exclude. Small change in `event-intelligence`; not made yet |
| A2 | Build the combined score into the platform | Do not |
| A3 | Forward shadow run (E4) | Do not start; the plan allowed it only after a pass |
| A4 | Buy point-in-time data with delisted companies (about $30–60 a month) | Only if you want to keep searching for a stock-picking signal. Every free test so far points the same way |
| A5 | Where effort is better spent | Things that do not need a prediction: position sizing, diversification, costs, and avoiding the signals that lose (the AI BUY signal, squeeze alerts, the options-flow bearish arm, chart-pattern alerts) |

---

## 6. Limits

- Point-in-time membership covers the S&P 500 only, and still lacks prices for former members
  (48% in 2006, falling to 3% in 2026).
- The inputs were chosen after the single-signal study had seen this data. Equal weights and no
  tuning limit that; it would matter if the result had been a pass.
- Earnings timestamps are Yahoo's. The analyst records are rating and price-target changes, not
  EPS-estimate revisions.
- Monthly holding, top 20, equal weight, 0.05% per side. Other holding periods and sizes were not
  tested, by design.
- The point-in-time and today's-S&P-500 runs were added after the primary result was read
  (`posthoc_point_in_time.py`). They explain the result; they are not part of the
  predeclared test.

---

## 7. Reproduce

```bash
E=docs/2026-11-10/combined-signal-evidence
B=<directory with 1d.jsonl.gz, earnings.json.gz, analyst.json.gz>
python $E/fetch_analyst_actions.py $B/1d.jsonl.gz $B/analyst.json.gz
python $E/e1_platform_earnings_check.py $E/platform-earnings-events.csv $B/1d.jsonl.gz $B/earnings.json.gz
cd $E && python combined_study.py $B/1d.jsonl.gz $B/earnings.json.gz $B/analyst.json.gz ../rzy-improvement-evidence/universe.json out
python posthoc_point_in_time.py $B/1d.jsonl.gz $B/earnings.json.gz $B/analyst.json.gz ../rzy-improvement-evidence/universe.json sp500-historical-components.csv
```

Analyst file sha256 `362a8da3…f10272`; S&P 500 membership file (fja05680/sp500, downloaded
2026-11-10) `36326709…d0b54`. The primary run was repeated after the post-hoc option was added
to `combined_study.py` and its results file is byte-identical.
