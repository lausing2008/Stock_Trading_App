# Deep Audit — Duplication, Refactors & Correctness Across the Trading Pipeline (2026-07-10)

**Scope:** Signal engine (`signals.py`, calibration in `routes.py`), ML prediction (`builder.py`, `trainer.py`, `tuner.py`, `meta_trainer.py`, `hmm_regime.py` — with a dedicated deep-dive on the recently-added meta-model), decision engine (`scorer.py`, `sizer.py`, `hard_rejects.py`, `regime.py`, `aggregator.py`), paper trading engine (entry/exit/scale-in/position-scaling-shadow), ranking engine (`kscore.py`), technical-analysis core, position sizing across both decision-engine and the real paper-trading engine, and the outcome-tracking/calibration feedback loop end to end.

**Method:** two independent multi-agent workflows (7 subsystem areas, then 2 more targeted areas — position sizing and the meta-model — added at the user's request). Each workflow: one recon/mapping agent per area -> one deep-audit agent per area (hunting duplicate logic, refactor candidates, correctness bugs, and dead code) -> dedicated cross-cutting agents comparing specific file pairs with documented architectural drift history (`scorer.py` vs `_should_enter()`, `kscore.py` vs decision-engine scoring, the indicator-dedup rollout, the calibration feedback loop's internal coherence, `sizer.py` vs the real sizing formula, the meta-model vs the base model's feature pipeline) -> one independent adversarial verifier per surviving candidate, each re-reading the actual current code before returning CONFIRMED / PLAUSIBLE / REFUTED. Every finding below is CONFIRMED — REFUTED findings were dropped before this doc was written.

**Outcome:** **101 confirmed findings** — 15 critical, 30 high, 30 medium, 26 low. By category: 28 correctness bugs, 47 duplicate-logic findings, 23 refactor candidates, 3 dead-code findings. This audit is prioritization output — no code fixes were applied as part of this pass. See `frontend/src/pages/improvements.tsx` (Tier 242) for the tracked action items, one per finding, each linked back to its `AUD232-NNN` ID here.

An interactive, filterable version of this report (search by file/keyword, filter by severity/area/category) was also published as a Claude artifact during the audit session — this markdown file is the durable, git-tracked copy.

---

## Summary by area

| Area | Findings | Critical | High | Medium | Low |
|---|---|---|---|---|---|
| Calibration Loop | 17 | 4 | 4 | 6 | 3 |
| Decision Engine | 16 | 3 | 9 | 2 | 2 |
| Position Sizing | 14 | 3 | 8 | 0 | 3 |
| Meta-Model | 12 | 1 | 3 | 4 | 4 |
| Technical Analysis | 11 | 2 | 0 | 6 | 3 |
| Ranking Engine | 11 | 0 | 3 | 6 | 2 |
| ML Prediction | 9 | 0 | 2 | 2 | 5 |
| Paper Trading | 6 | 1 | 0 | 3 | 2 |
| Signal Engine | 5 | 1 | 1 | 1 | 2 |

---

## CRITICAL (15)

### Calibration Loop

**`AUD232-001`** [BUG] `services/market-data/src/api/paper_portfolio.py`:1485

calibrate_entry_weights() fits and immediately applies a logistic regression on the full closed-trade dataset with no train/validation split or EV-lift gate, unlike every calibration mechanism in signal-engine's outcomes-driven loop (calibrate_ml_weight, outcomes_calibrate_apply, tune_style_profiles all use 70/30 walk-forward splits after the documented CAL-1 in-sample-overfit incident).

*Detail:* calibrate_entry_weights (paper_portfolio.py:1485-1575) loads every closed PaperTrade row, fits `LogisticRegression(max_iter=500, class_weight='balanced')` on the entire sample, and writes the resulting coefficients straight to entry_weights.json plus calls reload_entry_weights() — with no held-out validation slice anywhere in the function. This is functionally the same class of problem (parameter calibration from historical trade/outcome data, feeding back into live decision-making) that signal-engine's routes.py extensively hardened against in T232-OC3/T234-ML-WEIGHT-NO-VALIDATION-GATE/T234-SIG-INSAMPLE-GATE-TUNING — all three of those fixes exist specifically because an unvalidated in-sample fit was found to apply upward-biased flukes as if they were real signal. calibrate_entry_weights is called weekly from the same _weekly_full_refresh job (scheduler.py:2089-2097) that runs those hardened siblings, but was never given the same protection.

*Failure scenario:* A short run of lucky paper trades (e.g. 40-60 closed trades, meeting _MIN_CALIBRATION_TRADES but still a small, noisy sample) happens to correlate spuriously with one entry factor (say, kscore_at_entry) due to a temporary market regime. The logistic regression fits a large coefficient to that factor, entry_weights.json is overwritten, and reload_entry_weights() applies it to live paper-trading entry decisions immediately — with no check that this fit generalizes to a held-out slice of trades the fit never saw, unlike the analogous ml_weight and signal-threshold calibrations which would reject exactly this kind of overfit via their validation-slice EV comparison.

**`AUD232-002`** [BUG] `services/ml-prediction/src/training/meta_trainer.py`:110

meta_trainer.train_meta_model() joins signal_outcomes to stocks on symbol alone (not stock_id), but stocks.symbol is only unique per (symbol, exchange) — the same ticker string can exist on two different exchanges/markets.

*Detail:* shared/db/models.py's Stock table has UniqueConstraint('symbol', 'exchange') — symbol alone is not globally unique. Every other signal_outcomes consumer (calibrate_conviction_weights, outcomes/summary, confidence-calibration builder) joins on SignalOutcome.stock_id == Stock.id. meta_trainer.py's raw SQL instead does 'JOIN stocks st ON st.symbol = so.symbol', which can either silently duplicate rows (fan-out join if two stocks share a symbol string) or attach the wrong sector/market_cap features to an outcome row if a US and HK listing happen to share a ticker.

*Failure scenario:* If a US-listed stock and an HK-listed stock (or a delisted-then-relisted stock re-added with a new exchange) ever share the same symbol string, meta_trainer's query silently duplicates the outcome row (once per matching stocks row) or joins the outcome to the wrong stock's sector/fundamentals — corrupting the sector_code/market_cap_bin features the meta-model is trained on, with no error or log line to reveal it.

**`AUD232-003`** [BUG] `services/signal-engine/src/api/routes.py`:126

The confidence-calibration Redis cache key (signal:confidence_calibration) is a single global key shared across all horizons, directions, and markets with a 1-hour TTL, but the underlying signal_outcomes data it's built from is only refreshed once a week (Sunday's outcomes/evaluate) — the cache rebuilds 168 times a week against unchanged data, and there is no cache invalidation tied to outcomes/evaluate actually writing new rows.

*Detail:* _get_confidence_calibration checks Redis first and only rebuilds on a cache miss (1h TTL) or explicit ?refresh=true. outcomes/evaluate never calls _cache_set or deletes _CONF_CAL_CACHE_KEY after writing new SignalOutcome rows, so the calibrated_win_rate shown on signals can lag up to 1 hour behind a fresh evaluate run under normal operation (acceptable), but if evaluate_signal_outcomes silently stops running (per the exact jose-missing-library failure pattern already documented multiple times in this repo for other endpoints) there is no mechanism that would make the served calibration numbers look obviously stale — they will keep rebuilding hourly from the same underlying stale rows indefinitely, self-consistently, with no signal that the underlying evaluate job stopped.

*Failure scenario:* If /signals/outcomes/evaluate silently fails for weeks (e.g. python-jose missing, matching the exact recurring failure mode already seen for signal-engine, ml-prediction, and ranking-engine in this codebase), confidence-calibration's Redis cache keeps expiring and rebuilding every hour, but each rebuild queries the same stale signal_outcomes rows and returns numerically identical 'buckets' — there's no explicit staleness signal on this endpoint (unlike the outcomes.evaluation_stale log check added for evaluate itself), so calibrated_win_rate badges on the SignalCard UI keep showing confidently-labeled numbers that are actually weeks out of date.

**`AUD232-004`** [BUG] `services/signal-engine/src/api/routes.py`:2367

calibrate_ta_weights writes its fitted weights to a JSON file plus a global Redis key (stockai:ta_weights, 90-day TTL) and calls set_ta_weights() to update in-process globals — but this happens inside a single worker process; other signal-engine worker/replica processes (if any) never receive the in-process update and keep scoring with pre-calibration weights until they separately read the Redis key or restart.

*Detail:* The comment at line 2388-2391 explicitly documents that this exact class of bug (calibration reporting success while the running process keeps using old weights) was already found and fixed for the process that runs calibrate_ta_weights itself — but the fix (set_ta_weights(new_weights)) only updates the calling process's globals. If signal-engine ever runs with multiple uvicorn workers or multiple container replicas, every other worker/replica still needs to independently notice the Redis key changed; there's no push/pub-sub, only whatever periodic re-read (if any) the signal generator does.

*Failure scenario:* If stockai-signal-engine-1 is ever scaled to multiple workers/replicas (a plausible future change, not documented as explicitly forbidden), running calibrate_ta_weights on a Sunday updates weights in exactly one worker's memory and in Redis/disk — every other worker keeps scoring BUY signals with the previous week's TA weights until it happens to restart, producing inconsistent signal confidence for the same symbol depending on which worker handled the request.


### Decision Engine

**`AUD232-005`** [BUG] `services/decision-engine/src/api/core/hard_rejects.py`:184

check_hard_rejects() enforces a time-of-day gate (first 30 min / last 15 min) and a breakout-extension guard (>6% above breakout) that _should_enter() — the function this whole module is meant to faithfully port, and the actual fallback path used when DE is unreachable — does not have, making the fallback strictly looser than the primary gate on two dimensions with no comment calling this out as intentional.

*Detail:* Verified by reading _should_enter() in full (paper_trading_engine.py:1301-1552): no time-of-day check, no breakout-extension guard exist anywhere in that function. hard_rejects.py:184-215 has both. The existing T234-DE-MISSING-HARD-REJECTS comment at hard_rejects.py:124-127 explicitly documents porting gap-up and macro-blackout gates FROM the fallback (because the fallback had them and DE was missing them) — but does not address the reverse gap: DE now has two gates the fallback never had, meaning if DE goes down (_call_decision_engine returns None, paper_trading_engine.py:3779-3784 falls back to _should_enter()), the live system silently becomes MORE permissive during exactly the outage window when extra caution would be warranted, not less.

*Failure scenario:* decision-engine becomes unreachable (container crash, network partition, jose import failure per this codebase's documented recurring pattern) during the first 10 minutes of market open. gate_source falls back to 'fallback' (_should_enter()). A candidate that DE would have rejected via its time-of-day gate ("first 30 min... price discovery in progress") is now evaluated purely on score, with no equivalent check in _should_enter() — the system opens real paper positions in the exact high-spread, low-liquidity window the DE gate exists specifically to avoid, silently, for the duration of the outage.

**`AUD232-006`** [BUG] `services/decision-engine/src/api/core/scorer.py`:151

Layer 3f reads a combined `catalyst_score` and checks for `cs <= -30`, but event-intelligence's catalyst_score is clamped to [0, 100] and can never be negative — the negative-catalyst branch is unreachable dead code, silently dropping the entire bearish catalyst signal that _should_enter() captures via separate insider_score/congress_score checks.

*Detail:* event-intelligence/src/services/catalyst.py:131-137 computes `catalyst_score = 0.35*insider_score + 0.30*earnings_score + 0.25*congress_score + 0.10*economic_score` then applies `catalyst_score = min(100.0, max(0.0, catalyst_score))` — the max(0.0, ...) floors it at zero. signal-engine writes this same clamped value verbatim into `reasons['catalyst_score']` (signal-engine/src/api/routes.py:484-485). decision-engine's scorer.py Layer 3f then does `if cs >= 60: +1 / elif cs <= -30: -1 / else: 0` — the `cs <= -30` branch can never fire since cs is always >= 0. Meanwhile paper_trading_engine.py's _should_enter() (the system this scorer claims to be 'extracted faithfully from') scores insider_score and congress_score SEPARATELY and un-clamped (both range -100..100, insider.py:277 and congress.py's compute_congress_score), correctly penalizing heavy insider selling (score < -30 → -1) and reading a genuinely negative congress signal. Since decision_engine_mode defaults to 'primary' (paper_trading_engine.py:3738, _call_decision_engine at 2307-2367), DE's score feeds real entry-gate decisions and score_size_mult for live paper trades — this isn't cosmetic, it means real trade candidates with heavy insider/congress selling get zero catalyst penalty under DE's gate when they would be penalized under the fallback _should_enter() path.

*Failure scenario:* A stock has heavy insider selling (insider_score = -80) and no other catalyst activity, producing a blended catalyst_score that would be negative (e.g. -28) before clamping but is stored/read as 0.0 after the floor. decision-engine's Layer 3f sees cs=0.0, which falls into the `else: pts=0, note='Neutral catalyst signal'` branch — no penalty at all, even though _should_enter()'s equivalent insider_score check (reasons.get('insider_score') = -80 < -30) would apply a full -1 penalty. Since DE is the default authoritative gate (de_mode='primary'), this stock's real entry score is silently 1 point higher than the fallback system would compute for the identical signal, potentially pushing a marginal candidate (score exactly at min_score - 1) into a real BUY it shouldn't get.

**`AUD232-007`** [BUG] `services/decision-engine/src/api/core/scorer.py`:199

compute_score() has no cross-horizon consensus scoring layer at all, even though _should_enter() scores cross_style_buys as its own ±1 layer (including a penalty for zero consensus in bear/choppy regimes) and decision-engine itself extracts cross_style_buys and exposes it in Factors/sizer.py/llm_scorer.py.

*Detail:* paper_trading_engine.py's _should_enter() (lines 1500-1508) scores: `if cross_buys_h >= 2: score += 1 ... elif cross_buys_h == 0 and regime_state_h in ('bear','choppy'): score -= 1`. decision-engine's scorer.py has no equivalent — grep for cross_style_buys/cross_buys inside scorer.py returns zero matches. decision-engine does thread cross_style_buys through routes.py -> Factors (display only) and sizer.py (consensus_mult, a sizing multiplier, not a score point), and llm_scorer.py (prompt text only) — but the additive score itself, which gates BUY/HOLD/SKIP via min_score_for_regime, never reflects consensus at all. This is not documented as an intentional design difference anywhere (unlike sizer.py's docstring, which explicitly calls out its own divergences) — scorer.py's module docstring still claims 'extracted faithfully from paper_trading_engine._should_enter()'.

*Failure scenario:* Two otherwise-identical candidates are evaluated in a choppy regime: Stock A has cross_style_buys=0 (no other timeframe agrees), Stock B has cross_style_buys=2 (strong multi-timeframe alignment). Under _should_enter(), A gets -1 and B gets +1 — a 2-point spread that can be the difference between SKIP and BUY. Under decision-engine's compute_score(), both score identically on this dimension (0 points each) since no layer reads cross_style_buys — DE's score for A is systematically overstated (missing a real -1 penalty) and B understated (missing a real +1) relative to what the 'faithful extraction' is supposed to reproduce, and since DE gates real trades by default, A can pass DE's gate when the fallback system would have rejected it.


### Meta-Model

**`AUD232-008`** [BUG] `services/ml-prediction/src/training/meta_trainer.py`:382

predict_meta() builds its feature vector from the live builder.FEATURE_COLUMNS list instead of the bundle['feature_columns'] snapshot saved at training time, unlike trainer.py's predict_latest() which correctly uses the saved snapshot — a direct repeat of the documented 'index 66 out of bounds' incident class.

*Detail:* train_meta_model() saves `bundle['feature_columns'] = list(FEATURE_COLUMNS)` (L278) and also saves `non_const` (L277), an array of column *indices* selected against that training-time FEATURE_COLUMNS length via `np.where(X_raw.std(axis=0) > 1e-8)[0]` (L238). But predict_meta() (L299-401) never reads `bundle['feature_columns']` back — it re-imports the current `FEATURE_COLUMNS` from builder.py at inference time (L319, used at L382) and builds `vec` against that live list's current length/order. Compare to trainer.py's predict_latest() (L840): `saved_cols = bundle.get('feature_columns', list(FEATURE_COLUMNS))` — the base-model path was fixed to use the saved snapshot; the meta-model path was not. builder.py's FEATURE_COLUMNS has changed length multiple times already per its own changelog comments (T220-F/T237-ML2 removed eps_revision_direction, CRIT-3/4 removed 5 earnings-quality + 2 HK-flow columns) — each such change silently desyncs any meta_model.joblib trained before it.

*Failure scenario:* meta_model.joblib is trained today with FEATURE_COLUMNS at its current length L. A future PR removes or adds one column to FEATURE_COLUMNS in builder.py (as has already happened repeatedly) and deploys ml-prediction, but the monthly meta_model_monthly_retrain job (scheduler.py L3918) hasn't fired yet — the old joblib bundle with `non_const` indices computed for length L is still on disk. The next predict_meta() call builds `vec` of length L±1, then does `X_raw[:, non_const]` where `non_const` contains indices up to L-1 (or now points at the wrong columns if only the order changed) — this either raises `IndexError: index N is out of bounds for axis 1 with size M` (silently swallowed by the blanket `except Exception` at L399, returning None and quietly dropping the meta ensemble member from every live prediction until the next monthly retrain) or, worse, if the length happens to match but order changed, silently feeds mis-aligned feature values into the model with no error at all, corrupting the 15%-weighted meta contribution to every ensemble prediction without any log or crash to reveal it.


### Paper Trading

**`AUD232-009`** [BUG] `services/market-data/src/services/paper_trading_engine.py`:3466

Position-scaling gate's existing_position_pct_of_portfolio feature is computed as position value / remaining cash, not / total equity — a live-path bug distinct from the documented offline-mining placeholder for the same field.

*Detail:* In the position-scaling shadow block (_scan_for_entries, ~L3453-3470), `existing_position_pct_of_portfolio=round((_ps_trade.shares * _ps_live) / portfolio.current_cash, 4)` divides by `portfolio.current_cash` (cash remaining after all open positions were paid for) instead of by total account equity (cash + all positions' market value), which is what the feature name and every other 'pct of portfolio' calculation elsewhere in this file (e.g. `_compute_equity`, sector-cap checks) actually means. This is a different bug from the documented `candidate_event_mining.py` limitation (which uses a fixed 0.05 placeholder for offline-mined hypothetical positions and is explicitly flagged as such in that module's docstring) — this is the LIVE production path feeding the currently-shadow-mode PositionScalingGate model with real portfolio state on every scan tick.

*Failure scenario:* A portfolio that is 90% invested (10% of equity left as cash) holds an open GROWTH position worth $9,000 out of $10,000 total equity — the true concentration is 90%, but current_cash is only $1,000, so the computed feature is 9,000/1,000 = 9.0 (900%). As cash approaches zero this ratio diverges to infinity while the true percentage caps at ~100%. Every act_probability/suggested_size_multiplier prediction and every ps:shadow:pending verdict logged while a portfolio is heavily deployed is trained/scored against a systematically wrong concentration signal, corrupting the shadow-mode validation data this system depends on before position_scaling_mode is ever promoted from 'shadow' to a live add-placing mode.


### Position Sizing

**`AUD232-010`** [BUG] `services/market-data/src/services/paper_trading_engine.py`:3402

Scale-in never updates confidence_at_entry, kscore_at_entry, or market_regime_at_entry, so downstream consumers treat a position's conviction/regime as frozen at the ORIGINAL entry even after a scale-in materially changes cost basis and share count.

*Detail:* Lines 3365-3418 (the SCALE_IN block) update entry_price (3402-3405, weighted-average blend), entry_shares (3406), shares (3407), and entry_decision_notes (3408-3414) when a scale-in fires. confidence_at_entry, kscore_at_entry, and market_regime_at_entry (set once at original entry, lines 4020-4023) are never touched. This is a documented design ambiguity in the recon map but produces a measurable statistical bias in two independent downstream consumers (RL training and calibration reporting).

*Failure scenario:* A position opens at confidence=61 in a 'choppy' regime, then 3 weeks later scales in (pnl>=5%, fresh signal confidence=95) after the regime has shifted to 'bull'. rl_agent.py:260-264 trains its RL feature vector using confidence=61/regime='choppy' even though most of the position's dollar exposure and holding-period P&L reflects the confidence=95/bull-regime add. paper_portfolio.py:949-958's calibration-by-confidence-band report buckets this trade's full P&L under '55-65%' when the majority of the capital was actually deployed at 95% confidence, silently corrupting the calibration accuracy report used to judge signal-engine quality. thesis_persistence_gate.py's snapshot_from_paper_trade also uses the stale market_regime_at_entry as its baseline for later thesis-persistence checks on the now-larger position.

**`AUD232-011`** [BUG] `services/market-data/src/services/paper_trading_engine.py`:3901

The T234-PT-SIZING-MULT-STACK comment justifies the 25%-floor using a worst-case stack that assumes score_size_mult can reach 0.75, but score_size_mult is hardcoded to 1.0 whenever gate_source != 'de' (i.e. whenever the Decision Engine is unreachable) — the comment's worst-case arithmetic doesn't match the fallback code path it's meant to protect.

*Detail:* score_size_mult is only computed from _score_excess when gate_source=='de' (lines 3893-3899); on 'fallback'/'legacy' it's pinned to 1.0. The worst-case stack the comment cites (earnings 0.50 x regime 0.50 x confidence 0.75 x research 0.6 x score 0.75 = 0.084) is only achievable when gate_source=='de' — the fallback path's actual floor-triggering minimum is 0.1125, not 0.084.

*Failure scenario:* During a Decision Engine outage (de_result is None, logged at line 3779-3784 as 'DE unreachable; using _should_enter()'), an engineer tuning the 25% floor believes the worst realistic stack is 0.084 (per the comment) and reasons about the floor's safety margin using that number, when the real worst case on the fallback path (which is exactly when extra caution matters most, since the more sophisticated DE gate isn't available) is actually 0.1125 because score_size_mult can't drop below 1.0 in that branch — leading to incorrect assumptions about how much protection the floor actually provides during an outage.

**`AUD232-012`** [BUG] `services/market-data/src/services/paper_trading_engine.py`:3466

existing_position_pct_of_portfolio is computed as (shares * live_price) / portfolio.current_cash — dividing by remaining CASH, not total portfolio EQUITY — so the value is not actually 'percent of portfolio' as the feature name and FEATURE_COLUMNS docstring claim.

*Detail:* paper_trading_engine.py:3466-3468: `round((_ps_trade.shares * _ps_live) / portfolio.current_cash, 4) if portfolio.current_cash > 0 else 0.0`. position_scaling_gate.py:58 documents this as 'existing_position_pct_of_portfolio' implying position value / total equity (cash + all open positions), but the divisor used is only the uninvested cash balance.

*Failure scenario:* A portfolio that is 90% invested with only 10% cash remaining shows a wildly inflated existing_position_pct_of_portfolio for any open position (e.g. a position genuinely worth 20% of total equity computes as 200% of current_cash), while a mostly-cash portfolio shows an artificially small percentage for an identically-sized real position — training the position-scaling gate (once real data exists) on a systematically wrong 'how concentrated is this position' signal, biasing it based on unrelated cash-balance fluctuations rather than true position concentration.


### Signal Engine

**`AUD232-013`** [BUG] `services/signal-engine/src/api/routes.py`:540

The catalyst-score nudge in _bulk_persist() (and its duplicate in signal_for()) adjusts bullish_probability and can flip the signal label (HOLD→BUY, BUY/HOLD→SELL), but never recomputes AIConfidence.confidence, so the persisted/returned confidence is stale relative to the actual stored signal and bullish_probability.

*Detail:* confidence is derived purely as `round(abs(fused - 0.5) * 200, 2)` inside _apply_style_signal (signals.py line 2052). In routes.py, _bulk_persist() (lines 500-547) and signal_for() (lines 5438-5480) both apply a catalyst-driven delta (_cat_adj, up to +/-0.05) directly to `_ai.bullish_probability`/`_ai_sf.bullish_probability` and, per the T237-SIG3/CRIT-5 fix, correctly re-derive `_ai.signal`/`_ai_sf.signal` when the nudged probability crosses the buy/sell threshold — but neither code path recomputes `.confidence` afterward. The stale confidence (computed from the pre-nudge fused value) is what gets written to the signals table (`conf=ai.confidence` at line 638, `confidence=ai.confidence` at line 5500) and returned to callers.

*Failure scenario:* A stock sits at fused=0.60 (HOLD, confidence=20) with strong insider buying (insider_score>60, _cat_adj=+0.03) pushing bullish_probability to 0.65-0.68, which crosses the SWING bull buy_threshold (0.72 is high, but for e.g. GROWTH bull_threshold=0.60 this legitimately flips HOLD→BUY). The persisted row now shows signal=BUY with the stale confidence=20 (or whatever it was pre-nudge) instead of the true ~30-36 confidence implied by the new bullish_probability. Because /signals/{symbol}'s calibrated_win_rate lookup (T223) buckets by this same stale confidence into _CONF_BANDS, the SignalCard UI can display a materially wrong calibrated win rate for a signal whose direction was just flipped by the catalyst nudge — the label and the confidence-driven win-rate annotation now describe two different underlying probabilities.


### Technical Analysis

**`AUD232-014`** [BUG] `services/ranking-engine/src/scoring/kscore.py`:75

kscore.py's _adx_value() falls back to 20.0 (not NaN/None) on insufficient data, then _technical_score() feeds it into adx_boost, silently granting a fixed +2-point technical-score boost to every short-history stock instead of treating ADX as unknown — the exact bug class signal-engine's own _adx() already fixed as 'C3 FIX'.

*Detail:* signal-engine/src/generators/signals.py's _adx() has an explicit 'C3 FIX' comment: 'return None (not 20.0) when ADX is NaN. A 20.0 fallback silently passed adx_min=25 compression check on all short-history stocks... Return None so downstream callers can explicitly skip ADX-gated logic rather than silently misfiring.' ranking-engine/src/scoring/kscore.py:57-75 `_adx_value()` still has the pre-fix pattern: `return float(adx) if not pd.isna(adx) else 20.0`. This flows directly into `_technical_score()` line 111-112: `adx = _adx_value(df); adx_boost = np.clip((adx - 15) / 25, 0, 1) * 10` — with adx=20.0, adx_boost = clip((20-15)/25,0,1)*10 = 2.0, a real (non-neutral, non-zero) score boost, not a no-op.

*Failure scenario:* A newly-listed stock or one with <14 bars of true-range history (fresh IPO, recent watchlist addition) gets K-Score computed. `_adx_value()` can't really compute ADX (insufficient warmup) but returns 20.0 instead of signaling 'unknown', and `_technical_score()` silently adds +2.0 points to that stock's technical component — inflating its K-Score and improving its rank position purely due to missing data, exactly the false-confidence failure mode already identified and fixed for signal-engine's ADX but never applied to ranking-engine's independent copy.

**`AUD232-015`** [BUG] `services/technical-analysis/src/patterns/recognizer.py`:182

The mutual-exclusion fix for double-top/double-bottom (T237-TA-DTB-MUTUAL-EXCLUSION) assumes hits list order matches (bottom, top) or (top, bottom) via a name check, but silently mis-assigns if both entries happen to have the same name (impossible today, but fragile) — more importantly it only ever compares the single most-recent pair from each scan (both loops `break` after their first valid match), so mutual exclusion is evaluated on the latest bottom-candidate vs latest top-candidate, not all overlapping candidates.

*Detail:* Lines 96-137 (double bottom) and 140-174 (double top) both iterate their pivot-pair candidates newest-to-oldest and `break` after the first structurally-valid pair (gap 5-60 bars, price within 1.5%), regardless of confidence. Then lines 182-186 compare only these two single 'most recent' hits for overlap. If the most-recent valid double-bottom pair does NOT overlap the most-recent valid double-top pair, but an earlier (skipped-over) double-bottom candidate would have overlapped the reported double-top, that earlier non-conflicting bottom is never considered — the code can never detect that conflict because it never even constructs it (only the single newest candidate per pattern type is built as a PatternHit at all).

*Failure scenario:* A stock forms a valid double-bottom at bars 40-70 and, separately, a valid double-top at bars 45-75 (overlapping windows) — but ALSO has a newer, non-overlapping double-bottom-like dip at bars 150-160 that also passes the gap/price checks. The loop's `break` means only the bars 150-160 pair is ever built as the double_bottom PatternHit (the older, actually-overlapping one at 40-70 is skipped over and never evaluated), so the mutual-exclusion check at line 182 compares bars 150-160 vs bars 45-75, finds no overlap, and returns BOTH hits — even though the real overlapping conflict (40-70 bottom vs 45-75 top) that the fix was designed to catch was silently never examined, because only the newest candidate of each type ever reaches the overlap check.


## HIGH (30)

### Calibration Loop

**`AUD232-016`** [BUG] `services/market-data/src/services/paper_trading_engine.py`:1972

The signal_outcomes writeback in _close_trade wraps the SELECT+update in a bare try/except that only logs a warning on failure and continues — a failed writeback silently leaves that SignalOutcome row's entry_price/exit_price/return_Nd/is_correct_Nd fields unset for the (stock, horizon, signal_date) tuple, with no retry and no reconciliation path back to evaluate_signal_outcomes.

*Detail:* PT-J1's writeback only fires from the paper-trading exit path — it's a best-effort enrichment of an already-existing SignalOutcome row (looked up by signal_id) with real fill prices, distinct from and additional to evaluate_signal_outcomes's own price-based entry/exit calculation. If the select() raises (e.g. a transient DB hiccup, or the SignalOutcome row not yet existing because evaluate_signal_outcomes hasn't processed that signal_id yet since hold windows differ from paper-trading exit timing), the exception is logged at WARNING and swallowed — trade.signal_id's outcome row keeps whatever evaluate_signal_outcomes itself later computes from price bars, which may disagree with the actual paper-trading fill (real slippage-adjusted entry/exit vs. idealized D1-close-based entry/exit).

*Failure scenario:* A paper trade closes and its writeback to signal_outcomes throws (e.g. because evaluate_signal_outcomes hasn't yet created a SignalOutcome row for that signal_id, since paper-trading exits are driven by live stop/target hits and can close well before or after the fixed calendar-day hold window evaluate_signal_outcomes uses) — the warning is logged and the loop moves on, so that trade's real, slippage-adjusted return never overwrites the idealized D1-close return evaluate_signal_outcomes later fills in, and nothing surfaces this divergence to a human.

**`AUD232-017`** [BUG] `services/ml-prediction/src/training/meta_trainer.py`:115

meta_trainer's SQL join reconnects signal_outcomes to stocks via the denormalized `symbol` string column (`st.symbol = so.symbol`) instead of the actual foreign key (`so.stock_id`), even though SignalOutcome.stock_id exists and is indexed.

*Detail:* shared/db/models.py:126 declares `UniqueConstraint('symbol', 'exchange', name='uq_stock_symbol_exch')` on Stock — symbol is only unique per (symbol, exchange), NOT globally. meta_trainer.py's raw SQL (`FROM signal_outcomes so JOIN stocks st ON st.symbol = so.symbol`) joins purely on the ticker string, so if any two rows in `stocks` ever share a symbol across different exchanges/markets (e.g. a US ticker and an HK numeric-code collision, or any future dual-listing), the join silently fans out (returns duplicate rows, one per matching stocks row) or attaches the wrong sector/market_cap to an outcome. SignalOutcome already carries stock_id (the real FK, used correctly everywhere else in signal-engine and ml-prediction/builder.py), which this query ignores.

*Failure scenario:* Two stocks in different markets end up sharing the same symbol string (plausible for HK tickers that are purely numeric and could theoretically collide with a delisted-and-relisted US symbol, or via a data-entry/ingestion bug creating a duplicate stocks row before the UniqueConstraint catches it as (symbol, exchange) rather than symbol alone). The meta-model training query then either silently duplicates that outcome's row (via join fan-out, corrupting sample weighting) or attaches sector/market_cap_bin features from the wrong stock, quietly degrading the cross-symbol meta-model's feature quality with no error or warning — the training job would report a successful 'trained': true with a real (but subtly wrong) AUC.

**`AUD232-018`** [BUG] `services/signal-engine/src/api/routes.py`:4176

signal_watchdog() can tighten BUY thresholds by up to +12pp based on as few as 5 fourteen-day samples with zero out-of-sample validation, while every other threshold-mutation path in the same file (outcomes_calibrate_apply, tune_style_profiles, calibrate_ml_weight) requires 2x-4x min_samples plus a 70/30 walk-forward split before applying any change.

*Detail:* signal_watchdog (scheduled daily 06:10 ET per scheduler.py:3892-3902) reads signal_outcomes over a rolling 14-day window and, if win_rate_14d < 0.38 with >=5 samples, immediately writes stockai:watchdog:{STYLE}:threshold to Redis — a key _get_dynamic_buy_threshold() (signals.py:1450) checks BEFORE the properly walk-forward-validated stockai:signal_thresholds:{STYLE} key ('Check watchdog emergency adjustment first (most recent, tightest)'). Every other threshold-writing endpoint in this file was explicitly hardened after a documented live incident (T232-OC3: 'an unvalidated argmax over 31 correlated subsets is prone to surfacing an upward-biased fluke as optimal') to require train/validation splits and EV-lift gates. signal_watchdog has none of that: no train/val split, no EV comparison, no _record_tune_history call (it's invisible in the TuneHistory audit trail that every other calibration mechanism writes to), and can apply on n=5.

*Failure scenario:* A single bad week for a low-signal-volume style (e.g. GROWTH on HK, which the map notes has few evaluated outcomes) produces 5 signal_outcomes with a 1/5 win rate purely from noise (20% is well within normal variance at n=5). signal_watchdog tightens the threshold by 3pp immediately and takes precedence over the properly-validated calibrated threshold for up to 7 days, silencing legitimate BUY signals — and because it's not recorded in TuneHistory, nobody auditing the self-improve pipeline via tune_status or TuneHistory can see why the threshold moved.

**`AUD232-019`** [BUG] `services/signal-engine/src/api/routes.py`:4879

evaluate_signal_outcomes calls the research-engine's /research/{symbol}/summary endpoint with a 2-second timeout and swallows any failure into (None, None) silently — a slow research-engine response permanently blanks research_rec/research_score for that outcome row unless Phase 2's NULL-column backfill happens to retry it later.

*Detail:* _fetch_research() catches all exceptions (including httpx timeouts) and returns (None, None) with no logging. Phase 2 of the same function does retry rows where research_rec is None, but only for up to 500 rows per run (session.execute(...).limit(500)) and only on the NEXT scheduled run — a systemic research-engine slowdown during the outcomes/evaluate window (Sunday, same time as the weekly full refresh, tune_all kickoff, and other Sunday load) could blank research_rec across a full week's cohort with no alert, silently degrading the by_research_alignment breakdown in outcomes/summary.

*Failure scenario:* If research-engine is under heavy load during Sunday's full refresh (competing with tune_all, calibrate_ta_weights, etc. all firing around the same window) and every /research/{symbol}/summary call times out at 2s, every outcome evaluated that run gets research_rec=None — outcomes/summary's by_research_alignment then buckets that week's entire cohort into 'no_research', invisibly diluting the aligned/divergent signal-quality comparison with no error surfaced anywhere.


### Decision Engine

**`AUD232-020`** [DUPLICATE] `services/decision-engine/src/api/core/hard_rejects.py`:184

DE hard-rejects trades in the first 30 min / last 15 min of the session (T185 time-of-day gate); _should_enter() has no such check at all

*Detail:* hard_rejects.py lines 184-202: if market minutes fall in [570,600) (9:30-10:00 local) or [945,960) (15:45-16:00 local), hard-reject with 'Time-of-day gate'. paper_trading_engine._should_enter() (lines 1301-1551) contains no time-of-day check whatsoever -- it is purely a function of signal_data/game_plan/cfg with no wall-clock awareness.

*Failure scenario:* A candidate signal fires at 9:35am ET with an otherwise-strong score (say 6, well above min_entry_score=4). The fallback engine's _should_enter() enters immediately. DE, given the identical signal_data/game_plan/cfg plus the current time, hard-rejects with 'first 30 min of market open' before scoring even runs. Whenever DE is the active gate, this rejects legitimate opening-range entries that the fallback would take; if DE ever goes down mid-session and the caller falls back to _should_enter(), previously-rejected 9:35am candidates would suddenly be allowed.

**`AUD232-021`** [DUPLICATE] `services/decision-engine/src/api/core/hard_rejects.py`:46

DE hard-rejects on weekend/NYSE-holiday/outside-trading-hours (T193 market-closed guard); _should_enter() has no equivalent check

*Detail:* hard_rejects.py lines 46-73 check the local exchange clock against weekday, a hardcoded _NYSE_HOLIDAYS set, and session hours (HK 9:30-12:00/13:00-16:00, US 9:30-16:00), hard-rejecting with 'Market closed: ...' on any violation. _should_enter() in paper_trading_engine.py has no session-open check inside the function itself (this is instead enforced entirely by the calling scheduler's own market-hours gating elsewhere in the file, not inside _should_enter()).

*Failure scenario:* If _should_enter() is ever invoked directly (e.g. from a manual backfill/test path, or if the caller's own market-hours gate has a bug) outside of trading hours, it would happily score and potentially approve an entry with no internal safeguard, whereas DE would independently hard-reject the exact same call with 'Market closed'. The two systems' safety guarantees are not equivalent: DE's is self-contained, the fallback's depends entirely on correct caller-side gating that lives outside the ported function.

**`AUD232-022`** [DUPLICATE] `services/decision-engine/src/api/core/hard_rejects.py`:75

DE hard-rejects immediately on research_rec AVOID/SELL (when research_gating_enabled); _should_enter() never checks research_rec as a hard reject at all

*Detail:* hard_rejects.py line 75-76: `if cfg.get('research_gating_enabled') and research_rec in ('AVOID','SELL'): return reject`. _should_enter()'s signature (paper_trading_engine.py:1301-1309) doesn't even accept a research_rec parameter -- research alignment is scored only in scorer.py's Layer 4 (+2/+1/0/-1/-2 via _RESEARCH_SCORE) as an additive point, never as a hard block, and _should_enter() has no research scoring layer or hard-reject at all.

*Failure scenario:* A candidate with research_rec='SELL' but a strong additive score (rr=4.0, high volume, high bull_prob) reaching score>=min_entry_score: _should_enter() has no way to see or react to research_rec at all, so it enters freely. DE, with research_gating_enabled=true, hard-rejects the identical candidate outright before any scoring happens purely because of the SELL research rec -- a guaranteed disagreement whenever research gating is on and research disagrees with an otherwise-strong technical setup.

**`AUD232-023`** [DUPLICATE] `services/decision-engine/src/api/core/hard_rejects.py`:173

DE hard-rejects on a sector position-COUNT cap (max_sector_positions); this exists in paper_trading_engine's caller (_scan_for_entries) but not inside _should_enter() itself, and the real engine also has a parallel dollar-exposure cap (max_sector_pct) that DE explicitly cannot check

*Detail:* hard_rejects.py lines 167-182 (T232-DL-DUALSCORER comment) hard-rejects if open_sector_counts[candidate_sector] >= max_sector_positions (default 3). The comment in hard_rejects.py itself states this only reconciles the COUNT-based cap and that 'the real engine's dollar-exposure cap, max_sector_pct, needs live per-position prices this endpoint never receives' -- i.e. DE acknowledges it cannot replicate the full sector-risk check paper_trading_engine.py performs elsewhere (grep shows max_sector_pct/max_sector_positions checks at multiple call sites around line 3974 in paper_trading_engine.py, separate from _should_enter()).

*Failure scenario:* A sector has 2 open positions (under the count cap of 3) but those 2 positions already represent 35% of portfolio dollar exposure (over some max_sector_pct dollar threshold enforced elsewhere in paper_trading_engine.py's caller). The real engine's dollar-exposure check blocks a 3rd entry into that sector; DE's hard_rejects.py has no way to see live per-position dollar values and only checks the count (2 < 3), so DE would approve an entry the real engine's full gate chain would reject on dollar-exposure grounds.

**`AUD232-024`** [DUPLICATE] `services/decision-engine/src/api/core/hard_rejects.py`:103

DE's stop-distance hard-reject has an explicit separate 'stop above price' check with a distinct message; _should_enter() folds this into a single min_stop_dist comparison with different wording

*Detail:* hard_rejects.py lines 103-111: first checks `if stop_dist <= 0: return 'Stop is above price - invalid setup'`, THEN separately checks `if stop_dist < min_stop_dist: return 'too close to price'`. _should_enter() (paper_trading_engine.py:1336-1343) has only ONE check: `if stop_dist < min_stop_dist: return False, -99, [...]` -- a negative stop_dist is caught by this same single branch but produces the 'too close to price' message (potentially showing a negative distance value) rather than a distinct 'invalid setup' message. Both ultimately reject on the same numeric condition (stop_dist < min_stop_dist covers stop_dist<=0 since min_stop_dist>0), so this is a message/diagnostics divergence rather than a decision divergence.

*Failure scenario:* A malformed game_plan produces stop > live_price (stop_dist = -0.50). Both engines reject the trade (same outcome), but _should_enter()'s log/notes show 'distance $-0.50 < min $0.05' (a confusing negative distance in the user-facing reason) while DE shows the clearer 'Stop $X is above price $Y — invalid setup'. Not a BUY/no-BUY divergence, but anyone debugging why a trade was rejected sees materially different diagnostic text depending on which engine handled it -- worth flagging since a future edit to either message could accidentally change behavior thinking it's cosmetic.

**`AUD232-025`** [DUPLICATE] `services/decision-engine/src/api/core/scorer.py`:26

DE's compute_score() has no cross-horizon consensus scoring layer; paper_trading_engine._should_enter() has one (+1/-1)

*Detail:* _should_enter() (paper_trading_engine.py:1500-1508) scores reasons.get('cross_style_buys'): if >=2 other horizons also say BUY, +1 ('strong multi-timeframe alignment'); if 0 cross-horizon BUYs AND regime is bear/choppy, -1. scorer.py has no layer reading 'cross_style_buys' at all — it is never referenced anywhere in scorer.py.

*Failure scenario:* A candidate has cross_style_buys=3 (all other horizons agree BUY) in a choppy regime. _should_enter() gives it +1 for this. DE's compute_score() gives it 0 extra points for the same reasons dict. If the candidate is otherwise exactly at min_entry_score-1, the fallback engine enters the trade and DE (primary) rejects it on the same tick, same data.

**`AUD232-026`** [DUPLICATE] `services/decision-engine/src/api/core/scorer.py`:150

DE scores 'catalyst_score' as a single combined layer; _should_enter() scores 'insider_score' and 'congress_score' as two SEPARATE layers with different thresholds

*Detail:* _should_enter() (paper_trading_engine.py:1513-1527): insider_score >=60 -> +1, insider_score < -30 -> -1 (independent layer); congress_score > 50 -> +1 (independent layer, no negative case). Combined this can swing +2 (insider+congress both positive) or range -1..+2. scorer.py Layer 3f (lines 151-161) instead reads a single reasons['catalyst_score'] field: >=60 -> +1, <=-30 -> -1, else 0 -- capped at +1/-1 total regardless of how insider vs congress each individually scored. There is no reasons key named 'catalyst_score' shown being produced by the two-field insider/congress scheme in _should_enter, so DE's layer is reading a value that has entirely different semantics/derivation than the two fields the fallback scores.

*Failure scenario:* A stock has insider_score=65 (strong insider buying, +1 in fallback) AND congress_score=60 (net buying, +1 in fallback) = +2 combined from this category in _should_enter(). If signal-engine's 'catalyst_score' single field for the same underlying data computes to, say, 55 (between the -30/60 DE thresholds), DE's scorer.py awards 0 points for the identical underlying catalyst data. A borderline candidate needing exactly 2 points to cross min_entry_score enters in the fallback but is rejected by DE.

**`AUD232-027`** [DUPLICATE] `services/decision-engine/src/api/core/scorer.py`:26

DE has no RL policy adjustment layer; _should_enter() applies a +1/-1 RL Q-function adjustment (AL-1)

*Detail:* _should_enter() (paper_trading_engine.py:1477-1498): when _RL_AVAILABLE, calls _rl_recommend(rr_ratio, confidence, entry_score=score-so-far, kscore, style, regime) and if the linear Q-function's action is 'BUY', +1 ('RL policy BUY'), else -1 ('RL policy WAIT'). scorer.py has no equivalent call or layer anywhere -- compute_score() never imports or references any RL module.

*Failure scenario:* Same symbol, same signal_data, kscore=72, rr=2.8: the RL policy recommends BUY in the fallback engine (+1, tips the score from 3 to 4, exactly at min_entry_score=4 -> ENTER). DE's compute_score(), lacking this layer entirely, produces a score of 3 for the identical inputs and is rejected by min_score_for_regime's floor of 4 -- the two engines disagree on the same candidate purely because RL scoring exists in one and not the other.

**`AUD232-028`** [DUPLICATE] `services/decision-engine/src/api/core/scorer.py`:202

DE's decision rule is ALWAYS the additive-score threshold; _should_enter() can bypass it entirely via a calibrated logistic-regression probability model (PT-3) once >=100 closed trades exist

*Detail:* _should_enter() (paper_trading_engine.py:1531-1549): if _load_entry_weights() has an intercept and n_trades>=100, it computes cal_prob = sigmoid(intercept + w_rr*min(rr,8) + w_confidence*confidence + w_score*score + w_kscore*kscore) and decides should = cal_prob >= threshold (default 0.52) -- this REPLACES the score>=min_entry_score comparison entirely, using a completely different function of the inputs (logistic regression vs. linear threshold). min_score_for_regime() in scorer.py has no calibrated-probability path at all; DE always uses score >= min_score_for_regime(regime_state, cfg).

*Failure scenario:* Once a portfolio has >=100 closed paper trades, its fallback _should_enter() switches to the calibrated logistic model. A candidate with score=3 (below the raw min_entry_score=4) but high rr=4.5, confidence=85, kscore=80 could produce cal_prob=0.60 >= 0.52 -> ENTER via calibration. DE's scorer.py has no calibration path and no way to even know a calibration model exists -- it will always reject the same score=3 candidate outright via min_score_for_regime's floor of 4, producing a guaranteed real-world disagreement for any mature portfolio.


### ML Prediction

**`AUD232-029`** [BUG] `services/ml-prediction/src/api/routes.py`:39

PredictRequest.horizon defaults to 5 (SHORT's horizon) regardless of the style field, and signal-engine never sends horizon at all

*Detail:* `PredictRequest.horizon: int = 5` is a fixed default independent of `style`, whereas train-time horizon is always correctly derived server-side via `_HORIZON_BY_STYLE.get(style.upper(), ...)`. signal-engine's `_fetch_ml_data()` (services/signal-engine/src/generators/signals.py:335) sends `payload = {"symbol": symbol, "style": style_key}` with no `horizon` key at all, so every call for SWING/LONG/GROWTH styles silently falls back to horizon=5 (SHORT's horizon) inside predict_latest()/predict_latest_ensemble_three(). This currently causes no user-visible harm only because build_features() with inference_mode=True discards the fwd_ret/y_dir it computes from `horizon` and only the artifact's own trained buy_threshold/scaler/model (loaded from the correct per-style .joblib path) actually drive the prediction — but this is fragile: any future change to build_features() that starts using `horizon` for an X-column (not just the y target) would silently corrupt every non-SHORT-style live prediction with no error.

*Failure scenario:* A future feature addition to build_features() (e.g. a horizon-relative feature like 'days until horizon-day return realizes') would use the wrong horizon value for every SWING/LONG/GROWTH prediction request coming through signal-engine, since the request payload never carries the correct value and the server-side default silently substitutes SHORT's horizon instead of erroring or deriving it from style like every training code path already does.

**`AUD232-030`** [BUG] `services/ml-prediction/src/training/meta_trainer.py`:116

train_meta_model()'s SQL joins fundamentals with no as_of/date filter, causing row fan-out and an arbitrary (non-point-in-time) market_cap per signal_outcome

*Detail:* The query `JOIN stocks st ON st.symbol = so.symbol LEFT JOIN fundamentals f ON f.stock_id = st.id` has no date predicate and no LIMIT/window function, even though `fundamentals` has a `UniqueConstraint(stock_id, as_of)` — one row per stock per day the /fundamentals endpoint was called (confirmed in shared/db/models.py:753). Every signal_outcomes row is fanned out into N rows (one per historical fundamentals snapshot for that stock), and the `market_cap` value used for `market_cap_bin` is whichever row Postgres happens to return first (no ORDER BY) — not the market cap as of `so.signal_date`. This is the exact lookahead/point-in-time class of bug that T228-POINT-IN-TIME-FUNDAMENTALS and T234-ML-FUND-BROADCAST-LEAKAGE already fixed for builder.py's per-row fundamental features, but it was missed here. Downstream, `symbol_rows[row.symbol].append(row)` (line 134) has no dedup, so `sym_rows_sorted` contains duplicate signal_dates with different arbitrary market_cap values, each triggering a redundant build_features() call and injecting noisy/wrong market_cap_bin labels into meta-model training.

*Failure scenario:* A stock with 50 historical fundamentals snapshots and 10 signal_outcomes rows produces 500 joined rows instead of 10. Training records for the same real trading outcome get duplicated with different (effectively random) market_cap_bin values — e.g. the same BUY signal from when the stock was mega-cap could be labeled micro-cap in one duplicate row, actively corrupting the meta-model's cross-symbol generalization feature and silently degrading `predict_meta()`'s 15%-weighted ensemble contribution with no error or warning anywhere in the pipeline.


### Meta-Model

**`AUD232-031`** [BUG] `services/ml-prediction/src/training/meta_trainer.py`:8

Module docstring hardcodes FEATURE_COLUMNS length as '(61)' — the actual current length is 60 — a stale/incorrect count that risks masking a real drift check if anyone relies on the comment instead of the live import.

*Detail:* The docstring at the top of the file says 'Feature vector: FEATURE_COLUMNS (61) filtered to non-constant cols'. Counting the actual FEATURE_COLUMNS list in builder.py (including its WEEKLY_COLUMNS/SECTOR_COLUMNS/OUTCOME_COLUMNS/FUNDAMENTAL_COLUMNS expansions) gives 60, not 61. The code itself does NOT hardcode this number anywhere executable (it always does `for col in FEATURE_COLUMNS`, so there is no functional bug here) — but the comment is already wrong today, which is exactly the kind of stale documentation that would let a real future drift (adding/removing a column in builder.py) go unnoticed if someone trusts the comment instead of re-deriving the count.

*Failure scenario:* A future engineer reviewing meta_trainer.py trusts the docstring's '(61)' figure as ground truth when reasoning about vector length/debugging a shape mismatch, and concludes the feature count is unchanged/correct when it has already silently drifted (60 vs claimed 61), delaying detection of an upstream builder.py change.

**`AUD232-032`** [BUG] `services/ml-prediction/src/training/meta_trainer.py`:238

non_const column-masking is computed once at meta-model train time and persisted in the bundle; it silently assumes FEATURE_COLUMNS' length/order at train time exactly matches the length/order used to build vec at predict time, with no length/order assertion — if builder.py's FEATURE_COLUMNS changes (grows, shrinks, or reorders) between a meta-model's training and its later use for prediction, the positional non_const indices become meaningless with no error raised.

*Detail:* train_meta_model() builds `vec` positionally from `for col in FEATURE_COLUMNS` (imported fresh at call time from builder.py) plus 6 appended meta features, computes `non_const = np.where(X_raw.std(axis=0) > 1e-8)[0]` (pure position-based indices into that vector), and persists both `non_const` and (redundantly) `feature_columns` in the joblib bundle — but `feature_columns` is never actually used to validate anything at predict time (grep confirms `bundle['feature_columns']` is set at line 278 but never read back in predict_meta). predict_meta() rebuilds `vec` the same way — `for col in FEATURE_COLUMNS` (imported fresh again) + 6 appended features — and applies the *old* bundle's `non_const` positional index array directly via `X_raw[:, non_const]` with no check that len(vec) still matches what non_const was computed against.

*Failure scenario:* builder.py's FEATURE_COLUMNS is modified (e.g. a column is added mid-list, or one is removed) between when meta_model.joblib was last trained and the next predict_meta() call — a routine occurrence since builder.py has already been edited multiple times per its own history (T234-ML-FUND-BROADCAST-LEAKAGE, T228-POINT-IN-TIME-FUNDAMENTALS additions visible in this same file). predict_meta() builds a new-length `vec`, then does `X_raw[:, non_const]` using the OLD bundle's indices — either silently selecting the wrong (now-shifted) columns if the new length is >= max(non_const)+1 (feeding scrambled features into `scaler.transform`), or raising `IndexError: index N is out of bounds for axis 1` if the new vector is shorter. Either way this is exactly the 'index out of bounds' class of bug already seen elsewhere in this codebase's ML pipeline (CLAUDE.md's ml-prediction shape-mismatch incident, 'index 66 is out of bounds for axis 1 with size 66'), and here it would fail silently-wrong rather than loudly in the scrambled-selection case.

**`AUD232-033`** [BUG] `services/ml-prediction/src/training/meta_trainer.py`:116

train_meta_model()'s SQL join to fundamentals (`LEFT JOIN fundamentals f ON f.stock_id = st.id`) takes an arbitrary/unspecified fundamentals row per stock (no ORDER BY/LIMIT 1 on f, unlike builder.py's own _load_fundamentals which explicitly orders by as_of DESC), risking a non-deterministic or stale market_cap feeding market_cap_bin.

*Detail:* trainer.py's _load_fundamentals() (used by the base models) explicitly does `.order_by(sa_desc(Fundamental.as_of)).limit(1)` to guarantee the most recent snapshot. meta_trainer.py's raw SQL query (lines 110-120) does a plain `LEFT JOIN fundamentals f ON f.stock_id = st.id` with no ordering or limiting on `f` — if a stock has multiple `fundamentals` rows (which the schema clearly supports, given _load_fund_snapshots's point-in-time design), this join can return multiple rows per signal_outcome (silently multiplying that row into `records` once per matched fundamentals row) or an arbitrary one depending on the query planner/join order, rather than deterministically the latest.

*Failure scenario:* For any symbol with more than one row in the `fundamentals` table (normal for a stock tracked over multiple fundamentals refresh cycles), the JOIN silently fans out — a single signal_outcome row gets duplicated once per matching fundamentals row in the `rows` result set, each carrying a different (arbitrary, non-deterministic across DB engine versions/query plans) `market_cap` value. This both over-weights that outcome in training (duplicate rows with the same label) and makes market_cap_bin non-reproducible run-to-run for the same underlying data, without any error or warning.


### Position Sizing

**`AUD232-034`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:98

Confidence-band sizing breakpoints (>=80/>=62/else) in sizer.py are a materially different scale than paper_trading_engine.py's real sizing breakpoints (>=50/>=30/else, lines 3834-3841) — same concept, independently implemented, non-identical thresholds and multiplier values.

*Detail:* sizer.py:98-103: `>=80 -> 1.25, >=62 -> 1.00, else -> 0.85`. paper_trading_engine.py:3834-3841: `>=50 -> 1.25, >=30 -> 1.0, else -> 0.75`. This is now a DOCUMENTED divergence per T232-DL-DUALSCORER-DOCFIX (sizer.py:1-13), not a silent bug, but the risk of a future caller assuming they agree remains real since the /decide endpoint's response is user-visible.

*Failure scenario:* A developer building a new caller against sizer.py's /decide preview endpoint (or a user reading its PositionPlan response) reasonably assumes it approximates what paper trading will actually do for a given confidence level, e.g. expecting a confidence=55 signal to get the 0.85x tier shown in the preview — but the real engine would size that same signal at 1.25x (>=50 tier) since its bands and bottom multiplier (0.75 vs 0.85) disagree, producing a preview that materially misrepresents real position sizing at that confidence level.

**`AUD232-035`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:66

sizer.py floors stop_dist at live_price*0.01 (T237-DE1) to avoid a degenerate near-zero stop distance producing a misleading dollar_risk figure, but paper_trading_engine.py's real sizing path has no equivalent floor — only a bare `<= 0` check — relying entirely on downstream caps to absorb a tiny-but-positive stop_distance.

*Detail:* sizer.py:58-66 explicitly floors stop_dist at live_price*0.01. paper_trading_engine.py:3815-3817 only checks stop_distance<=0, with no dedicated floor before the shares division at line 3912.

*Failure scenario:* A game_plan with stop set at 99.9% of live_price (tiny but positive stop_distance) passes paper_trading_engine.py's `if stop_distance <= 0: continue` check unblocked, then `shares = risk_dollar / stop_distance` (line 3912) computes an extremely large raw share count; this is only reeled in afterward by the max_loss_per_trade_pct and max_position_pct caps rather than being floored at the source the way sizer.py handles the identical scenario, so the two files represent risk for the same degenerate input differently with no shared constant keeping them aligned.

**`AUD232-036`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:144

Composition operator differs: sizer.py uses min() across market signals but multiplies ALL per-trade multipliers together with no combined floor, while paper_trading_engine.py multiplies every one of its 6 per-trade multipliers together AND applies a 25% floor on the composed result that sizer.py lacks entirely.

*Detail:* sizer.py line 144-150: `market_mult = min(regime_mult, breadth_size_mult, vix_size_mult)`; `risk_dollar = equity * risk_per_trade * market_mult * earnings_mult * confidence_mult * research_mult * consensus_mult`. There is NO score_size_mult (sizer.py has no DE-score-to-size multiplier at all — DE-D2/T188 logic is entirely absent from sizer.py), and no floor is applied to the multiplied result. paper_trading_engine.py line 3900-3911: `_risk_base = equity * cfg['risk_per_trade_pct']`; `risk_dollar = _risk_base * earnings_size_mult * regime_size_mult * confidence_size_mult * research_size_mult * consensus_size_mult * score_size_mult`; then `risk_dollar = max(risk_dollar, _risk_base * 0.25)` (T234-PT-SIZING-MULT-STACK floor). sizer.py has 5 multiplying factors (earnings, confidence, research, consensus, market_mult-as-single-factor) with no floor; paper_trading_engine.py has 6 multiplying factors (earnings, regime(=market), confidence, research, consensus, score) WITH a 25% floor. A worst-case stack in sizer.py (earnings 0.50 x confidence 0.85 x research 0.60 x market_mult e.g. 0.50 = 0.1275, i.e. 12.75% of risk target) has no floor to catch it, whereas the identical stack in the real engine would be floored at 25%.

*Failure scenario:* A stock near earnings (dte=8, earnings_mult/earnings_size_mult=0.50) with confidence=65 (sizer.py confidence_mult=1.00 since >=62; paper_trading_engine confidence_size_mult=1.0 since 50<=65, both agree here) but AVOID/SELL-adjacent research (research_mult/research_size_mult=0.60) during a risk_off regime (regime_mult=0.50, market_mult=min(0.50, breadth, vix)=0.50 assuming breadth/vix are 1.0) and cross_style_buys=0 (consensus=1.00) and, in the real engine, a DE score right at the min threshold (score_size_mult=0.75): sizer.py risk_dollar = equity*0.01*0.50*0.50*1.00*0.60*1.00 = equity*0.0015 (0.15% risk, unfloored). paper_trading_engine.py risk_dollar = equity*0.01*0.50*0.50*1.0*0.60*1.00*0.75 = equity*0.001125, but then floored to max(0.001125, 0.01*0.25)=equity*0.0025 (0.25% risk). The two systems diverge by more than 2x on the same inputs purely because of the missing floor and missing score multiplier in sizer.py, and would diverge further for any stack that pushes the real engine's unfloored product below 0.0025 while sizer.py's product (missing one multiplier) computes something different.

**`AUD232-037`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:19

sizer.py's regime multiplier table has an explicit bear:0.00 (full block via zero size), but paper_trading_engine's regime_size_mult dict has NO 'bear' key at all — `.get(regime_state, 1.0)` means a bear regime state silently falls back to full size (1.0), not zero.

*Detail:* sizer.py lines 19-25: `_REGIME_MULT = {'bull':1.00,'neutral':1.00,'choppy':0.75,'risk_off':0.50,'bear':0.00}`. paper_trading_engine.py lines 3073-3078: `regime_size_mult = {'bull': cfg.get('regime_bull_size_mult',1.0), 'neutral':1.0, 'choppy': cfg.get('regime_choppy_size_mult',0.75), 'risk_off': cfg.get('regime_risk_off_size_mult',0.50)}.get(regime_state, 1.0)` — 'bear' is not a key in this dict, so if regime_state ever resolves to the literal string 'bear', the `.get(regime_state, 1.0)` default fires and sizing is full-size 1.0, not zero. (Separately, `regime_bear` is gated elsewhere as an entry-blocking gate per CLAUDE.md's 11-gate list, so in practice a bear regime may already return early via `_write_gate_block(..., 'regime_bear', ...)` before reaching this sizing code — but that upstream gate is a different code path from this multiplier table, and if that gate's condition and this dict's keys ever drift out of sync, the sizing table itself provides no bear-zero protection the way sizer.py's table claims to.)

*Failure scenario:* If any future refactor removes or narrows the upstream regime_bear entry gate (or a new caller invokes this sizing block directly, bypassing the gate) while regime_state is 'bear', paper_trading_engine.py would size the trade at full 1.0x regime multiplier (before VIX/HMM/breadth min() reductions), while decision-engine's sizer.py — asked to size the identical bear-regime trade — would apply its own regime_mult=0.00, functionally zeroing the position. The same (symbol, regime='bear') input produces a full-size trade in one system and a zero-size trade in the other.

**`AUD232-038`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:98

Confidence-multiplier breakpoints and values differ between the two systems: sizer.py uses 3 tiers (>=80:1.25, >=62:1.00, else:0.85) rescaled above a 55.8 floor per the T232-DE2 comment, while paper_trading_engine.py uses 3 different tiers (>=50:1.25, >=30:1.00, else:0.75) with no such floor rescaling.

*Detail:* sizer.py lines 98-103: `if confidence>=80: confidence_mult=1.25 elif confidence>=62: confidence_mult=1.00 else: confidence_mult=0.85`. paper_trading_engine.py lines 3832-3841 (PT-D2): `if sig_conf>=50: confidence_size_mult=1.25 elif sig_conf>=30: confidence_size_mult=1.0 else: confidence_size_mult=0.75`. These are on the SAME confidence scale (0-100, from signal engine) but use entirely different breakpoints (80/62 vs 50/30) and different floor multiplier (0.85 vs 0.75).

*Failure scenario:* A stock with signal confidence=65 hits sizer.py's middle tier (>=62 -> confidence_mult=1.00) but paper_trading_engine's TOP tier (>=50 -> confidence_size_mult=1.25) — a 25% sizing difference on identical confidence input. Conversely, a stock at confidence=55 hits sizer.py's bottom tier (0.85, since <62) but paper_trading_engine's top tier (1.25, since >=50) — a 47% relative sizing gap (1.25 vs 0.85) on the exact same signal-engine confidence value for the exact same symbol.

**`AUD232-039`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:145

sizer.py has no HMM bear-pressure dampening at all (confirmed absent) — paper_trading_engine.py applies an additional min(regime_size_mult, 0.70) reduction whenever the HMM model's bear_prob > 0.50, which sizer.py's market_mult calc has no equivalent input for.

*Detail:* paper_trading_engine.py lines 3120-3131 (QW-8): `if live_regime.get('hmm_bear_pressure'): regime_size_mult = min(regime_size_mult, 0.70)`. sizer.py's `compute_position()` signature (lines 37-49) takes no hmm_bear_pressure or hmm_state parameter whatsoever, and its market_mult = min(regime_mult, breadth_size_mult, vix_size_mult) has no third/fourth term for HMM. This gap is already called out in sizer.py's own corrected docstring (lines 7-9) as a known, accepted divergence, not a bug to fix — but it is a real formula difference that will size trades differently whenever HMM bear_prob crosses 0.50 outside of a rule-based bear/risk_off regime.

*Failure scenario:* Regime is classified 'neutral' (SMA/VIX rules see nothing wrong) but the HMM model's bear_prob is 0.55 (early-phase volatility clustering the rule-based classifier hasn't caught yet). paper_trading_engine.py: regime_size_mult starts at 1.0 (neutral), then HMM dampens to min(1.0, 0.70)=0.70. sizer.py: regime_mult=1.00 (neutral) with no HMM input available to reduce it further, so market_mult=min(1.00, breadth, vix) could stay at 1.00 if breadth/vix are also benign — a 30%+ sizing gap on the identical symbol/regime/HMM state, purely because sizer.py cannot see HMM state at all.

**`AUD232-040`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:160

max_position_pct cap multiplies by earnings_mult in BOTH systems (this part still agrees) but sizer.py's cap is applied strictly after the max_loss_pct cap with no min_position_value floor-skip check, while paper_trading_engine.py inserts a min_position_value skip-trade check between the max_loss cap and the max_position cap that sizer.py has no equivalent for.

*Detail:* sizer.py lines 153-166: max_loss cap, then directly `max_pos_value = equity * max_pos_pct; position_value = shares*live_price; if position_value>max_pos_value: shares = max_pos_value/live_price` — no min_position_value check anywhere, and note max_pos_value here does NOT multiply by earnings_mult (unlike paper_trading_engine.py's `max_pos = equity * cfg['max_position_pct'] * earnings_size_mult` at line 3940, which DOES fold earnings_mult into the cap itself, layered on top of earnings_mult already being baked into risk_dollar). sizer.py's own docstring (line 9-10) already flags 'the earnings multiplier doesn't compound into the max-position-pct cap the way it does in paper_trading_engine.py' as a known, accepted gap. paper_trading_engine.py lines 3922-3943: rounds shares, computes position_value, then skips the ENTIRE trade if `position_value < min_pos_val` (default $200) BEFORE applying the max_position_pct*earnings_size_mult cap.

*Failure scenario:* A stock 8 days from earnings (earnings_mult/earnings_size_mult=0.50) sized near the position cap: paper_trading_engine.py's cap becomes equity*max_position_pct*0.50 (half the normal cap, since earnings compounds into the cap itself), so a $100k-equity, 10%-cap portfolio caps this trade at $5,000 even though risk_dollar-derived shares might imply more. sizer.py's cap stays at equity*max_position_pct=$10,000 uncapped by earnings — the identical stock/equity/earnings-proximity combination produces a position capped at $5,000 in the real engine but allowed up to $10,000 in decision-engine's sizer.py, a 2x difference in maximum allowed position size.

**`AUD232-041`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:37

sizer.py has no K-score / ranking-engine input at all in its function signature or formula, while the task's own hint (and CLAUDE.md's gate documentation) references K-score as part of live paper-trading gating; paper_trading_engine.py's sizing formula (lines 3820-3911) likewise has no direct K-score-weighted multiplier in the sizing math itself — K-score is used only as a pre-entry gate (min_kscore), not a size multiplier — so this is not itself a drift, but confirms sizer.py cannot replicate ANY DE-score-based sizing since it has no `score`/`de_result` input, unlike paper_trading_engine's score_size_mult (T188) which sizer.py has no analog for.

*Detail:* paper_trading_engine.py lines 3890-3899 (T188): `if gate_source=='de' and de_result is not None: _score_excess = score - _min_score_cfg; score_size_mult = round(max(0.75, min(1.25, 0.75 + _score_excess*0.125)),3)` — a continuous multiplier from the DE entry score's excess above threshold, ranging 0.75 to 1.25. sizer.py's `compute_position()` signature has no `score`, `de_result`, or `min_entry_score` parameter and no equivalent multiplier is computed anywhere in the file — this entire sizing dimension is silently absent from decision-engine's model, not merely reparametrized like the confidence/regime tiers above.

*Failure scenario:* A DE-gated candidate scores 4 points above its min_entry_score threshold (score_excess=4): paper_trading_engine.py applies score_size_mult=1.25 (capped at max 0.75+4*0.125=1.25), a 25% size boost purely from DE score strength. decision-engine's sizer.py, asked to size the identical candidate with identical score/threshold inputs, has no mechanism to apply this multiplier at all — it always effectively computes as if score_size_mult=1.0, undercounting the position size by 25% relative to what the real trading engine would actually execute for a high-scoring candidate.


### Ranking Engine

**`AUD232-042`** [BUG] `services/decision-engine/src/api/core/scorer.py`:26

decision-engine's compute_score() has zero reference to K-Score or ranking-engine anywhere in the file (confirmed via full-file read and repo-wide grep for 'kscore'/'ranking' in services/decision-engine/src/ — no hits). The map's follow-up question ('whether/how kscore enters signal_data/reasons upstream') resolves to: it doesn't, at all. compute_score's 5 layers (price_zone, rr_quality, volume/earnings/ml/conf_delta/freshness/catalyst, pre_regime, research, regime) never incorporate the K-Score fundamental/technical composite that signal-engine's LONG style explicitly uses as a +/-0.08/+0.04/-0.06 fusion adjustment (signals.py:1909-1916) and that market-data's real conviction gate hard-requires >=55 for (scheduler.py:578-587).

*Failure scenario:* A LONG-horizon stock with kscore=25 (well below the LONG conviction gate's 55 floor and receiving signals.py's -0.06 fusion penalty) can still score highly in decision-engine's paper-trading entry gate purely on price_zone + rr_quality + ml_signal + regime layers, since compute_score has no K-Score-derived layer at all to reflect the fundamental weakness. This means the paper-trading engine's actual entry decision (which uses decision-engine's scorer, not signal-engine's conviction gate) can enter a position that the email-alert conviction gate would have blocked as 'K-Score below 55 — weak fundamental/momentum case' for the exact same symbol at the exact same time — two systems meant to represent 'is this a good buy' disagreeing because one of them structurally cannot see K-Score.

**`AUD232-043`** [BUG] `services/ranking-engine/src/api/routes.py`:722

_leaderboard_live() (the live-compute fallback used by GET /rankings whenever the Ranking table has zero rows for the requested market) has no per-stock try/except around its loop (lines 734-762), unlike _persist_rankings() which explicitly added per-stock exception isolation (lines 822-861) after the T232-RANKSTALE incident specifically to prevent one bad symbol from killing an entire batch.

*Failure scenario:* Immediately after a fresh DB bootstrap, or right after any operation that clears/rolls back the Ranking table for a market (e.g. a purge job, or a race where /rankings is hit before the first scheduled _persist_rankings run completes), GET /rankings falls through to _leaderboard_live. If any single stock in that market has price data that triggers an exception inside compute_kscore() or _stock_rs() (e.g. a malformed/NaN-only price row, or a divide-by-zero edge case not otherwise guarded), the uncaught exception propagates out of the for-loop and the ENTIRE leaderboard request 500s for every user viewing that market — rather than skipping the one bad symbol and returning results for the rest, which is exactly the resiliency behavior _persist_rankings was fixed to provide in the persisted path.

**`AUD232-044`** [BUG] `services/signal-engine/src/api/routes.py`:5116

gate_backtest's 'old vs new' comparison hardcodes a phantom baseline: the 'new' parameterization (relaxed MACD OR-condition, MACD soft-fail, GROWTH RSI floor=50) is not a proposed change — it IS the current, already-shipped behavior of the real _is_conviction_buy() in services/market-data/src/services/scheduler.py (lines 636-646, 691, 619-624). The function _is_conviction_buy referenced by name in this file's docstrings/comments (lines 5116, 5154, 5202) does not exist anywhere in signal-engine — grepping the whole repo shows the real implementation lives only in market-data's scheduler.py, a different service. gate_backtest was written to validate a historical migration (T234) and was never updated afterward to track the real gate's current parameters.

*Failure scenario:* An operator runs GET /rankings/gate_backtest expecting it to show how many additional historical BUY signals would be unblocked by relaxing the MACD condition and GROWTH RSI floor. Since scheduler.py's real gate has already had these relaxations in production for some time, old_pass and new_pass are computed identically for every historical row (both effectively replaying the same current-state logic under two different parameter labels), so the report will always show ~0 signals changed and ~0% difference in win rate — silently telling the operator 'this change had no effect' when in reality the comparison arms are identical to each other and both stale relative to the true baseline that existed before T234 shipped. Any future gate change proposal run through this endpoint will suffer the same false-negative unless someone manually re-syncs the hardcoded thresholds/conditions in this replica against scheduler.py first.


### Signal Engine

**`AUD232-045`** [BUG] `services/signal-engine/src/api/routes.py`:2266

calibrate_ta_weights() fits and persists a weight keyed "volume_surge", but _ta_score()'s _flag_map (signals.py) only ever reads "volume_z" — the calibrated volume weight is silently dropped from every live score until the process restarts.

*Detail:* signals.py's _TA_WEIGHTS_DEFAULT (line 133) stores the key as "volume_z" with an explicit comment "renamed from volume_surge to match reasons dict key", and _load_ta_weights()'s _apply_migration() (lines 221-224) does the volume_surge→volume_z rename — but ONLY when loading from Redis/file at process start. routes.py's calibrate_ta_weights() TA_FEATURES list (line 2266) and REASONS_MAP (line 2287) still use the old name "volume_surge", so the freshly-fitted dict it writes to ta_weights.json/Redis and passes to set_ta_weights() (line 2392) contains a "volume_surge" key, not "volume_z". set_ta_weights() (signals.py line 253-265) does a raw dict reassign with NO migration step, so the in-process _ta_weights dict now has a dangling "volume_surge" key that _flag_map (which looks up "volume_z", line 1202) never reads. The net effect: the volume feature's calibrated weight contributes 0 to every calibrated_ta_score computed by this process until it restarts and _load_ta_weights() re-applies the migration on next load from Redis/file.

*Failure scenario:* An admin runs POST /signals/calibrate_ta_weights; it returns success with a "volume_surge": 0.09 entry in the response. From that moment until the next container restart, every _ta_score() call's calibrated blend silently treats the volume dimension as weight-0 (regardless of what value was actually fit), quietly under-weighting a feature the calibration explicitly intended to change — with no error, warning, or visible signal that the just-run calibration didn't fully take effect.


## MEDIUM (30)

### Calibration Loop

**`AUD232-046`** [DUPLICATE] `services/ml-prediction/src/training/meta_trainer.py`:117

meta_trainer trains a single is_correct label pooling BUY and SELL signal_outcomes together, while every other calibration consumer (calibrate_conviction_weights, confidence-calibration, outcomes/summary) explicitly separates by signal_direction because BUY and SELL have documented divergent base rates.

*Detail:* The T232-OC5 comment in routes.py explains that pooling BUY (63.3%) and SELL (43.7%) win rates mixes populations that shouldn't be compared, and confidence-calibration/outcomes/calibration/calibrate_conviction_weights were all fixed to key by direction. meta_trainer.py's 'WHERE so.is_correct IS NOT NULL' query has no signal_direction filter and doesn't add direction as a feature either, so the meta-model is trained against a blended label whose base rate differs by ~20pp depending on which population dominates the most recent 20000 rows.

*Failure scenario:* As SELL signal volume grows relative to BUY (or vice versa) between meta-model retrains, the model's implicit prior shifts even though nothing about signal quality changed — a BUY prediction score from the meta-model is not comparable across retrains because the label's base rate silently drifted with the BUY/SELL mix, unlike the other three consumers which report BUY and SELL separately and would surface such a shift explicitly.

**`AUD232-047`** [DUPLICATE] `services/signal-engine/src/api/routes.py`:954

rolling_accuracy() and signal_accuracy() use a bare zero-line win rule (no cost hurdle), diverging from the canonical _OUTCOME_WIN_HURDLE_PCT=0.005 used by evaluate_signal_outcomes and every calibration endpoint that reads signal_outcomes.

*Detail:* evaluate_signal_outcomes (line 4980) defines is_correct as `pct_return > _OUTCOME_WIN_HURDLE_PCT` for BUY (a documented T232-OC4 fix: 'require clearing a real cost hurdle, not just a bare zero line'). This constant is reused consistently in _window_return, outcomes_calibrate, outcomes_calibrate_apply, and tune_style_profiles. But signal_accuracy() (line 827: `correct = (signal_type == 'BUY' and pct_change >= 0) or ...`) and rolling_accuracy() (line 954: `correct = exit_ > entry`) independently reimplement the same 'was this signal correct' concept using a bare >=0/>0 comparison with no hurdle, and rolling_accuracy additionally only ever evaluates BUY signals (never SELL) using a fixed 5-trading-day (7-calendar-day) exit regardless of the signal's actual horizon. Since the two endpoints don't read signal_outcomes at all — they recompute entry/exit from raw Signal+Price with their own first_close_after closures — the win-rate/accuracy numbers shown on the Signal Accuracy page (including the user-facing drift_warning banner) are computed under a materially different (and more lenient) correctness definition than every other calibration surface in the app.

*Failure scenario:* A BUY signal returns exactly +0.2% over its hold window (below the 0.5% cost hurdle). evaluate_signal_outcomes marks this is_correct=False (rightly, since a 0.2% gain doesn't clear commissions/slippage), and it counts as a loss in outcomes_summary, confidence-calibration, and every threshold-tuning sweep. But signal_accuracy() and rolling_accuracy() mark the identical trade as correct=True (pct_change=+0.2% >= 0), inflating the accuracy% shown on the Signal Accuracy page and potentially suppressing rolling_accuracy's drift_warning (latest_accuracy < 55%) even when the outcomes-based calibration data shows the model has genuinely drifted below its cost-adjusted breakeven.

**`AUD232-048`** [DUPLICATE] `services/signal-engine/src/api/routes.py`:2221

calibrate_ta_weights() independently recomputes forward returns directly from Signal+Price (its own bisect-based entry/exit lookup) instead of reading the already-computed, already-persisted signal_outcomes table that calibrate_conviction_weights (in the same file) correctly uses for the same underlying question.

*Detail:* calibrate_ta_weights (2221-2402) builds its own price map and `_lookup_price`/entry-exit logic from raw Signal.ts + Price.close, duplicating exactly what evaluate_signal_outcomes already computed and stored (entry_price, exit_price, pct_return keyed by hold_days per horizon). Just 184 lines later, calibrate_conviction_weights (2405-2514) does it correctly: `select(SignalOutcome.is_correct, Signal.reasons).join(Signal, ...)`. Both are 'weight/parameter calibration from historical trade outcomes' functions living in the same router, but only one of the two actually treats signal_outcomes as the source of truth.

*Failure scenario:* calibrate_ta_weights uses a single fixed hold_days query param (default 10) applied uniformly to every horizon regardless of that signal's own _OUTCOME_HOLD_DAYS (7/14/28 depending on style) — so a SHORT-horizon BUY signal meant to be held ~5 trading days gets evaluated on a 10-calendar-day forward return in this endpoint, while the SAME signal in signal_outcomes was evaluated (correctly) against its native 7-day window. If TA weights are refit from this endpoint, the fitted weights are optimized against a systematically different (and possibly look-ahead-inconsistent per-style) labeling than the win-rate/EV numbers reported everywhere else in the loop, and won't reconcile with outcomes_summary's per-horizon win rates even when both cover the same signals.

**`AUD232-049`** [DUPLICATE] `services/signal-engine/src/api/routes.py`:2249

calibrate_ta_weights does not read signal_outcomes at all — it independently re-derives its own entry/exit price lookups directly from the signals and prices tables, duplicating the win/loss labeling logic that outcomes/evaluate already computed and persisted.

*Detail:* evaluate_signal_outcomes() computes entry_price (first close strictly after signal_date), exit_price (first close on/after entry+hold_days), and derives pct_return/is_correct, persisting them to signal_outcomes. calibrate_ta_weights re-implements the identical T+1-entry, hold-days-later-exit price lookup pattern directly against Signal/Price tables to build its own y_rows (1 if fwd_ret > 0 else 0) — using a bare zero-line win definition, not the _OUTCOME_WIN_HURDLE_PCT (0.5%) cost-hurdle definition that outcomes/evaluate/is_correct uses. This is the same 'a +0.01% move counts as a win' problem that T232-OC4 fixed for signal_outcomes.is_correct, reintroduced independently here.

*Failure scenario:* TA weight calibration and outcome-based win-rate calibration can disagree on whether the identical historical BUY signal was a 'win', because calibrate_ta_weights uses fwd_ret > 0 (bare zero line) while signal_outcomes.is_correct (used by calibrate_conviction_weights, confidence-calibration, outcomes/summary) requires pct_return > 0.5%. A signal with +0.2% forward return counts as a win for TA-weight fitting but a loss everywhere else, so the TA weights and the reported/calibrated win rates are quietly fit against two different definitions of success.

**`AUD232-050`** [DUPLICATE] `services/signal-engine/src/api/routes.py`:3196

outcomes/summary's by_direction stats and confidence-calibration's per-(horizon,direction,market) buckets both compute a 'win rate by horizon+direction' from signal_outcomes independently, using different minimum-sample-size floors and different grouping granularity (no market split in by_direction vs. market-first in confidence-calibration), so the two endpoints can report materially different win rates for the same nominal horizon+direction slice.

*Detail:* outcomes/summary's direction_stats loop groups purely by (horizon, direction) with no minimum sample-size gate (reports even n=1 buckets) and no market split. _build_confidence_calibration groups by (horizon, direction, market) first with a hard _CONF_CAL_MIN_COUNT=30 floor, falling back to (horizon, direction) pooled only when the market-specific bucket is too thin. Both are valid 'win rate by horizon/direction' computations over the same table but use different grouping order and different reliability gates, so e.g. SWING/BUY's win rate shown by outcomes/summary (unfiltered, could be tiny-n) will not match SWING/BUY's calibrated_win_rate shown on a signal card (gated at n>=30, market-aware) even when queried at the same moment.

*Failure scenario:* A user viewing /signals/outcomes/summary for SWING/BUY sees e.g. a 71% win rate off 8 outcomes (no min-count gate), while the SignalCard on the same stock's detail page shows calibrated_win_rate falling back to the pooled (non-market-specific) 55% bucket because the market-specific bucket didn't reach 30 samples yet — two different, legitimately-computed numbers for what looks like the same 'SWING BUY win rate' metric, with nothing in the UI explaining why they differ.

**`AUD232-051`** [DUPLICATE] `services/signal-engine/src/generators/signals.py`:1506

SELL fallback threshold (0.35) is hardcoded independently in both signals.py and routes.py's outcomes_calibrate_apply, with no shared source of truth unlike the BUY threshold which reads from _STYLE_PROFILES.

*Detail:* signals.py:1506 does `sell_t = dynamic_sell if dynamic_sell is not None else 0.35`. routes.py:3751 independently defines `_CURRENT_SELL = 0.35  # fused-probability scale, matches the hardcoded fallback in signals.py` — a comment literally documenting that this must be kept in sync by hand. Unlike the BUY threshold, which both files source from `_STYLE_PROFILES[h]['buy_threshold']['bull']` (a single source of truth per T232-SIG12's own comment: 'no more independently-drifting hardcoded copies'), _STYLE_PROFILES has no sell_threshold key at all, so SELL has exactly the drift risk BUY was explicitly fixed to avoid.

*Failure scenario:* A future change updates signals.py's SELL fallback to 0.32 (e.g. to tighten SELL conviction) but the developer doesn't know (or forgets) to also update routes.py's _CURRENT_SELL constant used as the validation baseline in outcomes_calibrate_apply's SELL sweep. The calibration endpoint then computes ev_lift against the stale 0.35 baseline instead of the real live 0.32 threshold, silently misjudging whether a newly-suggested SELL threshold is actually better than what's running in production, and can apply a threshold that's actually worse than the true current baseline.


### Decision Engine

**`AUD232-052`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:98

confidence_mult tiers (>=80 -> 1.25, >=62 -> 1.00, else -> 0.85) use a deliberately different scale from the real trading engine's confidence_size_mult (>=50 -> 1.25, >=30 -> 1.0, <30 -> 0.75) — both read the identical 0-100 signal confidence field but produce very different multipliers for the same input, e.g. confidence=65 gives 1.00x in DE vs 1.25x in the real engine.

*Detail:* sizer.py's own T232-DE2 comment documents this as an intentional rescaling (to sit above DE's own higher hard-reject confidence floor of ~55.8), and the module docstring (T232-DL-DUALSCORER) explicitly disclaims that this is NOT meant to mirror paper_trading_engine.py. However, since `position` (from sizer.py) is only illustrative while `score` (from scorer.py, gated separately) is what's actually consequential for live trades, this divergence is lower-severity than the scorer.py findings above — flagging as a REFACTOR_CANDIDATE because the two confidence scales computing the same underlying quantity (0-100 signal confidence) with non-overlapping breakpoints and different max multipliers, spread across two services with only a code comment linking them, is a maintenance hazard: a reader modifying one confidence scale to 'match the real engine better' could easily break the documented independence without realizing it, or vice versa a future engineer could 'fix' the discrepancy assuming it's a bug when it is by design.

*Failure scenario:* A future maintainer, reviewing sizer.py in isolation without reading the full T232-DE2 comment chain, sees confidence=65 -> 1.00x here and confidence=65 -> 1.25x in paper_trading_engine.py's confidence_size_mult, assumes it's a bug (matching the pattern of every OTHER divergence in this codebase being flagged as a defect), and 'fixes' sizer.py to use the >=50 threshold — silently reintroducing the exact problem T232-DE2 fixed (every position above the ~55.8 hard-reject floor gets 1.25x with zero variation by conviction, since the floor already exceeds 50).

**`AUD232-053`** [DUPLICATE] `services/decision-engine/src/api/routes.py`:270

routes.py recomputes `_market_mult = min(multipliers.regime, multipliers.breadth, multipliers.vix)` inline for the micro-position skip check instead of reading a value returned by sizer.py, duplicating sizer.py's identical min()-composition logic (line 144) within the same service — a third in-repo copy of the same formula (the map already found two more in paper_trading_engine.py).

*Detail:* sizer.py:144 computes `market_mult = min(regime_mult, breadth_size_mult, vix_size_mult)` but this value is local to compute_position() and never included in the returned Multipliers model (models.py:69-76 has no market_mult/combined field). routes.py:270 (_decide()) then independently recomputes the exact same min() over the returned per-factor Multipliers fields, with a comment explicitly acknowledging this is 'the same fix already applied in sizer.py, missed here'. Both copies must be kept manually in sync by a human remembering to update both files.

*Failure scenario:* A future change adds a fourth market-wide dampening signal (e.g. a new liquidity_mult) to sizer.py's market_mult composition inside compute_position(), updating sizer.py's min() call to `min(regime_mult, breadth_size_mult, vix_size_mult, liquidity_mult)` but forgetting routes.py's separate inline copy at line 270 (easy to miss since it's a different file, not exported via Multipliers). The micro-position skip check in routes.py then uses a stale 3-factor market_mult while the real risk_dollar sizing in sizer.py uses the new 4-factor one — the two can now disagree on which candidates are 'dust positions', producing BUY verdicts that pass the skip check based on an outdated (and possibly too-lenient) market multiplier.


### ML Prediction

**`AUD232-054`** [DUPLICATE] `services/ml-prediction/src/features/builder.py`:84

SECTOR_ETF_MAP (builder.py) and SECTOR_MAP (meta_trainer.py) are two independently-maintained sector taxonomies that disagree on sector name coverage

*Detail:* builder.py's SECTOR_ETF_MAP includes "Financial" (no ETF-mapped counterpart in meta_trainer.py's SECTOR_MAP) while meta_trainer.py's SECTOR_MAP includes "Consumer Defensive" (absent from builder.py's SECTOR_ETF_MAP). Both maps exist to encode the same underlying stock.sector string but for different purposes (ETF lookup for RS features vs. ordinal encoding for the meta-model), and neither is derived from the other or from a shared canonical sector list.

*Failure scenario:* A stock with sector='Financial' (as opposed to 'Financial Services') gets valid sector_rs_20d/sector_rs_5d features from builder.py's ETF map, but the exact same stock is encoded as sector_code=-1 ('unknown sector') in meta_trainer.py's SECTOR_MAP — the meta-model treats it identically to a stock with no sector data at all, silently discarding real sector information that the 3-model ensemble already has available, for no reason other than the two maps not being kept in sync.

**`AUD232-055`** [DUPLICATE] `services/ml-prediction/src/training/trainer.py`:398

_load_outcome_features() hardcodes a 4th independent copy of the SHORT/SWING/LONG/GROWTH horizon-days map, duplicating the module's own _HORIZON_BY_STYLE constant

*Detail:* trainer.py already defines `_HORIZON_BY_STYLE = {"SHORT": 5, "SWING": 10, "LONG": 20, "GROWTH": 15}` at module level (line 34-39), but `_load_outcome_features()` at line 398 independently writes `{"SWING": 10, "LONG": 20, "GROWTH": 15, "SHORT": 5}.get(style.upper(), 10)` inline instead of referencing the constant already in scope in the same file. `routes.py` (line 17-21) and `meta_trainer.py`'s `_HORIZON_DAYS` (line 51-56) are two MORE independent copies of the identical mapping — four total copies of the same style→horizon-days table across the ml-prediction area. All four are currently in sync, but nothing enforces that; a future style addition (e.g. a 5th trading style) requires remembering to update all four, and history in this codebase (T237-ML-META1, T232-ML5, etc.) shows exactly this kind of drift has caused real bugs before.

*Failure scenario:* If a new style is added or an existing horizon is tuned (e.g. GROWTH moved from 15 to 12 days) and only _HORIZON_BY_STYLE or _HORIZON_DAYS is updated, _load_outcome_features() would silently keep using the stale horizon to rebuild feature vectors for outcome augmentation — meaning the 'live trading outcome' rows fed into train_model()'s final fit would be built against a different label horizon than the rest of the training set, degrading the 2x-weighted augmentation without any visible error.


### Meta-Model

**`AUD232-056`** [DUPLICATE] `services/ml-prediction/src/training/meta_trainer.py`:51

_HORIZON_DAYS dict (SHORT=5, SWING=10, LONG=20, GROWTH=15) is an independently-maintained duplicate of trainer.py's _HORIZON_BY_STYLE (same values), with no shared import — the two can silently drift.

*Detail:* meta_trainer.py L51-56: `_HORIZON_DAYS = {'SHORT':5,'SWING':10,'LONG':20,'GROWTH':15}`, used at L193 and L364. trainer.py L34-39: `_HORIZON_BY_STYLE = {'SHORT':5,'SWING':10,'LONG':20,'GROWTH':15}`, used at L1223. Currently identical (verified byte-for-byte), so no live divergence today, but nothing enforces they stay in sync — trainer.py is the module meta_trainer.py's own callers (predict_latest_ensemble_three) rely on, so trainer.py's copy is the de facto authoritative one for what 'SWING' etc. mean everywhere else in the service.

*Failure scenario:* A future change to trading-style semantics (e.g. widening SWING's horizon from 10 to 12 days) that only updates trainer.py's _HORIZON_BY_STYLE (the more visible, more-often-touched file) would leave meta_trainer.py silently building training/inference features against the old 10-day horizon — the meta model would be trained on a horizon_days value inconsistent with what the rest of the system now means by 'SWING', with no error or warning since both dicts independently default to the same fallback (10) and never cross-validate against each other.

**`AUD232-057`** [DUPLICATE] `services/ml-prediction/src/training/meta_trainer.py`:231

meta_trainer.py zero-fills NaN across all FEATURE_COLUMNS (including sparse fundamentals/sector/outcome columns) instead of leaving them as NaN for XGBoost to route natively, contradicting the codebase's own documented preprocessing rule and diverging from builder.py/trainer.py's actual practice.

*Detail:* builder.py's build_features() comments explicitly state 'XGBoost handles NaN natively' for FUNDAMENTAL_COLUMNS/SECTOR_COLUMNS/OUTCOME_COLUMNS/WEEKLY_COLUMNS, and trainer.py's predict_latest() (lines 904-909) goes out of its way to preserve NaN for exactly these columns ('filling with 0.0 breaks the learned split directions for sparse fundamentals'). meta_trainer.py independently reimplements its own feature-assembly step (train_meta_model line 210-211 builds `vec` from FEATURE_COLUMNS, then line 230-234 does `v if not NaN else 0.0` for every column) and zero-fills instead. This is a second, divergent preprocessing implementation on the same feature set rather than reusing trainer.py's NaN-preservation logic.

*Failure scenario:* A stock with no gross_margin/peg_ratio/debt_to_equity data (common for newly-listed or thinly-covered symbols) gets those columns coerced to 0.0 in the meta model's training vector. XGBoost's split-finding then treats 'true zero margin' identically to 'unknown margin' — a real, sector-typical zero (e.g. a REIT with genuinely near-zero gross_margin) becomes indistinguishable from 'no data available', silently biasing the meta model's learned splits on any column that is frequently missing, exactly the failure mode trainer.py's comment warns about for the base models.

**`AUD232-058`** [DUPLICATE] `services/ml-prediction/src/training/meta_trainer.py`:378

predict_meta() repeats the same zero-fill-instead-of-NaN preprocessing at inference time, so train/inference are at least internally consistent with each other but both diverge from the base models' NaN-preserving convention — meaning the meta model was trained and is served on a systematically different (arguably worse) missing-data treatment than its own inputs' origin pipeline uses everywhere else.

*Detail:* Lines 378-383: `float(latest.get(col, 0.0)) if not (isinstance(...) and np.isnan(...)) else 0.0`. This mirrors train_meta_model's zero-fill (so no train/inference skew *within* meta_trainer.py itself), but it means meta_trainer.py maintains its own parallel copy of 'how to turn a FEATURE_COLUMNS row into a model-ready vector' rather than importing/calling any shared helper — if builder.py or trainer.py's NaN-handling convention changes (e.g. someone switches base models to require zero-fill because of a model swap away from XGBoost), meta_trainer.py's copy will not pick up the change automatically and someone must remember to update it separately.

*Failure scenario:* If a future engineer changes builder.py's fillna behavior (e.g. switching FUNDAMENTAL_COLUMNS to a learned imputation rather than raw NaN, motivated by the base XGBoost models), nothing forces a corresponding update in meta_trainer.py — the meta model keeps zero-filling on its own, so the two now-different imputation strategies silently diverge without any test or import-level coupling to catch it.

**`AUD232-059`** [DUPLICATE] `services/ml-prediction/src/training/meta_trainer.py`:194

train_meta_model() recomputes compute_label_threshold() and build_features() per signal_outcome row using only that symbol's own historical price slice up to signal_date, independently re-deriving each base model's dead-zone/label logic rather than reusing any stored value — a second, separate execution path for feature/label construction that must be kept behaviorally identical to builder.py's own call sites (trainer.py's train_model/predict_latest) by hand.

*Detail:* This isn't wrong on its own (build_features/compute_label_threshold are imported and called correctly, and horizon_days/label_thr logic mirrors trainer.py's own patterns closely), but it means every future change to build_features' or compute_label_threshold's semantics (signature, defaults, dead-zone behavior, new required kwargs) has two independent call-sites in ml-prediction's training package that need to be updated in lockstep — trainer.py's train_model()/predict_latest()/validate_walkforward(), and meta_trainer.py's train_meta_model()/predict_meta(). There is nothing enforcing that both stay in sync (no shared wrapper, no test asserting the two produce consistent structural output).

*Failure scenario:* A future change to build_features() adds a new required kwarg (as has happened repeatedly in this file's own history — fund_snapshots, sector_df, outcome_df were all added incrementally) but is only updated in trainer.py's call sites because that's the code path someone is actively working on; meta_trainer.py's two call sites (train_meta_model line 195, predict_meta line 367) keep calling the old signature. If the new kwarg is optional with a safe default this fails silently (meta model quietly trains/predicts without the new feature); if it becomes required, meta_trainer.py breaks with a TypeError that isn't obviously connected to the actual change that caused it.


### Paper Trading

**`AUD232-060`** [DUPLICATE] `services/decision-engine/src/api/core/hard_rejects.py`:117

Decision-engine's authoritative hard-reject gate requires a stricter R:R (regime_min_rr_ratio, default 3.0) in choppy/risk_off regimes, but the fallback _should_enter() in paper_trading_engine.py has no regime-aware R:R check at all and always uses the flat 2.0 minimum.

*Detail:* hard_rejects.py L113-119: `if regime_state in ("choppy", "risk_off"): min_rr = max(min_rr, cfg.get("regime_min_rr_ratio", 3.0))`. paper_trading_engine.py's _should_enter() (L1344-1346) only checks `if rr < cfg.get("min_rr_ratio", 2.0)` with no regime branch whatsoever, and `_call_decision_engine`'s config_overrides (L2340-2350) never forwards `regime_min_rr_ratio` to DE either (DE just uses its own hardcoded default of 3.0). grep across the repo confirms `regime_min_rr_ratio` exists in exactly one file. Authoritative is decision-engine's value when decision_engine_mode='primary' (the default) — but the very purpose of the _should_enter() fallback is to be an equivalent safety net when DE is unreachable, and it is measurably looser (R:R 2.0 vs 3.0) in exactly the regimes (choppy/risk_off) where stricter standards matter most.

*Failure scenario:* Decision-engine goes down (network blip, container restart, jose-missing-401 per this repo's own recurring-issue history) during a choppy-regime trading session. _call_decision_engine returns None, gate_source falls back to _should_enter(). A candidate with R:R=2.3:1 that DE would have hard-rejected (below its regime-adjusted 3.0 floor) sails through the fallback's flat 2.0 floor and opens a real paper position — a materially weaker setup than the system's own design intends to allow during exactly the market conditions it's most cautious about.

**`AUD232-061`** [DUPLICATE] `services/decision-engine/src/api/core/sizer.py`:92

Confidence-based position sizing bands in decision-engine's sizer.py (>=80/>=62/else) use different breakpoints than paper_trading_engine.py's own confidence_size_mult bands (>=50/>=30/else) for the equivalent per-trade sizing decision — both real, both currently used, genuinely different scales by design per the module's own corrected docstring, but worth flagging since a casual reader comparing the two files would reasonably assume they should match.

*Detail:* sizer.py L98-103: confidence>=80 -> 1.25x, >=62 -> 1.00x, else -> 0.85x (rescaled in T232-DE2 to sit above DE's own hard_rejects confidence floor of ~55.8). paper_trading_engine.py L3833-3841 (_scan_for_entries' confidence_size_mult): sig_conf>=50 -> 1.25x, >=30 -> 1.0x, else -> 0.75x. Both are real, live-affecting sizing multipliers for the SAME underlying signal.confidence value on the SAME trade — DE's confidence_mult when decision_engine_mode='primary' is descriptive only in DE's own /decide response (paper_trading_engine.py does NOT consume DE's sizing plan — it always computes its own risk_dollar/shares locally per L3900-3912), so this isn't a live double-application, but a maintainer fixing 'confidence sizing feels off' in one file has no signal from the code that a second, differently-scaled implementation of the identical judgment exists in the other service.

*Failure scenario:* A future tuning pass adjusts paper_trading_engine.py's confidence bands (e.g. raising the top tier from 50 to 65 to match how min_confidence floors have crept up over past tiers), but nobody remembers sizer.py's independent copy exists in decision-engine — DE's /decide response (used for the entry go/no-go verdict and shown in any DE-facing UI/audit tooling) keeps reporting sizing multipliers on the old 50/30 mental model's paper_trading_engine.py while the real engine has moved on, producing confusing 'DE Audit' comparisons between what DE would have sized vs what the real engine actually sized, for a reason that has nothing to do with the DE/fallback agreement question that comparison exists to answer.

**`AUD232-062`** [DUPLICATE] `services/market-data/src/services/paper_trading_engine.py`:3963

Sector-cap dollar-value check in _scan_for_entries computes sector_value using _best_price() (live -> cached -> entry fallback chain) while the earlier portfolio-level sector-cap monitor in _monitor_positions (L2246-2261) recomputes an equivalent sum inline with a slightly different fallback chain (price -> current_price -> entry_price, no live_prices.get() miss distinguished from None) — same computation, two separate expressions.

*Detail:* _monitor_positions L2249-2253: `price = live_prices.get(trade.symbol) or trade.current_price or trade.entry_price; value = price * (trade.shares or 0)`. _scan_for_entries L3963-3967 instead calls the shared helper `_best_price(t, live_prices) * t.shares`. Both produce the same fallback semantics in practice (live -> current_price -> entry_price), so this is not currently a behavioral divergence, but _monitor_positions' sector-value loop is a hand-rolled reimplementation of exactly what _best_price() already exists to centralize — one of the two should call the other's helper rather than maintaining the fallback chain twice.

*Failure scenario:* If _best_price() is ever changed (e.g. to add a floor against stale prices, or to log a warning on fallback), only the _scan_for_entries/_sector_value call sites pick up the change automatically — the inline copy in _monitor_positions' sector-cap-exceeded warning silently keeps the old behavior, so a future fix to price-fallback logic looks complete (tests/manual checks against _scan_for_entries pass) while the monitor's own sector warning still uses stale semantics.


### Ranking Engine

**`AUD232-063`** [DUPLICATE] `services/decision-engine/src/api/core/scorer.py`:48

R:R (reward:risk) ratio is computed independently here (`rr = (take_profit - live_price) / max(stop_dist, 0.0001)`) and again, with a different guard clause, in ranking-engine has no analog — but the same formula is duplicated a third time inside decision-engine itself (hard_rejects.py line 113) and a fourth time in sizer.py, none of which import a shared helper.

*Detail:* scorer.py:48-49 computes `rr = (take_profit - live_price) / max(stop_dist, 0.0001)`. hard_rejects.py:113 computes the same ratio as `rr = (take_profit - live_price) / stop_dist` (no floor/epsilon guard — will raise ZeroDivisionError or produce inf where scorer.py silently clamps to a large-but-finite number via the 0.0001 floor). Both live in decision-engine/src/api/core/ with no shared helper — this is the same 'defining R:R independently at multiple call sites' anti-pattern as the ranking/decision cross-service duplication, just intra-service.

*Failure scenario:* A stock has stop_price exactly equal to live_price (stop_dist=0, a data glitch or a signal computed at a stale price). hard_rejects.check_hard_rejects() divides by zero and raises an uncaught exception, aborting the whole /decide call with a 500. scorer.compute_score(), running the same underlying formula but with the 0.0001 floor, would have silently produced a huge finite R:R and let the trade score through instead of crashing — so which code path runs first (hard_rejects always runs before scorer per aggregator.py's pipeline) determines whether the request 500s or silently passes an absurd R:R into the score, purely because the two independently-typed R:R formulas disagree on the zero-distance edge case.

**`AUD232-064`** [DUPLICATE] `services/decision-engine/src/api/core/scorer.py`:17

decision-engine hardcodes its own `_RESEARCH_SCORE` string→int mapping for the same research recommendation vocabulary (STRONG BUY/BUY/WATCH/AVOID/SELL) that ranking-engine and signal-engine also independently interpret, with no shared enum or scoring table.

*Detail:* scorer.py:17-23 defines `_RESEARCH_SCORE = {'STRONG BUY': 2, 'BUY': 1, 'WATCH': 0, 'AVOID': -1, 'SELL': -2}` used only in Layer 4 (line 187). ranking-engine's kscore.py has no equivalent, but signal-engine/src/api/routes.py independently interprets the same recommendation strings with its own `_ALIGNED_RECS = {'BUY', 'STRONG BUY', 'STRONG_BUY'}` set (routes.py:3142) for a different (divergence-detection) purpose. Neither decision-engine's numeric mapping nor signal-engine's aligned-set membership check is derived from a single shared vocabulary/scoring module — each service re-decides independently what 'STRONG BUY' is worth.

*Failure scenario:* Research engine returns 'STRONG_BUY' (underscore variant, as the T234 comment in decision-engine's own style-params section shows this app already has underscore/space inconsistency bugs elsewhere). decision-engine's `_RESEARCH_SCORE.get(rec_upper, 0)` at scorer.py:187 does `rec_upper = research_rec.upper().replace('_', ' ')` first so it normalizes to 'STRONG BUY' and scores +2 correctly — but signal-engine's `_ALIGNED_RECS` set literally includes both 'STRONG BUY' and 'STRONG_BUY' as separate defensive entries (routes.py:3142), evidence that the underscore-vs-space representation has already caused a mismatch once in this codebase. If research-engine's canonical output format ever changes again (e.g. to lowercase, or a new label), only one of these three independent recommendation-interpretation sites is likely to be updated, silently causing decision-engine's score (used for real paper trade entries) to disagree with what a user sees as 'aligned' on the signal-engine-driven UI, for the same stock at the same time.

**`AUD232-065`** [DUPLICATE] `services/market-data/src/api/routes.py`:2046

Two independent relative-strength-vs-sector-ETF implementations exist with the same formula but diverging bug-fix status. market-data's get_relative_strength() explicitly claims to be the 'Single source of truth for all signal consumers' (docstring, line 2052) and is what signal-engine's _fetch_relative_strength() calls (services/signal-engine/src/generators/signals.py:457-483). But services/ranking-engine/src/api/routes.py has its own fully independent _rs_score()/_stock_rs()/_etf_20d_return() (lines 92-154, 306-317) computing the identical rs_rank=(1+stock_ret)/(1+etf_ret), rs_score=clip(50+(rs_rank-1)*100,0,100) formula against the same _SECTOR_ETF map and ^HSI HK special-case, purely to feed K-Score's relative_strength component. ranking-engine's copy received the T234-RANK-RS-UNBOUNDED fix (rs_rank clipped to [-20,20] at routes.py:153) plus a tighter near-zero-denominator guard (1e-6 floor, line 145); market-data's copy has neither — its rs_rank at line 2117 is computed and returned completely unclipped, only guarded by a looser pre-check (abs(1+etf_ret)<0.01, line 2111) before the division.

*Failure scenario:* If a sector ETF (or ^HSI) has a 20-day return between -99% and -99.99% (etf_ret in roughly [-0.9999,-0.99]), market-data's pre-check (abs(1+etf_ret)<0.01, i.e. etf_ret < -0.99) does NOT trip for etf_ret=-0.99 exactly (abs(0.01)=0.01 is not <0.01), so rs_rank=(1+stock_ret)/0.01 can reach 100+ for even a modest stock_ret, and is returned as-is (no clip) in the JSON response. signal-engine reads this via _fetch_relative_strength and stores it verbatim into reasons['rs_rank'] (signals.py:2303), which is displayed in signal reasons/UI and used in _apply_style_signal's rs_rank<0.70 compression check (signals.py:1798-1803) — the check itself is unaffected by an unbounded high value (it only fires below 0.70), but any downstream consumer displaying rs_rank as a sane 'relative strength ratio' (e.g. a UI card, a CSV export, gate_backtest-style analysis) would show a nonsensical 3-digit ratio during a real market crash in that sector, exactly when a trader most needs credible data.

**`AUD232-066`** [DUPLICATE] `services/market-data/src/services/scheduler.py`:543

The real BUY-alert conviction gate (_is_conviction_buy, 5 layers: K-Score/Uptrend/RSI/MACD/OBV/ADX/ML + 2 disqualifiers) lives in market-data/scheduler.py, but signal-engine/src/api/routes.py:5106-5233 (gate_backtest) independently re-implements every layer's thresholds by hand (RSI 45-72 / GROWTH 50-85, K-Score<55, ML regime thresholds {bull:0.65,neutral:0.70,high_vol:0.78,bear:0.78}, soft-fail keyword set {OBV,ADX,ML,MACD}) as a parallel, manually-synced copy rather than importing/calling the canonical function (which it can't anyway, cross-service, so it was reimplemented instead of exposed via a shared endpoint or shared/common module).

*Failure scenario:* When someone tunes a threshold in the real gate (e.g. raises bull ML threshold from 0.65 to 0.68, or changes _REGIME_THRESHOLDS['unknown']), the gate_backtest replica in signal-engine silently keeps using the old hardcoded value (the replica's _REGIME_ML_THRESH dict at line 5151 is also missing an explicit 'unknown' key, relying on .get(regime, 0.70) happening to match neutral's value today — a future change to neutral's threshold without updating the fallback default would silently desync the replica for any 'unknown'-regime historical row). The backtest tool then reports misleading win-rate deltas for a gate configuration that no longer matches production, and nobody notices because there's no automated check comparing the two.

**`AUD232-067`** [DUPLICATE] `services/ranking-engine/src/scoring/kscore.py`:31

Both ranking-engine's K-Score and decision-engine's layered score independently implement a 'weighted-factor composite that redistributes weight when an input is missing' pattern, with no shared abstraction, and no cross-check that a stock's K-Score and its decision-engine verdict are using consistent underlying signals.

*Detail:* kscore.py:169-185 builds `_active_weights` from `_WEIGHTS`, deletes entries for None inputs (value/growth/relative_strength), then renormalizes by `weight / w_sum` before summing — a 'drop-and-renormalize' weighted composite. decision-engine's scorer.py implements a structurally similar but independently-authored idea in aggregator.py/scorer.py: it sums layer point contributions (price_zone, rr_quality, volume, ml_signal, research, regime, etc.) where several layers are conditionally included only `if reasons.get(...) is not None` (e.g. volume_z at scorer.py:79-89, conf_delta at 117-128, catalyst_score at 151-161) — i.e. the same 'only score what data exists, don't backfill with a proxy' philosophy that kscore.py's docstring explicitly calls out (T234-RANK-KSCORE-PROXY-MIXING) as a fix, reimplemented from scratch in decision-engine's point-additive model rather than reusing any shared weighting/aggregation utility.

*Failure scenario:* A stock lacks real fundamentals data (no value_score/growth_score) and also lacks a fresh confidence_delta / catalyst_score in signal reasons. ranking-engine's K-Score silently redistributes ~27% of its weight (value+growth) onto technical/momentum/volatility/relative_strength, potentially producing a K-Score of 78 (looks like a strong momentum-driven rank) that gets used to satisfy a portfolio's min_kscore gate in paper_trading_engine.py. Meanwhile decision-engine's scorer, evaluating the identical stock at the identical moment, only has price_zone + rr_quality + ml_signal + regime layers active (missing volume, conf_delta, catalyst) and returns a marginal total score of exactly min_score_for_regime's floor — a HOLD/SKIP verdict. Both engines are 'agreeing that data is missing' but arrive at opposite trade verdicts (ranking says buy-worthy, decision says skip) for the same underlying reason — missing inputs — because each independently decided how to handle 'missing' with no shared contract for what a partial-data score should mean across the two systems.

**`AUD232-068`** [DUPLICATE] `services/ranking-engine/src/scoring/kscore.py`:120

kscore.py's RSI-zone scoring (_technical_score, lines 93-110: 50-100 scale, asymmetric bullish zone with breakpoints at RSI 30/50/70, optimal 50-70) and signal-engine's independent RSI-zone scoring (signals.py lines 1114-1119 and 1349-1352: 0-1 scale, breakpoints at RSI 35/45/65/72, optimal 45-65 for the base pillar and 72-85 as a GROWTH-specific bonus zone) are two hand-tuned, differently-shaped mappings of the same underlying RSI value to a bullishness score, maintained completely independently with no shared config or reference to each other.

*Failure scenario:* A stock at RSI=68 scores in kscore.py's '90->100 as RSI 50->70' zone (rsi_score=99) — treated as near-maximally bullish for K-Score's Technical component (22% of composite weight) — while the exact same RSI=68 in signal-engine's momentum pillar falls in the '65<=rsi<72' bucket worth only 0.5/1.0 (half credit, on the way to being fully zeroed at 72 for overbought). A trader comparing the stock's high K-Score Technical sub-score against a comparatively lukewarm AI Signal momentum reading for the same RSI value has no way to know the discrepancy is definitional (two different scoring curves) rather than a data or timing issue — this is exactly the kind of cross-system disagreement CLAUDE.md's 'Paper Portfolio Badges' section warns traders not to over-interpret, but it isn't documented anywhere for K-Score vs AI-Signal specifically.


### Signal Engine

**`AUD232-069`** [DUPLICATE] `services/signal-engine/src/generators/signals.py`:148

_ml_service_token() (signals.py) duplicates _service_token() (routes.py) with a weaker cache-invalidation policy — it never checks expiry and will serve an indefinitely-cached token for the process lifetime, unlike the sibling implementation which explicitly refreshes 7 days before the encoded expiry.

*Detail:* signals.py's _ml_service_token() (lines 148-160): `if _ml_svc_token_cache: return _ml_svc_token_cache` — once set, this string is returned forever regardless of the embedded exp claim, with no _service_token_exp tracking at all. routes.py's _service_token() (lines 89-105) was later hardened with an explicit `_service_token_exp` global and `if _service_token_cache and time.time() < _service_token_exp - 7*86400` check, specifically so a cached token is "never used stale" (per its own docstring) — a fix that was never back-ported to the near-identical signals.py copy. Both mint 365-day tokens for the same jwt_secret with sub='signal-engine' vs sub='signal-engine' (routes.py) — functionally redundant, and now behaviorally diverged in exactly the dimension (staleness) this codebase has repeatedly been bitten by (see CLAUDE.md's jose/token incident history).

*Failure scenario:* If signal-engine's JWT secret is ever rotated without a full container restart (e.g. an in-place secrets update followed by a hot-reload path), _ml_service_token()'s indefinitely-cached old-secret token keeps being sent to ml-prediction on every /ml/predict_ensemble* call, causing 401s that fall through the existing 404-only-continues cascade logic (line 359-360 logs a warning but doesn't retry with a fresh token) — while routes.py's own service-to-service calls using _service_token() would already have refreshed. This produces an inconsistent, hard-to-diagnose partial outage where ML-fetch calls fail with 401 but other signal-engine-to-service calls succeed.


### Technical Analysis

**`AUD232-070`** [DUPLICATE] `services/ml-prediction/src/features/builder.py`:645

builder.py imports and delegates RSI/ATR to shared/common/indicators.py, but MACD (lines 645-648) and Bollinger Bands (lines 655-656) are still local, hand-rolled ewm/rolling computations rather than calling common.indicators.macd()/bollinger_bands().

*Failure scenario:* Local code: `ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean(); ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean(); macd_line = ema12 - ema26; sig = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()` and `bb_mid = c.rolling(20).mean(); bb_std = c.rolling(20).std()` duplicate common.indicators.macd()/bollinger_bands() exactly (same params) instead of reusing them. This is only a parity risk today (values currently match), but the module is imported specifically 'from common.indicators import rsi as _canon_rsi, atr as _canon_atr' showing the team already established the delegation pattern for two of six functions in this same file — MACD and Bollinger were left behind, so a future formula tweak to the shared module (e.g. changing bb_std ddof, or MACD span defaults) will not propagate here and will silently diverge from technical-analysis and any other consumer.

**`AUD232-071`** [DUPLICATE] `services/ranking-engine/src/scoring/kscore.py`:71

kscore.py's _adx_value() computes ATR locally via tr.ewm(...).mean() with no min_periods, instead of calling common.indicators.atr() (which the file already imports common.indicators for, just for rsi) — reintroducing the exact warmup-NaN bug class already fixed in the canonical version.

*Failure scenario:* Local code: `atr = tr.ewm(alpha=1/period, adjust=False).mean()` (no min_periods) inside _adx_value(), versus common.indicators.atr()'s `tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()`. Per this file's own T233-KSCORE-RSI1 comment two lines above, this exact class of bug (no min_periods -> numerically real-looking but meaningless warmup values) was already identified and fixed for RSI in this same module (and for ATR itself, T237-TA-ATR-MINPERIODS, in the canonical file) but the fix was never ported to this local ATX/ADX helper. A stock with only 5-10 bars of real history will get a fabricated ADX/DI value from ATR computed over an incomplete window, feeding directly into K-Score's technical component and potentially ranking a recent IPO/watchlist addition incorrectly.

**`AUD232-072`** [DUPLICATE] `services/research-engine/src/api/routes.py`:125

research-engine's standalone _atr() is a third independent ATR implementation (list-based, SMA-seeded Wilder smoothing) that structurally diverges from technical-analysis/core.py's pure-EWM atr() — different warmup values for the first ~2*period bars, unlike the RSI/MACD in the same file which already delegate to common.indicators.

*Detail:* research-engine/src/api/routes.py already imports canonical `sma`, `rsi`, `macd` from `common.indicators` (line 24) per the T233-ARCH-INDICATOR-DEDUP pilot, but ATR was left out of that migration. Its `_atr()` (lines 125-142) seeds with `sum(trs[:period])/period` (a plain SMA of the first `period` true-range values) then applies Wilder smoothing `atr = atr*(1-alpha) + tr*alpha` from bar `period` onward — a real, industry-standard variant, but numerically different from technical-analysis/core.py's `tr.ewm(alpha=1/period, adjust=False, min_periods=period).mean()`, which is a pure-EWM formulation seeded implicitly by pandas' ewm recursion from bar 0 (masked as NaN only pre-min_periods). The two converge asymptotically but diverge measurably in the first ~2*period bars after warmup — the same class of drift T233 quantified for RSI (~7.4pt mean difference) before deduping it.

*Failure scenario:* A research report is generated for a stock with 20-30 bars of price history (just past the 14-bar ATR minimum). research-engine's SMA-seeded _atr() and the canonical EWM-seeded atr() (used elsewhere, e.g. by market-data's candidate_event_mining.py and paper_trading_engine.py via common.indicators) can disagree meaningfully on the reported ATR value for the same stock/date, so a research report's volatility commentary and a live trading engine's ATR-based position sizing for the same symbol are computed from two different formulas with no shared source of truth — undetected because, per T233's own history, nobody has run a parity check on _atr specifically the way RSI was checked.

**`AUD232-073`** [DUPLICATE] `services/signal-engine/src/generators/signals.py`:549

signal-engine's _supertrend()/_adx() reimplement ATR via `.ewm(alpha=1/period, adjust=False).mean()` with no min_periods — missing the exact warmup fix (T237-TA-ATR-MINPERIODS) already applied to technical-analysis/core.py's atr(), which core.py's own docstring says is 'the same formula as in signal-engine's _adx helper'.

*Detail:* technical-analysis/src/indicators/core.py:94-102 atr() explicitly documents (via the T237-TA-ATR-MINPERIODS comment) that `.ewm(...).mean()` without min_periods serves a numerically real ATR from bar 0, before `period` true-range bars have accumulated, and fixes it with `min_periods=period`. signal-engine/src/generators/signals.py:549 (`_supertrend`) computes `atr_s = tr.ewm(alpha=1/period, adjust=False).mean()` and line ~591 (`_adx`) computes `atr = tr.ewm(alpha=1/period, adjust=False).mean()` — both still missing `min_periods=period`, i.e. still carrying the pre-T237 bug. core.py's own docstring cross-references this exact function ('same formula as in signal-engine's _adx helper') without noting the drift, implying the fix was made in one location and never propagated to the other despite the code explicitly acknowledging they're 'the same formula'.

*Failure scenario:* A recently-added stock with 12 bars of history hits the signal-engine pipeline. _adx()/_supertrend() compute a numerically real (but meaningless) ATR/Supertrend/ADX from bar 0 instead of NaN, so `_supertrend()` can return a real trend flag and `_adx()` a real ADX value confidently gating signal logic (bullish_trend, compression checks) on a fabricated multi-bar volatility read — the same class of false-confidence bug T237 was written to eliminate in the canonical TA service, just not ported to signal-engine's independent copy.

**`AUD232-074`** [DUPLICATE] `services/technical-analysis/src/api/routes.py`:60

get_indicators() recomputes EMA inline via raw .ewm(span=N, adjust=False) with no min_periods, instead of calling core.py's ema() — diverges from every other indicator in the same response and never gets the warmup-NaN fix.

*Detail:* core.py defines `ema(close, window)` as `close.ewm(span=window, adjust=False, min_periods=window).mean()` — consistent with sma/bollinger_bands/atr's warmup-NaN convention (explicitly called out in T237-TA-ATR-MINPERIODS as the standard this module follows). But `indicators/__init__.py` does not export `ema` at all, and routes.py's `get_indicators()` (lines 60-63) recomputes ema_12/ema_20/ema_26/ema_50 by calling `df["close"].ewm(span=N, adjust=False).mean()` directly, with no `min_periods`. This is the exact same bug class as T237-TA-ATR-MINPERIODS/TA-MACD1/T232-TA1 (all fixed elsewhere in this same file's history) but was never applied to the EMA branch, and the fix can't even be reused directly since `ema` isn't re-exported.

*Failure scenario:* A stock with only 5 bars of real history (recent IPO or new watchlist addition) requests GET /{symbol}/indicators. ema_50 (and ema_20/26) return numerically real-looking values from bar 0 onward — e.g. an EMA-50 computed off 5 data points — instead of NaN during the warmup window. The frontend chart renders a fabricated EMA-50 line for a stock that doesn't have 50 bars of history yet, indistinguishable from a real signal, exactly the failure mode T237/T232/TA-MACD1 were written to prevent for every other indicator in this same endpoint.

**`AUD232-075`** [DUPLICATE] `services/technical-analysis/src/indicators/core.py`:1

technical-analysis's core.py still contains fully independent sma/ema/rsi/macd/bollinger_bands/atr implementations and never imports shared/common/indicators.py at all — the rollout claim is backwards for this service.

*Failure scenario:* shared/common/indicators.py's own docstring states it was 'ported verbatim from services/technical-analysis/src/indicators/core.py (the service explicitly built to be canonical)' — i.e. core.py is the ORIGIN, not a consumer, of the shared module. No `from common.indicators import ...` exists anywhere under services/technical-analysis. If a future bugfix (e.g. another Wilder's-smoothing or min_periods correction) is applied only to shared/common/indicators.py, technical-analysis's RSI/MACD/ATR/Bollinger values will silently diverge from every other service that does import the shared module, since core.py has its own independent copies of all six functions.


## LOW (26)

### Calibration Loop

**`AUD232-076`** [REFACTOR] `services/signal-engine/src/api/routes.py`:89

_service_token() is duplicated verbatim between signal-engine's routes.py and market-data's scheduler.py — same JWT-minting/refresh-window logic maintained in two places with no shared implementation, despite CLAUDE.md's own stated invariant that all scheduler/service-to-service auth should use one pattern.

*Detail:* routes.py:89-105 and scheduler.py:105-124 both implement: a module-level cache + expiry timestamp, a 7-day-before-expiry refresh check, `from jose import jwt`, a 365-day-lifetime token with sub/jti/exp claims, encoded with `_settings.jwt_secret`. This is the same jose-dependent pattern that has independently broken (401s) in at least 4 different services per CLAUDE.md's own recurring-issue log (signal-engine, ml-prediction, ranking-engine, portfolio-optimizer) — all from the same root cause (jose missing from a container image). Having two independent copies of the token-minting logic means a fix to one (e.g., explicit handling for a jose ImportError, or a claims-shape change required by jwt_auth.py) has to be manually ported to the other, and nothing enforces that happens.

*Failure scenario:* A future change to shared/common/jwt_auth.py's verifier requires an additional claim (e.g. an audience check) on service tokens. The signal-engine copy of _service_token() gets updated (since that's the file being actively worked on) but the market-data scheduler.py copy is missed — scheduler-initiated calls (rankings/refresh, signals/refresh, outcomes/evaluate, calibrate_ta_weights, etc.) start failing with 401s from jwt_auth.py's now-stricter check, while signal-engine's own internal calls (e.g. _fetch_research) keep working, making the failure look market/service-specific rather than what it actually is — one of two duplicated token functions falling out of sync.

**`AUD232-077`** [REFACTOR] `services/signal-engine/src/api/routes.py`:1114

calibrate_ml_weight() independently re-derives forward returns from raw Signal+Price (its own _first_close_after/_first_close_at_or_after bisect closures) rather than reading the equivalent already-computed fields in signal_outcomes, making it the 4th independent reimplementation of the same entry/exit price lookup pattern in this one file.

*Detail:* At least four near-identical bisect-based 'find the close price on/after date X' closures exist in this single file: evaluate_signal_outcomes's _lookup_outcome_price (4852), signal_accuracy's first_close_after (780), rolling_accuracy's first_close_after (932), calibrate_ta_weights's _lookup_price (2313), and calibrate_ml_weight's _first_close_at_or_after/_first_close_after (1165/1174) — five call sites total. Each independently loads its own bulk Price query, builds its own per-stock (dates, closes) tuple-list, and re-implements the same bisect.bisect_left/bisect_right pattern with slightly different edge-case handling (some use bisect_left, some bisect_right; some require strictly-after, some on-or-after). This is the file-wide duplication the recon map already flagged, confirmed here across two more call sites (calibrate_ml_weight, calibrate_ta_weights) beyond the four the map enumerated.

*Failure scenario:* A future fix to the correct entry-date semantics (e.g. handling a market holiday edge case, or the T+1 look-ahead-bias fix already applied in evaluate_signal_outcomes and calibrate_ta_weights) needs to be manually re-applied to each of the 5+ independent closures separately. Missing even one (as already happened historically — calibrate_ta_weights's comment at line 2331 explicitly notes it had to be retrofitted to match a fix 'already applied in evaluate_signal_outcomes') leaves that one endpoint computing returns on a subtly different entry-price convention than the rest of the loop, producing calibration numbers that don't reconcile with signal_outcomes-sourced numbers for the same underlying signals.

**`AUD232-078`** [REFACTOR] `services/signal-engine/src/api/routes.py`:3507

outcomes_calibrate_apply is a single ~410-line function handling two fully independent sweep-and-apply pipelines (BUY and SELL) with heavy copy-paste structure between them, a strong split candidate.

*Detail:* Lines 3506-3917 implement the BUY threshold sweep (3570-3736) and then a near-mirror-image SELL threshold sweep (3737-3910) — same walk-forward split logic, same skip/apply/gate-failure bookkeeping, same _record_tune_history call shape, repeated with SELL-specific sign flips (fused_prob <= threshold instead of >=, -pct_return instead of pct_return). The two blocks share no helper functions (each defines its own local _stats_at/_sell_stats_at closure) despite being structurally identical except for the comparison direction and profit sign. Combined with routes.py being ~5540 lines total (the map already flags this), this endpoint alone is a clear candidate to extract a shared `_sweep_and_apply_threshold(outcomes, direction, ...)` helper.

*Failure scenario:* Not a live bug today, but a real risk: T232-OC3-FOLLOWUP's comment documents a PRIOR live incident where the SELL-side gate logic diverged from the BUY-side gate logic ('a run applied SELL:GROWTH 0.35->0.30 with a validated ev_lift of -0.01%, because the 5pt shift satisfied not small while the lift check was skipped') — precisely the failure mode expected when the same logic is duplicated with hand-copied sign flips instead of shared. Any future gate-logic fix applied only to the BUY block (since that's usually what's being read/modified first) risks leaving the SELL block on the old, already-proven-buggy gating a second time.


### Decision Engine

**`AUD232-079`** [REFACTOR] `services/decision-engine/src/api/core/aggregator.py`:76

_default_game_plan() widens stops using ATR (2.5x for GROWTH, 2.0x otherwise) when atr_14 is available, but build_game_plan()'s signal-reasons path (used whenever signal_data has entry2/stop/take_profit already populated) never applies this ATR widening even though it also extracts atr_14 from the same reasons dict and passes it to _default_game_plan only when the signal-reasons branch does NOT fully populate — meaning ATR-aware stops silently apply inconsistently depending on which upstream fields happen to be present.

*Detail:* build_game_plan() (aggregator.py:195-212): if signal_data.reasons contains entry2, stop, and take_profit (all three), it returns those values verbatim (lines 208-211) — no ATR adjustment, even though atr_14 was already extracted from the same reasons dict one line earlier (line 200) and then simply discarded in this branch. Only when the signal-reasons values are incomplete does the function fall through to _default_game_plan(live_price, style, atr_14), which does apply ATR-aware stop widening. Since signal-engine populates entry2/stop/take_profit for essentially every computed signal (this is the common case, not the fallback), decision-engine's ATR-aware stop logic is effectively dead in the common path and only exercised for edge cases (missing signal fields) — the opposite of what a reader would expect from a function that goes out of its way to extract atr_14 up front.

*Failure scenario:* A GROWTH-style stock has a signal with entry2/stop/take_profit already computed by signal-engine using its own (non-ATR-aware, or differently-ATR-aware) stop formula, and also has atr_14=3.50 in reasons indicating high volatility warranting a wider stop. build_game_plan() takes the signal-engine-provided stop verbatim, silently ignoring the atr_14 value it just read — a human reading aggregator.py's code and seeing atr_14 extracted at line 200 would reasonably assume it feeds into every game plan this function returns, when it actually only affects game plans built from style defaults, not the (much more common) signal-derived ones.

**`AUD232-080`** [REFACTOR] `services/decision-engine/src/api/routes.py`:363

The endpoint docstring claims 'the live (paper) trading path never calls this endpoint for sizing... do not assume position matches what the trading engine would actually do' — true for the `position`/PositionPlan field, but misleading by omission: the `score` and `verdict` fields from this same endpoint ARE directly consequential for real trades whenever decision_engine_mode is 'primary' (the default), both for the entry gate itself and for score_size_mult in real position sizing.

*Detail:* paper_trading_engine.py:3738 defaults `de_mode = cfg.get('decision_engine_mode', 'primary')`. When primary (default) and DE is reachable, `_call_decision_engine()` (2307-2367) sets `should_enter = (verdict == 'BUY')` and `score` directly from DE's JSON response — both flow straight into `_scan_for_entries()`'s real entry decision (3768-3784) and, when gate_source=='de', into `score_size_mult` (3891-3897), which scales real position size by up to 1.25x/0.75x based on DE's score margin over min_entry_score. The routes.py docstring and models.py's PositionPlan docstring both emphasize only the sizing field is discarded, without similarly flagging that score/verdict are NOT discarded — a reader skimming these docstrings (as this map-building pass initially did, per the recon map's characterization of DE as producing only an 'illustrative preview') could reasonably conclude the entire /decide response is advisory-only, which is false for score/verdict under the default config.

*Failure scenario:* A developer, trusting the 'illustrative preview only, live engine ignores this' framing repeated across routes.py/models.py/sizer.py docstrings, makes a scorer.py change (e.g. loosening Layer 1 price-zone scoring) assuming it only affects a cosmetic preview endpoint used for manual /decide calls or the explain endpoint — not realizing that with decision_engine_mode defaulting to 'primary', this change immediately alters which real paper trades open and at what size in production, with no additional review flagged as necessary given the docstrings' reassurance.


### ML Prediction

**`AUD232-081`** [REFACTOR] `services/ml-prediction/src/training/meta_trainer.py`:267

train_meta_model()'s reported AUC and predict_meta()'s auc<0.55 gate are both computed/read from a single chronological 80/20 split with no cross-validation, unlike every per-symbol model in trainer.py

*Detail:* trainer.py's per-symbol training uses 5-fold TimeSeriesSplit CV plus a held-out test/calibration split (four-way split with embargo gaps) to report cv_auc_mean, oos metrics, and overfit_gap. meta_trainer.py's train_meta_model(), by contrast, does one 80/20 chronological split (line 245-247) and reports a single `auc` value with no CV, no embargo/purge gap between train and validation (despite the same label being derived from forward-looking is_correct outcomes across potentially overlapping horizons/symbols), and no overfitting check.

*Failure scenario:* Because there's no purge gap and no CV, the reported meta-model auc could be optimistically biased in a way the rest of the pipeline's design (T232-ML4's embargo logic, T232-ML2's honest-holdout fix) was specifically built to avoid elsewhere — predict_meta()'s single auc<0.55 gate (line 325) is the only quality safeguard for whether the meta-model contributes to live predictions, and it's evaluated on a less rigorous methodology than every other model in the ensemble it joins.

**`AUD232-082`** [REFACTOR] `services/ml-prediction/src/training/trainer.py`:500

Outcome-augmentation dedup (T232-ML3 fix) and the whole _load_outcome_features()/train_model() augmentation pipeline is O(symbols x styles) redundant work — each call reloads and rebuilds full-history features

*Detail:* _load_outcome_features() (trainer.py:326-419) is called once per (symbol, style) inside train_model(), and independently reloads up to 400 days of Price rows and re-runs build_features() over the full history just to extract a handful of specific-date feature rows for outcome augmentation — duplicating almost all of the work train_model() itself just did seconds earlier for the exact same symbol (fetching prices, macro, building features). For a train_all_ensemble_three loop over hundreds of symbols x 4 styles x 3 models, this multiplies DB round-trips and full-history feature rebuilds substantially beyond what's structurally necessary.

*Failure scenario:* Not a correctness bug, but a performance/cost one: doubling (at least) the DB and CPU work per train_model() call scales linearly with the size of the active-symbol universe and the number of styles/models trained, making POST /ml/tune_all and train_all_ensemble_three background jobs run measurably longer than necessary — worth flagging before the symbol universe or style count grows further.

**`AUD232-083`** [REFACTOR] `services/ml-prediction/src/training/trainer.py`:1015

predict_latest_ensemble_three() is a 180-line god function combining model loading, meta-model blending, agreement-nudge logic, and threshold weighting in one block

*Detail:* The function handles: fetching 3 base models with individual try/except fallbacks, filtering oos_suppressed models, single-model early return, weighted blend renormalization, meta-model fetch/blend (with its own nested try/except and a stale-comment-fixed sector/market_cap lookup per T237-ML-META2), unanimous/split agreement nudging gated on a hardcoded 0.57 AUC threshold, and final threshold/AUC aggregation — all inline with no helper extraction. This mirrors the kind of god-function pattern already flagged for other services in this codebase's history (e.g. decision-engine's scorer.py).

*Failure scenario:* Not an active bug, but the function's complexity (many nested conditionals, several independently-evolving magic numbers: 0.30/0.45/0.25 weights, 0.15 meta blend, 0.57 nudge-reliability gate, 0.05 nudge magnitude) makes it easy for a future edit (e.g. adjusting ensemble weights for a new model) to accidentally affect the agreement-nudge or meta-blend logic without noticing, since none of these concerns are separated into testable units.

**`AUD232-084`** [REFACTOR] `services/ml-prediction/src/training/trainer.py`:213

_artifact_path()'s legacy {symbol}.joblib fallback silently and permanently 'sticks' once any new-style artifact is written, with no migration/cleanup path

*Detail:* `return legacy_path if (legacy_path.exists() and not new_path.exists()) else new_path` means: once a symbol is retrained under the new SWING naming, its legacy {symbol}.joblib file is simply abandoned on disk (never deleted), forever. There's no cleanup job or migration script referenced anywhere in ml-prediction to remove these orphaned legacy artifacts once every symbol has a new-style path, so /data/models/xgboost/ accumulates permanently-dead duplicate files for every historically-existing symbol.

*Failure scenario:* Not a correctness bug (predictions/training both resolve to the correct path), but disk usage in the models directory grows unboundedly with dead legacy artifacts that can never be cleaned up automatically — the same class of issue CLAUDE.md documents for EC2 disk filling from dangling Docker images, just for model artifacts instead of image layers.

**`AUD232-085`** [REFACTOR] `services/ml-prediction/src/training/trainer.py`:61

_load_prices() raises ValueError('Unknown symbol') without distinguishing 'symbol not in DB' from 'symbol has zero price rows', and callers vary in how they surface this

*Detail:* `_load_prices()` raises the same ValueError message pattern (`f"Unknown symbol: {symbol}"` vs `f"No prices for {symbol} — run ingestion first"`) for two structurally different failure modes: the stock doesn't exist in the `stocks` table at all, vs. the stock exists but has zero D1 price rows in the lookback window. train_model() catches this generically and returns `{"skipped": True, "reason": str(exc)}` either way, which is reasonable for a background batch job, but validate_walkforward() (line 1213-1216) does the same collapse, losing the distinction that would help diagnose e.g. a newly-added symbol still awaiting its first ingestion run vs. a genuinely mistyped/delisted ticker.

*Failure scenario:* Not a live bug, but when triaging why a batch of new HK symbols all show up as 'skipped' in a tune_all/train_all run, the flat string reason field doesn't let an operator immediately tell apart 'these are typos' from 'ingestion hasn't run yet for these' without manually re-deriving which branch fired from the message text.


### Meta-Model

**`AUD232-086`** [DEAD CODE] `services/ml-prediction/src/training/meta_trainer.py`:27

META_SCALER_PATH is declared but never written to or read from anywhere in the codebase — the scaler is actually persisted inside the main joblib bundle at META_MODEL_PATH.

*Detail:* grep across services/ finds exactly one reference to META_SCALER_PATH: its own declaration at L27. train_meta_model() stores the scaler inside `bundle['scaler']` (L276) alongside the model, written to META_MODEL_PATH (L272-294). predict_meta() loads `bundle['scaler']` from the same META_MODEL_PATH (L329), never touching META_SCALER_PATH.

*Failure scenario:* Not a runtime bug (dead code, not exercised), but a maintenance trap: a future engineer could reasonably assume META_SCALER_PATH is the real scaler artifact location (e.g. write a cleanup/migration script that manages files at that path, or add monitoring/disk-usage checks against it) and find it permanently empty/missing, wasting investigation time chasing a phantom file.

**`AUD232-087`** [REFACTOR] `services/ml-prediction/src/training/meta_trainer.py`:195

train_meta_model() and predict_meta() each contain a near-identical, independently copy-pasted block (price load -> macro fetch -> compute_label_threshold -> build_features(inference_mode=True) -> pull last row) instead of sharing one helper — inflates the file and creates two places that must be kept in sync for any future feature-building change.

*Detail:* Training path: L169-211 (load prices via Price/Stock/TimeFrame query, build df, fetch_macro_features, per-row compute_label_threshold + build_features(inference_mode=True), take X_feat.iloc[-1]). Inference path: L332-383 does the same sequence (SessionLocal price query, df construction, fetch_macro_features, compute_label_threshold, build_features(inference_mode=True), X_feat.iloc[-1]) with only cosmetic differences (400d lookback vs 5yr, single symbol vs loop, minor null-handling variance between L210 and L378-383). Any future change to how the feature vector is assembled/ordered (e.g. adding the missing bundle['feature_columns'] usage from Finding 1) needs to be applied in both blocks by hand.

*Failure scenario:* A developer fixes the feature-column mismatch bug (Finding 1) only in predict_meta() because that's the code path they were debugging, and forgets train_meta_model() has its own separate build_features() call site at L195-211 that also needs the same defensive handling (e.g. if a future change makes non_const selection order-sensitive) — leaving one of the two functions silently unpatched.

**`AUD232-088`** [DEAD CODE] `services/ml-prediction/src/training/meta_trainer.py`:279

bundle['feature_columns'] is persisted at train time but never read or validated against at predict time, making it dead defensive data rather than an actual drift guard — unlike trainer.py's base-model bundle, which does use its saved feature_columns to reindex/align inference features.

*Detail:* Compare to trainer.py's predict_latest() (lines 840, 907): `saved_cols = bundle.get('feature_columns', ...)` is actively used via `X_aligned = X.reindex(columns=saved_cols, fill_value=np.nan)` — this is a real safeguard that realigns inference-time features to whatever columns the specific loaded model was trained on, tolerating drift. meta_trainer.py's predict_meta() loads the exact same kind of field (`bundle['feature_columns']`, set at meta_trainer.py:278) but never references it anywhere in predict_meta() — it just re-derives `FEATURE_COLUMNS` fresh from the current builder.py import and assumes the vector this produces still lines up positionally with the persisted `non_const` mask.

*Failure scenario:* Because feature_columns is stored but unused, if builder.py's FEATURE_COLUMNS changes there is no reindex/realignment step available to fall back on for the meta model the way there is for the base per-symbol models — the meta model has strictly weaker protection against upstream feature-set drift than the base models sitting right next to it in the same file, despite persisting exactly the data that would be needed to build that protection.

**`AUD232-089`** [REFACTOR] `services/ml-prediction/src/training/trainer.py`:1095

predict_latest_ensemble_three() passes xgb['confidence']/100.0 as the meta model's 'confidence' feature, and the map's cited fix comment (T237-ML-META1) claims this now matches training-time normalization — verified consistent, but the consistency is fragile and undocumented at the call site itself.

*Detail:* trainer.py L1095: `_confidence = float(xgb.get('confidence', 0.0)) / 100.0` (dividing the XGBoost model's 0-100 confidence score down to 0-1), passed into `_predict_meta(confidence=_confidence, ...)` (L1108). Inside predict_meta() (meta_trainer.py L389): `vec.append(float(confidence))` — no further division, so the 0-1 value flows through unchanged. Training side (meta_trainer.py L220): `vec.append(float(row.confidence) / 100.0 ...)` where `row.confidence` is `SignalOutcome.confidence` (verified 0-100 in shared/db/models.py L501: '# 0-100'). So both sides do exactly one /100.0 conversion from a 0-100 raw value to 0-1 — they agree. However, this agreement depends on two separate, un-typed float parameters named identically ('confidence') in two different files with two different unit conventions upstream (xgb's raw 'confidence' field is 0-100; SignalOutcome.confidence is 0-100) each doing their own single division — there is no shared constant or docstring co-located at either call site making the '0-1 expected' contract explicit, only a comment referencing the other file.

*Failure scenario:* A future change to how XGBoost's own 'confidence' field is computed/scaled (e.g. someone redefines predict_latest()'s confidence to already be 0-1 instead of 0-100, a plausible normalization someone might do without realizing meta_trainer.py depends on the old convention) would silently break only the meta-model's confidence feature — dividing an already-0-1 value by 100 again, producing near-zero confidence values fed into predict_meta() with no error, degrading (not crashing) the meta ensemble member's quality in a way that would be very hard to notice without explicitly auditing this cross-file unit contract.


### Paper Trading

**`AUD232-090`** [REFACTOR] `services/market-data/src/services/paper_trading_engine.py`:4322

paper_trading_step() builds cfg without merging _STYLE_OVERRIDES, unlike _monitor_positions() and _scan_for_entries() which both include that layer — currently latent (no style override sets a key paper_trading_step reads) but a silent landmine for the next style-specific default added to _STYLE_OVERRIDES.

*Detail:* L1600 and L2707 both build cfg as `{**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {}), **portfolio.config}`. L4322 (paper_trading_step) instead does `cfg = {**_DEFAULT_CONFIG, **portfolio.config}` — missing the middle _STYLE_OVERRIDES layer entirely. This cfg is used for `enable_regime_filter` (gates whether _fetch_hk_market_regime/_fetch_market_regime is even called for that market) and `enforce_market_hours` (gates whether _scan_for_entries runs at all), then the resulting live_regime dict is persisted into `portfolio.config` for UI display. Neither key is currently set in _STYLE_OVERRIDES or _HK_MARKET_OVERRIDES, so no divergence exists today, but this is the third of three near-identical cfg-construction call sites and the only one that silently drifted from the other two's pattern — exactly the kind of independent-copy drift this codebase's CLAUDE.md repeatedly documents (regime classifiers, sizing formulas) as a recurring failure mode once one of the N copies is updated and the others aren't.

*Failure scenario:* A future change adds e.g. `"enforce_market_hours": False` to _STYLE_OVERRIDES['SHORT'] (to allow SHORT-horizon portfolios to trade extended hours). _monitor_positions and _scan_for_entries would pick it up correctly via their shared cfg-construction pattern, but paper_trading_step's market-hours gate check at L4347 would still see the _DEFAULT_CONFIG value (True) and skip calling _scan_for_entries entirely for that portfolio outside 9:30-16:00 — the style override would appear to have zero effect from the outside, and debugging would be misdirected toward _scan_for_entries' own logic (which is fine) rather than the real culprit three call sites away.

**`AUD232-091`** [REFACTOR] `services/market-data/src/services/paper_trading_engine.py`:400

_load_tuned_params() and _apply_tuned_hold_days() both read and json.loads() the same _TRADE_PARAMS_FILE independently on every paper_trading_step() cycle instead of loading it once and sharing the parsed dict, and both silently swallow all exceptions (bare or broad except) with no visibility if the file becomes malformed.

*Detail:* Both functions are called back-to-back in paper_trading_step() (L4271-4272) every single cycle (every 5-10 min during market hours). Each independently checks `_TRADE_PARAMS_FILE.exists()`, reads the file, and calls `json.loads()` — doubling the I/O and JSON parsing for no reason since they operate on the exact same file and are always called together. _load_tuned_params catches `Exception` and logs a warning; _apply_tuned_hold_days catches bare `Exception: pass` with zero logging at all, per the map's flagged silent-failure pattern.

*Failure scenario:* If /data/models/trade_params.json becomes truncated or corrupted mid-write by the tuning job (POST /paper-portfolio/tune-params) at the exact moment a scheduler tick fires, _load_tuned_params logs a warning (visible in logs) but _apply_tuned_hold_days silently does nothing — max_hold_days silently stops updating from tuned values with zero log signal, and because the two functions parse the file independently, an operator grepping logs for 'tuned_params_load_failed' would see the warning from one function and reasonably assume both failed the same way, when actually only the hold-days half is currently affected and stays on stale values indefinitely with no way to detect it from logs alone.


### Position Sizing

**`AUD232-092`** [REFACTOR] `services/market-data/src/backtest/multi_tranche_engine.py`:81

Docstring claims weighted_avg_cost matches paper_trading_engine.py's scale-in blend formula at lines 3211-3214, but the real blend is now at lines 3402-3406 — the citation has drifted and was never re-verified against the current formula.

*Detail:* multi_tranche_engine.py:78-87 weighted_avg_cost is `sum(t.shares * t.fill_price for t in self.tranches) / total`. The real scale-in blend (paper_trading_engine.py:3402-3405) is `(_si_old_shares * entry_price + _si_add_shares * _si_fill_price) / _si_new_shares` — algebraically equivalent for 2 tranches so the claim currently holds, but the stale line-number reference (off by ~190 lines) shows this cross-file 'matches exactly' comment already drifted once without being updated, and there's no shared helper or test tying the two formulas together.

*Failure scenario:* A future engineer trusts the docstring's line citation (3211-3214) to locate and compare the real formula, edits the wrong region of paper_trading_engine.py (which by then contains unrelated code), and ships a change that silently breaks parity between the backtest cost-basis simulator and the real engine's scale-in accounting — with no test enforcing the two stay in sync, this would go unnoticed until a Phase 2/3 backtest report diverges from live production numbers for reasons nobody can trace back to this drift.

**`AUD232-093`** [REFACTOR] `services/market-data/src/backtest/position_scaling_gate.py`:244

walk_forward_train's fold_size is derived once from n//(n_splits+1) then floored to min_samples_per_split without re-deriving n_splits from n, so for realistic near-term dataset sizes most of the nominal 5 folds get silently skipped rather than producing a usable walk-forward report.

*Detail:* walk_forward_train (lines 221-290): the skip-check at line 257 does correctly prevent bad folds from being reported as valid (not a live correctness bug), but the design silently produces mostly-empty fold sets for any n in the low hundreds or below, which is exactly the realistic near-term regime for this feature per the module's own ~12-real-events caveat.

*Failure scenario:* With n=32 events (a plausible early size once candidate-event-mining ships a modest first batch) and default min_samples_per_split=15: fold_size=max(32//6,15)=15, producing only 1-2 valid folds out of the nominal 5 (folds 2-4 have train_end/val_start already beyond n and get skipped via the line-257 guard) — someone reviewing walk_forward_report()'s output could mistake 'mostly skipped folds' for a data problem specific to their run rather than a structural consequence of fixed n_splits=5 not scaling down for small n.

**`AUD232-094`** [REFACTOR] `services/market-data/src/services/paper_trading_engine.py`:3966

Sector-concentration matching expression is needlessly convoluted and duplicated twice — `(st.sector is None) == (_sector is None) and (st.sector == _sector or _sector is None and st.sector is None)` reduces to plain `st.sector == _sector`, since Python's `==` already handles None==None correctly.

*Detail:* Since `None == None` is True and any string == None is False in Python, the whole boolean expression is logically equivalent to `st.sector == _sector` in every case; the extra is-None comparisons add no additional matching behavior, just redundant complexity duplicated in two adjacent blocks.

*Failure scenario:* The identical complex expression appears independently at lines 3963-3967 (sector_value sum) and 3975-3978 (sector_count sum); a future engineer 'simplifying' one occurrence without touching the other introduces an actual behavioral difference between the sector-value cap and the sector-count cap where none currently exists, since both are meant to use the exact same matching rule.


### Ranking Engine

**`AUD232-095`** [REFACTOR] `services/ranking-engine/src/api/routes.py`:574

rank_symbol() (the single-symbol live endpoint, GET /rankings/{symbol}) recomputes _sector_relative_scores() over the ENTIRE active stock universe (line 575: full table scan + sector percentile computation across all sectors) just to extract one symbol's value from the result dict (line 578). This endpoint is called once per symbol by signal-engine's _fetch_kscore() (signals.py:1356-1365) during _bulk_persist's per-symbol loop (routes.py:440-444) — meaning a full-market signal refresh triggers a full universe rescan for every single symbol, an O(N^2)-shaped cost pattern (N calls x O(N) work each) where the leaderboard endpoint already does the equivalent O(N) work exactly once per refresh via _persist_rankings's single _sector_relative_scores() call (line 810).

*Failure scenario:* During a full-market signal-engine bulk refresh (POST /signals/refresh, hundreds of US+HK symbols), each symbol's _fetch_kscore() call causes ranking-engine to reload every active Stock row, refetch bulk fundamentals, and recompute sector-relative percentile ranks across all ~14 sectors for the WHOLE universe — repeated once per symbol. For a 500-stock universe this is roughly 500x more sector-percentile computation than necessary, directly inflating bulk refresh latency and increasing the chance of the refresh running long enough to overlap the next scheduled cycle or hit downstream timeouts (the httpx.Client(timeout=8) in _fetch_kscore, signals.py:1359, could start failing under this load, silently degrading kscore to None for a growing fraction of symbols as the batch progresses).

**`AUD232-096`** [REFACTOR] `services/ranking-engine/src/api/routes.py`:685

The vol_ratio computation inlined into leaderboard() (lines 653-677: raw SQL query + manual per-stock list aggregation + avg5/avg20 ratio) duplicates a volume-ratio-over-average concept that likely overlaps with volume_z scoring already computed elsewhere in signal-engine (reasons['volume_z'], referenced at decision-engine/scorer.py:79-89 and signals.py:1085) and with RVOL used by the Screener page (per CLAUDE.md's 'Recurring Issue: Slow Frontend Builds' section, which explicitly names 'Min RVOL'/screener RVOL as a related feature). This 25-line block is inlined directly into the leaderboard() endpoint function rather than factored into scoring/ or a shared indicators helper, making leaderboard() harder to read and the vol_ratio metric's relationship to volume_z/RVOL non-obvious without cross-referencing three files.

*Failure scenario:* A future maintainer adding a new volume-based ranking filter is likely to write a fourth slightly-different average-volume-ratio calculation (different window lengths, different handling of zero-volume days) rather than discovering and reusing this one, because it's buried inline in leaderboard() rather than named/exported as a reusable scoring primitive alongside compute_kscore — the same drift pattern already observed between kscore.py's RSI zones and signal-engine's RSI zones, and between the two RS implementations, would repeat for volume ratios.


### Signal Engine

**`AUD232-097`** [REFACTOR] `services/signal-engine/src/api/routes.py`:1560

Three independent, differently-gated mechanisms (calibrate_ml_weight's global cap, tune_style_profiles' per-style cap, and the hardcoded _STYLE_PROFILES default) all write overlapping ml_weight_cap knobs with only an informal precedence comment tying them together — no single endpoint or dashboard shows the effective value across all three sources at once outside of tune_status.

*Detail:* _apply_style_signal (signals.py line 1559-1561) resolves eff_cap via: per-style Redis (stockai:style_tune:{STYLE}:ml_weight_cap, written by tune_style_profiles) > global file/Redis override (stockai:ml_weight_cap, written by calibrate_ml_weight) > hardcoded _STYLE_PROFILES default. Each of these three is calibrated by a separate endpoint, on a separate schedule, with separate train/validation gating logic (calibrate_ml_weight validates against a single global neutral baseline of 0.5; tune_style_profiles validates per-style against an uncapped baseline) — they can legitimately disagree about the same style's optimal ml_weight_cap and there is no automatic reconciliation, only precedence-at-read-time. tune_status (line 4311) does surface all the values together for inspection, but nothing prevents e.g. calibrate_ml_weight applying a new global cap that's immediately shadowed for SWING/LONG (which almost always have a per-style tuned value) while silently taking effect only for whichever style lacks one.

*Failure scenario:* An operator runs calibrate_ml_weight expecting it to tighten ML influence across all styles after observing SWING overconfidence; it validates and applies a new global cap. If SWING already has a per-style stockai:style_tune:SWING:ml_weight_cap key from a previous tune_style_profiles run, the new global change has zero effect on SWING signals (silently shadowed) while still affecting any style without a per-style override — the operator has no direct signal from the calibrate_ml_weight response that their change was overridden for the style they most cared about.

**`AUD232-098`** [REFACTOR] `services/signal-engine/src/generators/signals.py`:253

set_ta_weights() reassigns _ta_weights with a raw, unmigrated dict — it is the only writer of the in-process global that skips the volume_surge→volume_z migration _load_ta_weights() otherwise always applies, making it an easy trap for any future renamed reasons-dict key, not just the current one.

*Detail:* _load_ta_weights() (lines 216-241) is careful to run `_apply_migration()` on every load path (Redis hit, file hit, and even the bare-defaults fallback via `_apply_migration`'s dict-merge). set_ta_weights() (lines 253-265), added specifically to fix the T232-SIG6 staleness bug (so a running process picks up calibration results immediately), bypasses that migration entirely and does a bare `dict(new_weights)` reassignment. This means the single in-process code path that's supposed to keep _ta_weights authoritative and current is also the one path that can silently introduce orphaned keys the next time a reasons-dict field is renamed (history: volume_surge→volume_z already happened once).

*Failure scenario:* If a future contributor renames another reasons-dict field (following the same pattern as volume_surge→volume_z) and adds the rename to _apply_migration() thinking that covers all load paths, calibrate_ta_weights()'s direct call to set_ta_weights() (routes.py line 2392) will continue to bypass the migration exactly as it does today for volume_surge — reintroducing the same class of silent weight-loss bug with each future rename.


### Technical Analysis

**`AUD232-099`** [REFACTOR] `services/technical-analysis/src/api/routes.py`:134

get_patterns_bulk() inlines a copy-pasted duplicate of _load_prices()'s query-and-DataFrame-building logic (lines 121-143) instead of reusing the helper, differing only in swallowing 404s via try/except instead of raising them.

*Detail:* _load_prices() (lines 19-40) builds the exact same SELECT (Price.stock_id, TimeFrame, ts >= since, order_by ts) and the exact same 6-column DataFrame construction (ts/open/high/low/close/volume list comprehensions) as get_patterns_bulk()'s inline block (lines 119-143), just without stock lookup (already have `stock` from the outer loop) and with `continue` instead of HTTPException on empty rows. Any future change to the row-to-DataFrame shape (e.g. adding an adjusted-close column, per _adj_close usage seen elsewhere in the codebase) needs to be made in two places in the same file to stay consistent.

*Failure scenario:* A future change adds a new column to the price DataFrame (e.g. adjusted close, already a pattern used in signal-engine via _adj_close) by editing _load_prices() only, since that's the 'obvious' shared helper name — get_patterns_bulk()'s inlined copy silently keeps building the old 6-column shape, so /ta/patterns/bulk's DataFrame passed to detect_patterns() lacks the new column while /{symbol}/patterns's (which does call _load_prices) has it, causing the two endpoints to compute patterns from subtly different inputs for the same symbol with no error raised.

**`AUD232-100`** [REFACTOR] `services/technical-analysis/src/patterns/recognizer.py`:40

detect_head_and_shoulders() and detect_double_top_bottom() each independently call _find_pivots() on the identical full-length high/low series with the same order=5 — the same O(n) pivot scan is run twice per detect_patterns() call.

*Detail:* Line 40: `highs_idx, _ = _find_pivots(df["high"], order=5)` inside detect_head_and_shoulders(). Lines 92-93: `_, lows_idx = _find_pivots(low, order=5)` and `highs_idx, _ = _find_pivots(high, order=5)` inside detect_double_top_bottom() — `high`/`low` there are just `df["high"].astype(float)`/`df["low"].astype(float)`, i.e. the same series (modulo dtype cast) as df["high"] passed with the same order=5 in the H&S detector. detect_patterns() (line 245+) calls both detectors back-to-back on the same df, so the highs pivot scan runs twice with identical inputs and identical output. _find_pivots itself is an O(n) Python for-loop (not vectorized), so this is real, non-trivial duplicated work, not a cheap no-op.

*Failure scenario:* GET /ta/patterns/bulk iterates every active stock in a market (potentially hundreds) and calls detect_patterns() per stock, each call redundantly re-scanning the same high-series pivots twice via separate Python loops — doubling the pivot-detection cost of the bulk endpoint for no behavioral benefit, worsening the 6-hour cache's own build latency as the stock universe grows.

**`AUD232-101`** [DEAD CODE] `services/technical-analysis/tests/test_indicators.py`:4

test_vwap_finite() imports and calls an undefined `vwap` from core.py — core.py has no vwap function and the test file has no local definition or import of one — this test currently fails with NameError/ImportError, not just a stale reference.

*Detail:* Line 4: `from src.indicators.core import bollinger_bands, macd, rsi, sma, vwap` — core.py (verified in full) defines only sma, ema, rsi, macd, cog, bollinger_bands, atr, supertrend, fibonacci_retracement; there is no vwap function anywhere in the module. Consistent with CLAUDE.md's TA-D1 note that vwap() was deleted from technical-analysis. The import itself fails at collection time (ImportError: cannot import name 'vwap'), which means every test in this file fails/errors, not just test_vwap_finite — pytest would report collection errors for test_sma_window, test_rsi_in_range, test_macd_columns, test_bollinger_bands_order, and test_patterns_run too, since they're all in the same module and the bad import happens at module load.

*Failure scenario:* Anyone running `pytest services/technical-analysis/tests/` today gets a hard collection error for the entire file (ImportError on line 4) — not one failing test but zero tests executing, silently masking whatever real coverage test_sma_window/test_rsi_in_range/test_macd_columns/test_bollinger_bands_order/test_patterns_run were providing. A future regression in sma/rsi/macd/bollinger_bands or detect_patterns would go completely undetected by this test file until someone notices the whole suite errors out at collection, not just fails on one test.


---

## Notes on confidence

Every finding above went through an independent adversarial verification pass — a second agent, with no visibility into the first agent's reasoning, re-read the actual current source and either confirmed the specific lines/mechanism cited or refuted the claim outright. Findings that were refuted were dropped entirely and do not appear here. Two verification rounds hit the org's monthly spend limit mid-run; both were resumed from cache once the limit reset, so every finding shown here did complete a real independent verification pass — none are unverified defaults.

Severity was assigned heuristically post-hoc (correctness bugs touching money/sizing/confidence/positions -> critical or high; cross-system duplicate-logic on the entry/sizing decision path -> high; everything else scaled down) rather than by the auditing agents themselves — treat the severity labels as a starting triage order, not a guarantee, when picking what to fix first.