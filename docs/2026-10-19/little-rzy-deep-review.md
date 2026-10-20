# Little RZY deep review — 2026-10-19 (evening, Pacific)

Full review of `LITTLE_RZY_INSPIRED_V1` against [the specification](../Little%20RZY%20Integration.md): engine, data adapter, workflow/email, API, backtest and chart overlay. Production measurements below are read-only (`evaluate(persist=False)`, rolled back).

## Verdict

**Entry and exit logic is correct.** Pivots (7/7, non-repainting), trend/epoch, A/B/C geometry, D measured at the extreme's bar index, frozen targets, pre-confirmation replay, Bollinger context at C, trigger (touch ± 0.10 ATR, directional close, previous-bar break), no-chase, structural stop, next-open fill with costs, stop-first ordering, adverse-gap fills, partial/runner and next-open structural exits all match §2–§9. A randomized invariant check over 120 streams and 507 triggers found no violation: every trigger is a real line touch with a directional close and R/R ≥ 1.5; every fill/cancel is on the next bar's open with friction; stops and targets lie on the correct sides; every exit lies inside its bar; no stop fills better than the stop; positions never overlap.

**The defects were in data handling and workflow**, and the largest one made the 5m timeframe unusable.

## Defects found and fixed

| # | Defect | Evidence | Fix |
|---|---|---|---|
| R1 | **Exact-equality revision quarantine** permanently locked streams; no unlock path. | 95/95 5m streams locked. Re-fetched OHLC: 96% of differences < 1e-6 relative (storage noise), 8 of 8,273 > 1%. Volume is routinely restated. 15m/1h would lock after the next session. | Volume restatements and ≤ 1e-6 OHLC noise keep the point-in-time state. A material OHLC change, a removed bar, an off-grid snapshot consumed by the old code, or a gap after the checkpoint **rebuilds** the stream deterministically from current stored bars as a new generation. The superseded state is summarised (structures, positions, pivots, SHA-256 of the full checkpoint, the first differing bar before/after) in `little_rzy_stream_generations`; its events stay in `little_rzy_events`. Mail queued from a superseded generation is suppressed at send time. Legacy quarantines rebuild. |
| R2 | **One missing bar anywhere in the 6,000-row window excluded the stream** for ~2.5 months (5m). | 64 `missing_candles` + 3 empty streams. | Gaps are reported and split segments (`break_indices`); the engine runs on the latest contiguous segment — never bridged or filled. A gap within the last 30 minutes waits for backfill (`awaiting_backfill`) before rebuilding. A segment restart has unknown sequence confidence. |
| R3 | **Checkpoints grew without bound** (every bar + indicators forever), rewritten whole on each new bar. | 5m average 1.9 MB/stream, 94 MB table, +~30 KB/stream/day. | Absolute-index retention: last 1,200 bars plus anything a live pivot/structure/position still references; bounded pivots/structures/closed positions. Trigger volume, entry time and borrow rate are stored on the position. Proven event-identical to the unbounded engine on long random streams, including checkpoint resumes. |
| R4 | Every TRIGGERED email was followed by an ENTRY_CANCELLED email for the standing "data basis unverified" blocker. | 275 triggers, 275 cancellations (100%). | Cancellations caused only by standing blockers are recorded, not mailed; the trigger email states that paper execution is unavailable and why. |
| R5 | A runner stopped after the primary target was banked was labelled `INVALIDATED`. | Engine `_exit`. | Labelled `COMPLETED` (the measured move completed). |
| R6 | Exhaustion watch could fire for the last structure of a **previous** trend epoch. | Engine `_exhaustion`. | Current epoch only. |
| R7 | Chart overlay required chart and strategy OHLC to match within 1e-7, so restated bars withheld the overlay. | Same noise as R1. | 1e-4 relative: still rejects a basis change (adjusted vs raw differ by whole percents). |
| R8 | Overnight restatements would be absorbed by the 09:10 digest itself. | Rebuild ≈ 0.8 s/stream. | Pre-open maintenance pass (open −50 to −21 min, ≤ 60 streams/minute, never emails). |

Every fix has a test that fails when the fix is reverted (sabotage-checked), except the email freshness bound after a rebuild, which is an existing guard also covered by the new-event filter.

## Production dry run (new code, read-only)

| | Before | After |
|---|---|---|
| Usable research streams | 180 / 342 | **333 / 342** (111 each of 5m/15m/1h) |
| Quarantined | 95 | 0 (95 rebuild once as `legacy_quarantine`) |
| Excluded for gaps | 64 | 0 (39 streams restart on a short recent segment) |
| Digest candidates | 16 | 24 |

Remaining: 6 stale, 3 without completed bars. 342 evaluations including the 95 rebuilds took 68 s; slowest stream 1.1 s.

## Still open (not code defects)

- **Off-grid snapshot rows are platform-wide**: 3–8% of all 5m `prices` rows daily for at least a month (e.g. `13:30:01`). RZY excludes them; other consumers of 5m bars were not reviewed here.
- Paper execution remains blocked by design: no verified provider delay/execution basis, no shortability/borrow source. No paper fill can occur.
- §11 real-data backtest and the five comparisons, prospective and out-of-sample outcomes: not run.
- Point-in-time vs canonical: between rebuilds the live state reflects the values known at the time; a rebuild re-derives history from the restated bars. Events from both generations are kept and distinguishable by `generation`.
- Unrelated pre-existing frontend failure: `OptionsGamePlanCard.test.tsx` ("Preferred strategy to research: Long Call") fails on clean `HEAD`.

## Frontend outage of the release (recorded here for completeness)

The release's frontend was built locally with `frontend/.env.local`, baking `NEXT_PUBLIC_API_URL=http://localhost:8000` into 8 served chunks including `_app`. Fresh page loads failed site-wide from about 2026-10-19 13:51 UTC until a rebuild on EC2 (`scripts/deploy_frontend.sh`, build `gPmNYrHsN4tM8RCbwC7zc`) around 01:00 UTC on 2026-10-20. The release acceptance missed it because browser tests intercepted every API call and page checks only read server-rendered HTML. After the fix: 0 served chunks contain `localhost:8000`, and a non-intercepted browser load of `/stock/MU` used `/api/...` with 200 responses.
