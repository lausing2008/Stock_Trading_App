# Little RZY release — 2026-10-19

Application release `a959c5dc`. Commits: `04fa7d74` features, `addcc1e6` gateway coverage, `db1e8f28` causal liquidity, `8636b52d` checkpoint writes, `6d9d72eb` completed 15m/1h aggregation and central early-close calendar, `a959c5dc` off-grid snapshot exclusion and durable revision quarantine.

## Production verification

- 15/15 containers healthy; 12/12 backend source/shared trees match the committed release (0 drift).
- Public and localhost stock, settings and tracker pages returned 200 with the expected release markers, including the dynamic chart chunk. New read routes returned 401 without authentication.
- Three service-owned tables exist. Both new channels are enabled for `lausing` only; other users remain off. Timeframes: 5m, 15m and 1h; own US watchlist only.
- No blocked database sessions at verification. Existing price-risk delivery remains OFF. Acceptance/deployment operations sent no test email and placed no broker orders.

## Data readiness

A first production scan exposed missing native 15m/hourly storage for every symbol. Those frames now derive from complete 5m constituents, anchored to exchange sessions; missing bins never become fabricated candles. The after-hours history initialization persisted research events without calling enqueue or the sender. These are historical bootstrap counts, not a daily alert rate or performance estimate.

- Subjects: 114; streams checked: 342; states: {'stored_bar_revised_replay_required': 95, 'research_only': 180, 'missing_candles': 64, 'no_completed_bars': 3}.
- Persisted streams: 275; historical events: 44758; emails enqueued by initialization: 0.
- Actual digest preview: 16 qualifying candidate structures (delivery capped at 10); exclusions by reason: {'stored_bar_revised_replay_required': 95, 'no_armed_structure': 161, 'missing_candles': 64, 'insufficient_planned_reward_risk': 3, 'no_completed_bars': 3}.

Candidate counts describe structures, not unique stocks or forecasts. Entry references/stops are provisional; fresh confirmation and gap/risk checks remain required. Stored provider delay/execution basis and short borrow evidence are still unknown, so simulated fills requiring them remain gated. Gapped or absent source histories remain explicit exclusions. Revised checkpoint histories are durably quarantined, including their queued emails; a temporary provider reversion cannot unlock them. Existing evidence is preserved. Stable canonical completed bars and an explicit audited replay are required before resuming these streams; no automatic reset or replay-unlock endpoint was added. Off-grid one-second snapshots beside true candles are excluded before aggregation.

## Rollout

Frontend compiled locally; served build ID `u1Ln70rVm8D7qTdnE1jNy`. Backend source layers rebuilt from immutable running-image parents without dependency upgrades or build network access. Host deployment locks, serial restarts, per-service health gates and rollback tags were used. The shared calendar correction required all 12 backend layers to be rebuilt again; the existing frontend build remained valid. No EC2 reboot was required.

An earlier staging attempt was paused before production source changed or services restarted to include the causal volume fix. The checkpoint guard prevents repeated scans from rewriting full JSON state without new bars. Source/event behavior and these guards are covered by real-engine/ORM tests.

The central calendar uses published NYSE cash-equity early closes across its maintained 2024–2027 window; dated bounds and live session checks share the same close. Sources and parameters are linked in [implementation](little-rzy-implementation.md).

## Validation

| Backend suite | Passed | Skipped |
|---|---:|---:|
| market-data | 5460 | 21 |
| technical-analysis | 67 | 0 |
| ml-prediction | 457 | 0 |
| ranking-engine | 183 | 18 |
| signal-engine | 610 | 1 |
| strategy-engine | 62 | 0 |
| portfolio-optimizer | 83 | 0 |
| research-engine | 709 | 8 |
| api-gateway | 58 | 0 |
| decision-engine | 390 | 0 |
| event-intelligence | 632 | 0 |
| news-intelligence | 200 | 0 |

Full-suite baseline: 8,911 backend tests passed, 48 skipped; 669 frontend tests passed. After the final changes, 53 engine/workflow/aggregation cases, 310 existing calendar-consuming cases, and all 3 required PostgreSQL gates passed. Earlier affected engine/enforcement/options checks passed 103 cases. These overlapping subsets are not added to the baseline total. Production compilation, TypeScript and offline desktop/mobile browser acceptance passed. Reinstating the late liquidity and checkpoint defects made their regression checks fail.

## Remaining evidence

No profitable edge or verified live execution is claimed. Required follow-up: source/repair excluded bar histories, retain verifiable provider timestamps/execution basis, obtain shortability/borrow inputs, then collect prospective and chronological out-of-sample outcomes. Synthetic [CSV](little-rzy-synthetic.csv) and [report](little-rzy-synthetic-report.json) establish reproducibility only.

The morning window passed before activation; next scheduled digest is Tuesday 2026-10-20, normally 09:10 Eastern / 06:10 Pacific. Actual future receipt has not yet been observed. An empty qualifying set is valid and includes exclusion reasons.

Tracker tier 455 is served. Subsequent documentation-only commits require no additional application rebuild. See [machine-readable evidence](little-rzy-production-evidence.json) and [image identities](little-rzy-image-manifest.json).
