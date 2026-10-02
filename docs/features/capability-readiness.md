# M24 — capability readiness (`shared/common/capabilities.py`)

Built 2026-10-01. Read-only; exposed on `/health` as a `capabilities` block. Changes no
behaviour on its own — it answers a question nothing could previously answer.

## Why a migration ledger was not enough

`migration_applied(name)` answers **"did this statement run"**. The question that matters is
**"can this action be performed"**, and they are not the same:

- a table can exist with its **uniqueness constraint missing** — every insert still succeeds,
  the migration is recorded as applied, and the idempotency the outbox rests on is silently gone;
- a table can exist and be **unreadable by this role**;
- the database can be **unreachable**, which is not evidence that anything is absent.

EC-03 lived in exactly that gap: a service started, passed its healthcheck, and ran every job
whose premise was a migration that had failed.

## Three states, never two

`ready` / `not_ready` / **`unknown`**. Unknown is a first-class answer. Collapsing it to
`not_ready` blocks work on a transient outage; collapsing it to `ready` runs work on an
unverified prerequisite. Both are wrong, in opposite directions.

A **permission error** and a **missing table** are different facts — "we were not allowed to
look" versus "we looked and it is absent".

## The asymmetry that matters

`gate(evaluation, risk_increasing=...)`:

| Action | `unknown` |
|---|---|
| **Risk-increasing** (open a position, send mail) | **Blocks.** Proceeding on an unverified prerequisite is how an unapplied migration silently stamped every pending alert as delivered |
| **Risk-reducing** (protective exit, reconciliation read) | **Proceeds.** Refusing to close a position because a database could not be reached turns a degraded state into a trapped one |

Entry readiness is therefore **not** exit readiness, and the report lists
`exit_or_reconciliation_blocked` separately — it is expected to be empty, and a blocked exit is
a far more serious condition than a blocked entry.

## The matrix

| Capability | Blocks when unavailable | Impact |
|---|---|---|
| `outbox_enqueue` | Notifications cannot be queued; the legacy sender continues | delivery |
| `outbox_drain` | Queued notifications cannot be delivered — they remain queued, not lost | delivery |
| `broker_submission` | Broker orders cannot be submitted through the durable path | entry |
| `exposure_reservation` | New paper **entries are refused** — the concentration cap cannot be enforced | entry |
| `submission_reconciliation` | Unknown broker outcomes cannot be resolved; they accumulate holding exposure | reconciliation |

Each declares its required schema, constraints and the **recovery behaviour** that preserves
pending work without duplicate side effects.

## Liveness stays separate from readiness

`status` remains `"ok"` when a capability is degraded. A process with an unmet prerequisite is
healthy and **degraded**, not unhealthy — flipping the status would fail the container
healthcheck and cascade through `depends_on: service_healthy`, which is the 2026-09-17 outage.
Degradation is reported in **operator language**, naming the consequence rather than a table.

## Verification

17 tests against a real database, covering what a healthy database cannot show: an empty schema,
**a table present without its uniqueness constraint** (the case a ledger reports as success), an
outage, a permission error, a check that raises outright, a capability with no check, and the
repair path — unavailable → deferred → restored → **resumes exactly once**.

Five sabotage runs. **Two survived initially and both became tests:** collapsing `unknown` to
`not_ready` inside `Requirement.evaluate` broke nothing, because every concrete check guards
itself and the handler was unreachable — untested defence-in-depth for any future check that
forgets to; and the missing-constraint check had no direct coverage.

## Not done

This has not been run against the production schema. The next milestone is a **read-only
production capability and model inventory**, plus sandbox lifecycle evidence for M25 — neither
is closed by a passing unit-test count.
