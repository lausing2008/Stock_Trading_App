# Little RZY and Game Plan audit F01–F09: resolution

Audit: [2026-10-20 full audit](../2026-10-20/little-rzy-stock-game-plan-full-audit.md). All nine findings are fixed and deployed on 2026-10-21 (UTC). Trading rules and the RZY strategy configuration (`8ceb8cf780c3eb981983`) are unchanged. Tracker tier 459.

| Finding | Release | What changed |
|---|---|---|
| F02 (high) delivery stops at generation 129 | `3f609d74` market-data | The sender resolves the occurrence record → stream → **current** generation → that exact event id. Figures are compared with what was queued. Each refusal records its real cause (`superseded: …`, `paused: …`, watchlist/timeframe) instead of "recipient opted out". |
| F07 old snapshots "usable now" | `3f609d74` | `snapshot_current_since`: during a session a snapshot must be ≤15 min old; outside it, no older than the last in-session scan before that session's close. Expired rows are listed as `snapshot_expired` with their historical status, never as candidates or "usable now". |
| F08 pruning invalidates totals and first-seen | `3f609d74` | New append-only `little_rzy_occurrences` (first cutoff, first persisted time, first generation, `observed_in` = incremental / rebuild / bootstrap, queued figures/time) and `little_rzy_rebuilds`; 35-day retention. Admin and `scripts/rzy_session_report.py` read them; the report labels the older fallback as a lower bound. |
| F09 scan start used as record time | `3f609d74` | `now` stays the data cutoff; persistence and the freshness-before-enqueue check use actual elapsed time (`clock`). |
| F01 (high) forming daily bar used as a close | `c8b3183d` research-engine | `completed_daily_bars`: only sessions whose post-close ingest has settled (16:30 local + 15 min) are read. Seen in production before the fix: HK rows dated the current day at 13:00 HKT. |
| F03 four-session-old prices "fresh" | `c8b3183d` | Freshness compares the latest bar with the latest completed session; any missing completed session makes the price and every setup STALE. A Friday close stays current over a weekend. |
| F04 blocked/stale setups with buy branches | `febb3aea` | Blocked → only a `wait` branch listing what would make it eligible; stale → no current guidance; exit-triggered → leads with the exit; after-fill management labelled hypothetical. The LLM is told which cases exist. |
| F05 account cap ignored by structured sizing | `febb3aea` | One rule: a stated account caps setup sizes as it caps the narrative; an account below one share blocks. |
| F06 trigger equals no-chase ceiling | `febb3aea` | `max_fill` (highest fill keeping TP1 ≥ 2R) is separate from the confirmation level; when it is at or below the breakout level only a retest limit fill within 3 sessions qualifies. Targets are not moved. Packet v9. |

## Verification

- market-data 5,535 passed / 22 skipped; research-engine 717 passed / 8 skipped (SQLite; the PostgreSQL probe variants were not run); frontend 697 + TypeScript.
- Sabotage checks: reintroducing each defect (generation cap, scan-start freshness, no snapshot expiry, `ts <= now` selection, blocked full playbook, no account cap, coincident trigger wording) fails its test.
- Production after deploy: new tables present; `0005.HK` mid-session plan dated 2026-10-20 (completed), both setups blocked with a single wait branch; MU conditional with `max_fill` 925.42 / 1,125.05; 15/15 containers healthy.
- Before the fix the busiest streams were at generation 31 (DIS/DIA 5m) — F02 had not yet suppressed real mail.

## Not changed / follow-ups

- Intelligence reports (`generators.py`) still use the generic calendar-day `daily_bars`; only the copilot/Game Plan packet moved to completed sessions.
- Occurrences recorded before `3f609d74` have no record; one re-emitted by a later rebuild is labelled `rebuild`, not `incremental`, so it never inflates live latency.
- OD-01/OD-02 (tracker 457/458) remain open.
