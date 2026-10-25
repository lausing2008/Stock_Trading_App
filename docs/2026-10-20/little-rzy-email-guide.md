# Little RZY email guide and presentation update

**Current status — October 25, 2026:** [The deployed reliability/journal release](../2026-10-25/rzy-reliability-and-forward-journal.md) adds explicit simulated-fill messages and a durable lifecycle/performance view at `/little-rzy` (admin). Existing stored-price streams still lack execution-grade evidence; this does not make the research triggers tradable. Historical implementation notes below are retained; their old local/deployment/test status is superseded by the linked release record.

## Reading the alerts

- `OPEN_SHORT`: a bearish pattern triggered. The entry reference is a hypothetical short entry, not an order or fill. A short benefits from falling prices; its protective stop is above entry and target below entry.
- `OPEN_LONG`: a bullish pattern triggered; stop below entry, target above entry.
- `WATCH_ONLY`: watch for exhaustion or a possible reversal. This does not open a position or instruct a reversal.
- `PAPER_FILL`: the deterministic simulator recorded a fill, with its simulated price and time. This is not a broker fill or an instruction to enter now; current stored-price streams remain blocked from producing these fills.
- `INVALIDATED` / `EXPIRED`: research-structure lifecycle states, distinct from trade exits. Their renderers are explicit, but no new outbound invalidation/expiry subscription is enabled. Follow-up is never guaranteed.
- `EXIT_PENDING`: an existing paper position has a line-close invalidation; the engine requests exit at the next bar open while its protective stop remains active.
- `PAPER_EXIT`: a simulated exit. It may be partial; consult paper state and the actual exit price, not the original setup reference levels.

For the ANF 5m alert at October 20, 2026, 19:05 UTC (3:05 PM EDT): entry reference 161.2600, stop 161.6751 and target 157.2525 imply risk 0.4151 and reward 4.0075 per share. Reward divided by risk is approximately 9.66:1 before costs. This is planned geometry, not a win probability, expected return or guaranteed maximum loss. The signal is based on a completed five-minute candle, not a five-minute holding commitment. Actual fills, costs and gaps change outcomes.

## Will an exit email follow?

For the supplied ANF alert, **no corresponding paper-trade exit is expected**: provider delay, borrow availability and execution basis are unverified, so the pending paper entry is cancelled. Later watches or a cancellation notice may arrive, but neither means a trade was filled.

For an eligible paper position that actually fills, `EXIT_PENDING` and `PAPER_EXIT` can produce emails. Delivery still depends on opt-in, watchlist membership, selected timeframe, fresh events and the delivery pipeline. These events track the simulated strategy, not a user's broker holdings. Email is not a stop-loss mechanism. Targets and stops in research alerts are reference levels, not instructions to act.

RZY is independent of the AI signal. The historical evaluation has not demonstrated a trading edge.

## Local presentation change

New readable subjects replace raw action codes with labels such as “RZY research · Bearish setup triggered · ANF · 5m”. HTML uses a compact card with status first, separate level rows, interpretation and lower-priority technical details. Plain text retains the same content. Both formats explicitly explain blocked execution and exit-follow-up expectations. Timestamps include US Eastern and original offset; technical identifiers remain available for troubleshooting. HTML escapes dynamic content.

Changes are presentation-only: engine rules, recipients, consent, deduplication and event eligibility are unchanged. Existing working-tree audit fixes were preserved. This update is local, not deployed; queued emails retain their existing bodies. No test email was sent.

Validation: renderer tests cover blocked and pending entries, watch/cancellation semantics, partial/full exits, actual exit prices, risk arithmetic display, timezone and HTML escaping. All five renderer tests pass. The existing SQLite workflow probe passes its initial email enqueue/content assertions, but fails later at `_little_rzy_probe.py:140` (digest send count), so all eight tests sharing that fixture report setup errors. The working tree also contains ongoing audit fixes; this update does not claim full workflow validation or deployment readiness.
