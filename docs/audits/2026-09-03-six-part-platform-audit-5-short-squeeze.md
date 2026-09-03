## Deep Audit Series (2026-09-03): Short Squeeze Alerts — 5 of 6

**Scope**: `services/market-data/src/services/scheduler.py`'s 3 squeeze/gamma alert-emitting
functions (`check_short_squeeze_alerts`, `check_squeeze_ignition_alerts`,
`check_gamma_unwind_alerts`), their outcome-evaluation functions, calibration helpers, the
`squeeze_alert_outcomes`/`OptionsFlowAlertOutcome` DB tables, and the admin/frontend surfaces
(`admin.py`'s `squeeze_alert_performance`/`squeeze_alert_backtest`/`options_flow_alert_backtest`,
`frontend/src/pages/squeeze-alert-performance.tsx`/`options-flow-alerts.tsx`). Sequential
platform audit series (AI Signal → Decision-Making → Paper Trading → Model Training →
**this domain** → Options Trading & Alerts), per `docs/AUDIT_DOMAIN_SERIES_TEMPLATE.md`.

**Important context**: this exact domain (Short Squeeze / Gamma / Prebreakout alerts) was
already exhaustively audited 3 days ago, on 2026-08-31, as part of a **different** 5-part audit
series (`docs/audits/2026-08-31-five-part-deep-audit-series.md`), fixing 2 real bugs
(`AUD-SQUEEZE-HKLUNCHBREAK`, `AUD-SQUEEZE-IGNITION-DASHBOARD-OMITTED`) and explicitly concluding
"remaining findings: NONE" after re-checking a long list of prior candidates. The user
explicitly chose to run a full fresh re-audit anyway, on the chance that 3 days of elapsed time
or Domains 1-4's own fixes surfaced something new. This pass found **genuinely new ground**
the 2026-08-31 pass did not examine (real win-rate/return display correctness, and the
backtest's own fidelity to the live alert's current gates) — nothing here duplicates that
pass's own findings.

### Ground truth (queried directly against production before dispatching)

Real win-rate/return data by `alert_type` (`squeeze_alert_outcomes`, `is_correct_5d`/`return_5d`):
`gamma_unwind_puts` 188 total/56.3% win rate/-0.53% avg return (still firing through
2026-09-02); `gamma_unwind_calls` 87 total/37.5% win rate/-1.06% avg return (still firing);
`short_squeeze` 11 total/9.1% win rate/-6.17% avg return (all 11 fired 2026-08-17 to
2026-08-24, none since); `squeeze_ignition` 0 rows (already known/expected per the 2026-08-31
audit).

### Headline findings

1. **HIGH, independently re-verified, FIXED — return colors on both squeeze/gamma and
   options-flow performance dashboards ignored alert direction, displaying real wins in red
   and real losses in green for every bearish-thesis row.** `gamma_unwind_puts` is a bearish
   thesis (a win = price fell past the hurdle) — `evaluate_squeeze_alert_outcomes()`'s own
   `is_bearish_thesis = row.alert_type == "gamma_unwind_puts"` (scheduler.py:5215) correctly
   flips the WIN condition, but the stored `return_Nd` value itself is never sign-adjusted
   (line 5227: `ret = (price - row.entry_price) / row.entry_price`, always raw). Both frontend
   pages colored every return with a flat `>= 0 ? green : red`, with zero awareness of this —
   independently re-verified by reading every color-logic line in both files. A genuine
   `gamma_unwind_puts` win (price correctly fell, `return_5d` negative) displayed in red; a
   genuine loss (price rose, `return_5d` positive) displayed in green — self-contradictory
   against the same row's own green win-rate pill. The exact same pattern exists in
   `options-flow-alerts.tsx` for `OptionsFlowAlertOutcome`'s bearish-direction rows (same root
   cause: `evaluate_options_flow_alert_outcomes()`'s own `is_bearish_thesis =
   row.direction == "bearish"`, same raw unflipped return). **Fixed**: added a `returnColor()`
   helper to both pages (checks `alert_type`/`direction` before choosing red/green), applied at
   every return-displaying cell; `squeeze-alert-performance.tsx`'s backtest section
   (`BacktestWindowCell`) was correctly left untouched — it's bullish-only by construction
   (`short_squeeze` has no bearish variant, per the alert's own docstring) so a flat color was
   already correct there.

2. **MEDIUM, found while fixing Finding 1, FIXED — `options_flow_alert_backtest()`'s
   `by_sweep`/`by_volume_oi_band` groupings blended raw (unflipped) returns from BOTH bullish
   and bearish alerts into the same bucket**, meaning a genuine bullish win (+5%) and a genuine
   bearish win (-5%) would average toward ~0% — understating real performance whenever a bucket
   mixed directions. `by_direction` itself was already fine (each of its own buckets is
   single-direction by construction) — but its OWN bearish bucket also displayed the same raw,
   unflipped sign, inconsistent with the other 2 groupings once THEY were fixed to be
   thesis-direction-aware. **Fixed**: `_bucket_stats()` (admin.py) now sign-flips the bearish
   rows' return before it enters `avg_return_pct`, so the number always means "return in the
   direction the alert's own thesis predicted" — positive always means the thesis was right,
   matching `is_correct`'s own semantics exactly and consistent across all 3 groupings. This
   also means `squeeze-alert-performance.tsx`'s backtest colors (bullish-only, untouched by
   Finding 1) remain correct, and `options-flow-alerts.tsx`'s OWN backtest `BacktestWindowCell`
   needed NO frontend fix — the number itself is now correctly signed, so a flat `>=0=green` is
   accurate there.

3. **HIGH, independently re-verified, FIXED — `squeeze_alert_backtest()` never applied the RVOL
   confirmation gate the live `check_short_squeeze_alerts()` has required since
   `AUD288-SQUEEZE-NO-VOLUME-CONFIRM` (2026-08-18).** Independently confirmed via grep: zero
   RVOL/volume references anywhere in `admin.py` before this fix. The backtest replayed only
   the original 2-gate strategy (short-float floor + same-day price-move threshold), a strategy
   that hasn't existed in production for 2+ weeks — meaning the backtest's reported historical
   win-rate/return numbers understate what the LIVE, current alert would actually have
   produced. **Fixed**: added a parallel `volume_map` (day, volume) alongside the existing
   `price_map`, a new `_trailing_avg_volume()` pure helper (20-trading-day trailing mean,
   matching `refresh_avg_volume_cache()`'s own real ~1-month lookback), and a
   `day_volume / avg_volume >= _SQUEEZE_RVOL_BASE` check before a day is added to
   `candidate_days` — using the UNSCALED base threshold (2.2×) rather than the live alert's own
   session-elapsed-scaled version, since a completed historical daily bar always represents the
   full session's volume (the live alert's intraday partial-day scaling has no meaning here).
   4 pre-existing tests in `test_squeeze_alert_outcomes.py` needed their fixtures updated to
   include a real volume spike on the intended candidate day (they previously relied on
   `_make_price()`'s flat 1000.0 default volume for every bar, which the new gate correctly now
   rejects at exactly a 1.0x ratio) — this is expected fallout from closing a real gap, not a
   regression; the underlying behavior each test verifies (short-float floor, price-move
   threshold, point-in-time window boundaries) is unchanged and still correctly tested,
   now alongside a real, satisfied RVOL gate.

### Investigated and judged NOT a bug (a real, honest tradeoff — not forced into a fix)

**The `short_squeeze` alert type's 9.1% win rate (n=11) and its 9+ day silence since
2026-08-24** were investigated in depth and are NOT a bug:
- The silence is not shared infrastructure failure — `gamma_unwind_calls`/`gamma_unwind_puts`
  fired continuously through 2026-09-02 in the same job/file, ruling out a broken scheduler or
  Redis lock. A live filter simulation confirmed the candidate universe isn't empty (21 of 172
  cached symbols still clear the 15% short-float floor with fresh data) — the silence is the
  narrow, legitimate intersection of (high-short-float) × (live 3%+ move) × (RVOL confirmation)
  simply not co-occurring recently, not a broken gate.
- The n=11 historical sample is close to uninformative about the alert's true long-run
  quality: 9 of 11 fired within a single correlated 8-day momentum-reversal window (a period
  where SPY itself fell ~2% and the named symbols fell 11-22% cumulatively) — the effective
  independent sample size is closer to 2 than 11. The 9.1% win rate should not be read as a
  reliable long-run figure either way, positive or negative.
- Individual alert-row slippage (e.g. NBIS: `alert_price=277.68` vs. next-day
  `entry_price=248.43`, a ~10.5% gap) is real but is the honest, disclosed cost of the alert's
  own deliberate design — `entry_date = fired_date + timedelta(days=1)` is an intentional
  no-lookahead choice (confirmed by reading the code), and large gaps like this reflect a
  genuinely extreme, already-peaking intraday move that reverts hard by the next real entry
  opportunity — not a bug to fix, but a real, inherent tradeoff of an intraday-momentum alert
  design the code's own docstring already explains and defends.

### Unusual Whales — a real, nuanced situation, not a repeat of prior domains' boilerplate

`unusual_whales.py`'s `get_short_interest(symbol)` (real UW short-interest/borrow-fee/shares-
available data) IS used — but only by `GET /short_squeeze` (the screener endpoint powering
`short-squeeze.tsx`'s "Prime Candidate" banner, `routes.py:2486`), NOT by
`check_short_squeeze_alerts()` (the real-time email alert), which instead reads
`stockai:fundamentals:v2:{symbol}` (a free-tier-equivalent, weekly-refreshed cache). This means
the screener banner and the real-time alert email can disagree on short-interest data for the
same symbol at the same moment. A live spot-check found real divergence for 2 of the 11
historical `short_squeeze` alerts (IMVT: UW 8.22% vs. free-source 18.28%; CRWV: UW 12.83% vs.
18.89% — both among the worst-performing of the 11). `get_short_interest()` is already
called live for the same candidate set by the screener (6h-cached), so wiring it as a
**corroboration gate** into the real-time alert (not a full replacement) would cost near-zero
incremental API calls given the pre-filter typically narrows to 0-3 symbols per cycle.
**NOT fixed this pass** — this needs its own dedicated design decision (does a UW/free-source
disagreement suppress the alert, just get logged, or something else?) rather than a quick
patch bundled into this domain's other work. One caveat worth flagging for that future design
pass: UW's `fee_rate`/`rebate_rate` fields appear to return a shared floor value across several
unrelated large-caps spot-checked (AAPL, TSLA, IMVT, CRWV, FCEL all showed 0.25-0.28/3.29-3.38)
— likely a tiered/rounded "easy to borrow" default rather than a precise per-symbol rate, so
the borrow-fee figures specifically deserve more caution than the short-float percentage.

### Checked and found CLEAN (new checking, not a repeat of the 2026-08-31 pass)

- Evaluator windows (1d/2d/3d/5d/10d/20d) resolving correctly on schedule — an initial false
  alarm about "stalled" 10d/20d windows self-corrected on closer inspection (they simply
  hadn't reached their calendar target yet, not stuck).
- No interaction between this domain and Domains 1-4's own fixes in the current series
  (signal-freezing, portfolio config, broker-poll fixes all touch code paths squeeze alerts
  don't use).
- The 2026-08-31 HK-lunch-break fix is confirmed irrelevant to the all-US 11-row
  `short_squeeze` sample (its own US branch untouched by that fix).
- Fundamentals-cache-miss instrumentation and calibration cache-key isolation are symmetric
  across all 4 alert types.

### What was NOT independently verified / left open

- Whether UW would have actually blocked the 2 historical divergent alerts (IMVT/CRWV) on
  their real fired dates — UW has no historical archive, only current settlement data, so this
  can only be checked going forward, not retroactively.
- Whether `gamma_unwind_calls`'s 10d-vs-5d win-rate divergence (not independently re-derived
  here) is a real pattern or was itself partly an artifact of Finding 1's display bug making it
  hard to reason about visually before the fix — worth a fresh look now that colors are correct.
- Squeeze-family calibration band population for `gamma_unwind_calls` (a candidate finding that
  all 87 real rows may fall into a narrow band range, making 2 of 3 bands structurally dead) —
  flagged by the audit subagent but not independently re-run against live data by me before
  recording; left as an unconfirmed lead for a future pass rather than acted on here.

### What to check if this needs re-verifying

```bash
# Confirm the color fix is deployed (both pages):
grep -n "returnColor" frontend/src/pages/squeeze-alert-performance.tsx frontend/src/pages/options-flow-alerts.tsx

# Confirm the backtest RVOL gate is deployed:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-market-data-1 grep -n '_trailing_avg_volume\|_SQUEEZE_RVOL_BASE' /app/src/api/admin.py"

# Re-check short_squeeze's real win rate/silence — has it fired again, and does the sample
# size look less correlated/clustered now?
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
   \"SELECT alert_type, COUNT(*), MIN(fired_date), MAX(fired_date), \
     ROUND(100.0*COUNT(*) FILTER (WHERE is_correct_5d)/NULLIF(COUNT(*) FILTER (WHERE is_correct_5d IS NOT NULL),0),1) as win_pct \
     FROM squeeze_alert_outcomes GROUP BY alert_type;\""
```
