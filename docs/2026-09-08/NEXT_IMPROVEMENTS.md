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

---

## Update 2026-09-08 (later session): Ranking/K-Score audit + next-improvements pass — 11 findings, all fixed (tier 366)

Two rounds of work after the items above. Full detail:
`docs/audits/2026-09-08-ranking-kscore-audit.md`,
`docs/incidents/market-hours-gating-bugs.md`, `docs/incidents/wire-shape-mismatches.md`.

**Ranking/K-Score audit (6):** `AUD-RANK-RSPLACEHOLDER` (HK relative strength was a fabricated
50.0 on 56.5% of rows, and the weight tuner had already halved the factor's weight from it),
`AUD-RANK-BENCHINGEST` (XLP 102 days stale — omitted from the only job that feeds benchmark
ETFs), `AUD-RANK-SECTORLABELS` (sector→ETF map keyed on a taxonomy the data never emits, in 3
services), `AUD-RANK-THINPEERS` (null value/growth was cohort fragmentation, not missing
fundamentals), `AUD-RANK-CURVEDRIFT`, `AUD-RANK-VOLSATURATE`.

**Next-improvements pass (5):** `AUD-DIGEST-HOLIDAYBLIND` (13 emails on Labor Day showing
Friday's prices as live), `AUD-HOLIDAY-2027GAP` (3 drifted holiday calendars, 2 expiring
2027-01-01), `AUD-ADMINPAGE-GUARDGAP` + `AUD-NAV-ROTATIONEXPLAINER-DEADLINK` (nav/page guards
disagreeing in both directions), `AUD-CONVICTION-SOFTDRIFT` (3 small items),
`AUD-ING6-MARKETINFER` (the tier-364 HK volume fix was bypassable via `ingest_universe`).

### RESOLVED 2026-09-08 — was the one open item, now applied with user approval

`tune_kscore_weights` runs **Sunday 14:00 PT**, 365-day lookback, reading `rs_score` verbatim
from persisted `rankings` rows. **1,956 HK rows dated 2026-06-01 onward still hold the fabricated
`50.0`.** They are `50.0`, not `NULL`, so the tuner's own exclusion logic will not skip them and a
run may **re-demote `relative_strength` and undo the reset**.

```sql
UPDATE rankings r SET rs_score = NULL
FROM stocks s
WHERE s.id = r.stock_id AND s.market = 'HK' AND r.rs_score = 50.0;
```

HK-scoped deliberately: the 6 US rows at exactly 50.0 are **VOO, IGV, GOOG** — index-tracking
funds that genuinely move with their benchmark, which is also independent corroboration that the
HK values were fabricated. Nulling hands those rows to `_kscore_active_weights_for_row`, which
already drops the factor when `rs_score IS NULL`.

**APPLIED 2026-09-08 with explicit user approval** (it is a production write, and the SELECT-only
constraint correctly blocked the first attempt — permission was requested rather than worked
around). Verified inside the transaction before commit:

| Check | Result |
|---|---|
| Rows updated | **1,915** (HK placeholder 1915 -> 0) |
| HK nulls | 629 -> 2,544 — accounts for every changed row |
| HK total | 3,459 unchanged — nothing deleted |
| US rows at 50.0 | **6**, preserved (VOO/IGV/GOOG) |
| Redis `stockai:kscore_weights` | empty -> falls back to the 0.10 default |
| Live `relative_strength` | **0.1**, weight set sums to exactly 1.0 |
| HK data the tuner now reads | 2,544 excluded as NULL, **915 real scores** spanning 0-100 |

**A correction worth keeping:** this document and several session messages quoted **1,956** rows.
The update touched **1,915**. The 41-row gap is the fix already working — once
`AUD-RANK-RSPLACEHOLDER` deployed, HK rows began being written with a real score or `NULL` instead
of the placeholder, so that many had already been rewritten. A count moving DOWN between measuring
and acting is the expected direction here, not a discrepancy to chase.

If a future session needs to re-check this: `stockai:kscore_weights` should be absent or carry
`relative_strength` near `0.10`. If it is back near `0.05`, the tuner has re-learned from
placeholder data and something is re-introducing the fabricated value — look at `_rs_score()`'s
fail direction first.

### Still not worth doing (unchanged from above)

The five items listed earlier in this document remain correct. Add one: **do not add an
`n_factors_used` column to `rankings`** to signal K-Score factor exclusion — it needs a migration
and touches the write path, and `ranking.sector_percentile_unavailable` logging
(`cohort_too_thin` vs `metrics_missing`) already closes the diagnostic gap that mattered.

### Next un-audited domains (ranked, unchanged)

1. **Alert delivery / email pipeline** — partially covered now by `AUD-DIGEST-HOLIDAYBLIND`, but
   the survey found the dedup/cooldown/suppression path itself **clean**, so this dropped in
   priority.
2. **Backtest harness** (BT-1/BT-2/BT-4) — deliberately not re-audited in depth; BT-2 fidelity was
   built and run before BT-1 and the docs already carry two self-corrections.
3. **Research engine / LLM cost** — surveyed and found **clean**: all 12 `CALL_SITE_*` constants
   genuinely wired, quality-tiered TTLs, DB read-through surviving restarts, and `log_llm_call()`
   correctly storing NULL rather than a fabricated 0 on failure paths.

Also confirmed clean in the survey and **not worth re-checking**: api-gateway `_ROUTES`
completeness (all 28 prefixes present), api-gateway auth edges (path normalization before the auth
decision, blacklist fails closed), signal-alert wire shapes (`SignalAlertOut` vs `SignalAlertItem`
match field-for-field with `response_model` set), `check_price_alerts()`'s falsy-zero handling, and
a fail-open/falsy-zero grep sweep across five services that returned **zero hits**.

One genuinely latent item found and **deliberately NOT fixed** (recorded only): `_service_token()`
is copy-pasted 7 times and **3 copies never check expiry** (research-engine, decision-engine,
paper_trading_engine).
Tokens are 365-day and containers restart far more often, so there is no live symptom — recorded
because it is a real single-source-of-truth violation in a repo that keeps getting bitten by
drifted duplicates. Consolidating into `shared/common/` is the fix if it ever matters.


---

## Update 2026-09-08 (third pass): image durability closed, one survey claim corrected

**AUD-DEPLOY-IMAGEDRIFT (HIGH, resolved).** Every backend fix from this session existed ONLY
inside the running containers, not in the images. Measured directly rather than assumed:

```
                                  running container    image
shared/common/market_calendar.py        present         ABSENT
_VOL_SOFT_FLOOR (ranking)               4 refs          0 refs
```

A single `--force-recreate`, a `down/up`, or an unplanned reboot would have reverted all of it —
with asymmetric failure modes: the missing calendar module makes market-data fail on IMPORT
(loud), while the ranking fixes revert to fabricating `rs_score = 50.0` and silently re-corrupt
the weight tuner (quiet, and exactly the bug this session existed to fix). This is the documented
`docs/incidents/docker-deploy-staleness.md` class.

**Fixed properly:** `docker compose build market-data ranking-engine`, then verified the fixes are
in the IMAGES (not just containers), then `--force-recreate` — the very operation that used to
destroy them — and confirmed all of it survived:

```
calendar coverage      {'nyse': 2027, 'hkex': 2027}
Labor Day 2026 US      False
soft layers            [OBV, ADX, ML probability, MACD, Uptrend]
2800.HK benchmark      -0.0204   (real DB value, no yfinance)
rs_score(None)         (None, None)   <- fails closed
Consumer Cyclical      XLY
relative_strength      0.1
vol at 20%/day         0.625     <- used to tie at exactly 0.0
```

**A CORRECTION to the previous survey's `_service_token()` claim.** It reported "copy-pasted 7
times, 3 copies never check expiry," naming research-engine, decision-engine and
paper_trading_engine. Verified directly:

- Only **3 real definitions** of `_service_token` exist, and **all three check expiry** with a
  token cache. The "7" counted importers as copies.
- **research-engine has no `_service_token` at all** — it has `_generate_with_service_token`,
  an unrelated function. The survey conflated two names.
- The genuine issue is a **second family**, `_svc_token()`, in decision-engine
  (`aggregator.py:31`) and paper_trading_engine (`:67`). Both do
  `if _svc_token_cache: return _svc_token_cache` against a 365-day token, so neither refreshes.

**Deliberately NOT fixed, for a measured reason:** the longest container uptime on the box is
**2 days**. The ~358-day threshold is unreachable in practice, so a fix would be churn with no
behavioral change. Recorded so it is not re-discovered as a live bug — and so the corrected
*shape* of it (two `_svc_token`, not three `_service_token`) is on record.

**Also checked, clean:** zero DQ gauge alarms and zero scheduler job failures in 24h.

---

## Update 2026-09-08 (fourth pass): all-gates audit — 12 findings, 1 CRITICAL fixed (tier 368)

Full detail: `docs/audits/2026-09-08-gate-audit.md`. **Read its pattern section before fixing any
gate.**

### DEFERRED BY USER DECISION — SHORT-style R:R (revisit later)

`AUD-SIGALERT-RRUNREACHABLE` is fixed for GROWTH and LONG (877 of 1,365 BUY signals, 64%) and
partially for SWING (low-ATR only). **SHORT remains blocked and the user chose to leave it for
now.**

This is not a bug. SHORT's own style params — `stop_pct 0.97` / `target_pct 1.05`, i.e. a 3% stop
and a 5% target — cap R:R at **1.67 by design**, and the style cap binds before the derived
multiple does. Against a 2.25 floor it cannot pass. Blocking it is now a *correct* answer.

Affects **234 of 1,365 BUY signals (17%)**. Three options when revisiting:

1. **Leave it permanently** — SHORT candidates never alert. Defensible if 1.67:1 genuinely is not
   worth taking.
2. **Widen SHORT's target** (5% → ~8%) so its geometry can reach 2.25:1. This is a REAL
   trading-parameter change and must be validated, not guessed.
3. **Per-style R:R floors** — judge SHORT at ~1.6, its designed geometry. Most principled, most
   work.

**Do NOT force it by removing the style target cap.** That would fabricate a target SHORT's tuned
parameters do not support — the same error class as the original bug.

### Still open from this audit, ranked

1. **`AUD-EXIT-INDICATORSEMPTY` (CRITICAL)** — `momentum_fade` and PT-H5's RSI trail read the
   `indicators` table: **0 rows, no writer anywhere in the codebase**. Both unconditionally dead
   while reading as enabled. The RSI data is in `signals.reasons` instead (43,024 rows / 90d, 388
   above 75), which `_monitor_positions` already reads twice. **Bounded:** only `indicators` and
   `stock_connect_flows` are truly empty, and the latter is harmless (no readers).
2. **`AUD-ENTRY-CONSECLOSS-DEADLOCKLOOP` (HIGH)** — the consecutive-loss breaker is permanently
   bypassed at zero open positions and re-grants **every scan cycle**. Three portfolios are in
   that state; portfolio 5 took **7 trades under it and all 7 lost, −$452.92**.
3. **`AUD-ENTRY-BREAKOUTREF-DEONLY` (HIGH)** — the wrong-path pattern; DE's extension guard
   computes a constant −3.38% and cannot fire.
4. `AUD-EXIT-HKENTRYDATE` (HIGH, latent) · `AUD-CONVICTION-RSIDIV-NOWRITER` (MEDIUM) ·
   `AUD-ENTRY-CONFSIZEMULT-HKCONSTANT` + `AUD-ENTRY-SIZEEXCESS-STALEMINSCORE` (MEDIUM — **both
   raise risk capital on the two portfolios measuring worst**) · `AUD-EXIT-CORRSIGNMASK` ·
   `AUD-ENTRY-NYSEHOLIDAY-FOURTHCOPY` + 4 LOW.

### Methodology note worth keeping

`pg_stat_user_tables.n_live_tup` is a **stale estimate**, not a count. A sweep using it returned
**29 "empty" tables** including `users` and `price_alerts` (137 real rows). Only `COUNT(*)`
answers "is this table empty" — which mattered here because an empty table was the CRITICAL
finding.
