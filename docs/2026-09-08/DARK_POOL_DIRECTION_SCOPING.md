# Dark Pool Direction — Scoping and Method (2026-09-08)

**Question asked:** can we detect or find the direction of dark pool activity?

**Answer:** not directly — there is no side flag anywhere in the data — but one inference method
is defensible and now measured as *viable on this platform's real data*. Two commonly-cited
methods are ruled out here for concrete, measured reasons.

**Status: SCOPED, NOT BUILT.** The binding constraint is sample size (5 days), not method or
engineering. Scheduled for evaluation 2026-12-08.

---

## Why there is no direct answer

A dark pool print carries exactly: `price`, `size`, `premium`, `venue`, `executed_at`
(`DarkPoolPrintRow`, `unusual_whales.py:1410`; `DarkPoolPrint`, `shared/db/models.py`).

**There is no buy/sell field.** This is not UW withholding it — a print is reported to a FINRA
TRF *after* execution and the tape does not carry which side initiated. Any vendor selling a
"dark pool buy/sell ratio" is inferring it, not reading it.

This platform's existing code already takes the correct position: `check_dark_pool_alerts()`
"makes no directional claim" (`scheduler.py:5994`, `:6010`), and the outcome scorer is written
around that absence rather than papering over it. **That was the right call and this scoping does
not overturn it.**

---

## What was measured (2026-09-08, 5 days of real prints)

```
prints                13,622      symbols  62      window 2026-09-04 -> 2026-09-08
in US RTH (UTC 13-20) 12,964
median size            1,000      p95  11,400      max  2,435,959
prints >= 10k shares      877
5m bars available        172 symbols, same window  -> joinable
```

### Finding A — Lee-Ready trade-side classification is NOT implementable here

The textbook method compares print price to the **prevailing bid/ask at execution time** (above
mid = buyer-initiated, below = seller-initiated).

**There is no NBBO quote source in this codebase.** The only `bid`/`ask` fields are
broker-quote-shaped (`etrade_broker.py:441`, `alpaca_broker.py:217`) and are *current-moment*
only — nothing stores a historical quote at a print's timestamp. Without a quote at the print's
millisecond, Lee-Ready cannot be computed. Acquiring historical NBBO is a paid market-data
problem, not a code problem.

### Finding B — venue composition is DEAD as a signal here

```
venue  L  ->  13,377 prints (98.2%)
venue  2  ->     245 prints ( 1.8%)
```

**95%+ of prints carry one venue code.** There is no cross-venue variance to exploit. Had this
not been measured first, it would have been a plausible-sounding build that could never have
worked. Do not revisit without evidence the venue mix has genuinely changed.

### Finding C — the median print is 1,000 shares, so size filtering is mandatory

Median exactly **1,000** shares against a max of **2.4M** is an enormous spread. A 1,000-share
print is odd-lot/retail-scale noise, not an institutional footprint. Only **877 of 13,622** are
>= 10k shares.

**Any real signal lives in that tail.** An unfiltered computation would be dominated by noise
that has no institutional interpretation at all.

### Finding D — prints do NOT cluster at the midpoint (this was the deciding test)

My initial concern was that dark pools execute at the midpoint *by design*, which would leave
price-position carrying no information. **Measured, and that concern was wrong:**

```
joined to a 5m bar     12,840 prints
mean position-in-bar   0.5099
stddev                 0.2888
near mid (0.45-0.55)    1,511  (11.8%)
upper quartile (>0.75)  2,958
lower quartile (<0.25)  2,845
```

Only 11.8% sit near mid, and dispersion is real. **Method 1 below is therefore viable.**

### Finding E — but there is ZERO aggregate directional skew, so the signal must be per-symbol

Mean position is **0.5099** — dead center — and the tails are nearly symmetric:

```
all prints:      upper 2,958  vs  lower 2,845
blocks >= 10k:   upper   218  vs  lower   219
```

**Consequence that determines the design:** a market-wide or pooled "dark pool sentiment" reading
would show precisely nothing, correctly. Any usable signal must be computed **per symbol, per
day** — "this stock's prints skewed high *today*, relative to its own distribution" — and then
tested against that symbol's forward return. Pooling across symbols destroys it by construction.

---

## The method to build (when the sample supports it)

### Primary: price-position-within-bar, per symbol per day, block-filtered

For each print, join to its containing 5-minute bar and compute:

```
pos_in_bar = (print_price - bar_low) / (bar_high - bar_low)      -- 0 = at the low, 1 = at the high
```

Aggregate **per (symbol, day)** over prints with `size >= 10_000`:

```
dp_skew   = mean(pos_in_bar) - 0.5        -- > 0 suggests accumulation, < 0 distribution
dp_weight = sum(size)                     -- conviction / how much to trust the skew
dp_n      = count(prints)                 -- sample guard; below ~5 the mean is meaningless
```

The join predicate that works against this schema (verified):

```sql
JOIN prices p ON p.stock_id = s.id
             AND p.timeframe = 'M5'
             AND p.ts = date_trunc('hour', d.executed_at)
                      + INTERVAL '5 min' * FLOOR(EXTRACT(minute FROM d.executed_at)::int / 5)
WHERE p.high > p.low                       -- a zero-range bar makes pos_in_bar undefined
```

Note `p.high > p.low` is load-bearing, not defensive: a flat 5m bar would divide by zero, and
illiquid names produce them regularly.

### The actual validation — do NOT skip to labelling direction

**Do not ship `dp_skew` as a "direction" indicator on the strength of its plausibility.** Test it:

1. Compute `dp_skew` per (symbol, day) with `dp_n >= 5` and `size >= 10_000`.
2. Join to that symbol's **forward** N-day return (N = 3, 5, 10) from the D1 bars.
3. Ask: does `CORR(dp_skew, forward_return)` differ from zero, per horizon?
4. Split by `dp_weight` tercile — if the signal is real, it should be *stronger* where more block
   volume backs it. A signal that does not strengthen with conviction is probably noise.
5. **Hold out time.** Fit intuition on the first 70% of days, confirm on the last 30% unseen.

**Success criterion, stated in advance so it cannot be rationalised afterward:** a consistently
signed correlation that strengthens with `dp_weight` and survives the holdout. Anything less is a
null result, and a null result here is a legitimate, publishable answer — the honest outcome is
"dark pool prints do not carry recoverable direction on our universe," which is worth knowing.

### Secondary, cheap, worth including: sign persistence

Rather than the magnitude, test whether *consecutive-day agreement* predicts: does a symbol whose
`dp_skew` is positive 3 days running behave differently? Persistence is often more robust than a
single day's mean, and it costs one extra window function.

---

## Why this is not being built today

**5 days. 62 symbols. 877 block prints.** That is not enough to validate any directional method,
and this exact trap has already bitten this project repeatedly:

- A volatility saturation rate read at 6.8% on a recent subset was **12.81%** on the full table
  (this session, `AUD-RANK-VOLSATURATE`).
- "The system has defensive skill" — **retracted**, was a 0.355 beta artifact.
- "insider_score predicts returns" — **retracted**, was 6 stocks, 3 up / 3 down.

The standing rule from `docs/2026-09-05/SESSION_INDEX_AND_NEXT_STEPS.md` applies directly: on this
data, **always widen the sample and validate out-of-sample before believing a result.**

**One time-sensitive caveat:** UW's history is a **rolling window, not an archive** (the same
constraint that made the options-history capture urgent — see
`docs/features/options-and-institutional-data.md`). Prints *are* persisted durably to
`dark_pool_prints`, so the table grows from here — but uncaptured past days are gone permanently.
The correct posture is therefore **let it accumulate**, not backfill.

At current rate (~2,700 prints/day, ~175 blocks/day), by **2026-12-08** the table should hold
roughly **60+ trading days and ~10k block prints** — enough for a per-symbol test with a real
holdout.

### Calibrated expectation, measured by smoke-testing the script on the 5-day sample

`scripts/dark_pool_direction_test.sql` was run on 2026-09-08 to prove it executes (it does —
all 7 sections, no errors). What it revealed about yield matters for December:

- Only **44 symbol-days** cleared `dp_n >= 5` with `size >= 10_000` from 5 days — about **9/day**.
- So ~60 trading days should produce roughly **500-550 symbol-days**. Workable, but *not* large:
  a 70/30 split leaves ~150 rows out of sample. Treat a correlation there as suggestive, not
  settled, and expect to need more time for a confident answer at the 10-day horizon.
- Weight terciles already span **63k -> 576k shares**, so section 4's conviction test has real
  separation to work with.
- Sections 3/6/7 returned **empty on purpose**: forward returns need bars *after* the print date,
  and the newest print is always "today". `resolvable_3d/5d/10d` were all 0. That is the script
  behaving correctly on a sample with no future yet — **not** a bug to fix in December.

If the December run shows fewer than ~300 symbol-days, defer again rather than lowering
`dp_n` or the block-size floor to manufacture rows. Loosening the filters to reach significance
is how the retracted findings happened.

---

## What NOT to do

- **Do not buy or trust a vendor "dark pool buy/sell ratio."** It is inferred, and you cannot
  audit their inference.
- **Do not read short volume as direction.** It is a different measurement.
- **Do not treat a large print as accumulation.** It is a *transaction* — someone sold exactly as
  much as someone bought. The interesting question is only ever *where in the range* it printed.
- **Do not pool across symbols.** Finding E shows that reads as zero by construction.
- **Do not revisit venue composition** without evidence the mix changed (Finding B).
- **Do not add a directional claim to `check_dark_pool_alerts()`** until the validation above
  passes. Its current "makes no directional claim" stance is correct and deliberate.
