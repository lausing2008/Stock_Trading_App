# Signal persistence versus alert-delivery deadlock — diagnosis and proposed fix

Review date: **October 27, 2026, America/Los_Angeles**. Status: **diagnosis and isolated reproduction complete; no application changes or deployment**. Source baseline: `e8851a43`. F04 remains held. This follows [F03 §12 and the review notes](../features/2026-10-27-f03-signal-occurrence-and-delivery-attribution-design.md).

## Conclusion

The existing signal upsert and alert-delivery writer can acquire the same signal rows in opposite orders. Both keep their earlier row updates locked until the transaction commits. This creates a real deadlock independently of the new F03 tables/advisory locks.

**Recommend a common row order in the alert-delivery metadata write phase as the first patch.** Keep any defensive retry inside a database-only transaction. Do not retry the entire email-sending job. A PostgreSQL victim can be either writer: the current evidence does not support saying every deadlock necessarily loses a signal-generation batch.

## Production evidence — read only

At approximately **2026-10-28 00:05 UTC / October 27 17:05 PDT**, retained PostgreSQL logs since October 27 14:00 UTC contained **eight deadlocks**. A second sanitized parsing pass recovered the upsert/delivery-update pairing for all eight:

| Aborted statement | Events | Consequence |
|---|---:|---|
| Signal `INSERT … ON CONFLICT` | **5** | The signal writer's per-symbol transaction rolls back; earlier horizon writes in that transaction do not commit. Existing rows retain their earlier state; this is not necessarily deletion of rows. |
| Alert-delivery `UPDATE signals SET alert_delivery_state…` | **3** | The alert job's transaction rolls back, including its database metadata/pending alert state. If emails were already accepted, rollback cannot unsend them; retrying the whole job can duplicate them. These logs do not establish that a real email was accepted in each affected run. |

All eight reports contained a two-process transaction-ID wait cycle and the two SQL writer shapes. No F03 table/advisory-lock text appeared in those captured contexts. The mechanism is reproducible without F03; that does not prove F03 has no effect on transaction duration or event frequency. The previously documented pre-deployment deadlocks remain separate historical evidence.

Only lock metadata, statement classifications and referenced signal row IDs/stock IDs/horizons were exported. No email addresses, reasons payloads, SQL parameter values or credentials were saved. Source hashes of the running signal-engine `api/routes.py` and market-data `services/scheduler.py` match the reviewed files.

Limits: retained logs are not a complete historical population. Current row metadata was read after the event; logs do not preserve the complete acquisition sequence or every row already held by each transaction. We can establish the statement pair and wait cycle, but cannot reconstruct a full historic row-by-row trace for each event.

Evidence: [production lock summary](evidence/2026-10-27-signal-delivery-deadlock/production-lock-summary.json), [victim classification and deployed source hashes](evidence/2026-10-27-signal-delivery-deadlock/production-victim-summary.json).

## Actual writer and transaction boundaries

| Writer | Acquisition sequence | Commit/error boundary |
|---|---|---|
| signal-engine `_bulk_persist` (`api/routes.py`) | One stock per transaction; generated horizons iterate **SHORT → SWING → LONG → GROWTH**. Each `INSERT … ON CONFLICT` takes the relevant row/index conflict locks; F03 observes after that upsert and acquires its per-stock/horizon advisory lock. | Commit after all four horizons. An exception escapes that symbol's session and is logged as `signals.refresh.skip`; no bounded deadlock retry exists. The session closes/rolls back. |
| signal-engine live GET persistence path | Same four-horizon order and after-upsert F03 hook. | One commit for the symbol's horizons; there is no isolated database-only deadlock-retry boundary. |
| market-data `check_signal_alerts` | Reads subscriptions without a deterministic order. `_ai_delivery` accumulates row IDs in insertion order; `for _signal_id, _decision in _ai_delivery.items()` updates them in that order. That sequence is not canonical stock/horizon order. | The updates share the job session and commit after the whole metadata loop. Network email calls occur earlier in the job; pending `SignalAlert` changes are flushed at commit. A broad outer exception logs the failure; there is no safe whole-job replay guarantee. |

The session factory uses `autoflush=False`; the relevant explicit `UPDATE Signal` statements nonetheless execute immediately. Sorting delivery targets after the emails would not change email selection, message order, consent or eligibility. It would change the order in which the metadata transaction acquires signal row locks.

The existing Redis alert-job lock serializes alert jobs against one another, not against signal-engine. The F03 advisory lock serializes observation writers, not the alert writer, and is acquired after the signal row has already been updated. Neither prevents this cycle.

Minimal example, **illustrative rather than a recovered historical ordering**:

| Transaction A: generation | Transaction B: delivery metadata |
|---|---|
| Holds SHORT | Holds GROWTH |
| Wants GROWTH, held by B | Wants SHORT, held by A |

Locks are held until commit/rollback. PostgreSQL detects the cycle and aborts one transaction. Its victim selection must not be assumed in production. PostgreSQL recommends consistent acquisition order and, when needed, retrying a transaction after a deadlock. [PostgreSQL 16, explicit locking / deadlocks](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-DEADLOCKS).

## Isolated PostgreSQL reproduction before application changes

The [probe](evidence/2026-10-27-signal-delivery-deadlock/reproduce_pg16.py) ran against a **new local PostgreSQL 16.15 container**, bound only to localhost, with temporary in-memory data. It refused a container without the dedicated audit label or a non-empty public schema. Existing local databases and production were not modified. The disposable container was removed afterwards.

It uses:

- The actual `Stock` and `Signal` ORM table definitions and the production expression-based daily unique index.
- The actual `_bulk_persist` upsert SQL literal, extracted from the source AST.
- The actual `_ai_delivery` loop/SQLAlchemy update builder, extracted unchanged from the source AST; only its session, fixture decisions and timestamp inputs are supplied.
- Two actual transactions plus a monitoring connection. The test requires `pg_blocking_pids()` to prove the delivery writer is blocked by the generation writer before letting generation request the competing row. A lucky serial execution cannot pass.
- A minimal mechanism boundary: no F03 hooks, network requests, model inference, email calls or full scheduler initialization. Unequal local detector timeouts make generation the expected victim for the rollback assertion; that is a test control, not a production victim-selection promise.

| Schedule | Trials | Result |
|---|---:|---|
| Delivery GROWTH → SHORT versus generation SHORT → SWING → LONG → GROWTH | **3/3** | SQLSTATE **40P01**; generation rolls back. All four stored confidences stay at fixture value 40, so no partial horizon refresh commits. Delivery commits. |
| Delivery SHORT → GROWTH, same verified overlap | **3/3** | Both writers commit; all four generated confidences become 99; no deadlock. |

[Raw result and source hashes](evidence/2026-10-27-signal-delivery-deadlock/pg16-reproduction.json). The ordered arm changes the **experimental input order**, not application code. It is evidence for the proposed mechanism, not a claim that a production fix is implemented or that all possible database deadlocks are removed.

To reproduce from the repo with a fresh local container:

```bash
docker run -d --name stockai-deadlock-audit-20261027 \
  --label stockai.audit=signals-deadlock \
  --tmpfs /var/lib/postgresql/data:rw,size=256m \
  -e POSTGRES_HOST_AUTH_METHOD=trust -p 127.0.0.1::5432 postgres:16-alpine
python3 docs/audits/evidence/2026-10-27-signal-delivery-deadlock/reproduce_pg16.py
# Remove only this disposable audit container after the probe.
docker rm -f stockai-deadlock-audit-20261027
```

The script needs local Docker access, SQLAlchemy and psycopg2. The image was already available locally; no image pull or paid service call was needed. Re-running the script requires a fresh empty audit database, not a production connection.

## Proposed implementation, in order

### 1. Canonical metadata write order — primary repair

At the final alert-delivery metadata phase, load each target's immutable row ID, stock ID and horizon. Order targets by **stock ID, the explicit generation horizon rank (SHORT/SWING/LONG/GROWTH), then deterministic day/ID tie-breakers** before issuing updates. Use the same declared rank for all signal writers and reject/handle unknown horizons explicitly when Intraday is later added.

Do not rely on subscription order, dictionary insertion order, alphabetical horizon strings or numeric signal ID alone. Numeric IDs need not reflect current horizon order on old/migrated rows. The ordering implementation should be a small, directly testable database helper used by the real sender's metadata loop.

Keep the atomic per-symbol generation batch and existing F03 savepoint behavior. No shared schema, trade thresholds, generation logic, email content or subscription change is needed for this repair. The initial patch can therefore target **market-data only**; it does not require deploying the held F04 trainer.

### 2. Bounded database-only retry — defense in depth

After reviewing any remaining contention, add a maximum of **three total attempts** for SQLSTATE **40P01** at a safe persistence boundary, with rollback and a fresh transaction/session, bounded jitter and a clear exhausted-attempt metric. Do not catch every database exception as retriable, or retry only the failed statement inside an aborted transaction.

For generation, freeze expensive model/news inputs once. Extract the four-horizon persistence and observation writes into a database-only unit; optional research triggers/HTTP work currently performed while row locks are held must not be repeated by that retry. Handle observation counters/first-fire state atomically, preserve the original generation/input identity across a UTC/session boundary, and explicitly define what happens if a newer generation committed meanwhile. Do not overwrite a newer decision with a stale retry.

For delivery, **do not wrap `check_signal_alerts()` or its send loop in a retry**. An email accepted before a database rollback is an irreversible external side effect. A metadata-only retry needs frozen decisions, an explicit merge/recipient-count contract and safe treatment of pending `SignalAlert` state; otherwise it can lose or inflate acceptance evidence. F03's future durable send intent/receipt contract is the more complete solution to that ambiguity, but should not be pulled into this first ordering patch.

The matching-order repair is preferable first because it addresses this demonstrated cycle without replaying external work. Retry is additional resilience, not a substitute for removing the known inversion.

### 3. Validation before release

- Convert the mechanism witness into a **required PostgreSQL regression through the real ordering helper/write unit**. Keep the unsorted negative control: restoring insertion order must reproduce the race. Require server-observed overlap, not only barriers or sleeps.
- Include deliberately non-canonical numeric IDs and reversed subscription order, multiple stocks, all four horizons, missing targets, concurrent refresh/live writers and delivery updates. Assert no partial stock batch or lost acceptance metadata.
- Exercise F03 hooks and rollback/savepoint behavior on actual PostgreSQL; the minimal witness intentionally excluded them. Confirm retries do not duplicate generations/snapshots or turn an observation failure into a lost signal.
- Simulate an external send accepted before database failure. Assert no resend from a database retry and no recipient inflation. This remains an acceptance requirement even if the first patch only orders metadata writes.
- Run affected market-data/signal-engine suites and required PostgreSQL gates on the exact release. The six local schedules in this review are not a fresh full-suite result.

### 4. Rollout and observation

Deploy only the reviewed affected service(s), under the existing host lock, in a quiet window with rollback images/logs retained. Keep F04 held. Record release identities for **both** the sender and generator: sender changes can alter signal persistence/observation coverage even when signal-engine itself is not recreated.

Compare new deadlock SQLSTATE counts, refresh persistence/exhaustion, successful generation batches and alert acceptance/unknown states against actual attempt denominators. Retained-log absence is not proof of zero deadlocks. Preserve prior F03 captures and mark a new operational evidence segment; do not pool old high-loss persistence sessions with a new fix as an unchanged process. No profitable-edge or execution-readiness conclusion follows from fixing this deadlock.

## Decision and remaining work

Proceed next with the canonical metadata ordering patch and a regression through its actual write path. Review the database-only retry boundary separately. This report changes no application, sender, observation schema, schedule or model artifact; **the production deadlock is still unfixed**.
