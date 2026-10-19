Act as a senior quantitative developer working inside my existing AI Stock Trading Platform. Inspect the repository, architecture, data adapters, signal engine, alert delivery, charting, and paper-trading system before implementing changes.

Implement a versioned strategy named LITTLE_RZY_INSPIRED_V1 for signals, alerts, chart overlays, and backtesting. Complete the implementation using the existing stack and conventions.

This is an explicit algorithmic interpretation of a discretionary method. Do not claim it exactly reproduces Marci Silfrain’s trading or guarantees profitable signals.

References:
- https://www.youtube.com/watch?v=AVVM-FyewLg
- https://www.chartfanatics.com/strategies/measured-move-trend-strategy
- https://www.bollingerbands.com/bollinger-band-rules

Separate source-supported concepts from our engineering assumptions in the documentation. All numerical defaults below are configurable research assumptions.

1. SCOPE AND INTEGRATION

Implement continuation signals first. Implement reversal-watch alerts separately; do not automatically reverse positions.

Use existing market-data adapters. Record provider, timestamp, delay, session, and data freshness. Never represent delayed data as live.

Use completed OHLCV candles only. Align corporate-action adjustments consistently across OHLC. Do not synthesize missing candles silently.

Use the same deterministic strategy engine for historical replay and live evaluation. AI may explain events but must not invent, override, or silently modify signal conditions.

Integrate with the existing watchlist, alerts, charts, and paper-trading workflow. Do not enable live broker execution.

2. INDICATORS

Defaults:
- Bollinger Bands: SMA(close, 20), plus/minus 2 population standard deviations.
- ATR: Wilder smoothing, length 14; document initialization.
- Confirmed pivots: left=7 and right=7.
- Near-band tolerance: 0.25 ATR.
- Trendline-touch tolerance: 0.10 ATR.
- Stop buffer: 0.25 ATR.
- Minimum planned reward/risk to the primary target: 1.5.
- Maximum armed lifetime: 20 bars.
- Preferred continuation sequence numbers: 1 and 2.

Calculate:
- percent_b = (close - lower_band) / (upper_band - lower_band)
- bandwidth = (upper_band - lower_band) / middle_band

Handle zero denominators, insufficient history, stale data, and invalid prices explicitly.

3. NON-REPAINTING PIVOTS AND TREND

A pivot at bar i with right=7 becomes available only after bar i+7 closes.

Store pivot_time and confirmed_at separately. Charts may draw the pivot at its original location but must show when the strategy actually knew it.

Use only confirmed pivots available at the decision time.

Define:
- Downtrend: latest two confirmed highs and latest two confirmed lows are strictly descending.
- Uptrend: both pairs are strictly ascending.
- Otherwise: neutral; no new continuation entries.

Specify deterministic handling of equal extrema and consecutive same-type pivots.

Assign a trend_epoch_id when transitioning from neutral/opposite direction into a qualified trend. End the epoch when the condition ceases to hold. This is our operational trend definition, not a claim about the original method.

If history starts mid-trend without a verified epoch start, sequence confidence is unknown. Do not label the first observed pattern “first in the trend.”

4. EXPLICIT STRUCTURE DETECTION

Use the following initial pivot-based approximation:

Short:
- A = confirmed swing high.
- B = subsequent confirmed swing low.
- C = subsequent confirmed lower swing high.
- Require A.time < B.time < C.time and C.price < A.price.
- Require the current trend filter to be down.
- Require A.high - B.low >= 1.5 * ATR at B.
- Require the retracement (C.high - B.low)/(A.high - B.low) to fall between configurable defaults 0.20 and 0.80.
- Draw a descending line through A.high and C.high.

Long:
- Mirror the logic using swing low A, subsequent swing high B, and higher swing low C.
- Require an uptrend and an ascending line through A.low and C.low.

Use the most recent eligible consecutive alternating pivot triple. Do not scan arbitrarily many anchor combinations to select whichever produces the best historical outcome.

Define the measurement window as A through C inclusive. Reject geometry where the extreme is not strictly between the anchors or the measured distance is nonpositive.

These anchor and impulse rules are our chosen approximation. Clearly document that they may omit discretionary examples.

Assign a stable structure_id using symbol, timeframe, direction, and anchor timestamps.

5. MEASUREMENT AND FREEZING

Use trading-bar indices, not elapsed wall-clock time:

line(t) = pA + (pC - pA) * (t - tA)/(tC - tA)

Short:
- L = minimum low within the measurement window.
- tL = its bar index; choose earliest occurrence on ties.
- D = line(tL) - L.
- Primary target = L - D.
- Experimental runner target = L - 2D.

Long:
- H = maximum high within the measurement window.
- tH = its bar index; choose earliest occurrence on ties.
- D = H - line(tH).
- Primary target = H + D.
- Experimental runner target = H + 2D.

Measure the line at the extreme candle’s index, not at the entry candle.

Freeze anchors, measurement window, D, and targets when C becomes confirmed. Do not redraw an existing structure using future information.

Before arming, replay the period after C through its confirmation bar. Reject candidates already invalidated or whose primary target was already touched during that period.

Allow entry evaluation only on bars after the structure was armed.

6. BOLLINGER CONTEXT AND ENTRY CONFIRMATION

Evaluate pullback context at anchor C using the indicator values from C’s bar:

Short:
C.high >= middle_band[C] - 0.25 * ATR[C].

Long:
C.low <= middle_band[C] + 0.25 * ATR[C].

Record proximity to the relevant outer band as additional context, not a guaranteed quality score.

Default entry trigger, evaluated after bar t closes:

Short:
- Structure is armed.
- Candle range intersects line(t) ± touch_tolerance.
- close[t] < line(t).
- close[t] < open[t].
- close[t] < low[t-1].

Long:
- Structure is armed.
- Candle range intersects line(t) ± touch_tolerance.
- close[t] > line(t).
- close[t] > open[t].
- close[t] > high[t-1].

Default conservative stretch filter:
- Block a new short when close[t] < lower_band[t].
- Block a new long when close[t] > upper_band[t].

Label this NO_CHASE_FILTER, not proof of exhaustion. Provide a research variant with it disabled.

Apply the configured sequence filter only when sequence confidence is known.

Do not require a Bollinger midline cross for continuation entries; reserve it for the separate reversal-watch logic.

Emit a confirmed signal at bar close. Simulated entry occurs at the next tradable bar’s open with slippage, never retrospectively on the trigger bar.

7. STOPS, TARGETS, AND POSITION MANAGEMENT

Default structural stop:
- Short: max(C.high, trigger_bar.high, line(trigger_bar)) + stop_buffer.
- Long: min(C.low, trigger_bar.low, line(trigger_bar)) - stop_buffer.
- stop_buffer = 0.25 * ATR at the trigger.

Freeze the initial protective stop. Do not loosen it automatically.

At simulated entry, reject if:
- Entry is on the wrong side of the protective stop.
- The primary target is no longer ahead of entry.
- Planned reward/risk to the primary target is below 1.5.
- Data, liquidity, or shortability requirements fail.

Recompute reward/risk using the actual simulated fill and configured costs.

Baseline exit:
- Exit 100% at the primary measured target, protective stop, or structural invalidation.

Experimental variant:
- Exit 50% at the primary target.
- Move the remainder’s stop to entry plus a documented cost allowance.
- Exit the remainder at the 2D target or stop.
- Label this variant separately and compare it with the baseline.

A short close above its extended trendline, or long close below it, invalidates the structure. For an open paper position, schedule the resulting market exit for the next bar open. Protective stops remain active intrabar.

Treat target touches before entry as stale/completed setups, not profitable trades.

8. SEQUENCE AND LIFECYCLE

Use separate structure and position state machines.

Structure:
DETECTED → ARMED → TRIGGERED
with terminal outcomes COMPLETED, INVALIDATED, EXPIRED, or REJECTED.

Position:
PENDING_ENTRY → OPEN → PARTIAL → CLOSED
with cancellation reasons for failed entry checks.

Increment sequence once per distinct eligible structure within a trend epoch, including structures subsequently rejected by the Bollinger filter. Never increment per retest or alert.

For consecutive structures, require both anchors to advance and a new intervening extreme. Document how shared boundary pivots are handled.

Permit at most one armed structure per direction, symbol, and timeframe. A newer candidate must explicitly supersede the old one.

Deduplicate events persistently across restarts. Do not reopen the same structure after completion.

Expire pending setups after the configured lifetime or a trend-epoch change.

9. EXHAUSTION AND REVERSAL WATCHES

Never generate an automatic countertrend entry from sequence count or an outer-band touch alone.

Experimental exhaustion warning:
- Sequence >= 3.
- A close beyond the trend-direction outer band within the previous 5 completed bars.
- A later close back inside that band.
- Current structure D is smaller than the preceding structure D.

Label this combination as our research heuristic.

Separate bullish reversal watch:
- Prior downtrend had a completed target or exhaustion warning.
- Its short structure has subsequently invalidated.
- Within 20 bars of that context event, price crosses from at/below to above the Bollinger middle band.

Mirror for bearish reversal watches, explicitly labeling the symmetric extension as our assumption.

These are watch alerts, not continuation entries or automatic position reversals.

10. ALERT PAYLOAD AND CHARTS

Include:
strategy_version, config_hash, symbol, timeframe, event_type,
direction, position_action, signal_time, data_as_of, provider,
trend_epoch_id, structure_id, sequence_number, sequence_confidence,
anchor timestamps/prices/confirmation times, extreme candle,
trendline value, D, Bollinger values, percent_b, ATR,
planned entry, actual paper fill if applicable, stop,
primary target, optional runner target, reward/risk,
state, passed_rules, failed_rules, rejection_reason.

Position actions must distinguish:
OPEN_LONG, OPEN_SHORT, CLOSE_LONG, COVER_SHORT, and WATCH_ONLY.

Chart overlays:
- Confirmed anchors and confirmation timing.
- Structure line and measurement segment.
- Frozen targets and protective stop.
- Signal and simulated fill separately.
- Invalidation, expiry, and rejection markers.

Example wording:
“Little RZY-inspired SHORT #2 confirmed on 1H close.
Target: X. Stop: Y. Planned R/R: Z.
Pullback context and bearish trigger passed.
Paper entry pending next bar.”

Do not display invented win probabilities.

11. VALIDATION AND BACKTESTING

Test:
- Pivot confirmation delay and no retrospective signals.
- Identical historical/live event output on the same candle stream.
- Frozen geometry and correct target arithmetic.
- Pre-confirmation invalidation or target completion.
- Sequence reset, unknown initial sequence, and deduplication.
- Gaps through stops or targets.
- Target/stop touched within the same candle.
- Partial-exit ordering and restart recovery.

When intrabar order is unknown, use a documented conservative assumption or lower-timeframe data. Never assume favorable ordering.

Include commissions, spread, slippage, short borrow/availability where relevant, and session handling.

Use chronological out-of-sample and walk-forward evaluation. Prevent overlapping trades or labels from contaminating split boundaries.

Report trade count, net expectancy, profit factor, maximum drawdown, exposure, win rate, average win/loss, and uncertainty. Separate results by direction, timeframe, sequence, and market regime.

Compare:
- Bollinger context enabled versus disabled.
- No-chase filter enabled versus disabled.
- Sequence 1–2 restriction versus unrestricted.
- Full primary-target exit versus partial/runner.
- Structural stop versus the original line + 0.25 ATR stop.

Do not optimize toward a promised win rate or return. Report weak or inconclusive results honestly.

12. DELIVERABLES

Deliver working code, configuration, alert integration, chart overlays,
paper-trading integration, meaningful tests, and a reproducible backtest.

Save documentation under /docs/YYYY-MM-DD/ using the actual execution date:
- strategy specification;
- source-supported rules versus assumptions;
- parameter definitions;
- implementation and validation results;
- limitations and unresolved questions.

Do not substitute pseudocode for implementation. If data or infrastructure blocks a component, complete the unblocked work and identify the exact remaining dependency.