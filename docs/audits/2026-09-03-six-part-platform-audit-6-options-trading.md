## Deep Audit Series (2026-09-03): Options Trading & Alerts — 6 of 6 (SERIES COMPLETE)

**Scope**: every options-specific trading/alerting surface not already covered by Domain 5
(Short Squeeze Alerts) — `services/market-data/src/api/routes.py`'s options endpoints
(`get_options_flow`, `compute_options_pressure_score`, `_options_flow_gex_component`,
`get_options_chain`/`get_options_expirations`, `get_options_screener_route`/
`get_option_trades_route`, `compute_options_game_plan`/`get_options_game_plan`);
`services/market-data/src/services/scheduler.py`'s `check_options_flow_alerts()` and
`evaluate_options_flow_alert_outcomes()` (the alert-firing/outcome-recording logic itself, as
distinct from Domain 5's fixes to its downstream consumers); `frontend/src/pages/options-flow.tsx`,
`option-trading-guide.tsx`, and the Options Game Plan section of `stock/[symbol].tsx`;
`shared/common/unusual_whales.py`'s `get_gex_levels()`. Final domain of the sequential platform
audit series (AI Signal → Decision-Making → Paper Trading → Model Training → Short Squeeze
Alerts → **this domain**), per `docs/AUDIT_DOMAIN_SERIES_TEMPLATE.md`.

**Explicitly out of scope** (already covered by Domain 5 — do NOT re-audit):
`options-flow-alerts.tsx`'s display logic (the bearish-return-color bug), `options_flow_
alert_backtest()`'s return-averaging logic (the mixed-direction sign bug), `squeeze_alert_
backtest()`'s RVOL gate — all already fixed and documented in
`docs/audits/2026-09-03-six-part-platform-audit-5-short-squeeze.md`.

### Ground truth (queried directly against production before dispatching)

`options_flow_alert_outcomes` had 1471 total rows (698 bullish, 773 bearish), fired 2026-09-01
through 2026-09-03, with **zero** rows resolved on any forward-return window (`return_1d`
through `return_20d`, `is_correct_1d` through `is_correct_20d` all NULL) despite `entry_price`
being successfully filled for 1137 of 2026-09-01's 1145 rows. This was flagged as the #1 thing
to investigate before dispatching — grounded via live logs showing the evaluator runs daily at
18:25 ET and had run successfully on both 2026-09-01 and 2026-09-02 (each logging a real
`{"pending": N, "evaluated": M}` count), with today's (2026-09-03) run not yet having happened
as of the time this was checked.

`compute_options_pressure_score()` is confirmed a genuinely LIVE, actively-consumed surface
(unlike its already-documented-dead usage inside the paper-trading entry-scan loop, per Domains
2/3) — served via `GET /options_flow`, displayed on the stock detail page. Real GEX data has
persisted history: `gex_snapshots` (37 rows), `options_flow_snapshots` (1265 rows).

### Headline findings

1. **MEDIUM, independently re-verified, FIXED — `compute_options_pressure_score()`'s
   `cp_ratio` conviction component was asymmetric, contradicting its own docstring.** The
   docstring explicitly states the component should reach the full 40 points at BOTH
   documented extremes (`cp_ratio<=0.2` on the bearish side, `cp_ratio>=5.0` on the bullish
   side — both a genuine 5x fold-change from the 1.0 neutral point). The actual formula
   (`abs(cp_ratio - 1.0) / (5.0 - 1.0) * 40.0`) is symmetric in LINEAR distance from 1.0, not
   fold-change — since 0.2 and 5.0 are NOT equidistant from 1.0 in absolute terms (0.8 vs.
   4.0), `cp_ratio=0.2` only reached 8.0/40 while `cp_ratio=5.0` correctly reached 40.0/40.
   Independently re-verified by hand-computing the formula and confirming the codebase's own
   pre-existing test (`test_a_low_cp_ratio_below_1_still_scores_high_conviction_not_zero`) had
   already hand-verified this exact `8.0` value as if it were correct — the test's own author
   checked "not zero" but never checked "does this actually hit the docstring's claimed max,"
   a real, subtle miss. Confirmed real production impact: live `options_flow_snapshots` data
   shows 23 extreme-bearish (`cp_ratio<=0.3`) vs. 119 extreme-bullish (`cp_ratio>=4.0`) rows —
   a real, non-trivial population systematically under-scored on the bearish side. This reaches
   the frontend directly: the stock detail page's "Pressure {score}" badge (explicitly framed
   in its own tooltip as "conviction/intensity, not direction") would show a visibly weaker
   badge for an equally-extreme bearish reading than a bullish one, contradicting its own stated
   design. **Fixed**: the formula now scales each side of 1.0 separately — `(cp_ratio - 1.0) /
   (5.0 - 1.0) * 40.0` above neutral (unchanged), `(1.0 - cp_ratio) / (1.0 - 0.2) * 40.0` below
   neutral (new) — so both documented extremes now correctly reach 40.0. The one pre-existing
   test whose hand-verified `8.0` assertion documented the bug as expected behavior was updated
   to assert the correct `40.0`; a new test explicitly confirms both extremes now score
   identically.

### Investigated and confirmed NOT a bug

**The "zero options-flow-alert outcomes have ever resolved" concern — confirmed to be checked
too early, not a code defect.** Every row with a filled `entry_price` from the 2026-09-01
cohort has `entry_date=2026-09-02`; the 1d-window target date is therefore `2026-09-03` —
today, and not yet past the evaluator's own scheduled 18:25 ET run at the time this was
checked. `evaluate_options_flow_alert_outcomes()` (scheduler.py) is structurally identical to
the already-independently-verified-clean `evaluate_squeeze_alert_outcomes()`, uses the same
`_squeeze_outcome_lookup_price()` helper, and live logs confirm both prior scheduled runs
(2026-09-01, 2026-09-02) executed successfully. No code fix needed — this resolves naturally
once today's run completes; a future check should simply re-query after 18:25 ET.

### A secondary, out-of-scope observation (LOW, informational, not fixed)

8 of 1145 `fired_date=2026-09-01` rows are permanently stuck with `entry_price=NULL` — all the
same underlying symbol, **V (Visa)**, whose `Price` table is missing a D1 (daily) bar for
2026-09-02 specifically (every other trading day back through 2026-08-03 has a clean bar; only
this one date is missing, and only for this symbol). V's 5-minute intraday data is confirmed
flowing normally. This is a single-symbol daily-bar-ingestion gap in the general price
ingestion pipeline — outside this domain's scope (not options-specific code) — but worth
flagging since it will silently, permanently exclude these 8 rows from any win-rate calculation
with no error ever logged. **Not fixed this pass** — recommend checking whether V's 2026-09-02
D1 bar has appeared by the next ingestion cycle; if still missing after another full day, that
warrants its own investigation into the daily-bar ingestion job.

### Checked and found CLEAN

- **`get_gex_levels()` (unusual_whales.py)** — correctly fail-open, handles both list-of-rows
  and single-dict UW response shapes, correctly Redis-cached (15 min), gated behind
  `is_available()`.
- **GEX-proximity enrichment wiring end-to-end**: confirmed genuinely live, not merely
  documented as planned — `is_available()` returns `True` in production,
  `compute_gex_snapshots_eod()` (scheduler.py) is correctly scheduled (17:15 ET daily),
  correctly rate-limited, correctly upserts via `upsert_gex_snapshot()` with idempotent
  `ON CONFLICT DO UPDATE` — real, sane sample values confirmed (e.g. APP: call_wall=320,
  put_wall=292.5, gamma_flip=312.19).
- **`check_options_flow_alerts()`**: correctly gated, correct dual dedup (per-contract plus a
  coarser per-symbol/direction cooldown, matching the documented `AUD-OPTIONSFLOW-FLOODED`
  fix), correct candidate-recording regardless of email cap, correct fail-open Redis-lock
  handling, correct 4-way call/put × ask/bid-dominant direction mapping (verified against real
  options theory).
- **`_record_options_flow_alert_outcome()`** — correct existence-check-before-insert, correct
  fail-open rollback-on-exception.
- **`options_flow_alert_performance()` (admin.py, the LIVE win-rate endpoint, distinct from
  Domain 5's already-fixed backtest endpoint)** — correctly reads `is_correct_Nd` (already
  thesis-direction-aware at write time, unlike the raw `return_Nd` display bug Domain 5 found
  in the frontend pages), correct fraction-to-percent conversion.
- **`get_option_trades()`/`get_options_screener()` (unusual_whales.py)** — correct `is not
  None` (not truthy) checks throughout, including `max_dte=0` (0DTE) — no falsy-zero bug,
  unlike several found in earlier domains of this series.
- **`compute_options_game_plan()`** — protective-put/covered-call math independently
  re-derived and confirmed correct (`effective_floor_price = strike - mid`,
  `effective_cap_price = strike + mid`); `_nearest_expiry_in_dte_window()`/`_nearest_strike()`
  logic sound; frontend `option-trading-guide.tsx`'s own worked example arithmetically matches
  the backend formulas exactly. Confirmed this feature deliberately has no outcome-tracking
  table by design (its own docstring frames it as "here is what insuring/collecting income
  would currently cost," not a prediction) — correctly not treated as a finding.
- **`get_options_chain()`/`get_options_expirations()`/screener/scanner/net-flow routes** — all
  correctly fail open, correctly gated, correctly cache-scoped, no falsy-zero or param-mapping
  bugs found.
- **`frontend/src/pages/options-flow.tsx`** — all 5 tabs correctly disclose scope/freshness, no
  return-color or direction-display bugs (this page shows raw candidates, not win-rate
  outcomes — genuinely distinct from the separate `options-flow-alerts.tsx` file Domain 5 fixed).
- **`option-trading-guide.tsx`** — content-accurate throughout, no code, matches real options
  mechanics and the backend's actual computed fields.
- **Unusual Whales wiring — full, specific answer**: correctly and fully wired for every
  surface meant to use it — `get_gex_levels()` (GEX proximity + gamma-exposure endpoint +
  persistence), `get_short_interest()` (squeeze score, per Domain 5), `get_flow_alerts()` (the
  options-flow alert), `get_options_screener()`/`get_option_trades()`/`get_market_tide()`
  (Options Flow tab's 3 live-fetch views), `get_dark_pool_prints()` — all gated behind the
  same `is_available()` contract, all fail-open. `compute_options_game_plan()` deliberately
  does NOT use UW (uses yfinance's real options chain directly) — correct, since UW doesn't
  materially improve strike/expiry/bid-ask data over yfinance for this specific feature, and
  the feature's own docstring never claims otherwise.

### What was NOT independently verified / left open

- V's D1-bar ingestion gap's root cause (secondary finding above) — outside options-specific
  code, needs its own investigation if it persists past the next ingestion cycle.
- UW's `/api/screener/option-contracts`/`/api/option-trades` exact response field names —
  the code's own defensive multi-key-name parsing is a reasonable, honest choice given UW's
  published spec doesn't fully document these two endpoints' shapes, but this wasn't confirmed
  against a live UW response sample.
- `check_dark_pool_alerts()`/`evaluate_dark_pool_alert_outcomes()` — confirmed structurally
  identical to the already-verified-clean squeeze/options evaluators by pattern, but not given
  a full independent line-by-line pass (adjacent to options, not strictly in-scope).

### Overall series note

This is the final domain of the 6-part sequential platform audit series
(AI Signal → Decision-Making → Paper Trading → Model Training → Short Squeeze Alerts →
Options Trading & Alerts). This domain's own surface was narrower/cleaner than several earlier
domains — no falsy-zero bugs, no stale-vocabulary bugs, no per-day-upsert-vs-live-state
divergence — and the leading flagged risk (options-flow outcome evaluation "never resolving")
turned out to be a genuine non-issue rather than a real defect. One real, confirmed, fixed bug
(the `cp_ratio` asymmetry) plus one deferred, out-of-scope observation (V's ingestion gap) is
an honest, non-inflated result for the series' closing domain.

### What to check if this needs re-verifying

```bash
# Confirm the cp_ratio fix is deployed:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-market-data-1 grep -n 'AUD-OPTIONS6-CPRATIOASYMMETRY' /app/src/api/routes.py"

# Re-check that today's options-flow-alert evaluator run resolved real outcomes:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
   \"SELECT direction, COUNT(*) FILTER (WHERE is_correct_1d IS NOT NULL) as resolved_1d, \
     ROUND(100.0*COUNT(*) FILTER (WHERE is_correct_1d)/NULLIF(COUNT(*) FILTER (WHERE is_correct_1d IS NOT NULL),0),1) as win_pct_1d \
     FROM options_flow_alert_outcomes GROUP BY direction;\""

# Re-check whether V's 2026-09-02 D1 bar has appeared:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
   \"SELECT ts, close, volume FROM prices p JOIN stocks s ON s.id=p.stock_id \
     WHERE s.symbol='V' AND p.timeframe='D1' AND p.ts >= '2026-08-28' ORDER BY ts;\""
```
