# Little RZY validation — 2026-10-19

The shipped pure engine is exercised directly: confirmed-pivot timing, earliest ties and ambiguous extrema, population BB/Wilder ATR arithmetic, frozen geometry, actual default-threshold arming and triggers, next-open fills and gap rejection, stop-first ordering, partial/runner restart recovery, short borrow refusal, sequence confidence/filtering, pre-confirmation target/invalidation, expiry and reversal watches. Streamed, replayed and JSON-checkpoint-resumed event/state output must agree exactly. A final edge-case test also verifies equivalent timezone representations deduplicate, actual instants determine order, and naive timestamps/boolean numbers are refused.

The workflow probe uses real shared ORM classes, engine, outbox and central audience gate on SQLite and disposable PostgreSQL. Only the database connection and external email-provider boundary are replaced. It checks persistent deduplication, historical bootstrap suppression, watchlist ownership, independent channel consent, opt-out after enqueue, once-only fake-provider send, actual digest window/calendar/expiry, adjusted OHLC and stale/missing/forming bars. These automated tests sent no real email. Separately, the production scheduler sent two real messages to `lausing` on October 19 (provider acceptance observed; inbox delivery unobserved); see [deployment record](little-rzy-deployment.md#live-email-activity--corrected-2026-10-19-pacific). PostgreSQL is required in `make test-integration-pg-schema`; absence cannot silently pass that gate.

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

The broader gateway gate initially rejected the nested `/stocks/rzy` router by comparing its entire prefix against a table keyed by the first path segment. The coverage test now calls the shipped `_upstream` resolver; four behavioral cases also prove the nested routes use the existing `stocks` upstream and reject unauthenticated callers. No gateway runtime routing change was needed. Sandboxed asyncio tests stalled creating their local event-loop sockets; the gateway suite was rerun with the required local socket permissions.

Final causal review found that the next-open liquidity check read that entry candle's final volume. It now uses only the completed trigger candle's volume. The new regression replays the same trigger/open with sharply different eventual entry-bar volumes and requires identical fills, alongside a low-trigger-volume refusal. Rollout was paused during frontend staging before any service restart to include this fix.

After the causal fix, all 33 engine/SQLite workflow checks pass. Reinstating the original next-bar-volume expression makes the new causal regression fail; the shipped implementation was restored immediately. The checked-in synthetic report remains reproducible and establishes no real-market performance.

A final database-load review found that persisting every scan rewrote the full checkpoint JSON even when no candle arrived. Persistence now advances only after a new bar (including bars that produce no signal), while unchanged scans issue no checkpoint UPDATE. A SQLAlchemy statement listener verifies the actual SQL in the shared SQLite/PostgreSQL probe. All 33 focused checks and all 3 required PostgreSQL suites pass with this guard; this was the pre-activation test checkpoint, not the final production channel state. Both channels were subsequently enabled for `lausing`; two live pattern messages were provider-accepted under `8636b52d`.

Reinstating the unconditional checkpoint write makes the real-SQL regression fail. The shipped guard was restored before commit.


The first production scan covered 342 streams for 114 symbols: 95 research-only 5m streams, 18 gapped 5m histories, one absent 5m history, and no stored 15m/1h rows for any symbol. This exposed a source-availability gap, not a strategy result. The adapter now derives missing 15m/1h frames from completed 5m constituents with a bounded per-symbol cache. Tests verify actual OHLCV aggregation, session alignment, forming-bin refusal, missing constituent refusal, truncated-oldest-bin handling and preservation of unverified/stale metadata. The real ORM probe checks both fallback timeframes on SQLite and PostgreSQL.

The shortened-session aggregation test failed against the shared calendar because all trading days ended at 16:00. Published NYSE early closes now live in that central module and drive both `session_bounds` and `is_regular_session`. Regression cases cover every supported early-close date, full-day/holiday contrasts and unchanged Hong Kong lunch behavior, with a coverage ratchet requiring early-close years and holiday years to agree. The shared change requires rebuilding all backend source layers; no dependency upgrade or frontend rebuild is necessary.

Final aggregation/calendar checks: 310 existing calendar-consuming cases passed across market-data, decision-engine and research-engine; required PostgreSQL gates passed 3/3. The focused engine/workflow/aggregation cases also exercise the real derived-timeframe adapter rather than a separate signal implementation.

Final engine/workflow/aggregation subset: 51 passed after the shared early-close correction. No first-scan availability counts are represented as a daily alert rate or a strategy win rate.


Final live inspection found off-grid rows at session open plus one second alongside real five-minute bars. They are not additional five-minute candles. The adapter now excludes such snapshot rows with `off_session_bar_grid`; a regression proves a snapshot with extreme price/volume cannot alter actual candle aggregation. Separately, real stored bars were revised after checkpointing (including material volume/open changes), so existing affected 5m streams correctly require an audited replay. These source revisions are not suppressed with a numeric tolerance or silently reset; stable canonical history and explicit replay remain data prerequisites.

Revision quarantine is now durable in the checkpoint and is checked again by the sender. Restoring a provider's former values cannot silently unlock a quarantined stream, and an already queued alert cannot send after quarantine. The real ORM/outbox probe verifies both behaviors, including zero fake-provider sends from the blocked item. Final affected engine/workflow/grid cases: 53 passed. These checks do not authorize resetting original event history or mixing outcomes across data revisions.

Audit clarification: all 95 previously usable 5m streams are now quarantined, with no unlock path. Only 90 derived 15m and 90 derived 1h streams are currently usable, all research-only. Real-data §11 comparisons, verified provider/delay and borrow inputs, paper fills, digest receipt, and prospective/out-of-sample outcomes remain unverified or not started. Exact bar comparison may quarantine derived streams following routine revisions; the October 20 post-session check must measure that risk.
