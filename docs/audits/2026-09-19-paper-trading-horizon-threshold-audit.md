# Paper Trading Engine: Horizon Thresholds and Mechanism Audit

**Review dates:** September 18–19, 2026, America/Los_Angeles; report completed September 19.  
**Production data snapshot:** September 19, 2026, 06:39:37 UTC / September 18, 23:39:37 PDT.  
**Reviewed source:** checkout `7fb03da`; the running market-data engine matches the local file, SHA-256 `688c238414309b32f67d78a0b15da136c3c4b74f03a2c3a05e0ff21b4302b320`.  
**Scope:** equity paper-trading entries, position management, horizon settings, calibration, and experiment design. Read-only production SQL/cache/file inspection; local source execution and targeted tests. No configuration changes, calibrator runs, deployments, orders, or edits to `CLAUDE.md`.

## 1. Recommendation

**There is not yet a demonstrated profit-maximizing threshold for any horizon.** There are 123 closed trades: 61 SWING and 62 GROWTH; SHORT has none and LONG has only two open trades. Market/style splits are smaller still. Historical trades also span several changes to scoring, exits, and configuration behavior.

Use the explicit reference profiles in section 6, fix the inconsistent threshold resolution, and compare a small number of predefined challengers on identical opportunities. Do not raise every threshold to increase “accuracy,” or lower every threshold merely to produce trades. Both can make returns worse.

The most consequential findings are:

1. **All 11 portfolios currently resolve their base R:R floor to 2.0.** The calibration helper's 2.25 is not the base floor passed by these scans. The earlier explanation that a base 2.25 blocked these portfolios needs correction and historical decision evidence.
2. **Decision Engine and fallback apply different choppy-market R:R rules.** A configured 3.0 is not necessarily the actual Decision Engine floor.
3. **The SWING wide-stop experiment changes entry eligibility as well as exits.** With a 12% target and a 7.5% stop, prospective R:R is only 1.6. Its 2.0 entry floor can exclude precisely the trades it was intended to study.
4. **UW's 30-calendar-day expected move is used as a target across all four horizons**, without horizon conversion or an age limit at the paper-engine read.
5. **Entry and exit configuration resolution differ**, with observable effects on older portfolios and HK trailing settings.
6. **September closed trades do not yet establish improvement.** Separate recent entry cohorts from trades that merely closed this month; neither is evidence about positions entered after the latest calibration deployment.

The broker connection is sandbox, as clarified by the user. None of the 129 paper-trade rows has a broker order ID. This report therefore evaluates the simulated engine; it does not claim measured live execution performance. Equity `SHORT` means a short holding horizon, not selling stock short. Options-income trading is a separate engine; these thresholds must not be copied into option-contract strategies.

## 2. What the engine actually does

The main path is `_scan_for_entries()` → `resolve_entry_config()` → portfolio/candidate checks → `_build_game_plan_for_style()` → Decision Engine plus fallback comparison → sizing/opening. Decision Engine is authoritative in `primary` mode; the local `_should_enter()` becomes authoritative when Decision Engine is unavailable. Monitoring subsequently applies stops, targets, signal/time exits, and partial exits.

Entry is the intersection of many conditions: market hours, fresh BUY signals, watchlist membership, quote/price availability, confidence, K-Score, TA score, cross-horizon confluence, earnings, entry gap, market regime, portfolio losses, position/sector/risk limits, symbol duplication, conviction gates, R:R, and entry score. A threshold experiment must record the complete gate sequence. Passing R:R alone does not imply an order will be opened.

Position sizing starts with equity × `risk_per_trade_pct`, adjusts for several scores/conditions, divides by stop distance, and applies position/exposure limits. These multipliers and partial fills/exits make average trade return, total P&L, and return per unit of initial risk different metrics.

The displayed confidence threshold is a signal-strength setting, not an established probability of making money. Both entry implementations use **90% of `min_confidence` as their hard confidence floor**; the configured threshold also affects scoring. For example, configuring 50 permits confidence 45 to survive that hard check. Decision Engine can additionally raise its score floor by one when its recent win-rate input is below 30%. A displayed score floor of five therefore need not be the final threshold.

### Horizon definitions are not interchangeable

| Style | Signal BUY primary outcome window | Default paper maximum hold | Interpretation |
|---|---:|---:|---|
| SHORT | 7 calendar days | 10 weekdays | Short-duration equity momentum |
| SWING | 14 calendar days | 20 weekdays | Intermediate trade |
| LONG | 28 calendar days | 90 weekdays | Longer holding strategy |
| GROWTH | 14 calendar days | 60 weekdays | Growth/momentum strategy with much longer possible holding |

The paper engine currently uses `numpy.busday_count` without a holiday calendar here, so the implementation counts weekdays even though the comment says trading days excluding holidays. Actual portfolios can override maximum hold: portfolios 2 and 3 use 30. A five-day signal hit rate is not the realized win rate of a position managed for 20–90 weekdays.

## 3. Production evidence

### Closed trades by frozen trade style and portfolio market

Win means stored `pnl > 0`; mean return is the arithmetic mean of stored `pct_return`. Profit factor is the sum of positive P&L divided by the absolute sum of negative P&L. These are descriptive database results, not a new backtest, reconstructed ledger, or annualized portfolio return.

| Market/style | Closed | Wins | Win rate | Mean stored trade return | Profit factor | Distinct entry dates |
|---|---:|---:|---:|---:|---:|---:|
| US SWING | 57 | 17 | 29.82% | −0.042% | 0.621 | 26 |
| HK SWING | 4 | 0 | 0% | −5.661% | 0 | 1 |
| US GROWTH | 47 | 16 | 34.04% | −0.482% | 0.655 | 23 |
| HK GROWTH | 15 | 7 | 46.67% | −0.038% | 1.359 | 10 |
| SHORT, either market | 0 | — | — | — | — | 0 |
| LONG, either market | 0 | — | — | — | — | 0 |

There are six open trades: one SWING, three GROWTH, two LONG. Their unrealized gains/losses are excluded above. Portfolios 6, 9, 10, and 891 have no trades at all. All closed rows have R:R, confidence, K-Score, entry score, P&L, and return populated; **36 lack `entry_shares`**, so reconstructed initial-risk returns need additional ledger validation. Current portfolio style matches each trade's stored style in this snapshot, but future attribution must use the frozen trade field.

Do not add US and HK raw P&L or interpret pooled dollar EV without currency and capital normalization. The calibrator does no such normalization. Even within a market, different position sizes can make mean percentage return and dollar profit factor disagree. The table's HK GROWTH result is an example, not evidence of a robust edge.

### September evidence, separated correctly

| US cohort | Closed | Wins | Mean stored trade return | What this measures |
|---|---:|---:|---:|---|
| SWING entered September 1 onward | 4 | 0 | −2.807% | Recent entries that have already closed; three entry dates |
| GROWTH entered September 1 onward | 5 | 1 | −5.475% | Recent entries that have already closed; four entry dates |
| SWING exited September 1 onward | 7 | 2 | −0.250% | Includes positions entered before September |
| GROWTH exited September 1 onward | 9 | 3 | −3.955% | Includes positions entered before September |

There are no September-entry or September-exit closed HK trades. September open positions are not failures or wins yet. Comparing only resolved recent trades can favor fast exits and omit slow winners or slow losers. Preserve the complete entry cohort and mark unresolved observations explicitly.

The newest closed entry date is September 11. The calibration file was refreshed September 19 at 03:53:27 UTC. **There is no closed entry cohort from after that refresh to validate its effectiveness.** The file confirms `by_style` refresh and `pooled_threshold_updated: false`; it does not prove new trading eligibility or positive expectancy.

### What simple historical threshold filters show

These are filters over already-executed US closed trades, holding their recorded outcomes fixed. They cannot simulate rejected opportunities, alternate fills, changed exits, cash constraints, or which trade would replace a filtered trade. They are hypothesis generation only.

| Style | R:R ≥ | Remaining trades | Wins | Mean trade return | Profit factor |
|---|---:|---:|---:|---:|---:|
| SWING | 2.00 | 57 | 17 | −0.042% | 0.621 |
| SWING | 2.25 | 15 | 7 | +1.301% | 1.812 |
| SWING | 2.50 | 9 | 3 | +0.472% | 1.926 |
| SWING | 3.00 | 5 | 0 | −0.818% | 0 |
| GROWTH | 2.00 | 47 | 16 | −0.482% | 0.655 |
| GROWTH | 2.25 | 47 | 16 | −0.482% | 0.655 |
| GROWTH | 2.75 | 47 | 16 | −0.482% | 0.655 |
| GROWTH | 3.00 | 12 | 6 | −0.530% | 0.735 |
| GROWTH | 3.38 | 10 | 5 | −0.923% | 0.604 |

SWING 2.25 deserves a controlled challenger, not dismissal as automatically harmful and not promotion as “best”: it retains only 15 trades across 12 entry dates and nine symbols. SWING ≥3.0 contains only two symbols. GROWTH 2.0–2.75 cannot be distinguished at all using these already-selected US trades. A higher GROWTH win rate at 3.0 does not produce positive mean returns.

Other filters also fail to support a universal “higher is better” rule:

- US SWING confidence ≥50: 31 trades, 35.48% wins, +0.691% mean return, **0.652 dollar profit factor**. At ≥55: 22 trades, 40.91% wins, +1.389%, PF 0.822. At ≥60: 16 trades, 31.25% wins, −0.536%, PF 0.381. These use recorded confidence, not the engine's 90%-of-config hard floor.
- All 57 US SWING closed trades already have K-Score ≥55. They cannot tell us whether choosing a K-Score floor of 48, 50, 52, or 55 is better for the full candidate universe.
- US GROWTH K-Score ≥52 retains 42 trades with −0.658% mean return versus −0.482% across 47 trades at ≥48.
- Historical US SWING entry score ≥5 retains 38 trades with 18.42% wins. Scores span different scorer/configuration versions, so this does **not** justify lowering the current score gate. It does disprove treating the number as a stable, monotonic probability scale without versioned evaluation.

## 4. Mechanism findings and solutions

### PT-H01 — Calibrated base floor is masked by configuration resolution

**Confirmed source behavior and production configuration; high priority.** `_DEFAULT_CONFIG` supplies `min_rr_ratio=2.0`. `resolve_entry_config()` always includes it. Both `_should_enter()` and the Decision Engine request then use `cfg.get("min_rr_ratio", _default_min_rr_ratio(...))`. The fallback expression does not replace an existing 2.0. All 11 stored portfolio configurations also explicitly contain 2.0.

| Style | Calibration helper, neutral | Effective scan base floor | Regime value resolved from the inspected file, before Decision Engine's candidate cap |
|---|---:|---:|---:|
| SWING | 2.0 | 2.0 | 3.0 |
| GROWTH | 2.25 | 2.0 | 3.0 |
| LONG | 2.25 | 2.0 | 3.38 |
| SHORT, hypothetical empty style config | 2.25 | 2.0 | 3.38 |

This was reproduced by executing the actual AST-extracted resolver/helper with the production calibration JSON and whitelisted portfolio settings. It is not a reimplementation of the merge. Regime values are reconstructed from the file, not an inspection of every running worker's memory: `_load_min_rr_override()` caches the file in-process and `reload_min_rr_override()` invalidates only that process. Record the calibration version loaded by each worker; a file refresh alone does not prove fleet-wide adoption. The explicit base 2.0 finding does not depend on this cache.

**Correction to the previous explanation:** pooled calibration is style-blind and the diagnostics-refresh fix is useful, but the current evidence does not support “SWING's effective base floor changed from 2.25 to 2.0.” Nor is 2.25 currently the effective GROWTH base floor. Historical starvation attribution requires the actual decision path, gate reason, effective configuration, and version at the time. Regime changes can matter independently.

**Solution:** represent threshold mode explicitly: `manual` with a value, or `calibrated` with a versioned fallback. Resolve once, before all entry/exit/reporting consumers. Preserve explicit manual 2.0; do not silently migrate all portfolios to 2.25. Display value, source, style, market, regime, and calibration version. Test an actual portfolio scan/request payload, not only `_default_min_rr_ratio()`.

### PT-H02 — Choppy R:R differs between primary and fallback

**Confirmed in reviewed source; high priority.** Decision Engine's `hard_rejects.py` computes:

```text
candidate_cap = 0.95 × (price × style_target_multiplier − price) / (price − stop)
DE floor = max(base_floor, min(configured_regime_floor, candidate_cap))
fallback floor = max(base_floor, configured_regime_floor)
```

Example with a $100 SWING plan, stop $94.50, target $112, base 2.0, regime 3.0: R:R is 2.182. Decision Engine's cap is 2.073, so its R:R gate passes; fallback requires 3.0 and rejects. Other gates may still reject the candidate. This is a source-derived example, not a captured production order.

Furthermore, the cap uses a fixed style target while the incoming paper plan may use a UW expected-move target. It therefore does not necessarily describe the plan being evaluated. A floor made proportional to a fixed target can become almost automatic for fixed-target plans while behaving differently for UW plans.

**Solution:** share one pure, tested R:R policy across both paths. Use the supplied plan's provenance and horizon; separate “valid plan geometry” from the independently chosen minimum economic payoff. Do not automatically lower a required payoff to 95% of each candidate's own payoff merely to make it pass. If valid setups cannot meet policy, record abstention or evaluate a predefined challenger. Add parity tests for standard/wide stops, UW/fallback targets, all styles, and Decision Engine outage.

### PT-H03 — Entry/exit settings disagree and explicit intent is ambiguous

**Confirmed with production settings; high priority.** Entry resolution treats values equal to generic defaults as UI echoes, allowing style/HK overrides to win. `_monitor_positions()` uses a plain defaults → style → stored-config merge and does not apply the HK override layer.

Examples: portfolio 1 resolves entry-context breakeven/trailing triggers to 4%/7%, but monitoring uses 3%/5%; portfolio 3 resolves 1.5%/3%, but monitoring uses 3%/5%. HK portfolios resolve entry-context trailing ATR to 1.5, but monitoring uses 2.0. Entry-context exit parameters do not themselves execute exits; the problem is inconsistent effective policy and reporting.

Portfolio 9 also demonstrates why an HK override cannot be assumed: stored SWING confidence 50 and entry score 5 differ from the generic 45/4, so they override HK's intended 65/6. Portfolio 5's stored score 4, conversely, is treated as an echo and resolves to SWING 5.

**Solution:** share a resolver and store explicit overrides separately from defaults. Freeze entry policy and plan version on the trade. Define whether later settings apply to open trades; changes to existing stops must be deliberate. Migrate older configurations with a before/after preview. Verify actual exit behavior, not merely settings-screen values.

### PT-H04 — Wide-stop A/B is not an isolated stop experiment

Portfolio 891 uses `stop_pct_override=0.925`, `atr_stop_mult_override=2.5`; control 6 uses 0.945/2.0. Both have zero trades. With the fixed fallback target +12%, maximum permitted stop width changes from 5.5% to 7.5%, and nominal R:R drops from 2.182 to 1.6 when those width caps bind. The unchanged base floor 2.0 blocks the latter. Narrower ATR stops or larger UW targets can still qualify; it is not a total mathematical lockout.

Global `max_positions_per_symbol_global=1` also counts open trades across portfolios, so the first experiment arm to hold a symbol can prevent the other arm entering it. Different fills/selection and risk-based sizing then confound comparisons.

**Solution:** distinguish two experiments. For stop-management efficacy, use a common frozen candidate stream in isolated virtual books, with the same entry selection and risk budget, and compare paired stop/exit paths. For the complete wide-stop strategy, allow its own entry eligibility and measure the entire resulting portfolio. Label these separately. A 1.5 R:R challenger can test the 7.5%/12% geometry, but lowering the gate also changes the strategy; do not call that a stop-only test. Do not widen targets merely to manufacture R:R.

### PT-H05 — UW target horizon and freshness do not match the holding plan

`options_game_plan_snapshot.py` computes `IV × sqrt(30/365) × 100`. `_build_game_plan_for_style()` takes that same `expected_move_pct` as the upside target for every style when available. It does not consume the reference DTE to convert the move to the intended holding horizon. `get_latest_options_game_plan()` fetches the latest row without an age/as-of cutoff, despite the caller's comment referring to yesterday's snapshot.

**Solution:** store IV units, observed time, available time, reference maturity, and source with the plan. Use a target horizon tied to the strategy, with calendar-aware conversion and preferably a relevant IV term structure. Treat square-root-of-time conversion as a modeling approximation, not guaranteed price behavior. An implied-volatility move is a magnitude estimate, not bullish direction or a promised target. Combine direction with the equity setup and historically measured target-hit probability. Reject stale/malformed inputs or use an explicitly labeled fallback; historical replay must use only snapshots available at the decision time.

Until this is fixed, report results separately for UW-derived and fixed-target entries. Otherwise a threshold can appear to improve simply because the plan generator changed.

### PT-H06 — Exit thresholds can be unreachable or incompatible

Fixed-target SHORT exits fully at +5%, before its default +10%/+12% partial levels. SWING's second partial at +18% is above its fixed +12% full target. Standard SWING's `hold_stall_days=30` is beyond its maximum hold of 20. The full-target/time exits run before partial/stall handling. UW targets can change which conditions are reachable.

**Solution:** validate each actual plan, not just a style dictionary. Show disabled/unreachable conditions explicitly. For a future plan-relative policy, define optional partials in units of initial risk and require every partial level to precede the full target. Compare no-partial control against one predefined partial policy; don't add complexity without evidence. Implement exchange-calendar holding counts and independently label calendar versus session horizons.

### PT-H07 — Calibration still cannot establish an optimum

The refreshed file contains pooled base 2.25, pooled regime 3.38, and candidate/baseline validation mean P&L both −27.3937. Refreshing diagnostics despite an unchanged pooled candidate is a correct improvement; preserving a baseline is not evidence the baseline is profitable.

Remaining limitations:

- Optimization is mean raw P&L among surviving **closed executed trades**, pooled across style, market, account size, and currency units. Rejected opportunities and the cost of remaining in cash are absent.
- The 70/30 split orders by entry date, with no same-date grouping or purge for trades whose outcomes extend into validation. It requires 100 total trades, 20 validation rows, and 15 survivors per candidate—not 100 independent observations in every strategy slice.
- The style correction is a thin-sample fallback based on the 75th percentile, not a per-style return optimization. At 100 style trades, the fallback can disappear even though no independent style optimum has been validated.
- Style metadata currently comes from mutable portfolio config. Style overrides take precedence over market overrides without estimating their intersection. In another dataset a style fallback of 3.0 can override a lower market cap.
- A closed-trade R:R percentile describes selected historical plans; it is not a hard ceiling on today's candidate universe or a calibrated probability of success.
- The pooled baseline helper reports 2.25 although all current scan configurations pass 2.0, so the nominal anti-regression baseline differs from executed policy.

**Solution:** optimize net, risk-normalized results using versioned market × style cohorts and the full candidate stream. Require prospective validation and portfolio replay. Pool only through an explicit statistical model that accounts for market/style differences; do not reuse a pooled dollar threshold by default. Refresh diagnostics independently from promotion, and retain an explicit manual fallback when evidence is insufficient. Repeatedly searching parameters can overfit even with a holdout; log every tested variant and reserve genuinely untouched evaluation periods. See [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

### PT-H08 — Trade starvation needs persistent evidence

All current `paper:gate_block:{id}` and `paper:no_entry_summary:{id}` reads returned null after market hours. These summaries have a four-hour TTL; null does not mean no gate blocked trading. The entry-score summary also groups several underlying causes.

**Solution:** persist per-scan counts and sampled candidate decisions: every gate evaluated, actual threshold, raw feature, first failure, additional failures, policy/model/plan versions, and market/session time. Track `candidate → valid data → eligible setup → risk approved → order → fill` counts by style and experiment arm. Detect consecutive market sessions with BUY candidates but no eligible setups. Alert for investigation; do not automatically relax gates to reach a trade quota.

### PT-H09 — Scaling and accounting complicate threshold attribution

`position_scaling_mode="off"` controls a separate thesis/pullback mechanism. It does not disable the legacy winner scale-in branch, which reads `cfg.get("scale_in_enabled", True)`. None of the whitelisted live portfolio configs explicitly sets `scale_in_enabled`. Scale-ins can change entry cost basis and shares; historical missing `entry_shares` further complicates original-risk reconstruction.

**Solution:** explicitly set and display every scaling mode in experiment definitions. For the first entry-threshold experiments, use no scale-ins and a fixed exit policy in isolated paper books. Reconcile every fill, commission, partial exit, remaining quantity, and cash movement before using risk-normalized outcomes for promotion. Do not equate a planned stop with a guaranteed fill: real stops can execute beyond the trigger during gaps/volatility. See [FINRA's explanation of stop-order execution](https://www.finra.org/investors/insights/stop-orders-factors-consider-during-volatile-markets).

## 5. Stop/target geometry to preserve when tuning

With a valid ATR, the engine takes the tighter of the ATR distance and the fixed maximum stop width. The fixed stop multiplier is a **price multiplier**, not a risk percentage. Thus 0.945 means 5.5% maximum distance; it is not a minimum 5.5% stop distance. Rounding and entry slippage change realized entry R:R.

| Style | Entry stop ATR multiplier | Maximum fixed stop distance | Fixed fallback target | Nominal target/stop ratio when width cap binds |
|---|---:|---:|---:|---:|
| SHORT | 2.0 | 3.0% | +5% | 1.667 |
| SWING | 2.0 | 5.5% | +12% | 2.182 |
| LONG | 2.0 | 10% | +25% | 2.500 |
| GROWTH | 3.0 | 12% | +35% | 2.917 |
| Wide SWING experiment | 2.5 | 7.5% | +12% | 1.600 |

`/data/models/trade_params.json` is absent in the inspected container, so the Optuna overlay is not replacing these style literals there. UW expected moves can replace the target and, when ATR is absent, influence the stop. Consequently this table describes fallback geometry, not every live plan.

A higher nominal R:R is useful only if the target is plausible and the stop is appropriate. Making stops artificially tight or targets distant raises the number without establishing positive expected returns.

## 6. Concrete reference settings and challengers

**These are recommended paper-research starting policies, not empirically proven optima or changes already deployed.** Preserve current reference behavior where evidence cannot distinguish alternatives. Make R:R mode explicit/manual for reproducibility rather than relying on the currently masked calibration fallback. Do not paste a wholesale profile into an existing portfolio with open positions.

### US reference entry profiles

| Parameter | SHORT | SWING | LONG | GROWTH |
|---|---:|---:|---:|---:|
| `min_confidence` | 45 | 50 | 40 | 45 |
| Corresponding hard confidence floor | 40.5 | 45 | 36 | 40.5 |
| `min_kscore` | 48 | 52 | 50 | 48 |
| Base `min_entry_score` | 4 | 5 | 4 | 4 |
| `min_ta_score` | No additional floor | 0.65 | No additional floor | No additional floor |
| Manual base `min_rr_ratio` control | 2.0 | 2.0 | 2.0 | 2.0 |
| `max_entry_gap_pct` | 0.04 | 0.03 | 0.05 | 0.04 |
| `max_hold_days` reference | 10 | 20 | 90 | 60 |

“No additional floor” preserves the current style behavior; it is not a recommendation to discard TA from scoring. Keep the regime/earnings/data/risk gates. The first entry-threshold comparison should be restricted to neutral/bull conditions so a changing choppy cap does not obscure the experiment. Keep risk-off and bear entry blocks as the control. Test adverse regimes separately after PT-H02 is resolved.

Why these settings: they preserve the repository's style-specific reference gates and the **actually resolved** 2.0 base, avoiding a silent strategy change. They do not imply the existing profiles are profitable. SHORT's 2.0 can legitimately reject wide-stop +5% plans; the challenger below tests whether accepting lower payoff ratios is worthwhile. LONG's zero closed outcomes do not justify a mined threshold or copying GROWTH's calibration.

### Small, predefined challenger set

| Priority | Experiment | Control → challenger | Reason and decision criterion |
|---|---|---|---|
| First | US SWING base R:R | 2.0 → 2.25; other settings fixed | Fifteen historical survivors look better descriptively. Promote only if fresh paired opportunities improve net portfolio/risk-adjusted results without unacceptable starvation. |
| First, separate | SWING stop management | 2 ATR/5.5% cap → 2.5 ATR/7.5% cap | Compare identical entries in isolated virtual books first. Then separately evaluate the full strategy with its resulting entry exclusions. |
| Second | SHORT feasibility | 2.0 → 1.5 R:R in a shadow book | The +5%/−3% fallback plan yields only 1.667 before costs. No trade history supports profit expectations. Keep confidence/K-Score/score constant. |
| Second | GROWTH R:R sensitivity | 2.0 → 2.25 | All 47 historical US closed trades pass both; rejected-candidate capture is required to learn anything new. There is no evidence for a production-wide increase to 2.75 or 3.0. |
| Later | LONG duration | 90 → 60 maximum sessions after calendar correction | Compare matched path outcomes; retain 90 as control because two open trades cannot validate a shorter exit rule. |
| Later | SWING confidence | Configured 50 → 55 | Requires a new cohort; hard floors become 45 → 49.5. Do not mistake the historical ≥55 filter for this exact intervention. |

These are sequential experiments, not a Cartesian sweep. Do not change R:R, stops, confidence, TA, score, and exits simultaneously. There is no present justification for changing K-Score thresholds based on the closed-trade data.

### Exit controls and risk budget

Preserve the following source-style exits as named controls while fixing configuration consistency. Some are intentionally flagged for later redesign, rather than silently replaced with invented optimal values.

| Parameter | SHORT | SWING | LONG | GROWTH |
|---|---:|---:|---:|---:|
| Breakeven trigger | +3% | +1.5% | +4% | +4% |
| Trailing activation | +5% | +3% | +6% | +7% |
| Trailing ATR multiplier | 2.0 | 2.0 | 2.0 | 2.0 |
| Partial levels | +10% / +12% | +10% / +18% | +15% / +20% | +15% / +22% |
| WAIT exit setting | 5 | 3 | 7 | 5 |
| HOLD stall setting | 7 | 30 | 30 | 30 |

SHORT partials and SWING's second partial/stall require the reachability corrections in PT-H06. Keep the existing control for comparability; define a separate corrected exit-policy version before interpreting its performance.

For new isolated US experiments, use a **0.5% equity risk budget per initial trade**, 10% position-value cap, six positions, three new entries/day, and explicit `scale_in_enabled=false`. The 0.5% is a conservative experiment budget, not an optimized return setting; the current generic risk budget is 1%. Use the same budget in both arms and measure actual initial risk after sizing caps. Set a 3% aggregate initial-risk cap for that six-position experiment and retain sector/data/drawdown controls. Keep experiment books separate from shared portfolio symbol restrictions so one arm does not censor the other. These recommendations require explicit experiment configuration and no broker execution.

### HK

Use a distinct reference: confidence **65**, entry score **6**, TA **0.65**, style K-Score (**52 SWING; 48 GROWTH**), manual base R:R **2.0**, position cap **7%**, and an experiment risk budget of **0.35%** per trade, half the intended HK default 0.7%. These are conservative controls, not optimized values. Keep HK-specific market hours, transaction costs, currency accounting, and liquidity rules.

Only four HK SWING closed trades from one entry date and 15 HK GROWTH closed trades exist. Do not relax HK quality filters to obtain a larger sample. Resolve portfolio 9's actual 50/5 exception and entry/exit merge differences before comparing “HK defaults.” Do not launch HK SHORT/LONG tuning based on US results. A future choppy-policy study should compare a named strategy rule against the current rule within HK; US/style marginal percentiles are not a substitute.

## 7. How to determine genuinely better thresholds

The objective should be **positive net expectancy with acceptable drawdown and capital use**, not the highest win rate. For a simplified binary outcome, expected return in risk units is `p × average_win_R − (1−p) × average_loss_R − costs_R`. Partial exits, gaps, and time exits mean the real distribution is richer than “target or stop.” Estimate it from reconciled fills.

1. **Freeze the policy and candidate stream.** Snapshot symbol, market, horizon, timestamp, available features, plan source/age, stop/target, effective gates, model/code versions, and all rejected candidates. Deduplicate repeated scans of one opportunity. Retain both entered and rejected setups.
2. **Fix policy parity before tuning.** Resolve PT-H01–H03; verify main/fallback request behavior and final entry/exit settings. Record a new policy version at deployment. Historical pre-fix performance remains separately labeled.
3. **Replay whole positions and portfolios.** Model next executable quote/bar, commissions, spread/slippage, gap-through stops, partials, session calendars, cash, sector/symbol capacity, and unresolved positions. For same-bar target/stop ambiguity use intraday evidence or a stated conservative assumption. Never assume exact fills at stop prices.
4. **Split by time and availability.** Keep same-entry-date observations together; exclude training outcomes not yet known at the validation start. Use rolling out-of-time windows and cluster uncertainty estimates by date and symbol. Fit thresholds on training data only; do not adjust them repeatedly against the same validation period.
5. **Use a controlled reference and one challenger.** Run both on identical timestamped candidates with independent simulated capital. Record total eligible opportunities, accepted fraction, trade frequency, net expectancy per opportunity, realized risk units, profit factor, daily equity drawdown, exposure, turnover, and benchmark-relative results. Report unresolved outcomes separately.
6. **Require adequate independent evidence.** The existing 100-trade floor is a minimum engineering check, not statistical proof. As a proposed first review checkpoint, seek at least 100 resolved trades per market/style policy, 30 distinct entry sessions, and at least 30 genuinely out-of-time resolved observations; then inspect uncertainty and concentration. These counts may still be insufficient. Do not force trades or borrow a different horizon's outcomes to meet them.
7. **Promote on prospective evidence.** Require reconciled net-positive expectancy, improvement over the fixed control on the predefined objective, acceptable drawdown/cost sensitivity, and results not explained by one symbol or week. If uncertainty includes material deterioration, keep the reference and collect more data. Log every candidate policy tried to control selection bias.
8. **Monitor after promotion.** Keep the prior version available, publish gate-pass rates and daily equity performance, and roll back on predefined operational/risk failures. Separate an absence of opportunity from broken data or unreachable policy.

UW should enter this process as timestamped, optional evidence: compare matched equity policies with and without UW-derived targets or directional features. A UW flow or IV score should not automatically reduce minimum quality requirements. Options strategies need their own contract-level bid/ask, expiry, exercise/assignment, multi-leg fill, and payoff evaluation; an equity horizon's R:R floor does not establish an option strategy's quality.

## 8. Verification and implementation order

| Order | Deliverable | Acceptance check |
|---|---|---|
| 1 | Canonical manual/calibrated policy resolver | A real scan, Decision Engine request, fallback, monitoring, and UI show the intended effective values and provenance. Explicit manual 2.0 stays 2.0. |
| 2 | Shared R:R and plan validation | Identical neutral/choppy verdicts for the same inputs; UW and fallback plans use valid horizon/freshness rules. |
| 3 | Persistent decision/experiment records | Explain zero-entry sessions by horizon; record counterfactual eligibility without orders. |
| 4 | Isolated SWING R:R and stop experiments | Both arms see the same opportunities; no global-symbol interference; distinguish paired exit study from full-strategy comparison. |
| 5 | Reconciled, versioned evaluation | Net fill/cash accounting, session calendars, mature cohorts, risk-normalized and currency-aware metrics. |
| 6 | Sequential SHORT/GROWTH/LONG studies | Promote only after independent out-of-time evidence; retain explicit insufficient-data status otherwise. |

**Checks performed:** read-only SQL transactions with an eight-second statement timeout; whitelisted portfolio configurations and model files; known-key Redis reads; local/runtime engine hash comparison; actual-source AST execution of configuration resolution against the production snapshot; descriptive R:R/confidence/K-Score/score filters; **16 targeted calibration/style/refresh/wiring tests passed**. Those tests do not establish end-to-end trading profitability and did not catch the masked base default. No new application tests or application changes were made.

The running Decision Engine file was not separately hash-verified in this audit; its findings are attributed to reviewed local source. Expired/missing cache summaries cannot reconstruct historical rejection causes. This audit did not rebuild the fill ledger, backfill rejected opportunities, or claim the calibration deployment's causal effect.

## 9. Source map and reproducibility

Key local sources, relative to this document:

- [Paper engine](../../services/market-data/src/services/paper_trading_engine.py): `_default_min_rr_ratio`, defaults/style dictionaries, `resolve_entry_config`, `resolve_entry_gate_params`, `_should_enter`, `_build_game_plan_for_style`, `_monitor_positions`, `_call_decision_engine`, `_scan_for_entries`.
- [Calibration API](../../services/market-data/src/api/paper_portfolio.py): `calibrate_min_rr_ratio`, candidate/sample constants, per-market/style fallbacks.
- [Decision Engine hard rejects](../../services/decision-engine/src/api/core/hard_rejects.py): `_candidate_rr_ceiling`, confidence and R:R checks.
- [Decision Engine scoring](../../services/decision-engine/src/api/core/scorer.py): dynamic entry-score floor.
- [UW option-plan snapshot](../../services/market-data/src/services/options_game_plan_snapshot.py): 30-day expected move and latest-snapshot lookup.
- [Signal outcome definitions](../../services/signal-engine/src/api/signals_shared.py): BUY/SELL calendar-day windows.
- [Trade schema](../../shared/db/models.py): `PaperTrade`, `PaperPortfolio`, decision log.
- [Previous calibration incident explanation](../incidents/self-tuning-job-performance-bugs.md) and [wide-stop experiment](../features/paper-trading-gates.md): claims checked against effective configuration, not accepted as runtime proof.

The SQL basis for the principal cohort table is below. It intentionally uses the frozen trade style; market is taken from current portfolio config because this query has no frozen market column. A rerun on a later snapshot will naturally differ.

```sql
BEGIN READ ONLY;
SET LOCAL statement_timeout = 8000;
SELECT COALESCE(p.config->>'market', 'US') AS market,
       t.trading_style,
       COUNT(*) AS n,
       COUNT(*) FILTER (WHERE t.pnl > 0) AS wins,
       AVG(t.pct_return) AS mean_trade_return_pct,
       SUM(CASE WHEN t.pnl > 0 THEN t.pnl ELSE 0 END)
         / NULLIF(-SUM(CASE WHEN t.pnl < 0 THEN t.pnl ELSE 0 END), 0)
         AS profit_factor,
       COUNT(DISTINCT t.entry_date) AS entry_dates
FROM paper_trades t
JOIN paper_portfolios p ON p.id = t.portfolio_id
WHERE t.stage = 'closed' AND t.pnl IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 2;
ROLLBACK;
```

For September entry cohorts add `t.entry_date >= DATE '2026-09-01'`; for September exit cohorts add `t.exit_time >= TIMESTAMP '2026-09-01 00:00:00'`. The latter follows the stored timestamp boundary, not an exchange-session conversion. Historical R:R filters add `t.rr_ratio_at_entry >= threshold` to the relevant US/style closed cohort. All win-rate denominators are surviving closed rows, not signals, alerts, or independent trials.
