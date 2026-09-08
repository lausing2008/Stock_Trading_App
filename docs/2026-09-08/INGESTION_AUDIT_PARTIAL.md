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
3. **Adjusted-vs-unadjusted consistency across ALL ingest paths** — I verified the main adapter
   path only. `paper_trading_engine.py` has ~8 separate `yf.download(..., auto_adjust=True)`
   call sites that were not traced.
4. **`_fetch_live_bulk` and its per-symbol fallback** — `BUG-YFCALLVOL2` previously found the
   fallback amplifying a rate-limit event. Not re-verified.
5. **HK timezone handling** — a documented past bug stored HK bars at the wrong UTC offset. Not
   re-verified.
6. **The 21 symbols with <400 bars / 7 with <100** — beyond the two dead tickers, are the rest
   genuinely new listings or silently under-ingested?


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
