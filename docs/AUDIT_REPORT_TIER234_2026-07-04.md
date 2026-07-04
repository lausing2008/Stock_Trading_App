# Deep Financial/ML Audit — Tier 234 (2026-07-04)

**Scope:** Signal engine (`signals.py`, calibration in `routes.py`), ML pipeline (`builder.py`,
`trainer.py`, `tuner.py`, `meta_trainer.py`, `hmm_regime.py`), decision engine (`scorer.py`,
`sizer.py`, `hard_rejects.py`, `regime.py`, `aggregator.py`), paper trading engine, ranking
engine (`kscore.py`), technical-analysis core, competitive positioning vs TradingView/Finviz/
WallStreetZen/Morningstar, a systemic config/threshold audit, and a cross-check of every entry
in `docs/KNOWN_LIMITATIONS.md`.

**Method:** 15-agent workflow — 6 parallel deep-read subsystem audits, 1 competitive-rating
agent, 1 dedicated config/threshold-arbitrariness agent, 1 KNOWN_LIMITATIONS.md cross-check
agent, then 6 adversarial verify passes (one per code-audit subsystem) each independently
re-reading the 3 most severe findings against the actual source before they were trusted. The 2
CRITICAL findings (position-sizing multiplier stacking, decision-engine sizer output discarded)
were additionally hand-verified a third time by directly reading the cited lines before being
written into the tracker, since they bear on real capital allocation.

**Outcome:** 15 tracker entries added under Tier 234 in `frontend/src/pages/improvements.tsx`
(13 `todo` findings + 2 `done` documentation deliverables — competitive rating and
known-limitations recheck). This audit is prioritization/documentation output — no code fixes
were applied as part of this pass; see each tracker entry's `fix` field for the specific
next-step recommendation per finding.

---

## Part 1 — Competitive Rating vs TradingView / Finviz / WallStreetZen / Morningstar

*Distinct from Tier 11 (2026-06-12), which was a feature-parity checklist audit. This rates
analytical rigor and trustworthiness of the numbers shown to a user, not feature-list coverage.*

### Summary Ratings Table (1–10, 10 = best-in-class)

| Category | vs TradingView | vs Finviz | vs WallStreetZen | vs Morningstar | Absolute score |
|---|---|---|---|---|---|
| Signal/alert quality | 6 | 7 | 7 | 5 | **6/10** |
| Charting | 3 | 4 | 6 | 6 | **3/10** |
| Screening | 5 | 4 | 6 | 5 | **5/10** |
| Fundamental research depth | 4 | 5 | 5 | 3 | **4/10** |
| Backtesting rigor | 6 | 8 | 8 | 7 | **6/10** |
| Portfolio tracking | 7 | 8 | 6 | 6 | **7/10** |
| Trustworthiness of numbers shown | 6 | 7 | 5 | 4 | **6/10** |

**Signal/alert quality (6/10).** Strongest area, plausibly beating a subset of the competitor
set on one specific dimension: a live reliability diagram (`signal-quality.tsx`) plotting
expected-vs-actual win rate in 5-point confidence buckets, a rolling 90-day accuracy drift
monitor, and a `suggested_min_confidence` derived from real outcome data. None of the four
competitors show this. Docked from 8-9 to 6 because: the forward-return window defining a "win"
isn't shown on the page itself; HK SHORT-horizon SELL is hard-disabled due to a documented 29.2%
win rate (a sensible guardrail, but the resulting survivorship isn't disclosed inline); and
BUY/HOLD/SELL thresholds vary by regime/style so "70% confidence" means different things in
different contexts, with no cross-regime normalization shown.

**Charting (3/10).** Weakest area by a wide margin — this was never built to be a charting
product. Single-symbol line/candlestick with basic overlays, no drawing tools, no custom
indicator scripting, no saved layouts, no volume profile. Only competitive against Morningstar
(itself weak on charting).

**Screening (5/10).** Tier-11 fundamental filters closed real ground on Finviz, but Finviz's
actual differentiator — 7,000+ tickers, sub-second re-filter, saved screens, backtested screener
replay (Elite) — isn't matched. This system's screener runs over a curated universe, not
thousands of tickers, and there's no evidence of screener backtesting.

**Fundamental research depth (4/10).** The AI research report is a legitimate step up from
Finviz, and its failure mode is unusually honest — the UI explicitly labels `report_quality ===
'fallback'` output as placeholder, not real analysis. But it's categorically shallower than
Morningstar's human-analyst moat/fair-value framework built and revised over years.

**Backtesting rigor (6/10).** A genuine surprise strength. `signal-accuracy.tsx` has walk-forward
windowed accuracy with a clickable historical heatmap, a rolling drift monitor, and an empirical
TA/ML blend-weight sweep with a "Calibrate" action — closer to a real research process than what
Finviz or WallStreetZen expose to retail users at all. Not higher than 6 because train/eval
separation isn't visibly proven on-page, and there's no visible slippage/commission drag in the
backtest curve separate from the live paper-trading engine.

**Portfolio tracking (7/10).** Second-strongest area — Sharpe, Sortino, CAGR, max drawdown,
SPY-relative overlay, CSV export with entry rationale. More complete than Finviz (no native
tracker) or WallStreetZen (basic watchlist only). Not higher because Fama-French factor
attribution and Monte Carlo are listed as "done" in Tier 11 but their statistical soundness
wasn't independently re-verified in this pass.

**Trustworthiness of numbers shown (6/10).** Unusually self-aware for a retail product — labels
fallback LLM output as placeholder, shows calibration gaps instead of a bare headline accuracy
number, disables a signal type once data showed no edge rather than showing a number anyway. Not
higher because the underlying pipeline has a documented history (this repo's own CLAUDE.md) of
silent multi-day staleness incidents (missing `jose` dependency causing 401s across 4 services;
a SQLAlchemy cast bug silently dropping signal writes) with no visible freshness indicator next
to the signal shown to a user.

### What This System Does Better Than All Four

1. **Outcome-based confidence calibration exposed to the end user.** None of TradingView,
   Finviz, WallStreetZen, or Morningstar show a retail user a reliability diagram of their own
   rating/signal system's historical accuracy by confidence band.
2. **A real, config-driven gate/sizing engine with visible, named suppression reasons.**
   `paper-gates.tsx` enumerates ~20 named gates and shows, per candidate, which passed/failed
   and why — closer to a live risk-management rulebook than anything the four competitors expose.

### What's Missing (2026 retail-trader expectations, distinct from Tier 11)

- No point-in-time data-integrity guarantee for backtests (survivorship bias is already a
  documented known limitation in CLAUDE.md, not newly discovered here, but still unaddressed).
- Zero options-derived signals (implied volatility, put/call skew, options flow).
- No peer-reviewed or third-party-auditable signal track record.
- No macro/rates/cross-asset context feeding regime detection (SPY + VIX + Fear&Greed only).
- No confidence interval or sample-size weighting on any headline number — a 62% win rate on 40
  signals displays identically to 62% on 4,000.
- No LLM research report provenance/versioning (model/prompt version, data snapshot) — two
  reports for the same stock a week apart aren't reproducibly comparable.

### Would a Real Trader Trust This With Real Money Today?

Cautiously, partially, and only with position sizing gated exactly the way the app already
gates it — not as a standalone oracle. It's one of the few retail-facing systems that measures
and displays its own calibration gap rather than asserting confidence on faith, and it has
demonstrated the discipline to disable a signal type once data showed no edge. But this
project's own incident history shows repeated silent failures that left signals days-stale with
full-confidence display and no visible warning — a trader relying only on the BUY/SELL badge
without checking freshness could have traded on stale signals during any of those incidents.
Trust the calibration methodology; do not trust pipeline uptime/freshness enough to trade
unattended without a visible, always-on freshness indicator next to every signal.

---

## Part 2 — Config/Threshold Audit: ~27 Unjustified Constants

Full methodology: every numeric threshold/weight in decision-engine (`scorer.py`, `sizer.py`,
`hard_rejects.py`), `kscore.py`'s `_WEIGHTS`, and `paper_trading_engine.py`'s `_DEFAULT_CONFIG`
was classified as (a) empirically derived (cites a specific backtest/audit), (b) industry-standard
convention, or (c) arbitrary/unjustified (a specific number with no citation). Category (c),
most-impactful first:

1. **`hard_rejects.py:97`** — `min_confidence` fallback = 62.0, independently duplicated (not
   shared) from `paper_trading_engine.py`'s 45.0 for the same concept. **Verify-pass nuance:**
   `paper_trading_engine.py` always explicitly passes `min_confidence` in decision-engine's
   `config_overrides`, so this fallback is dead code on the current live path — real bug
   downgraded from "silently applies" to "maintenance footgun if a future caller omits the
   override."
2. **`hard_rejects.py:116`** — `regime_min_rr_ratio` fallback = 3.0, narrative-only justification
   ("human traders demand better setups"), no cited data.
3. **`hard_rejects.py:166`** — `max_breakout_extension_pct` fallback = 6.0%, no citation for why
   6% vs 5%/8%. Gates every entry attempt.
4. **`hard_rejects.py:147/152`** — time-of-day gate windows (first 30min/last 15min), asserted
   not derived.
5. **`sizer.py:66-75`** — `research_score_val` tiers (75/65/60) and multipliers
   (1.20/1.00/0.80/0.60), no citation, creates a cliff-edge sizing change for a 1-point score
   difference.
6. **`sizer.py:83-88`** — `confidence_mult` tiers (80/62 breakpoints), rescaled to be "reachable"
   per a T232-DE2 comment but the values themselves aren't economically justified.
7. **`sizer.py:100-105`** — `earnings_mult` tiers (50%/75% size reduction at specific DTE
   windows), no citation.
8. **`scorer.py:52-64`** — breakout-extension "chasing" penalty at 3%, largest single-layer
   score penalty (-3), no citation.
9. **`scorer.py:69-74`** — R:R quality tiers (3.5/2.5), no citation, feeds directly into the
   entry score gate.
10. **`scorer.py:82`** — `volume_z` asymmetric bands (>1.0/<-0.5), no rationale for the asymmetry.
11. **`scorer.py:102/104`** — `bull_prob` thresholds (0.70/0.58), one of the most influential
    single inputs to the entry score, no citation.
12. **`scorer.py:115/117`** — confidence-delta thresholds (±8), no backing data.
13. **`scorer.py:134/136`** — signal freshness thresholds (4h fresh / 18h stale) — **directly
    contradicts** the well-documented 72h `max_signal_age_hours` staleness policy elsewhere in
    the codebase (`paper_trading_engine.py` T222-C, cited to "5×/day refresh, 3 days = 15+
    refreshes stale"). Same concept, two different orders of magnitude, unshared. **Verify-pass
    note:** confirmed real, but this is a ±1 soft-score nudge, not a hard gate — smaller blast
    radius than a hard-reject conflict.
14. **`scorer.py:149/151`** — catalyst-score thresholds (+60/-30), asymmetric, unexplained.
15. **`scorer.py:174-181`** — entry-zone drift 4-way tiering (-2/4/8%), no citation.
16. **`kscore.py:27-34`** — `_WEIGHTS` (technical 0.22, momentum 0.23, value 0.13, growth 0.14,
    volatility 0.18, relative_strength 0.10) — **the master weighting behind every `min_kscore`
    gate across every paper-trading style, zero empirical citation anywhere.** Docstring only
    says value/growth are "proxies until we wire fundamentals." Highest-leverage unjustified
    constant found in this audit.
17. **`kscore.py:85-92`** — RSI-to-score piecewise mapping (breakpoints 50/90/100/62.5, slopes
    2.0/0.5/2.5), no backtest citation.
18. **`kscore.py:96`** — `adx_boost` normalization constants (15, 25, 10), no rationale.
19. **`kscore.py:119`** — volatility score scaling factor 1500, no comment.
20. **`kscore.py:133`** — value-proxy discount scale factor 200, no citation.
21. **`kscore.py:149`** — growth-proxy CAGR scale factor 120, no citation.
22. **`paper_trading_engine.py:299`** — `max_portfolio_drawdown_pct` = 0.20 — the master circuit
    breaker for the whole portfolio, no comment citing 20% vs 15%/25%.
23. **`paper_trading_engine.py:304`** — `max_open_risk_pct` = 0.12, no backing data.
24. **`paper_trading_engine.py:308-309`** — `hold_stall_days`/`hold_stall_max_gain` = 30 days/5%,
    no justification for either number.
25. **`paper_trading_engine.py:329`** — `index_trend_gate_pct` = -1.5% — gates ALL new entries
    market-wide, mechanism explained but not the specific -1.5% value.
26. **`paper_trading_engine.py:474`** — HK `regime_suspension_days` = 7, narrative justification
    only, unlike most other HK numbers in the same dict which cite an audit.
27. **`hard_rejects.py:103`** — `min_stop_dist` floor (`max(price*0.005, 0.05)`), no rationale.

**Cross-file consistency risks (beyond individual arbitrariness):**
- `min_confidence` fallback: 45.0 (paper_trading_engine.py) vs 62.0 (hard_rejects.py) — same
  concept, unshared, currently dead-code on the decision-engine side (see #1 above) but a live
  footgun if a caller ever omits the override.
- Signal staleness: 72h (empirically cited) vs 4h/18h (uncited) — same concept, two orders of
  magnitude apart, no shared constant (see #13 above).
- `min_ta_score` converges to 0.65 in both SWING and HK overrides but via two separately-dated,
  independently-maintained literals (T225-A/T226-B vs T224-C/T226-B) — not currently a bug since
  they agree, but a future edit to one could silently desync from the other.
- `min_rr_ratio` gate (2.0, pass/fail) vs `scorer.py`'s scoring tiers (2.5 = merely "Acceptable,"
  3.5 = "Excellent") — same axis, different boundary sets, no cross-reference.

**Representative empirically-derived constants (category (a), for contrast):** `signals.py`'s
SWING `buy_threshold` history (SA-28/SA-31/SA-32, each citing win-rate deltas from specific
outcome audits); `paper_trading_engine.py` T226-A `regime_risk_off_gate` ("9/30 closed paper
trades entered in risk_off — 0% win rate, avg -5.0% return"); GROWTH `ml_weight_cap` T225-C
("ml_prob>0.85 GROWTH BUY had only 33% win rate (9 samples) vs 100% for ml_prob 0.75-0.85").

---

## Part 3 — Known-Limitations Cross-Check

Full per-entry status now recorded in `docs/KNOWN_LIMITATIONS.md`. Summary: all 6 entries remain
**unchanged** since they were written, with one partial exception:

- **T232-PT6** (scale-out backfill): unchanged, needs a live DB check on UPST/IMVT.
- **T232-OC6** (delisting confirmation): unchanged — and now confirmed **structurally dead**:
  `Stock.delisted` exists as a column but is **only ever read** as a filter, never written
  `True` by any ingestion job anywhere in the codebase. The "Revisit" criteria this entry
  specifies literally cannot be met until something populates this column.
- **T233-ARCH-CONGRESS-DEDUP**: unchanged — confirmed via `git log` that `congress.py` hasn't
  been touched since before the 2026-07-03 re-scoping note; the same broken S3 URLs are still
  present verbatim.
- **T232-DL-OBSERVABILITY**: unchanged — silent-exception count is roughly the same order of
  magnitude as originally catalogued (no batch triage visibly attempted).
- **T232-ML1** (PIT epoch retrain): partially checkable now — `trainer.py` carries a queryable
  `trained_at` field, so confirming a retrain happened is mechanically possible, but wasn't
  checked live as part of this audit.
- **T232-OC4** (MAE/stop-loss deferred): unchanged — confirmed the Backtest Harness
  (`T233-SELFIMPROVE-PHASE2`) it's waiting on is still `todo`/design-only, correctly not jumped
  ahead of.

---

## Part 4 — Code-Audit Findings by Subsystem

See `frontend/src/pages/improvements.tsx` Tier 234 for the authoritative, individually-tracked
version of every finding below (each with its own `fix` recommendation and status). This section
is a narrative index for cross-referencing.

### Signal Engine
- **[HIGH]** `tune_style_profiles` (routes.py ~3623) — in-sample gate-parameter tuning applied
  live with no train/validation split, reproducing the exact failure mode its sibling
  `outcomes_calibrate_apply` was built to avoid after a prior live incident. → `T234-SIG-INSAMPLE-GATE-TUNING`
- **[HIGH]** `calibrate_ml_weight` (routes.py ~1128) — uses "most recent close" instead of a
  fixed hold window, mixing arbitrary holding periods into a globally-applied ML weight sweep.
  → folded into `T234-SIG-INSAMPLE-GATE-TUNING`
- **[HIGH]** `gate_backtest`'s inline gate replica (routes.py ~4784) has drifted from the real
  gate (scheduler.py ~686-704) — missing the `ml_weight==0` soft-pass carve-out.
  → `T234-SIG-GATEBACKTEST-DRIFT`
- **[not fully detailed in journal]** `volume_z` double-counted across VOLUME and TREND pillars
  (signals.py ~1140); plausible double-compression of ML/TA-disagreement evidence via two
  overlapping filters; both real and replica gates still check a dead `rsi_divergence` key;
  a diagnostic log hardcodes `symbol="unknown"`. Not individually promoted to their own tracker
  entries — worth a follow-up pass if someone wants to itemize them.

### ML Pipeline
- **[CRITICAL]** `piotroski_score` + 8 of 12 `FUNDAMENTAL_COLUMNS` broadcast today's snapshot
  across all historical training rows (builder.py ~777-781) — not covered by the T228
  point-in-time fix's `_PIT_COLS` list. → `T234-ML-FUND-BROADCAST-LEAKAGE`
- **[HIGH]** Optuna's tuning objective (`-mean(aucs)`, tuner.py ~137) is structurally
  disconnected from what the system actually trades on (precision at the 0.53-0.78 tail
  threshold). Not yet promoted to its own Tier 234 entry — recommend a follow-up.
- **[HIGH]** Threshold-selection PR-curve estimate uses too few rows for reliability at the
  `len(X)==200` training minimum (trainer.py ~772-789).
- **[MEDIUM]** `meta_trainer.py` (~180, 357) omits `symbol=` when calling
  `fetch_macro_features()` — every HK stock in the cross-symbol meta-model gets NaN
  HSI-based features and an SPY-derived (wrong-market) bear-market flag instead.
- **[MEDIUM]** No same-day partial-bar filter in meta-model feature extraction, unlike
  `train_model()`'s equivalent filter.
- **[MEDIUM]** `train_model()` and `validate_walkforward()` use different label-threshold
  methodologies for nominally the same model family.
- Confirmed-clean: `fetch_signal_outcome_features()` PIT correctness, the T228 merge_asof for
  its 4 covered columns, `validate_walkforward`'s explicit historical-window fundamentals
  blanking, `TimeSeriesSplit(gap=horizon)` purging, train-only scaler fitting, overfit-gap
  detection, coin-flip suppression, and all of the recently-fixed HMM regime model changes.

### Decision Engine
- **[CRITICAL]** `sizer.py`'s computed `position` is never read by `_call_decision_engine` —
  dead code on the real trading path. → `T234-DE-SIZER-DISCARDED`
- **[HIGH]** `hard_rejects.py` is missing the macro-calendar blackout and gap-up filter that the
  fallback `_should_enter()` enforces as unconditional hard rejects — the "primary" gate is
  looser than the "fallback" on two binary-event-risk dimensions. → `T234-DE-MISSING-HARD-REJECTS`
- **[MEDIUM/HIGH]** `scorer.py` double-counts the same static entry-zone-vs-price relationship
  across Layer 1 and Layer 3h. → `T234-DE-SCORER-DOUBLECOUNT-ENTRYZONE`
- **[MEDIUM]** `sizer.py`'s `max_position_pct` cap doesn't multiply by `earnings_mult`, unlike
  the real engine; confidence-multiplier tier boundaries differ between DE and the real engine
  (compounds with the dead-code finding above, but the divergence itself is a maintained
  inconsistency); no HMM bear-pressure dampening exists in decision-engine's parallel path.

### Paper Trading Engine
- **[CRITICAL]** Six position-size multipliers chain by plain multiplication with no combined
  floor — worst case ~8.4% of intended risk target. → `T234-PT-SIZING-MULT-STACK`
- **[CRITICAL]** `_monitor_positions` skips all exit checks with zero fallback when a live quote
  is missing, inconsistent with `_best_price()`'s documented fallback pattern used elsewhere.
  → `T234-PT-MONITOR-MISSING-PRICE-FALLBACK`
- **[HIGH]** Scale-in doesn't update `entry_shares`/blend `entry_price` — overstates close-time
  P&L and corrupts calibration data (the scale-IN counterpart to already-fixed T232-PT6).
  → `T234-PT-SCALEIN-COST-BASIS-BUG`
- **[MEDIUM]** A reintroduced N+1 query pattern in double-top detection; two unbatched
  research-engine HTTP calls run before cheaper already-prefetched local gates, contributing to
  the slow-step problem that forced the distributed lock TTL up to 300s.
- Confirmed-clean: the distributed lock's compare-and-delete release logic; no other
  cross-tick shared-mutable-state races found; regime hysteresis globals don't cross-contaminate
  between US/HK paths.

### Ranking Engine / Technical-Analysis Core
- **[HIGH]** K-Score silently mixes fundamentals-grounded and price-proxy values under one label
  — momentum can be triple-counted when fundamentals are missing. → `T234-RANK-KSCORE-PROXY-MIXING`
- **[HIGH]** Unbounded `rs_rank` (only the clipped `score` is bounded). → `T234-RANK-RS-UNBOUNDED`
- **[HIGH]** Sector peer-count gate off-by-one — "≥3" actually guarantees only 2 real peers.
  → `T234-RANK-SECTOR-PEER-OFFBYONE`
- **[MEDIUM]** `macd()`/`atr()` lack the `min_periods` warm-up gating that `sma()`/`ema()`/
  `rsi()`/`bollinger_bands()` correctly apply; a `(50.0, 1.0)` benchmark fallback is
  indistinguishable from a stock genuinely tracking its benchmark exactly; `_momentum_score` has
  a hard cliff at 127 bars with no partial-data smoothing; `_value_proxy`'s "52-week high"
  silently degrades to all-time-high under 252 bars; `_adx_value`'s documented 20.0 neutral
  fallback doesn't actually gate on history length, only genuine NaN.
- Confirmed-clean: RSI, MACD, Bollinger Bands, and Supertrend formulas are all formulaically
  correct (proper Wilder smoothing, correct alpha, correct band math) — no off-by-one or
  wrong-divisor bugs in the indicator math itself. Relative-strength methodology (trailing,
  non-lookahead) is sound in concept.

---

## Coverage Notes

All 15 workflow agents completed with results; none were empty or errored. Two subsystem
agents (signal-engine, decision-engine) reported slightly higher aggregate finding counts via
their internal `ReportFindings` calls (10 and 9 respectively) than could be reconstructed from
the persisted narrative text in the workflow journal (7 and 6 respectively) — the remainder were
referenced only in aggregate ("everything else is captured in the findings list") without
enough journal detail to reconstruct file:line specifics. This is a limitation of what the
workflow journal persisted, not a claim that those additional findings don't exist — flagged
here for anyone revisiting this audit who wants to re-run those two subsystem agents for full
itemization.
