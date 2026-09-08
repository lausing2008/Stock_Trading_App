# Deep Audit (2026-09-08): Ranking / K-Score — 6 findings, all fixed (tier 366)

The first audit of the ranking domain. Ran after the six-part trading audit (tier 363) and the
data-ingestion audit (tier 364) had covered everything upstream and downstream of it.

**Read this before touching `services/ranking-engine/` or `kscore.py`.**

---

## The headline finding, and why it is the most instructive one so far

`AUD-RANK-RSPLACEHOLDER` is the same class as tier 363's dead-ML-pillar — **a silent fail-OPEN
feeding a learner** — but it went one step further: the learner had already acted on the bad
data, and its response looked like a legitimate optimization result.

```python
def _rs_score(stock_ret, etf_ret):
    if etf_ret is None:
        return 50.0, 1.0      # <-- fabricated "exactly in line with sector"
```

Four independently verified links:

1. `^HSI` is **not in the `stocks` table** (0 rows).
2. `_etf_20d_return()` explicitly skips its DB path for any `^`-prefixed ticker
   (`not ticker.startswith("^")`), so **yfinance was its only possible source**.
3. That fetch fails on essentially every cycle — **28 `ranking.etf_return_fetch_failed` events
   in 72h**, `^HSI` rate-limited on the hour, every hour.
4. Result: **1,956 of 3,459 all-time HK ranking rows (56.5%)** had `rs_score` exactly `50.0`,
   against a US control of **6 of 9,328 (0.1%)**.

That ~565x ratio between markets is what proves it wasn't organic. And the 6 US rows at exactly
50.0 turned out to be **VOO, IGV, and GOOG** — index-tracking funds that genuinely move with
their benchmark. Real 50.0s exist; that is precisely why the fabricated ones were invisible.

**The damage was downstream and left no error trail.** `tune_kscore_weights` reads `rs_score`
verbatim from persisted `rankings` rows. It measured a factor that was constant for over half
its HK sample, correctly concluded it carried no signal, and demoted `relative_strength` from
its `0.10` default to **`0.0501`** — precisely halved, the largest relative move of any factor
(every other weight sat within a few percent of default; `value` actually *rose*
0.13 → 0.1779). **The optimizer was working correctly on corrupt input.** Nothing errored,
nothing looked anomalous, and the resulting weight set was globally applied — so this degraded
US rankings too.

The `T247-RANKINGENGINE-HSI-SILENT` comment **predicted this exact failure in detail** and only
added a log line. The log fired 28 times and nothing consumed it. A log without a consumer is
not a guard.

### The fix has three parts, and only doing one would not have worked

- **Fail closed.** `(None, None)` instead of `(50.0, 1.0)`. Safe end-to-end because `None` was
  *already* the contract for a too-short price history: `rankings.rs_score` is nullable,
  `compute_kscore()` drops the factor and renormalizes, `_fetch_relative_strength()` propagates
  it, and the thesis-persistence gate skips its check on `None`.
- **Give HK a resolvable benchmark.** Seeded **`2800.HK`** (Tracker Fund of Hong Kong): tracks
  the same index, but is an ordinary equity, so it can be ingested and read from the DB like
  every sector ETF. Tried *before* the raw index.
- **Clear the weight the placeholder taught.** Fixing the data does **not** undo the demotion —
  the Redis override had to be deleted separately so it falls back to `0.10`.

**Verified live:** `2800.HK` → `-0.0189` from the DB, no yfinance involved. Six real HK stocks
now score 27.56 / 36.85 / 44.44 / 52.07 / 52.18 / 54.77 — genuinely differentiated, where every
one would previously have been exactly `50.0`.

### One item deliberately left open

`tune_kscore_weights` runs **Sunday 14:00 PT** with a **365-day lookback**. 1,956 HK rows dated
2026-06-01 onward still hold the fabricated `50.0`, and because they are `50.0` rather than
`NULL`, the tuner's own exclusion logic will not skip them — so a run may **re-demote the weight
and undo the reset**.

The remedy is one scoped `UPDATE` nulling those rows (HK only; the US 50.0s are real). That
write was correctly blocked by the SELECT-only production DB constraint and needs explicit
approval. **Do not assume it has been applied** — check `stockai:kscore_weights` against the
`0.10` default first.

---

## The other five findings

| ID | Sev | What |
|---|---|---|
| `AUD-RANK-BENCHINGEST` | HIGH | The only job that ingests benchmark ETFs omitted XLP → 102 days stale |
| `AUD-RANK-SECTORLABELS` | MED | Sector→ETF map keyed on a taxonomy the data never emits, in 3 services |
| `AUD-RANK-THINPEERS` | MED | Null value/growth was cohort fragmentation, not missing fundamentals |
| `AUD-RANK-CURVEDRIFT` | MED | Curve tuner could promote incoherent params; steps ratcheted |
| `AUD-RANK-VOLSATURATE` | MED | 12.81% of rows tied at the volatility clip floor |

### `AUD-RANK-BENCHINGEST` — found by asking why *one* ETF was stale

`_symbols_for()` filters `Stock.active.is_(True)`, and **every benchmark ETF is deliberately
seeded `active=False`**. So the TIER94 block inside `_refresh_market()` is the *only* thing in
the codebase that ingests them — and its hardcoded list, **the fourth copy** of the sector-ETF
set, silently omitted `XLP`.

XLP therefore sat **102 days stale** (newest bar 2026-05-29) while ranking-engine kept computing
a confidently-wrong Consumer-Staples relative strength from its ancient bars. The other six were
fresh, so nothing looked broken. No DQ gauge covered them either, because every per-symbol
staleness check filters on `active`.

> **The generalisable trap:** a constant that is correct in three places and wrong in a fourth is
> *harder* to spot than one that's wrong everywhere — the three correct copies make the behaviour
> look right in every test anyone thinks to run.

Derived from the canonical map now. XLP backfilled: 275 bars, 102 days → 4 days, and it resolves
to a real `-0.0063`. Also added a `2800.HK` ingest, since it is `active=False` the same way and
would otherwise have gone stale and silently re-broken the HK fix.

### `AUD-RANK-SECTORLABELS` — worse than first reported

`stocks.sector` comes from yfinance, which emits its own taxonomy. Measured in production:
`Consumer Cyclical` (4), `Financial Services` (7), `Consumer Defensive` (1), `Financial` (2),
plus **18 US stocks with no sector at all**. Note `Financial` *and* `Financial Services` both
occur — the source is not even internally consistent. The map's `Consumer Discretionary` /
`Consumer Staples` keys matched **nothing**.

The same map was duplicated in two more services with the identical gap:

- `paper_trading_engine._SECTOR_ETF_MAP` drives **PT-M1's sector-relative-weakness exit gate** —
  those stocks mapped to no ETF, so the gate silently never evaluated them.
- `brinson_attribution`'s alias map lacked the two Consumer labels, so every real Consumer trade
  landed in `unclassified` and was excluded from the attribution effect sums. (That one at least
  fails **closed** and visibly.)

Fixed in all three, plus `_resolve_sector_etf()` matching case/whitespace-insensitively and
logging `ranking.sector_label_unmapped` — because "benchmarked against SPY by design" and
"benchmarked against SPY because the label didn't match" were previously indistinguishable.

### `AUD-RANK-THINPEERS` — the obvious diagnosis was wrong

Null `value`/`growth` on 71 and 86 of 250 rows was **not** missing fundamentals: **162 of 172
stocks (94%) have a warm fundamentals cache**. The cause is cohort fragmentation against a
**per-metric** 4-peer gate. `_MIN_PEER_GROUP = 4` is applied per metric against a cohort grouped
by the *raw* label, and the taxonomy drift above splits those cohorts: `Financial` (2) +
`Financial Services` (7) is really one viable 9-member group, split into two that can **never**
clear a 4-member gate on *any* metric.

`compute_kscore()` then drops both factors and renormalizes, pushing **~32% of composite weight**
onto price-derived factors, with no column recording that it happened.

Cohorts now group on the canonical benchmark ETF, while **unmapped/empty sectors deliberately
stay on their raw label** — keying those on the ETF would pool every unclassified stock into one
large meaningless cross-sector cohort, and a thin *real* sector beats a large fake one for
percentile ranking. Added logging that distinguishes `cohort_too_thin` from `metrics_missing`,
since the two call for completely different remedies.

**Deliberately not done:** an `n_factors_used` column. It needs a migration and touches the write
path; the logging closes the diagnostic gap that actually mattered. A scoping choice, not an
oversight.

### `AUD-RANK-CURVEDRIFT` — and a real bug in my own first attempt

Two properties compounded. Each perturbation step is relative to the **live** value, so
promotions **ratchet**: `volatility_scale` went 1500 → 1200, making the next candidate 1440
(= 1200 × 1.2), never returning toward the default. And the RSI knobs are an **ordered ladder**
that per-key perturbation can silently invert — `rsi_mid > rsi_high` doesn't error, it produces a
monotonically *wrong* technical curve the EV search may still prefer on one train slice.

Added per-constant bounds plus a strict `rsi_low < rsi_mid < rsi_high` check, filtered at
candidate generation **and** re-validated on the merged set before the Redis write (the merged
dict is what goes live for 30 days). Bounds are deliberately **wide** — they reject incoherence,
not aggressiveness; the walk-forward EV gate still judges whether a coherent candidate is better.

> **My first version was wrong and a pre-existing test caught it.** I validated
> `{**base, **candidate}` unconditionally, so an *already-incoherent base* rejected **every**
> candidate — including ones perturbing unrelated keys.
> `test_a_zero_valued_base_constant_produces_no_candidates_for_that_key` failed. That is exactly
> the permanent lockout the calibration watchdog once inflicted on itself (tier 363). Both checks
> now skip when the base is already invalid, pinned by its own test.

### `AUD-RANK-VOLSATURATE` — re-measuring nearly doubled the reported rate

The score is `clip(100 - vol * volatility_scale, 0, 100)`, and the hard floor was not a rare
edge: **1,638 of 12,787 rows (12.81%)** sat at exactly 0 — against **6.8% first reported**, which
had measured only a recent subset. At the live scale of 1200, every stock above 8.33%/day
realized vol scored identically 0, so the **18% of composite weight** on volatility could not
distinguish a merely-volatile name from a genuinely wild one.

Linear region kept **byte-identical** (so `volatility_scale` keeps its calibrated meaning for the
~87% above the floor — this is not a recalibration in disguise); below a soft floor it now
compresses hyperbolically: strictly monotonic, asymptotic to 0, never tying, continuous at the
join.

> Same lesson as tier 363's retractions: **widen the sample before believing a rate.**

---

## Checked and found clean — do not re-derive these as findings

- **Point-in-time integrity** — no `T234-ML-FUND-BROADCAST-LEAKAGE` analogue in the K-Score path.
- **All K-Score consumers fail closed** on a missing composite.
- **No falsy-zero bug in any K-Score read path.**
- **`compute_kscore()`'s renormalization is correct** — it already drops a `None` factor and
  renormalizes the rest unconditionally via `(weight / w_sum)`, which is *why* failing closed was
  safe. The `dict(...)` copy there is load-bearing, not decorative.
- **The weights tuner's promotion safety** (walk-forward split, beat-the-live-baseline, positive
  EV lift required) is sound — `AUD-RANK-CURVEDRIFT` was about candidate *validity*, not the
  promotion gate.
- **The live tuned weight set summing to 1.0001** is harmless: `compute_kscore()` renormalizes
  unconditionally.

---

## Two findings that came out of fixing the others

`AUD-RANK-BENCHINGEST` (above) and `AUD-ING6-MARKETINFER` were both found *while* fixing, not by
the audit sweep. The second one is worth its own note:

**`AUD-ING6-MARKETINFER`** — adapter selection derives the market from the **symbol suffix**
(`symbol.endswith(".HK") or market == "HK"`) while `allow_zero_volume` tested only the **`market`
parameter**. `ingest_universe()` calls `ingest_symbol()` *without* a market, so it always
defaulted to `"US"`: an HK symbol was routed to the correct HK adapter by suffix while being held
to the strict US `volume > 0` rule, silently dropping every zero-volume bar of an illiquid HK
name.

That is the exact defect `AUD-ING6-HKZEROVOLUME` (tier 364) fixed for the explicit-market path,
still fully reachable through any caller that omitted the argument — and `ingest_universe()` is
the bulk path the scheduler uses. Both now derive one `_effective_market`.

> **Two things inferring the same fact by different rules is a bug waiting for one of them to
> change.**
