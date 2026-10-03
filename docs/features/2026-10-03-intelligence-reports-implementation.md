# Intelligence reports — implementation (first vertical slice)

Built 2026-10-03 against the [templates](2026-10-02-intelligence-report-templates.md) and the
[implementation prompt](2026-10-02-intelligence-reports-claude-prompt.md). All four report types
generate, persist, version, render and export. **Deterministic only — no LLM on this path.**

## Where it lives, and why

| Concern | Location | Why there |
|---|---|---|
| The contract | `shared/intelligence/report_contract.py` | shared, so any service can produce a report that validates the same way |
| Snapshots | `IntelligenceReport` in `shared/db/models.py` | a new table; `research_report_cache` is a one-row-per-symbol CACHE whose uniqueness constraint is the opposite of a versioned snapshot |
| Adapters | `services/research-engine/src/intel_reports/adapters.py` | read-only over already-ingested data |
| Generators | `…/intel_reports/generators.py` | report ORCHESTRATION is a research capability; source services keep owning their data |
| Persistence | `…/intel_reports/store.py` | immutability/versioning/idempotence are storage properties, not caller discipline |
| API | `…/api/intelligence_routes.py`, prefix `/intel` | registered in the gateway's `_ROUTES`; the route-coverage test guards it |
| UI | `frontend/src/pages/intelligence-reports.tsx` | a new page under the existing **Reports** nav group |

**Placement answer (asked directly).** Under the **Reports** group, as its own page — not more
tabs on `/reports`. That page is 902 lines of live-data aggregation across seven tabs; these are
generated, versioned, frozen documents with history, diffing and export. Same nav home, separate
page, because they are a different kind of object.

**No new service, news engine, gateway, frontend app or prediction model was created.** No
Airflow/Kafka/Snowflake/dbt. No new provider calls at all: every adapter reads what the platform
has already ingested, so a report costs zero provider quota and cannot be blocked by a rate limit.

## The contract is the product

Four failure modes the templates warn about are all one shape — a field quietly becoming
something it is not. They are prevented at construction rather than tested for afterwards:

- **A `Field` with no value and no state raises.** "Empty because nobody looked" and "empty
  because the provider has nothing" cannot both render as a blank cell.
- **Five distinct empty states** (`UNKNOWN`, `UNAVAILABLE`, `STALE`, `CONFLICTING`,
  `NOT_APPLICABLE`), each requiring a reason. One generic "missing" would throw away the only
  information an empty cell carries.
- **Five separate timestamps per evidence record** — published, first-available, retrieved,
  observed period, revision. A retrieval time cannot establish what was knowable at a cutoff,
  which is the whole of look-ahead prevention.
- **Statement class on every field** — observed / calculated / interpretation / conditional /
  model forecast. A reader never has to guess which they are looking at.

`status` is derived from measured coverage, and is COMPLETE only when every field resolved —
deliberately not a threshold, because a report calling itself complete while hiding a gap is
worse than one that admits it.

## What each report answers today

| | Resolved | Reported unavailable, with reason |
|---|---|---|
| **Market Outlook** | 13 / 18 | volatility, rates/credit/FX, macro, liquidity, aggregated positioning |
| **Stock Outlook** | 15 / 20 | fundamentals series, estimate revisions, valuation, options positioning, news join |
| **Pre-Earnings** | 12 / 19 | guidance, expected move, consensus snapshot metadata, fiscal period |
| **Post-Earnings** | 14 / 22 | guidance change, options reaction, accounting basis, pre-report link when absent |

Those are not omissions. Each names the dimension and why this platform cannot source it yet, so
a reader knows the report looked.

### Three places where the honest answer is "no"

**Fiscal period is always UNKNOWN.** `EarningsEvent.fiscal_quarter` is derived from the
period-end calendar month (`(month - 1) // 3 + 1`), which is wrong for every non-calendar fiscal
year — MU's Q4 is stored as "Q3"
([incident](../incidents/inferred-fiscal-period-mislabels-non-calendar-years.md)). The templates
require source-confirmed periods. Repeating the stored value would put a known-wrong label on the
one field identifying *which* results these are.

**No forecast probability is ever emitted, and no horizon outlook either.** No model here has a
demonstrated calibration for these horizon definitions. (An earlier version of this doc cited a
pooled "flat 36.5–43.9%" figure as justification; that statistic was withdrawn as a pooling
artifact in the checkpoint review and is not restored here — the absence of demonstrated
calibration for *these* horizons is the reason, and it stands on its own.)

Stronger still after review: the three horizon fields originally restated one
latest-close-versus-20-bar-average rule under three labels. Three fields that look independently
derived but share one daily heuristic are three times the confidence with none of the evidence,
so the report now gives **one** `observed_daily_structure` and marks each horizon UNAVAILABLE
with what would be needed. Reporting less is the correction.

**A missing pre-earnings baseline is recorded, never reconstructed.** A baseline built after the
release would contain information the original could not have had; scoring against it is hindsight
wearing a forecast's clothes. `frozen_pre_report()` requires BOTH `cutoff_at` and `generated_at`
to precede the release, so a late-written "pre" report cannot qualify.

## Storage guarantees

- **Immutable.** A revision inserts a new row pointing at what it supersedes. Nothing updates a
  payload — the pre-earnings freeze is worthless if it can be edited after the results land.
- **Versioned** inside the transaction, so two concurrent generations cannot both claim v3.
- **Idempotent on an input fingerprint**, not on elapsed time: regenerating from unchanged
  evidence returns the existing report; a genuine input change creates a linked new version
  however little time passed.
- **Private by ownership.** `user_id` NULL is public context; a portfolio-bearing report is
  owner-only and is never served by the public read paths. They do not share a row, so they
  cannot share a cache entry.

## Review corrections (2026-10-03)

An implementation review found five correctness gaps, all reproduced here before fixing and all
now closed — see [the review](../audits/2026-10-03-intelligence-report-implementation-review.md).

| | Gap | Fix |
|---|---|---|
| IR-01 | The API compared the baseline against the **post-report's own generation time**, which every post-release report trivially satisfies | bound to the event's release boundary; a pre-report is refused once the release is known, and on the release day itself, since only a date is stored |
| IR-02 | Evidence lists were empty and input selection ignored the cutoff — a 25 Sept report read the 2 Oct close | `cutoff` is a required argument; every observation used is recorded; a report whose citations do not resolve is refused at save; stale prices now degrade what is derived from them |
| IR-03 | Three horizons restated one rule, and the bearish invalidation named its own supporting condition | one observed daily structure; horizons UNAVAILABLE; direction-aware invalidation |
| IR-04 | The surprise table used the **mutable current** estimate while the verdict used the frozen one — a −11.79% "miss" beside a verdict of "above" | one frozen expectation drives both; the later revision is shown separately and dated; `RECONCILED_RESULTS` is never claimed, and a thesis with no directional claim scores `not_evaluable` |
| IR-05 | Version allocation had no uniqueness or locking | unique index on (subject, type, owner, version) with COALESCE for public NULLs, plus bounded retry with jittered backoff |

Other corrections accepted from the review: `decision_engine_assessment` renamed
`signal_engine_assessment` (it reads the `signals` table, not a risk-checked decision); the
macro/liquidity/news gaps reworded as *not joined to this report* rather than asserting
platform-wide absence (event-intelligence does carry an economic calendar); breadth now reports
**both** denominators, since participation (above/covered) and coverage (covered/universe) are
different ratios; and bar-count fields renamed `return_N_bars`, because counting stored rows
does not establish exchange sessions.

## Acceptance

18 scenarios run end to end through the real generators and real persistence —
[evidence](../audits/evidence/2026-10-03-intelligence-reports-acceptance.json), captured on
**PostgreSQL 15**, and the same scenarios also run on SQLite so `make test` needs no server
(`_meta.engine` records which produced a given result). 12 pytest tests assert the verdicts.

The sharpest one: a pre-earnings report is frozen with consensus EPS 1.50, the release lands,
**the stored estimate is then revised to 1.95**, and the post-earnings verdict still scores
against 1.50 with the frozen payload byte-identical.

Ten sabotages across both rounds: the cutoff check removed from `frozen_pre_report`, `save()`
mutating instead of inserting, `fiscal_period` trusting the inferred label, the zero-estimate
guard removed, `daily_bars` ignoring its cutoff, the evidence book not recording, staleness not
propagating, the post-release guard removed, the surprise table reverting to the mutable
estimate, and the unique index dropped.

**One sabotage found a gap in my own test rather than the code**: removing the price evidence
record failed nothing, because checking only that no citation *dangles* is trivially satisfied by
a field that cites nothing. The test now names the load-bearing fields that must carry evidence.

**And the concurrency race found a limitation in my own fix**: four concurrent writers exhausted
a three-attempt retry budget, because threads that collide once re-read the same maximum and
collide again. Retries are now eight with jittered backoff — four writers, four unique versions,
three consecutive runs.

`make test: all services passed`; research-engine 81 → 93. Frontend 447, `tsc --noEmit` clean.

## Not done in this slice, and deliberately

- **No LLM narration.** Deterministic tables are the report; narration is a later optional layer.
- **No scheduling and no email.** On-demand only. Pre-open/post-close briefings, pre-earnings
  refreshes and follow-through reviews are specified in the templates and not activated.
- **Browser rendering still not verified.** The page typechecks and its logic is covered, but no
  browser check was performed — stated rather than implied, and it remains the review's
  outstanding item 3.
- **No outcome scoring beyond the pre/post join.** Forward evaluation needs matured horizons.
- Guidance, consensus snapshots with contributor counts, per-leg option evidence and a news join
  are the highest-value next inputs; each is currently a named UNAVAILABLE.

Nothing here changes signal thresholds, paper trading, broker intent or exits, and no flag was
activated.
