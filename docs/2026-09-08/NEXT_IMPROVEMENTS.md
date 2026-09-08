# Next Improvements (2026-09-08)

Derived from what the two audits actually left open, plus one new defect found while checking
whether a "standing gap" was still real. Ordered by value, with the reasoning shown.

---

## 1. HIGHEST VALUE — The delisting detector can never fire (NEW defect, found today)

**This corrects a claim I made twice in this session.** I stated that `Stock.delisted` "is never
set by anything, only read." **That is wrong.** `_record_delisting_signal()`
(`ingestion.py:76-96`) is fully built and **does** set `stock.delisted = True` once a
confirmation threshold is reached — Redis counter, 30-day TTL, a `_clear_delisting_signal()`
companion, fail-open error handling. The mechanism is complete.

**It has nonetheless never fired once.** Verified: zero `stockai:delisting_signal:*` keys in
Redis, zero `delisting_signal`/`delisted_confirmed` log events in 7 days, and both dead tickers
still `active=true, delisted=false`.

### Root cause — an explicit date window hides the delisting error

The detector triggers only on `YFTickerMissingError`. But yfinance's behaviour depends on **how
you ask**:

```
yf.Ticker("SKHYV").history(start="2026-07-11", end="2026-09-09")   -> 1 bar (2026-07-17)   SUCCESS
yf.Ticker("SKHYV").history(period="1mo")                           -> YFPricesMissingError  RAISES
```

`ingest_symbol()` always builds an explicit `start`/`end` (incremental from `head - 7 days`).
For a delisted ticker that window still straddles its final real bar, so yfinance returns that
one stale bar, ingestion counts it as success, **`_clear_delisting_signal()` runs**, and the
counter is reset to zero on every single cycle. The threshold is unreachable by construction.

Traced live end-to-end — a real `ingest_symbol("SKHYV")` logged `inserted=1` and left no signal key.

**Consequences:** dead tickers stay in the ingest universe forever (API quota on every cycle),
stay in the ML training universe (8 impossible training attempts nightly each), and the
`WHERE delisted = false` filters that several call sites rely on never exclude anything.

**Recommended fix:** don't depend on the fetch raising. After a successful fetch, if the newest
bar returned is older than N days (7 is consistent with the new `stale_symbols_d1` gauge), record
a delisting signal *instead of clearing it*. That uses the existing threshold/TTL machinery and
needs no new infrastructure — it changes which branch calls which function.

**Also worth noting: `SSNLF` is NOT delisted.** It returns 23 bars on a relative-period fetch —
our DB simply has 1 bar from 305 days ago. So it is an *ingest* failure, not a dead ticker, and
two symbols that look identical in the DB have completely different causes; the fix above must
not blanket-delist both.

### SSNLF follow-up (attempted 2026-09-08) — force re-ingest does NOT fix it

Ran `ingest_symbol("SSNLF", force=True)`: yfinance returned **751 bars** and `validate_ohlcv`
dropped **750**, leaving the same single bar. Same class as the HK zero-volume finding, but
SSNLF is `market='US'` so it still gets the strict `volume > 0` gate.

**SSNLF is Samsung Electronics' unsponsored OTC ADR** — a grey-market instrument that trades
sporadically. Its one surviving bar is `open=high=low=close=65.21` on volume 1,531: a single
print. The 750 dropped bars are no-trade days.

**Deliberately NOT fixed by loosening the US daily gate.** Two reasons:

1. **The OTC-ADR theory does not generalise.** The only other 5-letter F/Y-suffix ADR in the
   universe, `AMADY` (Amadeus IT Group), has a healthy **752 bars**. So this is one symbol, not
   a class — loosening the US invariant for 1 of 131 US symbols is a bad trade, and I kept that
   gate strict on purpose (liquid US names lose ~1% either way, so there is nothing to gain and
   a real invariant to lose).
2. **The impact is cosmetic, not a trading risk.** SSNLF is on 3 watchlists and produced 104
   signals in 30 days — but **all of them HOLD or WAIT at ~0.8 average confidence, never a
   BUY**. The stale price generates noise, not actionable output.

**Recommended instead:** remove SSNLF from the universe (`active=false`) or from the 3
watchlists. It is an untradeable grey-market ADR that no strategy here should be scoring in the
first place — a data-curation decision, not a code fix. Left for the user, since removing a
symbol someone deliberately added is their call, not mine.

---

## 2. The ML small-sample problem — the only lever left

The 2026-09-07 audit established the retrain cannot finish in one night, and my proposed universe
trim was **retracted** after measurement (169 of 173 symbols produce BUY signals; the 40 a trim
would drop generate 38% of all signal output).

That leaves the feature pipeline, where the real constraint is:

| stage | bars |
|---|---|
| loaded | 752 |
| after feature warmup | 500 |
| after dead-zone filter | **~325** |

`n_test` ≈ 31 rows is what produces the extreme 0.0/1.0 AUCs, the dead-recall pathology, and the
89% suppression rate. Two concrete actions:

- **Backfill price history.** ~667 bars is 2.6 years; the 5-year lookback is already requested,
  the data simply is not there. The only change that attacks small-sample at its source.
- **A/B the dead-zone filter on `cv_auc`** (not test AUC, which is noise at n=31). It discards
  24–36% of post-warmup rows and has never been re-examined since `n_test` fell this low.

**Now that ingestion Area 6 is fixed, both HK symbols gained ~450 bars each** — a reminder that
bar counts can be recovered rather than only waited for.

---

## 3. Un-audited domains, ranked

The two audits covered the trading decision path and ingestion. Remaining, by expected value:

1. **Ranking engine / K-Score** — feeds `min_kscore` gates, LONG's unique `kscore_boost`, and
   position sizing. Never verified as computed-as-documented or point-in-time.
2. **Alert delivery / notification layer** — Domain 6 found *what* was sent was wrong (expired
   contracts); nothing has audited *who* receives what, per-user suppression, or whether tier
   gating holds at send time.
3. **Backtest harness fidelity (BT-1/2/4)** — built recently, never independently audited, and
   it is what future tuning decisions will rest on.
4. **Research engine + LLM cost path** — real spend; a prior audit found a genuine leak.

---

## Explicitly NOT recommended

- **A fleet-wide ML retrain** — 89% of freshly retrained models immediately self-suppress, and
  stale models measure marginally *better* than fresh.
- **Any threshold retuning** — measured worse every time it has been tried.
- **Retuning the options-flow direction logic** — verified correct; its 20.6% bearish win rate
  measures a two-day rally, not the alert.
- **Rewriting the 14 historical SPY bars** — self-healed, 0.128% overshoot, nothing consumes a
  daily bar's `open`.
- **Re-auditing the six trading domains** — all 17 findings fixed and deployed.
