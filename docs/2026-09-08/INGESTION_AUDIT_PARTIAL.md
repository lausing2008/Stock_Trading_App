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

## NOT YET AUDITED — scope for the completing pass

1. **Ingest-time validation** — is there any? A bar with `open > high`, a 3000% move, or a zero
   price appears to be written unchallenged.
2. **`data_quality_checks` scheduler jobs** — what do they actually verify? They evidently did
   not catch the SPY anomaly or the two dead tickers.
3. **Adjusted-vs-unadjusted consistency across ALL ingest paths** — I verified the main adapter
   path only. `paper_trading_engine.py` has ~8 separate `yf.download(..., auto_adjust=True)`
   call sites that were not traced.
4. **`_fetch_live_bulk` and its per-symbol fallback** — `BUG-YFCALLVOL2` previously found the
   fallback amplifying a rate-limit event. Not re-verified.
5. **HK timezone handling** — a documented past bug stored HK bars at the wrong UTC offset. Not
   re-verified.
6. **The 21 symbols with <400 bars / 7 with <100** — beyond the two dead tickers, are the rest
   genuinely new listings or silently under-ingested?
