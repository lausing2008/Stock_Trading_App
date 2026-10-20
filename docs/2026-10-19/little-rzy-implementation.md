# Little RZY-inspired short-term research — 2026-10-19

## Scope and source contract

Implements the accepted specification in [Little RZY Integration](../Little%20RZY%20Integration.md), named `LITTLE_RZY_INSPIRED_V1`. This is a testable engineering interpretation, not a claim of exact reproduction, endorsement, or profitable performance.

Source concepts: [Chart Fanatics measured-move trend strategy](https://www.chartfanatics.com/strategies/measured-move-trend-strategy) motivates structural trend, measured moves and continuation; [Bollinger's rules](https://www.bollingerbands.com/bollinger-band-rules) distinguish band context from standalone buy/sell instructions. Numerical thresholds, pivot confirmation, execution ordering, costs, sequence policy and data gates below are the accepted research implementation assumptions.

## Using it

- Chart → Indicators: SMA 5, SMA 10 and Little RZY-inspired. Off initially; daily SMAs use daily bars, intraday SMAs use the selected bar period. RZY supports US regular-session 5m, 15m, 1h and daily; 4h is explicitly unavailable.
- Settings → Short-term trading & Little RZY-inspired: separate opt-ins for morning digest and pattern emails, selected timeframes, candidate preview and exclusions. Paper Portfolio also exposes the separate research ledger.
- Digest: own US watchlist only; both bullish and bearish short-term/day-trading preparation. Due 20 minutes before exchange regular open (normally 09:10 Eastern / 06:10 Pacific). Five-minute enqueue window, once per user/session; expires at open. Holidays, weekends, early closes and DST follow the exchange calendar. Existing morning emails are independent.
- Candidates require an armed structure, sufficient planned reward/risk and fresh completed bars. Entry reference and stop are provisional, target is frozen; wait for a later intraday confirmation and recheck the open. No qualifying candidates is a valid digest. Ranking is deterministic structure ordering, not a probability or confidence score.
- Pattern emails are separate from AI signals: continuation triggers, exhaustion/reversal watches, pending exits and paper cancellation/exit updates. Watches never open a reverse trade.

## Deterministic rules

Default population BB(20,2), Wilder ATR14, confirmed pivots with 7 bars on each side. Existing chart BB retains its existing sample-standard-deviation convention. Two higher highs/lows or lower highs/lows define the structural trend. The first visible trend has unknown sequence confidence until an observed transition establishes an epoch.

Consecutive alternating A/B/C pivots require advancing anchors, impulse at least 1.5 ATR and retracement 0.20–0.80. D is measured from the A–C line at the intervening price extreme's bar index; geometry freezes when C confirms. Replay of the confirmation delay rejects structures already invalidated or already at target. Band context is evaluated at C. Entry requires a later completed directional trendline rejection, previous-bar confirmation, 0.10 ATR touch tolerance, no chasing; a middle-band cross is not required for continuation and belongs to the separate reversal-watch logic. Preferred known sequences are 1/2; unknown sequences remain explicitly unknown. Armed structures expire after 20 bars.

Structural stop adds 0.25 ATR; minimum reward/risk is 1.5 at trigger and again at next-bar open. Paper execution includes commissions, half-spread, slippage, liquidity from the already completed trigger candle, and data checks, plus shortability/borrow for shorts. Stop-first ordering handles ambiguous bars, adverse gap stops fill at the worse open, and queued structural exits execute at the next open. Baseline exits fully at the primary target. Partial/runner is an experimental comparison, never a default recommendation; its stop never loosens. Reversal context persists after completion without automatic reversal.

Replay candles require explicit timezone offsets, normalize to UTC, and reject boolean OHLC/volume values. All configuration fields are in `Config`; the scheduler reads optional JSON `LITTLE_RZY_CONFIG`, validates it and uses the configuration hash as part of stream identity. Replay accepts an explicit configuration without changing live defaults. Changes require a separate comparison/measurement period, not pooled outcomes.

## Persistence, consent and API

Native stored candles are used when present. When 15-minute/hourly rows are absent, the adapter derives them from existing completed 5-minute bars, anchored to the exchange open. Rows off the exchange-anchored timeframe grid are excluded as snapshots rather than treated as additional candles. Every constituent must be present; incomplete bins are withheld, not interpolated. A partial oldest query bin is disclosed and excluded as warmup. Final shortened session bars close at the actual exchange close. A per-symbol scan cache shares the source read and bounds memory; it is not a cross-user or durable quote cache.

The central calendar now represents the published 13:00 cash-equity closes for its supported 2024–2027 years; the same close drives dated bounds and the live regular-session gate. Sources: [NYSE/ICE 2024–2026 notice](https://ir.theice.com/press/news-details/2023/NYSE-Group-Announces-2024-2025-and-2026-Holiday-and-Early-Closings-Calendar/default.aspx) and [2026–2028 notice](https://ir.theice.com/press/news-details/2025/NYSE-Group-Announces-2026-2027-and-2028-Holiday-and-Early-Closings-Calendar/default.aspx). Dates beyond the maintained holiday coverage are not invented.

Service-owned `little_rzy_streams`, `little_rzy_events`, `little_rzy_preferences` are registered before the existing guarded schema initializer. Checkpoints, stable event IDs and shared outbox unique IDs suppress reprocessing and duplicate enqueue. First persisted scan is a warm bootstrap: historical events are recorded but never emailed. Changed overlapping stored bars or excluded off-grid observations in an existing checkpoint create a durable replay quarantine. It survives provider values reverting and blocks queued pattern emails at dispatch. Original evidence is preserved; unlocking requires a stable canonical archive and an explicit audited replay, not an automatic reset.

Central alert registry types `little_rzy` and `short_term_digest` both declare default OFF. Settings, enqueue and send use the same central permission contract. Only active users with email and explicit opt-in qualify. Sender checks consent again, and pattern sends recheck watchlist and timeframe. Signed unsubscribe footers use the existing mechanism. Batch leases use a unique owner. Scheduler runs once per minute under the alerting environment guard and a Redis lease; per-stream commits avoid retaining a transaction across the watchlist scan, and scans without a new candle do not rewrite checkpoint JSON.

Authenticated gateway routes under `/stocks/rzy`: GET/PUT `/preferences`, GET `/digest`, GET `/paper`, POST `/replay`, GET `/{symbol}?timeframe=...`. Caller identity scopes preferences, watchlists and ledger; client cannot select another owner. Replay is bounded to 6,000 bars and does not write or place orders.

## Data and remaining evidence

Stored Price rows do not retain reliable upstream provider/delay or borrow availability. The adapter labels these unknown and sets execution eligibility false: live research signals can exist, but paper fills requiring verified execution inputs cancel. OHLC is resolved as a consistent whole; overlays are withheld when chart OHLC differs. Forming/settling bars, gaps, stale data and unsupported coverage are explicit exclusions; there is no fabricated replacement candidate. No live broker path is connected and existing portfolio balances are untouched.

The backtest CLI uses the same streaming engine, costs and conservative ordering. It reports samples, Wilson hit-rate intervals, expectancy, profit factor, mark-to-market drawdown, exposure, unresolved/censored trades and grouping. Chronological 70/30 and expanding walk-forward partitions start fresh engines with no crossing positions or tuned winner selection. Report caveats include dependent observations and borrow charges accrued at exit.

Next evidence work: retain/verify provider timestamps and execution basis, source real shortability and borrow costs, collect an audited completed-bar archive, then run prospective shadow and chronological comparisons. The included synthetic CSV/report establish reproducibility only; they establish no market edge. Live execution remains outside scope. Existing price-risk delivery must remain OFF.
