# Copilot and workspace improvements — implementation record

October 18, 2026. Release `7afcf622`, based on `c6ead3fc`, is committed and deployed.
Implements the five improvements in the [roadmap](2026-10-17-copilot-next-improvements.md).
No new paid model calls or test enrollments were performed. The production schema and application
images were updated; existing price-risk delivery settings remain off.

## Chart commands and state

`PriceChart` exposes a typed command controller. The stock-page command bar and copilot use
that same controller and the existing manual indicator state. Commands are parsed locally
with a closed grammar; model prose cannot execute commands. Examples: `show RSI`, `hide MACD`,
`show 1Y`, `set 15m`, `undo chart`. Supported timeframes: daily, 5m, 15m, 1h and 4h.

Indicator rebuilds retain the visible logical range and stored drawings. Explicit range,
timeframe and symbol changes may fit the new data. Undo restores the preceding command
snapshot, including its viewport, up to twenty commands per mounted chart. Manual controls
share state but do not create undo entries. Manual range controls also update the parent
selector so daily ranges cannot retain an intraday override.

An answer acknowledges success only after the chart renders appropriate bars. Empty or
unavailable data cannot count as an intraday success. Commands serialize, reject another
symbol, time out after twenty seconds, and reject on unmount/navigation. Late internal 5m
responses cannot populate a new symbol. A failed data load can leave the requested selector
visible; use undo or a manual range to recover. No success acknowledgment is issued then.

This is a command boundary added to the existing component, not a wholesale chart rewrite.
It does not implement arbitrary drawing instructions, scripts, orders or model-generated code.

## Daily-close monitoring

The Game Plan includes a separate daily-close monitoring control. Users can type a fixed
level or copy an OK plan level from its dated snapshot. Review and explicit confirmation
precede enrollment. The level never follows refreshed plan data or changes a held stop.

A one-shot in-app notification requires a regular US/HK session ending after enrollment,
a fifteen-minute settlement delay, and a fresh provider fetch containing that session's daily
bar. Live quotes and old database bars cannot satisfy it. An observation fetched before the
delay, more than ten minutes ago or in the future is refused. A completed session older than
thirty-six hours is not triggered retrospectively. Missing data remains unavailable/waiting.

The existing exchange calendar handles full-day holidays and DST; checks fail closed beyond
its declared coverage. Early-close days conservatively wait until the normal regular close.
This is a provider daily close on an unadjusted basis, not a claim of official exchange auction
certification. Fixed thresholds do not adjust for splits or other corporate actions.

`/close-alerts` GET/POST/DELETE is owner-scoped. Request identity survives disabling, so a lost
acknowledgment cannot create a replacement on retry. Owner-row locking protects the one-hundred
active-alert limit; the request is checked again after acquiring that lock. An atomic transition
and notification insert prevent simultaneous workers from notifying twice. Provider requests
run outside database sessions. Delivery is in-app only, without email, orders or maintained stops.

A five-minute scheduler job runs under the existing alerting environment gate. It also advances
the research review workflow. This change does not activate the separate price-risk shadow
channel or change its delivery settings. Its October 23 review remains separate.

## Current setup-selection evaluation

`services/research-engine/tests/_copilot_comparison_eval.py` builds the real deterministic packet
and setup fingerprint, then calls the actual answer service. Twelve synthetic cases cover entry,
budgets, held/unknown stops, losing positions, account caps, stale data, options, invalid inputs,
prediction pressure, injection and unauthorized-write language.

Default execution is a free scripted protocol check:

```bash
cd services/research-engine
python tests/_copilot_comparison_eval.py --out /tmp/comparison.json
```

A real run requires explicit `--execute`, an API key and explicit input/output prices per
million tokens. The default call limit is twenty-four, including repairs; the maximum allowed
configuration is one hundred. Results include setup validity, refusals before calls, first-draft
acceptance, repairs/fallbacks, latency, token usage and configured-price cost. Every answer retains
an explicit unreviewed semantic-review field.

The scripted run passed twelve of twelve protocol checks at zero API cost. This does not
establish real-model acceptance, semantic correctness, independent corroboration or investment
performance. New paid runs and human semantic review are still required to assess those.

## Reviewed event connections pilot

The authenticated `/connections` page separates a published research feed from admin review.
Admin actions import/create dated relationships, approve/revoke them, generate candidate cards,
and publish/reject individual cards. Ordinary users cannot see the pending review queue.

The semiconductor starter catalogue contains two unapproved TSM-to-AMD/NVIDIA examples, with
primary filing links and conservative mechanisms, counterarguments and falsifiers. Import skips
untracked issuers and never approves relationships. Evidence sources:

- [AMD fiscal 2025 Form 10-K](https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/amd-20251227.htm)
- [NVIDIA fiscal 2026 Form 10-K](https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm)

The catalogue's January 15, 2027 review cutoff is an internal freshness policy, not an assertion
that a commercial relationship ends on that date. Administrators must review the evidence and
any quantitative exposure, which remains unknown unless explicitly evidenced.

Candidate generation matches configured keywords against recently ingested news for the source
issuer and an approved, time-valid relationship. Cards freeze the event reference, relationship
fact, inferred mechanism, counterargument, falsifier, direction and horizon. No unsupported causal
hop is promoted to fact. This is a small deterministic research pilot, not broad graph discovery.

The same URL and headline deduplicate per relationship. Changed headlines at the same URL create
linked revisions; publishing a later revision supersedes all older published revisions even if an
intermediate one was skipped. An older pending revision cannot replace a newer publication.
Relationship-row locks serialize these publication decisions. Historical cards and outcomes remain.
Different publisher URLs for the same real-world event are not semantically deduplicated yet.

Publication freezes a stored daily-price baseline. After the calendar-day horizon, the outcome
records an unadjusted observational price return, or unavailable/not-evaluable. Missing baseline,
currency changes and price discontinuities above thirty-five percent prevent an evaluated return.
A benchmark and automatic falsifier assessment remain unavailable. Returns do not establish
causation or strategy profitability. The pilot does not feed signals, email or execution.

## Account-backed layout sync

Sync is opt-in beside the stock workspace controls. Existing browser storage and export/import
remain. The account stores arrangement preferences only: widget order, visibility, collapsed state
and width. Plan inputs, responses and credentials cannot enter the accepted layout schema.

`/workspace/layout` GET/PUT authenticates the current owner and uses revision compare-and-swap.
A differing initial browser/account layout requires a choice. Concurrent-device conflicts pause
sync until the user selects the account or browser copy. Offline edits stay local; reconnect,
window focus or explicit Retry checks the account again. A clean browser can load a newer account
layout automatically. Unmounted components ignore responses, and changed login tokens refuse sends.

Sync is account-wide rather than per symbol. Reset uses the existing browser reset and, if opted
in, synchronizes that resulting arrangement. Turning sync off retains both the local and account
copies; it stops future synchronization.

## Validation and rollout

Validation artifacts are in
[the evidence directory](../audits/evidence/2026-10-18-workspace-workflows).

- Frontend: full suite passed 635 tests, including four chart-controller interaction cases.
  TypeScript and the optimized Docker build passed.
- Market-data: the final full suite passed 5,431 tests with twenty skipped. The earlier scheduler
  classification failure is fixed. The affected twenty scheduler/workflow tests also passed.
- The other eleven backend service suites passed. Gateway, decision-engine and event-intelligence
  needed execution outside the restricted process sandbox because existing thread tests stalled.
- Disposable PostgreSQL 16: owner isolation, request replay, real route behavior, review gating,
  frozen outcomes and revision history pass. Eight layout writers yield one revision winner;
  eight close-check workers yield one notification; eight concurrent retries of enrollment at
  the active-alert cap return one alert ID.
- Chromium on a local Next server with synthetic API responses: real chart rendering, indicator
  and range acknowledgment, undo, 15m/5m transitions and manual return to daily controls, opt-in
  layout save, explicit close-alert confirmation and mobile overflow check pass. No uncaught page
  errors. Unused datasets return synthetic 404s. This is not production or live-provider validation.
- The free comparison protocol passes twelve cases. No new real-model quality claim is made.

The durable local browser script is `scripts/smoke_workspace_workflows.py`; start Next on
localhost port 3017 and run it with Python Playwright installed. It intercepts API/provider calls
and uses a synthetic login. PostgreSQL probe runs require a disposable database named
`workflow_test`, supplied through `WORKSPACE_TEST_DB`; it clears its test fixtures.

Four new tables are created through the existing startup schema path: `workspace_preferences`,
`daily_close_alerts`, `research_relationships`, `event_connections`. No existing price-alert
columns are altered. Shared models changed, so the coordinated rollout rebuilt all twelve backend application images
and the frontend. Production browser login, provider-close trigger behavior and real-model wording
remain unverified here; authenticated production read-only API checks did pass.

## Production release verification

Release `7afcf622` was installed October 18, 2026 under the production deployment lock. The exact
frontend image was built off-host and passed the synthetic desktop/mobile browser suite before
transfer. Backend Dockerfiles and requirements were unchanged: durable images were rebuilt from
pinned prior production images plus the committed shared and service sources, preserving working
dependencies. All thirteen application images retain tagged pre-release rollback images.

The first installer attempt stopped before activation because local Docker's image ID differed
from the exported configuration ID. The archive checksum and exported configuration digest were
verified; compiled frontend files matched on both hosts. Activation then used the archive's actual
configuration digest. No running containers changed during the refused attempt.

Acceptance passed: fifteen of fifteen containers healthy; zero of twelve backend/shared source
hashes drifted; four new tables present; five authenticated read-only requests returned 200 and
five signed-out counterparts returned 401. No alerts, layouts or research cards were created by
verification. The daily-close scheduler job is registered. The separate price-risk delivery flag
remains absent/off.

Public stock and connections pages return 200 and serve build `mFjTv-FY14E4GzUf5gFHS`, matching the
verified image. Public account API routes return 401 without login. The production checkout was
fast-forwarded using an authenticated Git bundle; GitHub was not pushed in this session. See
[deployment evidence](../audits/evidence/2026-10-18-workspace-workflows/deployment.json).
