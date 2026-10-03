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

**No forecast probability is ever emitted.** No model on this platform has a demonstrated
calibration for these horizon definitions — the 2026-09-30 checkpoint measured confidence bands
flat at 36.5–43.9% over n=19,256. Each horizon carries a conditional outlook, its key condition
and its invalidation instead, and `forecast_probability` is explicitly null with that reason.

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

## Acceptance

11 scenarios run end to end through the real generators and real persistence —
[evidence](../audits/evidence/2026-10-03-intelligence-reports-acceptance.json), captured on
**PostgreSQL 15**, and the same scenarios also run on SQLite so `make test` needs no server
(`_meta.engine` records which produced a given result). 12 pytest tests assert the verdicts.

The sharpest one: a pre-earnings report is frozen with consensus EPS 1.50, the release lands,
**the stored estimate is then revised to 1.95**, and the post-earnings verdict still scores
against 1.50 with the frozen payload byte-identical.

Four sabotages: the cutoff check removed from `frozen_pre_report` (a late report becomes the
baseline), `save()` mutating instead of inserting (history destroyed), `fiscal_period` trusting
the inferred label, and the zero-estimate guard removed (crashes with `ZeroDivisionError`).

`make test: all services passed`; research-engine 81 → 93. Frontend 447, `tsc --noEmit` clean.

## Not done in this slice, and deliberately

- **No LLM narration.** Deterministic tables are the report; narration is a later optional layer.
- **No scheduling and no email.** On-demand only. Pre-open/post-close briefings, pre-earnings
  refreshes and follow-through reviews are specified in the templates and not activated.
- **Browser rendering not verified.** The page typechecks and its logic is covered, but no
  browser check was performed — stated rather than implied.
- **No outcome scoring beyond the pre/post join.** Forward evaluation needs matured horizons.
- Guidance, consensus snapshots with contributor counts, per-leg option evidence and a news join
  are the highest-value next inputs; each is currently a named UNAVAILABLE.

Nothing here changes signal thresholds, paper trading, broker intent or exits, and no flag was
activated.
