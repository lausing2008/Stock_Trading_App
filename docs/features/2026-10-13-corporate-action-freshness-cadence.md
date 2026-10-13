# Corporate-action evidence freshness: measured cost and proposed cadence

**2026-10-13.** The first production shadow run of price-risk detection produced 110 events of
which **0 were verified**. This is the measurement behind the remedy, and the separation of two
limits that both land on "not verified" and have completely different fixes.

## The two limits, which are not the same thing

| Limit | What it means | Fixed by re-fetching? | Status it caps at |
|---|---|---|---|
| **Freshness** (`coverage_span`) | The source was never asked about these dates | **Yes** | `unverified` → `provisional` |
| **Completeness** (`source_completeness`) | The span is covered; the source does not promise its list is exhaustive | **No** | `provisional`, permanently |
| **Unappliable action** (`unapplied_action`) | An action is recorded in-window with no usable ratio | No | excluded entirely |

This distinction is now machine-readable: `resolve_window` returns a `limiting_factor`, and
`best_achievable_status(completeness_basis)` states the ceiling for a source regardless of how
fresh its coverage is.

**The practical consequence, stated plainly because the opposite is the easy assumption:** a
refresh campaign against yfinance raises every affected window from `unverified` to
`provisional` and **stops there**. It never reaches `verified`. Reaching `verified` requires a
source that documents exhaustiveness, which is a data-sourcing decision, not a cadence one. My
earlier note that "the verified path is unreachable until the ingest keeps up" conflated the
two — refreshing does not make it reachable.

## Measured cost

Production, 2026-10-12, 8-symbol sample with every outbound HTTP call counted:

```
symbols 8 | resolved 8 | errors 0
http calls total 18  ->  2.25 per symbol   (NVDA 4: one-off cookie/crumb handshake)
wall time 4.63s      ->  0.58s per symbol
```

Steady-state is **2 calls / 0.58s per symbol**. The span requested does not change the cost:
yfinance returns the symbol's entire split and dividend series regardless, so an incremental
refresh is not cheaper than a full one — which removes the usual argument for a sparse cadence.

### Population

| | |
|---|---|
| Followed symbols (on a watchlist) | **157** |
| Symbols with any coverage row | 203 |
| Coverage rows with `source_guarantee` | **0** |
| Coverage rows with `response_only` | 203 |
| `evidenced_to`, all rows | 2026-10-08 |
| Latest daily bar | 2026-10-13 |

Every window therefore ran five sessions past the evidence. The gap is uniform, which is what a
weekly-or-slower sweep produces.

## Cadence options, costed

| Cadence | Calls/refresh | Calls/day | Wall time | Worst-case staleness |
|---|---|---|---|---|
| Per session, followed only | 314 | 314 | ~91s | 1 session |
| Weekly, followed only | 314 | ~63 | ~91s | 5 sessions ← **observed state** |
| Per session, all 194 active | 388 | 388 | ~113s | 1 session |

For scale: the premarket price ingest alone makes ~2,840 yfinance calls a day after its own
optimisation, so a daily refresh of followed symbols adds roughly **11%** to one existing job's
volume.

## Proposed and implemented: daily, after the close, followed symbols

`corporate_action_refresh`, 19:50 ET weekdays — after the close and **before** price-risk
detection at 20:10, so the evening's detections read coverage extended to today rather than to
whenever the last sweep happened to run. One batched request to
`POST /intelligence/corporate-actions/refresh`, not 157 internal round trips.

Why daily rather than something sparser, given actions are rare: the cost is small and fixed,
and the failure mode of being late is not "we miss an action" but "every figure in the window is
unverifiable", which is what was observed. `ingest` widens coverage and never narrows it, so a
failure for one symbol leaves that symbol's previous claim intact rather than shrinking it, and
failures are reported **by symbol and reason** rather than as a count — "7 failed" cannot be
acted on.

Followed symbols only, not all 194 active: the 37 unfollowed ones produce no price-risk events
for anyone, and widening is one parameter if that changes.

## Exclusions are now individually explained

`GET /alerts/risk-history/exclusions` lists every unmeasurable subject with its limiting factor,
the best status its source could ever reach, how far its evidence actually extends, and
`fixable_by_refresh`. This is a delivery precondition rather than a diagnostic nicety: "17
unmeasurable" cannot be acted on, and a refresh campaign that cleared the `coverage_span` cases
would leave the other two kinds untouched while looking like a complete repair.

## Still open

The 7% pullback first rung stays as-is for the shadow period, per direction. The remaining
delivery preconditions — several sessions of deduplication, escalation and recovery evidence;
per-user volume comparisons for alternative first rungs; opt-in delivery, quiet hours and a
digest — are not built, and delivery stays off.

---

## Production result (commit `f42be888`)

The real job was run, not the endpoint directly — service token, batched request, the path the
scheduler will take.

```
BEFORE   coverage evidenced_to 2026-10-08, 203 symbols
RUN      requested 157 | refreshed 155 | failed 2 (SKHYV, US10Y) | 48.5s | ceiling=provisional
AFTER    coverage evidenced_to 2026-10-13, 155 symbols current
```

48.5s against the ~91s serial estimate: the handshake is paid once, not per symbol. The two
failures are named rather than counted — both are identifiers yfinance does not resolve as
tradable tickers, which is the `SymbolNotResolved` case, and a failed lookup correctly writes no
coverage claim at all rather than an empty one.

Detection before and after, same universe, same code:

| Run | Events | `unverified` | `provisional` | `verified` | Unmeasurable |
|---|---|---|---|---|---|
| 06:26, stale coverage | 110 | **110** | 0 | 0 | **17** |
| 07:31, fresh coverage | 13 | 0 | **13** | 0 | **0** |

Three things this establishes, and one it does not.

- **Freshness was the binding limit.** Every unmeasurable subject became measurable; the figure
  that mattered was `evidenced_to`, not anything about the prices.
- **The ceiling held exactly as predicted.** Every refreshed event is `provisional`. **None is
  `verified`, and none ever will be from this source.** If a later reading shows `verified`
  rows, something has changed about the source or someone has written a `source_guarantee`
  claim that yfinance does not support.
- **The escalation ladder suppressed most repeat reporting**: 110 on the cold start, 13 on the
  next session over the same universe.

What it does **not** establish: a steady-state alert rate. 13 is **one** session after the cold
start, and the preconditions ask for several sessions of deduplication, escalation and recovery
evidence before any of this is read as a rate.
