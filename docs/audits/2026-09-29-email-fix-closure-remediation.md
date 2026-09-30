# Email fix closure remediation — EC-01, EC-02 (2026-09-29)

Response to [the closure review](2026-09-28-email-fix-closure-review.md), which verified EF-01,
EF-02, EF-04 and EF-05 closed and raised two new findings against my own remediation. **Both are
real. Both are fixed.** EC-01 is a defect I introduced that would have made the EF-02/EF-03 retry
mechanism inert after the first service restart.

This is the fourth round on the same surface: EA-01…EA-12 (the outage), EF-01…EF-05 (three of them
regressions from that fix), and now EC-01…EC-02 (one more regression from that fix). The pattern is
worth naming: **each round's regressions came from the fix, not from the original code.**

## EC-01 — P1 — a row-mutating migration ran on every startup

**Source:** `shared/db/session.py`, the legacy price-alert closeout inside `_apply_isolated_ddl()`.

The EF-03 remediation added:

```sql
UPDATE price_alerts SET last_sent_at = triggered_at
WHERE triggered IS TRUE AND last_sent_at IS NULL AND triggered_at IS NOT NULL
```

with a comment of mine calling it *"one-shot: after this runs there are no NULL-timestamped
triggered rows left to match."*

**That reasoning is wrong, and the review caught it precisely.** It is a claim about DATA, and data
changes. `init_db()` calls `_apply_isolated_ddl()` on every startup of every one of the twelve
backend services. A triggered row with a NULL `last_sent_at` is exactly what a **failed send** looks
like — producing one is the entire purpose of the retry mechanism the same remediation had just
added.

So the next restart of any service would have stamped every genuinely-pending alert as delivered,
with **zero transport calls and no log line**, and the retry query — which requires
`last_sent_at IS NULL` — would never have seen it again.

This is worse than not having written the migration. A migration that silently destroys the evidence
of the failure it exists to disambiguate leaves the retry feature *looking* implemented and *being*
inert, with nothing anywhere to indicate it.

### Production exposure, measured before fixing

| | count |
|---|---|
| triggered, `last_sent_at IS NULL` (genuinely pending) | **0** |
| triggered, `last_sent_at = triggered_at` (legacy closeout stamp) | 60 |
| triggered, `last_sent_at > triggered_at` (a real send since the fix) | 0 |
| `applied_migrations` table | did not exist |
| most recent `triggered_at` | 2026-09-21 13:31:02 |

**No damage has occurred.** No price alert has fired since the EF-03 deploy, so there was never a
pending row for a restart to consume. This is a latent defect closed, not an incident cleaned up —
and the 0 is luck about alert timing, not a property of the code.

### The fix — two independent guards

**A real migration ledger.** `applied_migrations (name PRIMARY KEY, applied_at)`, and a new
`_apply_once(name, sql, params)`. The `INSERT ... ON CONFLICT (name) DO NOTHING` and the statement
share **one transaction**, so twelve services starting at once cannot both run it: the second blocks
on the primary key until the first commits, then conflicts, inserts nothing, and skips. Row-mutating
migrations now live in `_apply_one_shot_migrations()`, separate from `_apply_isolated_ddl()`, whose
statements are all `IF NOT EXISTS` DDL and are genuinely correct to re-run.

**A deploy watermark.** The UPDATE additionally requires `triggered_at < '2026-09-28'`. Rows
triggered before that predate `last_sent_at` recording delivery at all; rows at or after it are
unambiguous. Even on a database that lost its ledger — a restore from backup, a hand repair — the
statement can no longer touch a post-fix pending row.

The review also asked that legacy uncertainty stay distinguishable from delivery evidence. It is,
without a schema change: the closeout sets `last_sent_at = triggered_at` **exactly**, and a real
price-alert send stamps strictly later.

**CORRECTED 2026-09-29** by the [EC closure verification](2026-09-29-ec-closure-verification.md).
This paragraph originally claimed that equality "identifies the closed-out set precisely", with no
qualifier. That is wrong as stated. `check_technical_alerts` stamps a RECURRING technical alert's
`last_sent_at` and `triggered_at` to the same `fire_time` **before** delivery
(`scheduler.py`, the `if alert.recurring:` branch), so equality alone is not a legacy marker.

Measured on production the same day:

| rows where `last_sent_at = triggered_at` | count |
|---|---|
| ...and `triggered IS TRUE` — the legacy closeout set | 60 |
| ...and `triggered IS FALSE` — a recurring technical fire | **1** |
| any triggered state | 61 |

So the marker is `triggered IS TRUE AND last_sent_at = triggered_at`, and an unqualified equality
query already over-counts by one row today — a number that grows every time a recurring technical
alert fires. Even qualified, it is a **migration convention, not a delivery-state model**: it says
which rows this migration touched, and nothing about whether any of them was delivered. A non-null
timestamp is likewise not universal proof of delivery on every path. Explicit per-event delivery
provenance remains part of the outbox work, listed as still open below.

### What the review asked for that I did not build

A `delivery_status = legacy_unknown` column, and a reviewed immutable manifest of the affected
rows. Not built: the 60 rows are already stamped in production, all triggered on or before
2026-09-21, and the review's own guidance is explicit that they must not be repaired by blindly
clearing timestamps equal to the trigger time. The equality marker above preserves the same
information for the rows that exist. A separate delivery-record table is the right long-term shape
and remains open with the outbox work.

## EC-02 — P2 — a delayed alert asserted a crossing that had reverted

**Source:** `email_service.send_price_alert_email()`.

EF-02 made the retry fetch a real current quote, which was the right fix for the previous bug (it
used to substitute the configured threshold for an observation). But the renderer still wrote one
sentence joining the historical condition to the current price:

```text
Subject: Price Alert: RETRY has risen above 90.0
RETRY is now 80.0000 (risen above your target of 90.0).
Note: Delayed notification — this alert triggered at ...
```

Both facts are true. Their combination is false — 80 is not above 90. The explanatory note
underneath does not unsay the headline above it, and a reader who sees only a subject line has
already been told the wrong thing.

**The fix.** The renderer now asks whether the crossing still holds, and the retry path passes the
original `event_at` as its own field rather than only burying it in prose. When the crossing no
longer holds:

```text
Subject: Price Alert (delayed): RETRY had risen above 90.0
Earlier alert: RETRY risen above your target of 90.0 at 2026-09-28T13:31:02+00:00.
Current price is 80.0000 — back below the threshold.
```

The subject carries the tense too, since that is often all a reader sees. The HTML's big number
stops being painted green (an "above" alert's colour) on a reverted crossing — the same false claim
in colour rather than words — and its caption says *"Current price (alert has since reverted)"*.

Scoped deliberately: a crossing that still holds keeps its original wording exactly, and an
indicator alert (EA-04's descriptive-condition path) is untouched — its threshold is in different
units and there is no crossing to re-evaluate.

**The predicate is inline, and that is not a style choice.** I first wrote it as a module-level
`_crossing_still_holds()` helper, and three existing EA-04 tests immediately raised `NameError`.
`email_service.py` states in `_crossing_words`' own comment that every renderer in the module is
extracted and executed on its own by this repo's test and audit harnesses, so a module-level sibling
is out of scope exactly where the rendering is checked. The convention is load-bearing. My EC-02
tests consequently drive the predicate through the rendered output rather than calling it directly —
which is the stronger assertion anyway, since it pins what a reader is actually told.

## Verification

- `test_ec01_one_shot_migration.py` — 8 tests, running the **real** `_apply_one_shot_migrations()`
  against a **real** database in a subprocess (market-data's conftest stubs `sqlalchemy`, so no
  statement can reach a database inside the suite).
- `test_ec02_delayed_alert_wording.py` — 15 tests, rendering the **actual** function and asserting
  on the **actual** output string.
- Sabotage-verified. **The first pass exposed a weak test of my own:** deleting the ledger guard
  entirely left all 7 EC-01 tests green, because the watermark alone already covers the realistic
  pending row — so those tests were checking the watermark, not the once-only claim. Added
  `test_the_statement_does_not_run_a_second_time_at_all`, which isolates the ledger by introducing a
  second pre-watermark row after the migration has run; the watermark would match it happily and
  only the ledger can leave it alone. That test now fails when the guard is removed.

### Running the review's own script after the fix

`evidence/2026-09-28-email-fix-closure-checks.py` has two halves. Its `checks()` half — the EF
acceptance assertions — **passes**: the mixed signal batch still makes both sender calls with the
first transition pending and the second advanced, the retry still requests its own symbol and
renders the observed 123.0, a missing quote still produces zero sends, `_classify_flow_side` still
separates unknown from a measured zero, and the preference ratchet still rejects a comment-only
call.

Its `migration()` half is a **defect witness**, and it no longer reproduces:

```text
KeyError: 'legacy price-alert delivery closeout'
```

— the statement is no longer in `_apply_isolated_ddl()`'s every-startup list for it to find. That
is the intended outcome. As the previous review noted, assertions written to require a bug cannot
serve as a release gate; the behaviour they described is now covered by
`test_ec01_one_shot_migration.py` against a real database.

### Independently verified, and one scenario it tested before I did

The [EC closure verification](2026-09-29-ec-closure-verification.md) confirms both defects closed
against real databases, and raised a scenario my own tests did not cover: **a migration whose
statement fails must not leave its name claimed in the ledger.** If it did, the ledger would report
"already applied" forever and the statement would never get a second chance — a migration that
silently never runs, which is EC-01's own failure shape one step along.

Checked against this implementation: it holds, because the `INSERT ... ON CONFLICT DO NOTHING` and
the statement share one transaction, so the rollback takes the claim with it. Verified directly —
after a deliberately failing statement the ledger carries no claim, and a retry under the same name
then runs and commits.

It is now a permanent test rather than a one-off check in someone else's evidence file
(`test_a_failed_migration_does_not_leave_its_name_claimed` and its retry half). Sabotage-verified:
splitting the claim and the statement into two `engine.begin()` blocks — an easy thing to lose in a
later edit, and something nothing else in the suite would notice — fails both.

**Test-harness defect found while adding it:** `_apply_once` reports a failure with `print()`, so a
scenario that deliberately fails a migration emitted that warning on stdout ahead of the probe's
JSON payload, and the parent's `json.loads` choked on it. The probe now marks the payload boundary
explicitly rather than assuming its whole stdout stream is JSON.

## EC-03 — migration readiness (the verification's point 3, now closed)

Raised by the [EC closure verification](2026-09-29-ec-closure-verification.md) as its one
unaddressed point, and restated in the follow-up review's priority table. It was correct.

`_apply_once` logs a failed migration and returns — deliberately, so one bad statement cannot take
down twelve services at startup. But `/health` returned `{"status": "ok"}` **unconditionally**, so
the failure had nowhere to surface: the container reported healthy, the scheduler started, and the
jobs whose premise was that migration ran anyway.

**The concrete risk.** The price-alert retry can only tell "never delivered" from "delivered before
we started recording it" on a database where the legacy rows were closed out. Without that
migration, every pre-cutoff legacy row is indistinguishable from a failed send, and the job would
email people alerts that had already gone out.

**Fixed in three parts.** Failures are recorded rather than only printed, so they survive the
function that produced them. `/health` reports a `migrations` block. And `migration_applied(name)`
lets a dependent job ask — `check_price_alerts` now asks before running its retry, skipping it and
leaving the rows pending if the closeout cannot be confirmed. The ordinary alert path is untouched,
because it does not depend on the migration.

**`status` deliberately stays `"ok"` when a migration failed.** Flipping it would fail the
container healthcheck, and several services declare `depends_on: condition: service_healthy`, so
one unapplied *data* migration would cascade into a refusal to start — a far worse outcome than the
thing being reported, and the exact shape of the 2026-09-17 outage recorded in
`scripts/rebuild_backend_images.sh`'s own docstring. Enforcement belongs at the job that can skip
precisely the affected work.

**A three-state answer, not a boolean.** `migration_applied()` returns `None` when the question
cannot be *answered* — no ledger table, database unreachable. The gate treats unknown exactly like
"not applied". Returning `True` there would run the retry on an unverified prerequisite; returning
`False` would be a definite claim the code cannot support.

**Two sabotages survived my first test pass**, both in the branch that matters most: removing the
recorded-failure fast path, and returning `True` instead of `None` on an unreachable database.
Neither was covered, because every test used a healthy ledger. A scenario that drops the ledger
table mid-run now fails both.

The 24-hour cutoff still bounds *this* migration's exposure — all 60 legacy rows predate it — but
that is a property of one migration, not a general guarantee, which is why the gate exists rather
than relying on it.

### The verification's other points, unchanged

## Still open, unchanged

The transactional outbox, checked lease ownership, recorded expiry/dead-letter state, and
provider-accepted-versus-delivered distinctions. Durable retry for **technical** alert failures —
the price retry correctly excludes them, but no equivalent queue exists for them yet, so that part
of EA-06/EF-03 stays open. Immutable source event IDs, strategy-specific watch/actionable TTLs, real
stored-signal freshness (EA-08's deeper point), and separating candidate diagnostics from
delivered-alert outcomes from executed trades.

**The 69 consumed signal transitions are still not recovered**, and the recommendation is unchanged:
a read-only manifest and a reviewed current-state digest, not a bulk `last_signal` reset — which
would not reliably produce an alert anyway, because the transition logic recognises None→BUY but not
None→SELL.
