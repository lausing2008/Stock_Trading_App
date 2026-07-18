# Fit-Gap Analysis: "AI Trading Platform — Combined Agent Catalog" vs. This Codebase

**Date:** 2026-07-18
**Source doc:** `Improvements/AI Trading Platform - Combined Agent Catalog.md` (849 lines: 14 LLM-agent
specs + data/infrastructure prerequisites + a closing "what actually moves win rate" section)
**Method:** every agent in the catalog was mapped against the actual current code (grep/file-verified,
not assumed from memory). Tracker items for the genuine gaps live under **Tier 258** in
`frontend/src/pages/improvements.tsx`.

## Headline conclusion

**The large majority of this catalog is already built — and mostly in a stronger form than the
catalog specifies.** The catalog describes LLM agents that *narrate* scores from rubrics; this
codebase implements most of the same functions *mechanically* (deterministic math over real data)
and, critically, *calibrates them against measured outcomes* — which is exactly what the catalog's
own closing section ("What actually moves win rate and returns — the honest answer") says matters
more than any prompt. The catalog's single highest-leverage item (the calibration loop, section A)
is the single most built-out area of this codebase.

The genuine gaps are 6 features, none critical, tracked as T258-* items.

## Agent-by-agent verdict

| # | Catalog agent | Verdict | What exists / what's missing |
|---|---|---|---|
| 1 | Market Regime Agent | **DONE — stronger** | Canonical regime classifier (`get_last_regime()`/`get_last_hk_regime()`, bull/neutral/choppy/risk_off/bear), HMM regime model (`hmm_regime.py`: state/bear_prob/bear_pressure), pre-regime early warnings (`is_pre_choppy`/`is_pre_risk_off`), Fear & Greed w/ SP500 regime. "Posture recommendation" is mechanically *enforced* (sizing dampeners, min_entry_score raises, entry blocks in `paper_trading_engine.py`), not narrated. |
| 2 | Macro Event Impact Agent | **PARTIAL** | T249 P0 (real FRED release calendar), P2 (release-day fast poll + FOMC RSS + LLM reaction paragraph), P3 (pre-market brief), macro-blackout entry gate (T220-D). **Missing:** structured sector-impact output (`most_bullish_sectors`/`most_bearish_sectors`) — explicitly deferred in T249-P2 as `sectors_helped`/`sectors_hurt`; the live reaction is a narrative paragraph only. → **T258-MACRO-SECTOR-IMPACT** |
| 3 | Sector Rotation Agent | **PARTIAL** | Two live implementations: ETF RS vs SPY (`/stocks/sector_rotation`, 1w/1m/3m, leading/lagging) and K-Score momentum per sector (`_compute_sector_rotation()`, weekly → Redis). **Missing:** rotation *trajectory* (Emerging/Established/Fading Leader, Emerging Laggard) — requires persisting historical snapshots and diffing rank vs. N days ago; today only the latest snapshot exists (Redis, 3-day TTL). Also no HK sector-ETF equivalent (already a known gap from the Reports-tab research). → **T258-SECTOR-ROTATION-TRAJECTORY** |
| 4 | Relative Strength Leader Agent | **DONE** | K-Score's `rs_score`/`rs_rank` (relative strength vs. benchmark), RS compression per style in signal generation, rankings sortable by RS. Window granularity differs from the catalog's 5/20/60/120d spec but covers the same function. |
| 5 | Volume, Breakout & Institutional Flow Agent | **PARTIAL** | Volume profile (POC/VAH/VAL/HVN/LVN, Tier 250), session-elapsed-scaled RVOL (T241), per-minute volume anomaly alert (T257), FVG detection + trade plan (T254), S/R + trendlines + game-plan breakout levels, per-symbol options flow (cp_ratio, whale premiums), 13F QoQ institutional accumulation (T220-E), OBV as a conviction-gate layer. **Missing:** an explicit price/volume accumulation-vs-distribution classifier and a "real vs. fake breakout" follow-through assessment (poke-and-reject is documented as a manual chart read; `T252-VALUE-AREA-BREAKDOWN-ALERT` remains todo). Block-trade/dark-pool data: no source exists (catalog itself says omit in that case). → **T258-ACCUM-DIST-BREAKOUT-QUALITY** |
| 6 | Earnings Surprise Predictor | **PARTIAL — low value** | `eps_beat_rate`, `eps_avg_surprise_pct`, `forward_eps` already computed and shown; `earnings_events` table with actuals/surprise/strength score; P1 day-of reminder + post-release reaction alerts. **Missing:** an explicit "probability of beat" number. The history to compute a mechanical base rate exists — but the catalog itself flags this agent as its highest hallucination risk, and a base rate adds little over the beat-rate % already displayed. Documented here as considered-and-deprioritized rather than tracked as a build item. |
| 7 | Trade Quality & Setup Agent | **DONE — stronger** | This is decision-engine's `scorer.py`/`hard_rejects.py` + `_should_enter()` (layered scoring, hard rejects, entry/stop/target from game plan) + the frontend Confluence Score + the 7-layer Conviction Gate. Unlike the catalog's static rubric, ours is calibrated against outcomes (`calibrate_entry_weights`, min_entry_score gate harness). The A+/A/B/C/Avoid letter-grade presentation is cosmetic. |
| 8 | "What Could Go Wrong?" Agent | **GAP — the one genuinely new agent** | No adversarial pre-trade risk check exists anywhere: nothing argues *against* a proposed entry and enumerates concrete failure modes before entry. Research reports contain risk sections, but per-report (slow, on-demand), not per-trade-decision. Cheapest genuinely-new item in the catalog; the value is the forced risk enumeration, NOT the `probability_of_failure_pct` number (unvalidated — per the catalog's own honest-answer section, either omit it or clearly label it uncalibrated). → **T258-WHATCOULDGOWRONG-AGENT** |
| 9 | Position Sizing Agent | **DONE — stronger** | Position Sizer (ATR stop, risk-% sizing, currency mismatch warnings), paper-trading risk-based sizing with regime dampeners + position caps + scaling gate (T241), and — verified — **Kelly already exists**: `GET /paper-portfolio/kelly` computes Kelly from real closed-trade history and recommends **quarter**-Kelly (more conservative than the catalog's half-Kelly), surfaced in `decide.tsx`/`regime.tsx`. |
| 10 | Portfolio Risk & Correlation Agent | **PARTIAL** | The math exists and is user-facing: `/portfolio-risk/risk` (pairwise correlation matrix, per-symbol betas, portfolio beta, sector concentration, parametric VaR, warnings) — relocated to portfolio-optimizer 2026-07-18. Paper trading enforces coarse versions pre-entry (sector % cap, sector position cap, market-cluster cap, heat brake, cross-portfolio caps). **Missing:** the catalog's core point — checking a *new* trade against the *open book's* pairwise correlation and beta-weighted exposure at entry time. The existing endpoint math is never called from `_scan_for_entries`/decision-engine. → **T258-PORTFOLIO-CORRELATION-PREENTRY** |
| 11 | AI Conviction Ranking System | **DONE — stronger** | K-Score IS the cross-sectional composite (technical/momentum/value/growth/volatility/RS, weighted); the "top N" query mode is T257's top-3 conviction alert — which gates on *measured* signal_outcomes win rates rather than rubric-weighted narration, precisely the catalog's own recommended upgrade. |
| 12 | Market Intelligence Dashboard | **DONE** | `intelligence.tsx` overview, `reports.tsx` (Trend/Assets/Top Stocks/Money Flow/News & Macro/Self-Tuning), morning digest + pre-market brief emails. Cross-signal conflict surfacing exists via the Confluence Score ("signals conflict") and the documented badge-vs-tabs divergence explanation. |
| 13 | Exit Optimization Agent | **DONE — stronger cadence** | `_monitor_positions()` runs every intraday cycle (vs. the catalog's daily): stops, targets, ATR trailing stops, breakeven trigger, time stops, signal-decay exits (the mechanical form of "thesis no longer valid"), scale-out partials, position-scaling adds. |
| 14 | AI Post-Mortem Agent | **PARTIAL** | The *aggregate* learning loop is built and validated: `calibrate_entry_weights` (learns from closed trades), `entry_factors` (per-factor win-rate analysis), retro-feedback (`realized_ev_pct_after` backfill on TuneHistory). `PaperTrade` already stores plan (entry/stop/target at entry) AND actuals (exit price/reason/pnl) — the data for plan-vs-actual adherence exists. **Missing:** the per-trade review itself (plan adherence + what-went-right/wrong per closed trade, surfaced in the UI). → **T258-TRADE-POSTMORTEM** |

## Prerequisites section — all 5 already exist

1. Market data feed — yfinance + Alpha Vantage + Polygon aggregates, OHLCV/VWAP/MAs/volume profile. ✔
2. Fundamentals/analyst feed — yfinance fundamentals incl. beat rates, revisions (analyst_actions). ✔
3. Journal data store — `PaperTrade` (full plan+execution lifecycle), `journal.py`, `signal_outcomes`. ✔
4. Scheduler — APScheduler across all services, extensively used. ✔
5. Orchestration — scheduler job chains + decision-engine's `aggregator.py` fan-out. ✔

## Closing section (A–D) — the catalog's own "what actually matters"

- **A. Calibration loop ("highest leverage item in this whole catalog")** — this codebase's most
  built-out area: confidence calibration (real bucket win rates, n≥30), `outcomes/calibrate/apply`
  with chronological walk-forward splits, `tune_style_profiles`, the T255 joint tuner,
  `TuneHistory` (every attempt recorded), promotion gates, the signal watchdog, retro-feedback
  realized-EV checks. **DONE, extensively.**
- **B. Backtest before trusting** — gate harness (min_entry_score replay), `/signals/walkforward`,
  strategy-engine backtest DSL. Full equity-curve replay is the known, deliberately-deferred
  Phase 2b (`DESIGN_SELF_IMPROVEMENT_LOOP`). **PARTIAL by explicit prior decision.**
- **C. Portfolio risk + Kelly sizing ("second-highest leverage")** — Kelly: done (quarter-Kelly).
  Portfolio correlation at entry: the T258 gap above.
- **D. Per-agent calibration** — confidence calibration is per (horizon, direction, market, band);
  `entry_factors` is per-factor. **DONE.**

## Resulting Tier 258 tracker items (priority order)

1. **T258-WHATCOULDGOWRONG-AGENT** (medium/M) — the one genuinely new agent worth building.
2. **T258-PORTFOLIO-CORRELATION-PREENTRY** (medium/M) — wire existing `/portfolio-risk` math +
   beta-weighted exposure into the pre-entry gate as an advisory score layer.
3. **T258-MACRO-SECTOR-IMPACT** (medium/S) — structured sectors_helped/hurt on macro reactions
   (finishes what T249-P2 explicitly deferred).
4. **T258-SECTOR-ROTATION-TRAJECTORY** (low/M) — persist rotation snapshots, classify
   Emerging/Fading leaders, surface in Reports → Money Flow.
5. **T258-ACCUM-DIST-BREAKOUT-QUALITY** (low/M) — A/D classification + breakout follow-through
   assessment (overlaps T252-VALUE-AREA-BREAKDOWN-ALERT).
6. **T258-TRADE-POSTMORTEM** (low/S) — per-closed-trade plan-vs-actual review from data
   `PaperTrade` already stores.

**Explicitly considered and NOT tracked:** earnings beat-probability (agent 6 — marginal over the
beat-rate % already shown, and the catalog's own highest-hallucination-risk item); rebuilding any
DONE-verdict agent as an LLM version (would replace validated mechanical scoring with unvalidated
narration — the exact failure mode the catalog's closing section warns against).
