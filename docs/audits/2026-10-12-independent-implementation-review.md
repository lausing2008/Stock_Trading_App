# Independent implementation review

Reviewed checkout `6e25c43c` on October 12, 2026, including the product-impact implementation,
the enrolment review, and the subsequent calibration/startup-DDL changes in `28c04853`.
This is a code and local execution review. Production was not queried or modified.
Application code was left unchanged.

## Findings

### P1 — every recognized product event fails to persist on PostgreSQL

`services/market-data/src/services/news_impact_watch.py:98` assigns
`official_spec_and_provider_observation` to `NewsImpactEvent.evidence_status`. The value is
38 characters. Both `shared/db/models.py:3364` and migration 005 declare `VARCHAR(32)`.
The immediate `session.flush()` fails before receipts, in-app notices or outbox messages are
created, including for a historical event. This is consistent with the reported zero-event
count, but that production cause was not independently confirmed from logs.

**Reproduction:** ran the shipped `_news_impact_probe.py` with its SQLite engine replaced by a
disposable PostgreSQL 15 engine, with no application-code changes. The first event insert raised
`psycopg2.errors.StringDataRightTruncation: value too long for type character varying(32)`.
SQLite does not enforce the declared varchar length, so the existing probe misses this.

Use a status value that fits the existing schema, or widen the column with an upgrade for
already-deployed tables. Merely changing migration 005 or the ORM does not alter existing tables.
Exercise the actual producer on PostgreSQL in the regression test.

### P1 — the opt-in default is enforced in settings but not in delivery

The registry now correctly declares `product_impact.default_enabled = False`, and
`api/alerts.py` correctly displays that default. However, the producer and outbox worker call
`scheduler._may_send()`, which still returns `True` when there is no preference row
(`scheduler.py:343`) and on a preference-query error. It never reads the registry default.
An account can therefore see the channel as disabled in settings while the sender admits it.
`_filter_by_alert_pref()` also retains the old absence-means-subscribed convention.

**Reproduction:** executed the shipped `_may_send` function with an active emailed user and
a query returning no preference row: `product_impact -> True`. Then ran the real SQLite
producer probe using the real scheduler gate instead of its injected preference lambda:
the user with no row received `email_queued`. No real email was sent.

The hardcoded October 7 event is stale today, so this does not establish an unauthorized
production send. It does show the consent repair is incomplete for fresh-event delivery.
The separate `option_opportunity.enqueue_if_eligible` helper checks explicit opt-in itself;
that helper does not share this particular defect.

Centralize effective preference resolution and use it in settings, enqueue and dispatch.
Test absent, true, false and lookup-failure cases against the actual sending gate.

### P2 — an opted-in user without email cannot receive the promised in-app notice

`news_impact_watch.py:118` calls the email-specific `_may_send()` before creating an
`AppNotification`. `_may_send()` rejects a user without an email address, so the
`in_app_only` branch at line 128 cannot be reached for a normal missing-email account.
The receipt incorrectly calls this `preference_disabled` even with an enabled preference.

**Reproduction:** added an explicit enabled preference for the no-email user in the shipped
SQLite fixture, then used the real scheduler gate. The result was `preference_disabled`,
with no in-app notice, instead of `in_app_only`. The existing test's injected lambda omits
the email check and therefore exercises behavior the live caller cannot produce.

Resolve activity and channel preference separately from email-address eligibility; then
create the in-app notice and decide whether email is possible.

### P2 — metadata creation bypasses the new migration lock timeout

`shared/db/session.py:136` calls `Base.metadata.create_all(bind=engine)` directly.
The timeout is installed only by `_ddl_begin()`, so metadata creation gets no table-lock
timeout. The advisory lock serializes app startups, but does not protect them from locks
held by ordinary database clients. Creating a missing table with a foreign key to a locked
existing table can block the first startup and hold the advisory lock while the others wait.
The idle-in-transaction timeout does not time out a statement actively waiting for a lock.

**Reproduction:** executed the shipped `_create_schema` body with real SQLAlchemy metadata
against disposable PostgreSQL. One session locked a parent table; metadata creation attempted
to add a child referencing it. The creation connection reported `lock_timeout = 0` and only
stopped because the diagnostic engine had a separate one-second `statement_timeout`.
The resulting SQLSTATE was `57014`, not a lock-timeout failure from the application setting.

Pass a connection from `_ddl_begin()` into `create_all`, so its DDL uses the same timeout and
retry contract. Test this real metadata path rather than replacing `_create_schema` entirely.

### P2 — isolated migration lock failures are swallowed before the retry loop

`shared/db/session.py:1155` catches every exception from `_apply_isolated_ddl()` and only
prints a warning. This includes `55P03` lock timeouts on required broker-submission columns
and the options-equity `mark_evidence` column. Consequently `init_db()` can return success
without the missing columns and without retrying, despite the new documented guarantee that
contention retries and ultimately fails startup. The known duplicate-index exception does
not justify treating required column failures the same way.

**Reproduction:** executed the shipped `init_db` and `_apply_isolated_ddl` bodies with a
DDL context that raises a lock-timeout exception. All four statements logged warnings once;
`init_db` returned successfully with no retry. The existing guard probe replaces
`_create_schema` with a function that raises directly, bypassing these catches.

Propagate contention errors to the outer retry loop; distinguish optional index conflicts
from failures to apply required schema changes.

## Scope and validation

The new scheduler job is classified and registered under the alerting guard. Model metrics
expose suppression reasons and held-out counts; the original low model quality is not thereby
improved. The Surface rule handles one reviewed milestone, not future arbitrary product news.
The option playbook remains explicitly research-only, and actionable capture still has no
producer or verified live quote/account evidence. These are documented limits, not completed
live-trading capabilities. The newer checkout contains the AQ04 calibration patch and the
AQ06/AQ07 wiring; the earlier message listing those as unstarted is stale.

All twelve backend service suites passed: **8,621 passed, 19 skipped**. The combined
`make test-all` invocation stalled in an API-gateway asynchronous test under the sandbox;
that process was stopped after the first eight service suites completed. The gateway and
remaining three backend suites passed when run outside that sandbox with bounded runtimes.
Frontend: **573 passed** and typecheck passed. Disposable PostgreSQL/Redis integration suite:
**94 passed**. These green results do not cover the direct reproductions above.

Direct reproductions used local fixtures and a disposable PostgreSQL container, not
production, real recipients, or brokerage endpoints. The review PostgreSQL container was
removed. The only workspace change is this review document.

---

# Remediation — all five fixed, 2026-10-12

Each finding was reproduced before being fixed, and each fix was then re-tested by reinstating
the defect to confirm the new test fails. Reproductions used a disposable PostgreSQL 16 and
local fixtures; production was not touched during the work.

## P1 — the 38-character status in a 32-character column

**Widened, not shortened.** `evidence_status` names *what the evidence is*; trimming the value
to fit would make the record less legible to protect a column width that was itself arbitrary.
`String(32) -> String(64)` in the model and in migration 005 — **plus an `ALTER TABLE` in
`_run_migrations()`**, because the table already exists in production and neither the ORM nor a
migration file alters an existing one. That is the same `create_all()` class this codebase has
hit repeatedly, and fixing only the first two would have looked like a fix and changed nothing.

## P1 — the opt-in default was enforced in settings but not in delivery

This was my own incomplete repair, and the shape of the miss is worth stating: I fixed the
*settings display* and the *registry*, reported the consumer as failing closed, and never
touched the sender. `_may_send()` kept returning True for a missing row. An account could see
the channel switched off and be sent it anyway.

The rule now exists **once**, in `common.alert_prefs.effective_enabled()`, and the settings
endpoint, the single-recipient gate and the bulk filter all call it. Three readers previously
had three implementations; that is how a declared default ends up being honoured by one of them.

Failure falls back to the **declared default**, which is deliberately not the same as "fail
open". For the long-standing types that still *is* fail-open and the original reasoning holds.
For an opt-in channel it is fail-closed, because there the unrecoverable mistake is sending.

The bulk filter needed inverting rather than tweaking: `WHERE enabled = FALSE` and remove cannot
express "keep only those who said yes" — in that shape absence is indistinguishable from
consent.

## P2 — an opted-in user without an email could not get the in-app notice

Audience and channel are now separate questions (`may_notify` / `may_send`). The receipt also
stops reporting `preference_disabled` for someone whose preference was enabled; "we have no
address for you" and "you asked us not to" are different facts.

## P2 — metadata creation bypassed the lock timeout

`create_all()` is now bound to the `_ddl_begin()` connection, so the statement most likely to
block runs under the same timeout and retry contract as everything else. Unbound it would wait
indefinitely **while holding the startup advisory lock**, which is the convoy one layer down.

## P2 — isolated migration failures swallowed contention

Contention now propagates from `_apply_isolated_ddl` and both `_apply_once` handlers. The
existing catch stays for its real purpose — a statement that *cannot* succeed, such as a
duplicate index, where dying at startup is worse than continuing. A lock timeout is the
opposite case: the statement is fine and would apply on retry.

## Where the coverage actually was

Two new PostgreSQL suites, both wired into `make test-integration` and both **REQUIRED**, so a
missing container fails the target instead of skipping into a green summary line:

- `test_news_impact_postgres.py` — the producer and the real gate on the production engine.
- `test_ddl_guard_postgres.py` — startup DDL against a lock another session actually holds.

**The SQLite probe's injected `may_send` lambda was deleted.** It implemented a different rule
from the shipped gate — one preference row, no registry default, no email check, no activity
check — so the suite was green on behaviour the live caller could not produce. The probe now
calls the real gate, which is possible because the rule moved to `alert_audience`, a module that
imports nothing heavy.

**Four source-text tests were rewritten as behavioural.** They matched literal strings in
`_may_send`'s body and broke the instant the rule moved while behaving identically — the tell
that they pinned spelling rather than decisions. One of them, `test_an_undeclared_default_enrols
_nobody`, *could never have caught the defect that shipped*: the sender kept its own copy of the
rule in a file that test never read.

### Reinstatement checks

| Defect reinstated | Caught by |
|---|---|
| `String(32)` | `StringDataRightTruncation` on the first insert |
| missing row means subscribed | 6 assertions, incl. the no-row account reaching `email_queued` |
| audience fused with channel | in-app notice missing; receipt reads `preference_disabled` |
| `create_all` unbound | hung past the budget — never raised |
| contention swallowed | isolated DDL warned instead of raising; no retry |

One attempt did **not** catch its defect and had to be rewritten: the first DDL probe called
`create_all` itself rather than the shipped `_create_schema()`, so reverting the real function
changed nothing it executed. The "contention" it observed was the `ALTER` on the next line,
which was already inside `_ddl_begin()` and was never the question. That is the third time this
session a test re-implemented the rule it was meant to check.

### Suites

8,620 backend across twelve services, 573 frontend plus typecheck, 94 integration, and the 2 new
PostgreSQL schema/lock tests. The review's note that `make test-all` stalls in an api-gateway
async test under its sandbox did not reproduce here; the full run completed.

## Deployment verification (commit `c1b082ed`)

15/15 containers healthy, **0/12 drifted or errored**, tier 434 present in the served chunk
(`/app/.next/static/chunks/pages/improvements-8cc9f6cbc9057808.js`), pages 200, 0 blocked
sessions and 0 idle-in-transaction beyond five minutes.

**The schema fix is confirmed by production data, not by inspection.** `news_impact_events`
now reports `varchar(64)` on the already-existing table, and the table holds **1 event where it
previously held 0** — the producer persisted for the first time. The receipt reads
`historical_no_alert / event older than four hours when discovered`, and `notification_outbox`
holds 0 `product_impact` rows, so nothing was sent.

**The consent fix is confirmed against the live gate**, called with the production database:

```
sample account has a preference row: False
product_impact    (opt-in, no row) -> False
option_opportunity(opt-in)         -> False
signal      (long-standing, no row)-> True      <- unchanged
price_alert (essential)            -> True      <- unchanged
```

`alert_preferences` is **empty platform-wide**. That is what made this defect consequential
rather than theoretical: with no rows anywhere, every account is governed purely by the default,
so a sender that treated absence as consent would have enrolled every watchlist holder of the
three symbols. It also means the opt-in channel currently has an audience of nobody, which is
the correct starting state for a channel nobody has been offered yet.

### A deploy warning that was worth checking and was not what it looked like

The deploy script flagged `iowait 41.2%` and named the EBS credit-drain pattern that made this
instance unreachable for ~50 minutes on 2026-09-10. Measured rather than assumed: disk
utilisation was **0.8–4.2%** with reads under 1 MB/s, and three minutes later iowait was 0.00%
with load falling 3.46 -> 1.24. Thirteen images had just been rebuilt, so every container was
re-reading its working set through a cold page cache.

High iowait with ~1% device utilisation is not an I/O shortage, and the two cases are worth
telling apart: the real incident showed sustained high *utilisation*. The warning did its job by
prompting a look, and no change was made to it on the strength of one benign occurrence.

One pre-existing pattern was noticed in passing and deliberately not changed: the options-history
capture holds a transaction open across per-symbol work, so `SELECT max(as_of) ...` sessions sit
`idle in transaction` for 30–35s. They are well inside the new five-minute timeout and are not
blocking anything; recorded because that is the shape the 2026-10-12 convoy's orphan had.
