# Data Ingestion & Price Integrity — Partial Audit (2026-09-08)

**Status: PARTIAL.** The subagent dispatched for this domain was killed by an org monthly spend
limit before producing findings. I completed the highest-value item myself — root-causing the
SPY OHLC anomaly — and documented the remaining scope for a future pass. This is deliberately
labelled partial so nobody reads it as a completed audit.

---

## The overall picture is GOOD

Ingestion is materially cleaner than the six trading domains audited on 2026-09-07. Across
**123,475 D1 bars / 185 symbols**:

| check | result |
|---|---|
| duplicate `(stock_id, date)` bars | **0** |
| `high < low` | **0** |
| `close` outside `[low, high]` | **0** |
| non-positive close | **0** |
| negative volume | **0** |
| symbols with interior gaps >4d (last 120d) | **1** (`1671.HK`, 5 gaps, worst 11d) |

That is a genuinely clean bill on the checks that matter most. Worth stating plainly, because
the previous series found something in every domain and it would be easy to assume the same here.

---

## Finding 1 — LOW — 14 SPY bars have `open` outside `[low, high]`

**What it is NOT** (two hypotheses I formed and then disproved, recorded so they are not
re-derived):

1. ~~`open` is stored raw while `high`/`low`/`close` are dividend-adjusted.~~ **Wrong.** The
   yfinance adapter passes `auto_adjust=use_adjusted` with `use_adjusted = (timeframe == "1d")`,
   and `ingest_symbol` defaults `timeframe="1d"`, so all four OHLC columns are adjusted together.
2. ~~A different/rogue writer produced these bars.~~ **Wrong.** The sector-ETF job
   (`scheduler.py:631-633`) calls `ingest_universe(_SECTOR_ETFS, "1d")` — the same correct path.

**What it actually is.** A sector-ETF backfill ran 2026-03-19..06-11, writing 59 bars each for
SPY and all ten XL* ETFs with `adj_close` populated. **Only SPY produced bad bars (14 of 59);
every XL* ETF produced zero.** The distinguishing property is dividend size: yfinance's own
adjusted output can place the adjusted `high` fractionally *below* the unadjusted-looking `open`
when a dividend adjustment is applied across the window.

**Magnitude — this is why it is LOW, not HIGH:**

| | |
|---|---|
| affected bars | 14 of 569 SPY bars (2.5%) |
| average overshoot | **0.128%** |
| worst overshoot | **0.233%** |
| affected symbols | SPY only |

An `open` that is 0.13% above the `high` is an internal-consistency violation, not a materially
wrong price. No feature in the platform derives from `open` in a way that a 0.13% overshoot would
change — TA is close-based, and the regime benchmark reads SPY's close.

**Why it still deserves a fix:** it is the kind of impossible-by-construction row that a naive
downstream consumer (a candlestick renderer, an intraday-range calculation, an OHLC-based
backtest) would either draw wrong or divide by. It is cheap to guard.

**Recommended fix:** an ingest-time sanity clamp — reject or repair a bar where
`open`/`close` fall outside `[low, high]`, log the symbol and date. Do NOT silently rewrite
prices without logging; a bar that fails this check is evidence about the upstream feed.

---

## Finding 2 — MEDIUM — Two dead tickers still marked `active`

| symbol | bars | last bar | stale |
|---|---|---|---|
| `SSNLF` | 1 | 2025-11-07 | **305 days** |
| `SKHYV` | 1 | 2026-07-17 | **53 days** |

Both hold a single bar and have not updated since. They consume API quota on every ingest cycle
and pollute the ML training universe (each gets 8 training attempts nightly that cannot possibly
succeed).

This connects to a long-standing known gap: `Stock.delisted` exists and its migration is live,
but **nothing in the codebase ever sets it to `True`** — it is only ever read as a filter. The
`AUD-SURVIVORSHIP-DELISTDETECT` work added `raise_errors=True` so yfinance raises a
`YFTickerMissingError` on a genuinely delisted ticker rather than returning empty, so the
*detection* primitive exists; nothing wires it to a write.

**Recommended fix:** on a `YFTickerMissingError` for N consecutive cycles, set `delisted=True`.
The read-side filters already exist and would immediately take effect.

---

## Finding 3 — SUSPECTED (not confirmed) — Possible unadjusted splits

Extreme single-day moves that may be unapplied split/reverse-split adjustments. **Not
investigated to conclusion** — flagged for the completing pass:

| symbol | date | move |
|---|---|---|
| QUCY | 2024-12-03 | 0.20 → 6.92 (**+3377%**) |
| DFNS | 2024-12-17 | 173.75 → 1485.00 (**+755%**) |
| BULL | 2025-04-14 | 13.25 → 62.90 (+375%) |
| RXT | 2026-02-18 | 0.42 → 1.37 (+227%) |

**Important caveat:** DFNS was confirmed a *genuine* 10x melt-up by a prior audit, so these must
not be assumed to be bugs. A split not applied retroactively corrupts every rolling feature
computed across it, so each needs individual verification against the real corporate-action
record before any correction.

---

## Area 1 — Ingest-time validation: **NO FINDING. Validation exists and is correct.**

I initially reported that there was *no OHLC validation on write*. **That was wrong** — I
grepped for the checks rather than for a validator by name and missed it.

`validate_ohlcv()` (`ingestion.py:110-133`) checks exactly what I claimed was missing:

```python
df = df[(df["high"] >= df["low"]) & (df["high"] >= df["open"]) & (df["high"] >= df["close"])]
df = df[(df["low"] <= df["open"]) & (df["low"] <= df["close"])]
df = df[(df[["open","high","low","close"]] > 0).all(axis=1)]
if not allow_zero_volume:
    df = df[df["volume"] > 0]
```

It is called at `:255`, **immediately after fetch and before any write**, on every path:
`ingest_universe` → `ingest_symbol` → validated, and `pg_insert(Price)` at `:318` is the **only**
write to the `prices` table in the entire codebase (confirmed across all services). The validated
`candidate` frame is the one persisted. There is no bypass.

### The open question this leaves — and where I stopped

If validation is correct and universal, **how did the 14 bad SPY bars get in?** What I
established:

- **6 of 14 predate the repo's first commit** (2026-04-17) — those are pre-history and
  unexplainable from this codebase.
- **8 of 14 are AFTER it**, so they are not purely historical.
- **It is not float-precision rounding.** The overshoots are $0.24–$0.69, orders of magnitude too
  large.
- **The raw values are diagnostic:** `open` carries unadjusted precision (`741.7899780273438`,
  `711`) while `high` carries adjustment artifacts (`741.5496453663399`). So my *original*
  adjusted/unadjusted-mixing hypothesis was right, and my earlier retraction of it was premature.
- **Our code does not do the mixing** — there is no post-fetch adjustment anywhere in the adapter,
  and `auto_adjust` is passed uniformly for all four OHLC columns.

**Most likely explanation:** yfinance itself intermittently returns a row with `Open` unadjusted
while `High`/`Low`/`Close` are adjusted. `validate_ohlcv` *should* still have rejected such a row,
which is the part I could not close. **Not resolved — this needs a live reproduction against
yfinance for one of the affected dates, which I did not do.**

Severity remains LOW regardless (0.128% avg overshoot, SPY only, 2.5% of its bars) — but the
mechanism is open, not solved.
2. ~~**`data_quality_checks` scheduler jobs**~~ — **AUDITED, see Area 2 below.**
3. ~~**Adjusted-vs-unadjusted consistency**~~ — **AUDITED, see Area 3 below. SPY question CLOSED.**
4. ~~**`_fetch_live_bulk` and its per-symbol fallback**~~ — **AUDITED, see Area 4 below. CLEAN.**
5. ~~**HK timezone handling**~~ — **AUDITED, see Area 5 below. CLEAN.**
6. ~~**The 21 symbols with <400 bars**~~ — **AUDITED, see Area 6 below. 1 REAL FINDING.**


---

## Area 2 — the `data_quality_checks` job: **2 findings**

The framework itself is mature and well-reasoned — it separates query-errors from staleness
(T243-DQ5, so a DB outage cannot report "all healthy"), has ratio/gauge check types, and adds a
market-closed guard (T242-DQ1) to stop false weekend alerts. 16 checks, every 2 hours. **It is
not a rubber stamp.** Both findings are gaps at its edges, not a broken design.

### Finding 2a — MEDIUM — The price checks use a single `MAX()` across all symbols, so per-symbol death is invisible

```sql
SELECT MAX(p.ts) FROM prices p JOIN stocks st ON p.stock_id=st.id
WHERE st.market='US' AND p.timeframe='D1'
```

One aggregate over the whole market. **If any single US symbol updated in the last 48h, the
check passes** — regardless of how many others have stopped updating entirely.

That is exactly why it never flagged the two dead tickers:

| symbol | last bar | days stale | visible to the check? |
|---|---|---|---|
| `SSNLF` | 2025-11-07 | **305** | ❌ hidden behind `MAX()` |
| `SKHYV` | 2026-07-17 | **53** | ❌ hidden behind `MAX()` |

Measured blast radius today: **2 of 131 US symbols** are >30d stale and invisible. Small now —
but the check is structurally incapable of detecting the failure mode it exists to catch, so the
number could grow to any size without an alert.

**Recommended:** add a per-symbol staleness check — count active symbols whose own latest bar is
older than N trading days, fail if that count exceeds a small threshold. The existing framework
supports this shape directly; it needs a new `_DQ_CHECKS` entry, not new machinery.

### Finding 2b — LOW — The two price checks are missing the `market` tag, so they fire false alerts every long weekend

T242-DQ1 added a market-closed guard so a check does not report stale when its market is simply
shut. The guard keys on `check.get("market")` — and **`prices_us_d1` / `prices_hk_d1` do not
carry that key**, while their `rankings_*` and `signals_*` siblings do:

| check | `market` tag |
|---|---|
| rankings_us / rankings_hk | ✅ |
| signals_us / signals_hk | ✅ |
| **prices_us_d1 / prices_hk_d1** | ❌ |

**Caught live during this audit.** `dq_check:prices_us_d1` currently reads
`{"ok": false, "age_hours": 99.3}` — because the last US bar is Friday 2026-09-04, Monday
2026-09-07 was Labor Day, and today is Tuesday pre-close. **The data is completely healthy;**
the check is firing anyway.

> **A false alarm of my own, worth recording.** I first read that 99.3h as an active ingestion
> outage and started chasing it. It is not — the pipeline is fine and the holiday explains it
> entirely. The finding is the *missing guard*, not stale data. Checking the holiday calendar
> before escalating is what separated the two.

The fix is one key per entry (`"market": "US"` / `"market": "HK"`) — the guard logic already
exists and needs no change.

### Why neither finding caught the SPY anomaly

Worth stating explicitly: **the DQ framework was never going to.** Every check is a
*freshness/staleness* test — "is the newest row recent enough". None of them validate row
*content*. An OHLC-ordering violation on a bar from March is perfectly fresh by every check here.
That is a coverage gap in kind, not a bug: the framework does what it says.


---

## Area 3 — adjusted vs unadjusted: **NO live finding, and the SPY question is now CLOSED**

### The SPY anomaly: solved, historical, and already self-healed

After two wrong turns (recorded below), the mechanism is proven by matching values:

```
yfinance UNADJUSTED, 2026-05-15:  open=741.7900  high=743.4600
yfinance ADJUSTED,   2026-05-15:  open=739.8839  high=741.5496
our DB:                           open=741.7900  high=741.5496
                                       ^unadjusted     ^adjusted
```

Our stored `open` is **byte-identical to yfinance's UNADJUSTED open**, while `high`/`low`/`close`
are the **adjusted** values. The row genuinely mixes the two scales — which is exactly the
original hypothesis, now confirmed with matching numbers rather than inferred from precision.

**It is historical, not live:**

| | |
|---|---|
| last bad bar | **2026-05-27** |
| SPY bars written since | **70** |
| bad bars among them | **0** |
| bad bars predating the repo's first commit (2026-04-17) | 6 of 14 |

No current code mixes scales — every fetch site passes `auto_adjust` uniformly for all four OHLC
columns, and there is no path that fetches both and merges. The writer responsible was removed
before this repo's history begins or shortly after. **`validate_ohlcv()` would reject such a row
today**, which is consistent with none having appeared in 70 subsequent bars.

**Action: none needed.** The 14 rows are cosmetically wrong (0.128% avg overshoot) on a symbol
whose `open` nothing consumes. Rewriting historical prices to fix a self-healed cosmetic defect
carries more risk than the defect.

### Two wrong turns, recorded so they are not repeated

1. **I retracted the adjusted/unadjusted hypothesis prematurely.** I disproved it by noting the
   adapter passes `auto_adjust` uniformly — true, but it only rules out *current* code, not a
   historical writer. Correct conclusion: "not caused by today's code", not "not an adjustment
   mix".
2. **I nearly concluded "off-by-one row".** Our 05-15 open (741.79) is yfinance's *unadjusted*
   05-15 open — not the previous day's (743.65). Checking the actual previous-day value is what
   killed that theory before it got written down.

### The live paths: consistent, with three benign exceptions

All ~20 yfinance fetch sites were inventoried. Three make real fetches **without** `auto_adjust`
(yfinance defaults to `False`, i.e. unadjusted):

| site | use | verdict |
|---|---|---|
| `scheduler.py:1141` `_build_game_plan` | derives strike/target from its own fetched price | **benign** — self-contained, no cross-scale comparison |
| `routes.py:4714` sector-ETF momentum | `Close[-1]/Close[-21]`, `Close > SMA50` | **benign** — ratios and self-comparisons are scale-invariant |
| `routes.py:4503` current price | single latest close | **benign** — see measurement below |

**Measured, rather than argued:** the latest close is **identical** raw vs adjusted for SPY, XLF,
AAPL and NVDA (diff 0.0000%). Adjustment only moves *historical* bars — AAPL's 21-day return
differs by 0.088pp raw vs adjusted, SPY/XLF/NVDA by 0.0000pp. So a site reading only the latest
close cannot be affected, and the one site computing a multi-week ratio uses a scale-invariant
form.

**Recommendation (low priority):** add `auto_adjust=True` to those three for consistency. It
changes nothing measurable today, but the next person to compute a longer-horizon return from
one of them would inherit a silent 0.088pp-class error.


---

## Area 4 — `_fetch_live_bulk` rate-limit amplification: **CLEAN, guard held**

`BUG-YFCALLVOL2` (2026-08-17) found the per-symbol fallback amplifying a real Yahoo throttle: the
bulk call failed for all ~165 symbols, and the fallback then fired 150+ individual requests (up
to 2 each) into an already-throttled endpoint.

**The guard is present and correctly enforced** (`routes.py:161`, `:267-272`):

```python
missed = [s for s in stocks if s.symbol not in fetched]
if missed and len(missed) > _LIVE_BULK_FALLBACK_MAX:      # 20
    log.warning("live_prices.bulk_fallback_skipped_too_many_misses", ...)
elif missed:
    ...individual fetches...
```

A large miss count means the bulk call *itself* is throttled, so the fallback is skipped
entirely and the cache carries fewer symbols that cycle rather than amplifying the throttle.

**Verified against 7 days of production logs:**

| | |
|---|---|
| `bulk_fallback_skipped_too_many_misses` firings | **0** |
| yfinance fallback events total | **20** |
| yfinance rate-limit errors | **0** |

The guard has not needed to fire, and yfinance is not under pressure.

### A large number that turned out to be a different subsystem

The log search initially returned **53,024** rate-limit mentions in 7 days, which looked alarming.
Breaking it down by source:

| source | count |
|---|---|
| `unusual_whales.rate_limit` | 26,394 |
| `unusual_whales.dark_pool_failed` | 17,433 |
| `unusual_whales.flow_alerts_failed` | 8,962 |
| **`yfinance.fast_info.fallback`** | **20** |

**None of it is yfinance.** It is Unusual Whales, a separate subsystem — and it is *already
fixed*:

- **All 26,394 fell on a single day: 2026-09-05**, between **19:00–23:00 UTC (3–7pm ET, market
  closed)**.
- **Zero on 09-06, 09-07 and 09-08.**

That is exactly the window `AUD-OPT6-NOMARKETHOURSGATE` (fixed 2026-09-07, Domain 6 of the
previous series) now blocks — the options-flow job was running every minute, 24/7, hammering UW
against a 48h `newer_than` window while no options were trading. The spike stopping dead the day
after that deploy is independent confirmation the fix worked.

**No action needed for Area 4.**


---

## Area 5 — HK timezone handling: **CLEAN. The old offset bug stayed fixed.**

The documented past bug stored HK daily bars at the wrong UTC offset (fixed in `base.py` +
`routes.py` + a DB migration). Re-verified against all 22,292 HK daily bars:

| check | result |
|---|---|
| hour component | **0 on all 22,292 bars** — no offset |
| weekend bars | **0** (an offset bug shifts Mon→Sun) |
| weekday spread | even (Mon 4,373 … Thu 4,588) |

The fix held. `_to_canonical`'s daily branch (`base.py:65-66`) preserves the LOCAL market date
rather than the UTC date, which is what makes this correct.

### A control-group anomaly worth recording: GOOGL

Running US as a control surfaced **762 bars at hours 4 and 5 (UTC)** — all GOOGL, no other
symbol. Investigated to conclusion; **benign and already self-resolved**:

| check | result |
|---|---|
| dates correct? | **yes** — `2026-05-05 04:00` still reads as date `2026-05-05` |
| duplicate dates? | **no** — 767 bars, 767 distinct dates, zero excess |
| weekend bars? | **no** |
| OHLC violations (the SPY signature)? | **zero** |
| still happening? | **no** — no offset bars since June 2026 |

**Same historical writer as the SPY bars.** All 767 GOOGL bars carry `adj_close` populated — the
identical fingerprint that isolated the SPY window in Area 3, where only 45 of 555 SPY bars had
it. So one legacy writer produced both artifacts, but GOOGL got only the timestamp-hour quirk
and none of the OHLC mixing.

**No action needed.** The dates are right, nothing derives from the hour component of a daily
bar, and the writer is gone.


---

## Area 6 — under-covered symbols: **1 CONFIRMED finding**

Classifying all 21 symbols with <400 daily bars by coverage density (bars per weekday since
their first bar):

| verdict | count | evidence |
|---|---|---|
| **New listing** | 17 | 0.93–1.03 bars/weekday — full coverage since inception |
| **Stale** | 2 | SSNLF, SKHYV — the known dead tickers |
| **GAPPY** | **2** | `1671.HK` at **0.36**, `0117.HK` at **0.41** |

### Finding — MEDIUM — `validate_ohlcv`'s `volume > 0` rule silently deletes ~half the history of illiquid HK stocks

I first assumed the two gappy symbols were thinly-traded stocks that genuinely don't trade some
days. **Checking yfinance disproved that** — it returns 24–28 bars for the exact gap windows.
The data exists upstream; we were discarding it.

Traced to `validate_ohlcv()` (`ingestion.py:129`):

```python
if not allow_zero_volume:
    df = df[df["volume"] > 0]
```

Running the real 3-year fetch through the real validator:

| symbol | fetched | kept | **dropped** | zero-volume | bad OHLC |
|---|---|---|---|---|---|
| `1671.HK` | 735 | 277 | **458 (62%)** | **448** | 10 |
| `0117.HK` | 735 | 320 | **415 (56%)** | **398** | 17 |

**448 of the 458 dropped bars are zero-volume.** For a thinly-traded HK small cap, a zero-volume
day is a legitimate no-trade session with a real carried-forward price — not an invalid bar.

**Scope — the rule is right for liquid names and wrong for illiquid ones:**

| symbol | zero-volume share |
|---|---|
| 0700.HK, 0005.HK, 9988.HK, 3750.HK | **0.6–1.2%** |
| `0117.HK` | **26.9%** |
| `1671.HK` | **48.6%** |

The docstring states the assumption explicitly — *"Regular-session and daily bars keep the strict
volume>0 check — real trading always has nonzero volume there."* That is true for US liquid
equities, which is what it was written against (`allow_zero_volume` exists only for yfinance's
pre/post-market intraday quirk), and false for HK small caps.

**Why it matters:** every rolling feature for these symbols is computed across a series missing
half its bars, so a "200-day SMA" spans ~400 calendar days. They are in the training universe and
generate live signals. And it is **self-concealing** — the weekly `force=True` refresh re-fetches
all 735 bars every week and re-drops the same 458, so the gap can never heal and the only
symptom is a `ohlcv.drop_invalid` log line nobody reads.

**Recommended fix:** keep the volume gate for US daily bars, but allow zero-volume daily bars for
HK (or, better, gate on price validity alone for daily bars and keep `volume > 0` only where a
zero genuinely indicates a bad bar). The `allow_zero_volume` parameter already exists — this is
plumbing a market-aware value into it, not new machinery.

**Not fixed — reported, per the one-area-at-a-time protocol.**
