# SCOPING: Quantitative Economic-Moat Score

**Date:** 2026-09-06
**Question:** replace/augment the existing LLM-generated `moat_rating` with a deterministic,
fundamentals-derived moat score (the way Morningstar's own *Quantitative* moat model works —
persistence of excess returns, margin durability, pricing power).

**Verdict up front: NOT buildable as designed today. The blocker is data history depth, and it is
a hard blocker, not a difficulty.** A genuine persistence-based moat score needs 5-10 years of
ROIC/margin history. Production has **~3 months**. What IS buildable now is a narrower
"current-quality score" that should not be called a moat score, plus a data-ingestion prerequisite
that unlocks the real thing in 12-24 months of accumulation (or immediately, via a backfill).

---

## 1. The blocker — real production data depth (queried directly, 2026-09-06)

| Table | Span | Distinct dates | Rows | Symbols |
|---|---|---|---|---|
| `fundamentals` | 2026-06-16 → 2026-09-07 | 76 | 10,688 | 173 |
| `fundamentals_snapshot` | 2026-06-30 → 2026-09-06 | 11 (weekly) | 1,751 | 173 |
| `earnings_events` | **2022-11-09 → 2026-12-03** | **206** | 789 | — |

Input coverage within `fundamentals` is actually good — `return_on_equity` 8,622/10,688 (81%),
`gross_margin` 9,136 (85%), `return_on_assets` 9,021 (84%). **Coverage is not the problem; time
depth is.**

`fundamentals_snapshot`'s own docstring (`shared/db/models.py:1493-1499`) confirms the design
intent: it is a *weekly forward-accumulating* snapshot for revision-momentum tracking, and
`models.py:1510-1513` states plainly that "History accumulates going forward only — rows before
this column existed have NULL here." It was never intended as a multi-year financial-statement
archive.

### Why 3 months cannot produce a moat score

A moat is, definitionally, **persistence of excess returns on capital across a full business
cycle**. Measuring it requires observing whether high ROIC/margins *survive* competition,
recessions, and capex cycles. Over 12 weeks:

- ROE/margin values barely move (they're derived from the same trailing-twelve-month filings)
- There is no cycle, no recession, no competitive-response window in the sample
- Any "stability" metric computed over 11 weekly points is measuring **reporting-cadence
  artifacts, not durability** — the same TTM figure repeated ~11 times reads as perfect stability

Building a score on that would produce a confident-looking number with no informational content —
precisely the failure mode this codebase's own audit history repeatedly warns about (see the
2026-09-05 retractions on "defensive skill" and "insider_score predicts returns", both of which
reversed once the sample widened).

---

## 2. What the existing LLM `moat_rating` actually does (and why replacing it is low-risk)

- **Generated:** `services/research-engine/src/api/routes.py:411` — an LLM (Claude/DeepSeek)
  judgment returning Very Strong / Strong / Moderate / Weak / None.
- **Consumed in exactly two places:**
  1. `services/research-engine/src/scoring.py:701,712` — a single pass/warning/fail checklist item
     ("Clear competitive moat?").
  2. `frontend/src/pages/research/[symbol].tsx:625-627` — a `MoatBadge` plus explanation text.
- **Grep confirms zero other consumers** across `services/` and `shared/` outside research-engine.

**Critically: no trading logic depends on it.** It does not feed the K-Score, any entry gate, the
decision engine, or paper trading. That makes introducing a quantitative sibling score genuinely
low-risk — there is no live behavior to regress.

Worth noting the platform already self-assesses this gap honestly: improvements tracker entry
(tier 226-era, `improvements.tsx:13702`) rates fundamental research depth **4/10**, explicitly
citing "categorically shallower than Morningstar's human-analyst moat/fair-value framework."

---

## 3. Is ROIC computable? No — and this is the second real gap

`ROIC = NOPAT / Invested Capital`. `fundamentals`'s full column list is:

```
trailing_pe forward_pe price_to_book gross_margin profit_margin return_on_equity
return_on_assets revenue_growth earnings_growth free_cashflow market_cap
short_percent_of_float short_ratio recommendation_mean number_of_analysts
peg_ratio debt_to_equity dividend_yield target_price
```

**Missing for a real ROIC:** operating income / EBIT, tax rate, total debt (absolute), total
equity (absolute), cash & equivalents. `debt_to_equity` is a *ratio*, which cannot reconstruct the
absolute invested-capital denominator.

**Best available proxies, in descending order of usefulness:**

| Proxy | Available? | Caveat |
|---|---|---|
| `return_on_equity` | Yes (81%) | Leverage-inflated — a highly levered weak business can post high ROE. Must be paired with `debt_to_equity` to be meaningful. |
| `return_on_assets` | Yes (84%) | Leverage-neutral, so a better moat proxy than ROE, but penalizes asset-light businesses (exactly the ones most likely to *have* a moat). |
| `gross_margin` | Yes (85%) | The single best available pricing-power signal. |
| `profit_margin` | Yes | Captures operating efficiency + pricing power together. |
| `free_cashflow` | Yes | Absolute, not scaled — needs `market_cap` or revenue to normalize. |

---

## 4. What IS buildable today — and what to call it

A **`quality_score`** (explicitly NOT a moat score) from current-snapshot fundamentals:

```
gross_margin           — pricing power proxy         (highest weight)
return_on_assets       — leverage-neutral returns
return_on_equity       — returns, cross-checked against debt_to_equity
profit_margin          — operating efficiency
fcf / market_cap       — cash conversion
debt_to_equity         — inverted: balance-sheet fragility penalty
```

This is a legitimate, useful cross-sectional quality ranking. It is **not** a moat score, because
it measures *level*, not *persistence*. Naming it honestly is the whole point — calling a
level-based score a "moat" score is the exact overstatement that makes it untrustworthy.

**Estimated effort:** S-M. One new scoring module, ~6 inputs, following the K-Score conventions
below. No new data ingestion required.

### Conventions any new score must follow (from `kscore.py`)

The K-Score establishes a pattern worth mirroring exactly:

- `_WEIGHTS` dict of hardcoded defaults (`kscore.py:31-38`)
- Optional live Redis override from a tuning endpoint (`_KSCORE_WEIGHTS_REDIS_KEY`,
  `kscore.py:40`), read via `_load_active_weights()` (`kscore.py:115`)
- **Strict validation:** the override is rejected unless its key set exactly matches `_WEIGHTS`
  (`kscore.py:135`) — no partial overrides
- **Always returns a fresh dict**, never the module-level object (`kscore.py:125-130`), with an
  explicit comment that a caller mutating it would permanently corrupt the defaults
- Curve-shape constants separated into their own `_CURVE_DEFAULTS` with a parallel Redis override
  (`kscore.py:49-71`), each defaulting to the original literal so existing callers are
  byte-identical
- Falsy-zero discipline: a genuine `0.0` margin must never be treated as "missing" — this
  codebase has fixed that bug class at least five times

---

## 5. The prerequisite that unlocks the REAL moat score

Two options, very different cost/benefit:

### Option A — start accumulating now (free, slow)

Extend the existing weekly `fundamentals_snapshot` job to also capture the income-statement and
balance-sheet fields needed for true ROIC (EBIT, tax rate, total debt, total equity, cash).
yfinance exposes `.financials` / `.balance_sheet` / `.cashflow`, which already return **multi-year
annual and quarterly statements** — so this is a schema + ingestion change, not a new vendor.

- **Cost:** low (one scheduler job extension + migration)
- **Time to usable moat score:** genuinely years for persistence measurement, though quarterly
  statement history from yfinance may be *immediately* backfillable — see Option B.

### Option B — backfill historical statements (free, fast, the actual recommendation)

yfinance's `.financials`/`.balance_sheet` return **4-5 years of annual statements per ticker, right
now, including EBIT**. A one-time backfill across the ~173 active symbols would immediately provide
4-5 annual observations per company — enough to compute a real multi-year ROIC/margin persistence
score without waiting.

**Verified live against the production container (2026-09-06, yfinance 1.5.1):**

| Ticker | Annual periods | Balance-sheet periods | `EBIT` present |
|---|---|---|---|
| AAPL (US) | 5 (FY2021-FY2025) | 5 | Yes |
| 0700.HK | 5 | 5 | Yes |
| 0001.HK | 5 | 5 | Yes |
| 9988.HK | 4 | 5 | Yes |

**The HK-coverage concern is resolved: HK statement depth matches US.** This was the one risk that
could have invalidated this option, and it does not hold — all three HK tickers tested returned 4-5
annual periods with `EBIT` available, meaning true ROIC (`NOPAT / Invested Capital`) becomes
computable for both markets.

- **Cost:** one backfill script + a new `financial_statements` table (~173 tickers × ~2 API calls,
  well within rate limits if throttled — note this codebase's documented yfinance
  rate-limit-amplification history, so throttle deliberately rather than fanning out)
- **Time to usable moat score:** immediate once backfilled

### What is NOT worth doing

Licensing Morningstar/Sustainalytics data. The proprietary items (Star Rating, Economic Moat, Fair
Value Estimate, Uncertainty, Stewardship) are unobtainable free from any source — verified against
yfinance 1.5.1 (187 `.info` keys, zero Morningstar-attributed; `.sustainability` returns empty as
Yahoo's `esgScores` module now 404s) and against Unusual Whales' full OpenAPI spec (`morningstar`,
`moat`, `fair_value`, `star_rating`, `stewardship`, `uncertainty`, `style_box`, `sustainalytics` —
all **zero** hits; it is an options-flow vendor with only `pe_ratio` and `earnings_per_share` as
fundamentals).

And the platform already has Morningstar's *headline* product: `_dcf_fair_value()`
(`services/research-engine/src/scoring.py:829`) is a 2-stage DCF with sector-specific WACC
returning `dcf_fair_value` / `margin_of_safety_pct` / Undervalued-Fairly-Overvalued — structurally
the same construct as Fair Value Estimate + Star Rating, with transparent auditable assumptions
rather than a black-box rating.

---

## 6. Recommendation

1. **Do not build a "moat score" on current data.** Three months cannot measure persistence, and a
   confident-looking score with no informational content is worse than no score.
2. **Do Option B** — backfill yfinance financial statements into a new `financial_statements`
   table. This is the unlock, it's free, it's fast, and HK coverage is now **verified** to match US
   (4-5 annual periods with EBIT), so it works for the whole universe rather than US-only.
3. **Then** build the real persistence-based moat score on 4-5 annual observations: ROIC level,
   ROIC stability (stdev/trend across years), gross-margin durability, and reinvestment runway.
4. **Optionally, in the meantime**, ship the narrower `quality_score` from §4 — but name it
   `quality_score`, keep the LLM `moat_rating` as the separate qualitative read it already is, and
   don't wire either into trading logic until measured.
5. **ESG remains a genuine, unclosable gap** without licensing. Yahoo's free path is dead.

**Deliberately not decided here:** whether the quantitative score should eventually *replace* the
LLM `moat_rating` or sit beside it. They measure different things (mechanical durability vs.
qualitative competitive narrative), and the honest answer is probably both, displayed separately.
