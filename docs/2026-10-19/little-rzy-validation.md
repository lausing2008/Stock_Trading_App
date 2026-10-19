# Little RZY validation — 2026-10-19

The shipped pure engine is exercised directly: confirmed-pivot timing, earliest ties and ambiguous extrema, population BB/Wilder ATR arithmetic, frozen geometry, actual default-threshold arming and triggers, next-open fills and gap rejection, stop-first ordering, partial/runner restart recovery, short borrow refusal, sequence confidence/filtering, pre-confirmation target/invalidation, expiry and reversal watches. Streamed, replayed and JSON-checkpoint-resumed event/state output must agree exactly. A final edge-case test also verifies equivalent timezone representations deduplicate, actual instants determine order, and naive timestamps/boolean numbers are refused.

The workflow probe uses real shared ORM classes, engine, outbox and central audience gate on SQLite and disposable PostgreSQL. Only the database connection and external email-provider boundary are replaced. It checks persistent deduplication, historical bootstrap suppression, watchlist ownership, independent channel consent, opt-out after enqueue, once-only fake-provider send, actual digest window/calendar/expiry, adjusted OHLC and stale/missing/forming bars. No real email was sent. PostgreSQL is required in `make test-integration-pg-schema`; absence cannot silently pass that gate.

Frontend behavioral tests cover explicit consent and save failures, empty/excluded digest previews, paper ledger, chart geometry/timezone/basis matching, and chart command grammar. Two obsolete Python source-text assertions from the previous options release moved to actual rendered component tests: unusable-quote visibility and settled-price/last-trade recommendation gating.

Offline Playwright acceptance (`scripts/smoke_little_rzy.py`) intercepts every API request, enables SMA 5/10 and RZY overlays, verifies matching chart grid and research-only status, and checks desktop/mobile rendering without horizontal overflow or browser exceptions. It does not validate live provider data. Screenshots are local `/tmp/little-rzy-desktop.png` and `/tmp/little-rzy-mobile.png`.

Reproduce synthetic chronological comparisons using the command in [parameters](little-rzy-parameters.md). The checked-in [CSV](little-rzy-synthetic.csv) and [report](little-rzy-synthetic-report.json) exercise the CLI and split policies. They are not market evidence, and low/zero counts are not substituted with invented performance claims.

Final suite counts and production checks are recorded separately in the deployment record after completion. Required outstanding evidence: real archived completed-bar quality, provider delay/execution basis and short borrow data, plus prospective outcomes. These cannot be closed by unit tests or synthetic backtests.

Completed checks before release staging:

- Market-data full suite: 5,460 passed, 21 skipped. The subsequent timestamp/boolean guard and affected engine/workflow/enforcement/options checks: 103 passed.
- Technical-analysis full suite: 67 passed.
- Frontend full suite: 669 passed across 51 files; final RZY panel/geometry subset: 7 passed. TypeScript and production compilation passed.
- Required PostgreSQL integration target: 3 passed (news impact, DDL guard, RZY workflow).
- Offline browser acceptance passed on desktop and mobile with no page exceptions.

The remaining backend suite results and actual rollout are recorded in the deployment record; no deployment claim is made by these test results.
