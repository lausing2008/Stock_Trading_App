# M20 — durable notification delivery (`shared/common/outbox.py`)

Built 2026-10-01. **PARTIAL.** Schema, state machine, and a first bounded integration for
earnings release-phase alerts (`services/market-data/src/services/earnings_outbox.py`). The
rollout flag is **OFF by default** and no scheduler job calls the producer or worker yet, so no
email behaviour has changed. Other alert families are unmigrated.

## What it replaces, and why a TTL was never idempotency

Alert de-duplication across this platform is a Redis `SETEX` key —
`stockai:early_earnings_news:{uid}:{sym}:{date}:{phase}` and its siblings. A TTL answers *have I
seen this key lately*, which is a different question from *has this notification been sent*, and
the two diverge in both directions: Redis restarts or evicts and the alert re-sends; or the key
is written and the send then fails, suppressing the alert for the whole TTL with no record that
anything was lost. AUD266-DEDUP-KEY-SET-BEFORE-SEND is this repo's own prior incident of exactly
that shape.

A UNIQUE constraint on an immutable `event_id` is idempotency. It survives restarts and
evictions, cannot expire, and a duplicate enqueue is refused by the database rather than by a
cache that may not still remember.

## What is deliberately NOT promised: exactly-once delivery

A crash between the provider accepting a message and this process committing that fact is
locally indistinguishable from a crash before acceptance. Retrying risks a duplicate; not
retrying risks a loss; **no local state separates them.** Without provider-supported idempotency
keys or reconciliation against the provider's own record, that uncertainty is irreducible.

So the system aims at at-least-once delivery with a bounded, auditable duplicate risk, and marks
ambiguous outcomes `unknown` rather than guessing. `EXACTLY_ONCE = False` is asserted by a test.

## Acceptance evidence

| Requirement | How it is demonstrated |
|---|---|
| Atomic event/outbox persistence | Domain write + enqueue in one transaction; rollback leaves **0 events, 0 notifications**; commit leaves 1 and 1 |
| Stable notification identity | Three scan cycles in three separate sessions → **one row** |
| Worker ownership | A worker whose lease lapsed **cannot** settle a row another worker reclaimed (`False`); the holder can |
| Ambiguous send outcome | Timeout after possible acceptance → `unknown`; claims neither success nor failure; **never auto-retried** |
| Preferences and expiry | Re-checked immediately before the provider call; opt-out blocks, stale blocks, unchecked says so, lookup error fails open with a reason |
| Honest delivery states | `queued → attempted → provider-accepted` distinct from observed `delivered`/`bounced`; **no `delivered_at` column** exists to be misread |
| Safe migration | Pre-cutover events stored `suppressed` + `reconstructed`, never sent, and their keys block a later duplicate |
| Operational visibility | Queue **age** (not just depth), retries, unknown count, and a `reconciles` flag when state counts don't sum to the total |

## Two defects this work surfaced in its own code

1. **`mark_accepted`/`mark_failed` originally mutated the row unconditionally.** A worker paused
   past its lease — GC, a slow provider, a loaded host — could overwrite the outcome recorded by
   the worker that legitimately reclaimed and sent it. Settles are now compare-and-set on
   `(id, state=leased, lease_owner, lease_expires_at > now)` and return whether ownership held.
2. **`enqueue` treated *any* `IntegrityError` as a duplicate `event_id`.** A foreign-key
   violation would have been reported as "already enqueued" and the notification silently
   dropped. It now confirms the row exists and re-raises otherwise.

Both were found by running against a real database, not by review.

## Design notes worth keeping

- **Attempts increment on claim, not on send**, so a payload that crashes its worker cannot spin
  forever. The cost — a worker killed between claim and send burns an attempt — is the lesser
  failure.
- **Retry content is frozen at enqueue.** EF-01: a retry that re-rendered its content shipped
  the threshold in place of the price, because the value had moved between attempts.
- **Expiry, dead-letter and suppressed are three distinct terminal states**: too late, could not
  send, chose not to send. Collapsing any two misreports every delivery rate derived from them.
- **Timestamps are naive UTC instants, never dates.** Calling `.date()` on one re-creates
  [utc-vs-et-date-boundary](../incidents/utc-vs-et-date-boundary.md).
- **Trading execution stays independent of email status.** Nothing here gates an order.

## The earnings-phase integration

The first bounded family, chosen because it is the one the MU incident broke. Requirements and
the behaviour that satisfies each:

| Requirement | Behaviour |
|---|---|
| Preference errors **defer** | The provider is never called; the attempt is refunded; delivery resumes when the source returns; a defer cap dead-letters with the real cause |
| Recheck at send time | Opt-out → `suppressed`; stale → `expired`; neither reaches the provider |
| Acceptance/crash gap | `begin_dispatch` is committed **before** the provider call; an abandoned in-flight row is quarantined to `unknown`, **not resent** — provider call count stays 1 |
| Cutover vs phase markers | Legacy marker present → `suppressed` (**confirmed**); pre-cutover → `suppressed` + `reconstructed` (**confirmed by date**); marker **unreadable** → stays `pending` and **defers**, re-checked before every send; every withheld key still blocks a duplicate |
| Unknown outcomes | `needs_review()` queue, `reconcile_unknown()` requiring external evidence, and `oldest_unknown_age_seconds` so stranding is visible |
| Full path with a fake provider | release → event → outbox → claim → recheck → dispatch → send → recorded acceptance, plus timeout, rejection, restart |

**Rollout modes are mutually exclusive by construction** — `off`/`shadow` deliver via the legacy
sender, `outbox` delivers via the outbox, and no mode does both. Absent, unrecognised or
unreachable configuration all read `off`.

### The deferral correction

`may_send` originally failed **open** on a preference lookup error, following this repo's
established rule that absence of a preference means subscribed. That rule was written when there
was no durable queue, so the only options were "send anyway" or "lose the alert". **The outbox
removes that dilemma** — a deferred notification is retried when the preference source returns —
and sending on an unreadable preference is an unenforced opt-out that logging does not undo.
Deferrals are counted separately from send attempts so an outage cannot dead-letter the backlog.

### Confirmed suppression vs uncertainty

An unreadable legacy marker was originally suppressed too, reasoning that withholding beats a
possible duplicate. That is right as a momentary choice and **wrong as a permanent verdict**:
one second of Redis being unreachable would have cancelled a real alert forever, recorded as
though delivery had been confirmed. "We could not tell" is not "already delivered". The row now
stays deliverable, the worker re-checks the marker before every send, and it defers while the
answer is unknown — bounded by the same defer cap, so it cannot defer forever unnoticed.

### Reconciliation is auditable and idempotent

Resolving an `unknown` is a human overriding the system's own record, so it records **actor,
prior state, verdict and external evidence**, and refuses without any of them. A second
reconciliation — repeating the verdict or conflicting with it — is **rejected rather than
applied**, so the first decision and its evidence stand. There is deliberately **no `retry`
resolution**: the content may be stale, and re-queueing would resend expired mail under cover of
an audit action.

### Attribution stays separate

`accepted` means the provider took this message. It never means the right release was
identified, and `earnings_phase.KNOWN_UNRESOLVED_RISK` is untouched by anything here.

The contract is stated **directionally**, because that is the thing that would actually go
wrong: *delivery acceptance must never promote classification confidence or trading
eligibility.* Tests assert that `paper_trading_engine` references no outbox or delivery state
(`accepted_at`, `delivery_status`, the table itself), and that the producer imports no trading
or decision module. If an engine ever read delivery state to decide an entry, a mail-server
outage would become a trading input. An earlier version asserted only the absence of a column
named like "verified", which was near-worthless — a differently-named field would have passed.

## Verification

31 behavioural tests (`services/market-data/tests/test_outbox_delivery.py`) against a real
SQLite database in a subprocess — market-data's conftest stubs `sqlalchemy`, and every claim
here is about what the database does, so asserting them against a mock would assert on the mock.

**Five sabotage runs, all caught:** ownership check removed from settle; ambiguous outcome
treated as a retryable failure; cutover ignored so historical events became sendable; expiry not
re-checked at claim; pre-send expiry recheck removed.

### PostgreSQL, not just SQLite

SQLite establishes the state machine but serialises writers, so it structurally cannot exercise
contention. Verified separately on **PostgreSQL 16** (`test_outbox_postgres_concurrency.py`).

**This is a REQUIRED release check, not an optional local one.** The CI workflow runs it as its
own job with a `postgres:16-alpine` service and `OUTBOX_PG_REQUIRED=1`, which turns a missing
database into a FAILURE rather than a skip — a skipped concurrency test is indistinguishable
from a passing one in a summary line, so a misconfigured service container would otherwise
produce a green job that exercised nothing. Locally it still skips when `OUTBOX_PG_URL` is
unset.

- 8 workers on 8 connections against 20 rows → **20 claimed, 0 double-claimed, 0 transaction
  errors**, max 1 attempt per row (a losing claim must not burn retry budget)
- 3 impostors racing the lease holder → **exactly one** settle lands, no exceptions
- 8 simultaneous enqueues of one event → **1 row**, 1 created, 7 readers, no unhandled error
- BIGSERIAL identity confirmed on the production type, rather than inferred from the SQLite
  variant
- crash recovery quarantines rather than resending

## Not done

No scheduler job calls the producer or worker; the rollout flag is off. Other alert families are
unmigrated. Provider-callback ingestion for `delivery_status` is unimplemented. Production
activation, historical recovery sends, recovery-marker deletion and live-trading activation all
remain separate and unauthorised.
