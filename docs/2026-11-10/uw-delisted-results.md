# Filling the delisted-company gap with Unusual Whales — results (2026-11-10)

Written for the platform owner. It answers whether Unusual Whales data changes the
`combined-signal-results.md` conclusion. Plan and pass marks were committed before the pulled
data was read (`uw-delisted-plan.md`, 7daf0f16). Evidence: `uw-delisted-evidence/`.

---

## 1. Bottom line

1. **Unusual Whales fills part of the gap, not all of it.** It had prices for 122 of the 274
   missing former S&P 500 members. The month-by-month share of real S&P 500 members with prices
   rose from 79% to 90%. Coverage is complete only from about 2022; 2012 is still 75%. The plan
   required at least 95% on average and 90% in every month, so **the stock-picking test does not
   count as a clean test.**
2. **Even so, nothing changed direction.** With the filled data the combined score made +0.02% a
   month over the average S&P 500 stock and momentum +0.24%, both with intervals spanning zero.
   With Yahoo only they were −0.11% and +0.33%. Neither version comes close to passing.
3. **Earnings surprise is the one result that held up.** With Unusual Whales' exact before-open
   and after-close times, a beat above 10% was followed by **+0.42% over 5 days** relative to the
   market, after costs and after the opening jump (interval +0.17% to +0.67%, 4,466 events,
   2022–2026). It passed its predeclared test. It is small: the stock beat the market 52% of the
   time.
4. **What this means in practice:** no stock-selection rule tested here is worth building. The
   earnings drift is real but small. The useful change is to show it correctly on the Earnings
   Impact tab, which currently overstates it about tenfold.

---

## 2. What was pulled

| Item | Result |
|---|---|
| Requests | 2,423, one at a time, 07:22–08:11 UTC; 0 refused, 4 errors; no extra refusals in production ingestion |
| Former members needed (members at some point from 2012, no Yahoo prices) | 274 |
| Returned prices | 122, kept 114 (8 had under 60 bars) |
| Returned nothing | 152, mostly companies that left the index before 2020 (2012–2020: 139 of them) |
| Earnings records | 106,676 rows for 1,267 symbols, back to the 1990s |
| Earnings rows with an EPS estimate and actual | Only from 2022 (about 4,000 a year); none before |

Coverage of the S&P 500 as it stood each month:

| Year | 2012 | 2014 | 2016 | 2018 | 2020 | 2022 | 2024 |
|---|---|---|---|---|---|---|---|
| Yahoo only | 64% | 66% | 71% | 76% | 82% | 86% | 91% |
| **With Unusual Whales** | **75%** | **78%** | **84%** | **89%** | **96%** | **100%** | **100%** |

Unusual Whales looks up history by today's ticker, so companies whose ticker was reused or that
left years ago tend to return nothing.

---

## 3. The stock-picking test (U1, U2)

S&P 500 as it stood each month, 2012-01 to 2026-10 (178 months). Excess return per month over
the equal-weight average of members with prices, after costs.

| Portfolio | With Unusual Whales | 95% interval | Yahoo only | 95% interval |
|---|---|---|---|---|
| **Combined score (momentum, pullback, surprise)** | **+0.02%** | −0.37 to +0.39 | −0.11% | −0.50 to +0.28 |
| Momentum alone | +0.24% | −0.46 to +0.93 | +0.33% | −0.38 to +1.03 |
| Pullback alone | +0.08% | −0.50 to +0.65 | +0.10% | −0.49 to +0.67 |
| Combined plus analyst revisions | +0.19% | −0.19 to +0.57 | +0.14% | −0.26 to +0.53 |
| Earnings surprise alone (2022 on only) | +0.13% | −0.68 to +0.97 | +0.09% | −0.61 to +0.84 |

By period, combined score with Unusual Whales: 2012–2016 −0.16%, 2017–2021 +0.04%, 2022–2026
+0.18%. Momentum: −0.07%, −0.39%, +1.21%. Momentum's only positive stretch is the most recent
one, where coverage is complete. On its own that is a reason to keep watching momentum, not a
result.

| Predeclared check (U1) | Result |
|---|---|
| Interval above zero | No |
| Beats each input alone | No (momentum, pullback and surprise each did better) |
| Positive in each period | No (2012–2016 negative) |
| U2: momentum interval above zero | No |

The earnings input to the combined score exists only from 2022 (section 2), so before then it
was neutral for every stock. That weakens the combined score as a test of combining, and is a
second reason this run is not decisive.

---

## 4. Earnings drift with correct announcement times (U3)

Entry at the first open after the announcement (before-open reports: that morning; after-close
reports: the next morning). Excess over the equal-weight universe, after 0.10% round trip.
2022–2026, the years with estimates.

| Surprise | Events | Opening jump (not captured) | 1 day | **5 days** | 20 days | Beat the market (5d) |
|---|---|---|---|---|---|---|
| Beat above +10% | 4,466 | +2.3% | +0.26% (+0.12 to +0.40) | **+0.42% (+0.17 to +0.67)** | +0.30% (−0.11 to +0.69) | 52.2% |
| In line (±2%) | 2,835 | −0.8% | −0.04% | −0.09% | −0.61% | 47.8% |
| Miss worse than −10% | 2,767 | −1.4% | −0.17% | −0.01% | +0.28% | 47.5% |

- **U3 passes:** the 5-day interval is above zero.
- **Comparison:** the Yahoo-based study gave +0.14% over all years and +0.26% for 2021–2026, so
  this agrees in direction and is somewhat larger with exact times.
- **Only beats drift.** Misses no longer keep falling after the open, as in the earlier study.
- **Size:** about 900 such beats a year in this universe; +0.42% each before any losing streaks.
  It is a modest tilt, not a strategy on its own.

---

## 5. Deviation found after the run

The planned split rule (an overnight jump near a common split ratio is treated as a split)
misread real crashes as splits: First Republic on 2023-03-13 and 03-16, FMC on 2025-02-05,
SolarEdge on 2023-10-20, and Abiomed's takeover jump on 2022-11-01. That would have hidden
exactly the failures this data was fetched for. The whole study was re-run with no split
adjustment (`posthoc_no_split_adjust.py`). Every figure in sections 3 and 4 was identical to two
decimals, so these stocks never entered a top-20 portfolio or an earnings window. The rule should
not be reused.

---

## 6. Recommendations

| ID | Recommendation | Why |
|---|---|---|
| A1 | **Fix the Earnings Impact tab** using Unusual Whales' `report_time`: measure from the first open after the announcement and net of the market. Expected to show roughly +0.4% instead of +4.19% | The tab overstates the drift about tenfold |
| A2 | Optionally, a research-only earnings-beat watch: after a beat above 10%, record the 5-day excess per event, no emails, read after 3 months | The one effect that passed; a forward record is the clean check |
| A3 | Do not build a stock-selection or combined signal | Nothing passed with or without the filled data |
| A4 | Paid point-in-time data | Only needed to settle 2012–2019 momentum. Unusual Whales cannot: it lacks most companies that left before 2020 |

None of these has been done.

---

## 7. Reproduce

```bash
E=docs/2026-11-10/uw-delisted-evidence
# 1. pull (inside the market-data container, outside US market hours)
python uw_pull.py pull-list.json earnings-symbols.json <out> 12:30
# 2. study (workstation)
python $E/uw_study.py <yahoo 1d.jsonl.gz> <pull out> <analyst.json.gz> docs/2026-11-10/rzy-improvement-evidence/universe.json \
    docs/2026-11-10/combined-signal-evidence/sp500-historical-components.csv <work>
python docs/2026-11-10/signal-research-evidence/earnings_study.py <work>/merged-1d.jsonl.gz <work>/uw-earnings.json.gz <out.json>
```

Hashes of the pulled and merged files are in `uw-delisted-evidence/local-data-manifest.json`.
The run output tables (`uw-results.txt`) carry the period labels of the original script's header
(2006-15, 2016-20, 2021-26); the columns are 2012–2016, 2017–2021 and 2022–2026.
