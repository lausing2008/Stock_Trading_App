# M20 — durable notification delivery (`shared/common/outbox.py`)

Built 2026-10-01. **Schema and state machine only — not yet wired to any alert path.** No
production module enqueues to it, no worker drains it, and no email changes. The next step is
one bounded alert family, then migration of the rest.

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

## Verification

31 behavioural tests (`services/market-data/tests/test_outbox_delivery.py`) against a real
SQLite database in a subprocess — market-data's conftest stubs `sqlalchemy`, and every claim
here is about what the database does, so asserting them against a mock would assert on the mock.

**Five sabotage runs, all caught:** ownership check removed from settle; ambiguous outcome
treated as a retryable failure; cutover ignored so historical events became sendable; expiry not
re-checked at claim; pre-send expiry recheck removed.

## Not done

One bounded alert path has not been migrated yet; no worker loop exists; provider-callback
ingestion for `delivery_status` is unimplemented. Recovery-marker deletion, recovery emails and
live-trading activation remain separate and unauthorised.
