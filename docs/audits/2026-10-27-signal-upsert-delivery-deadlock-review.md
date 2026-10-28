# Signal persistence versus alert-delivery deadlock — diagnosis and proposed fix

Review date: **October 27, 2026, America/Los_Angeles**. Status: **step 1 canonical write order deployed from `8dc84150`; full-session acceptance pending; no retry added**. Source baseline: `e8851a43`. F04 remains held. This follows [F03 §12 and the review notes](../features/2026-10-27-f03-signal-occurrence-and-delivery-attribution-design.md).

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

To reproduce the **pre-fix baseline**, use a separate checkout at `2aba2e14` (it contains this probe and the reviewed `e8851a43` application source), then run the following with a fresh local container. The current fixed sender no longer has the old loop this probe extracts; use the new regression tests for the fixed implementation:

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

**Diagnosis-stage decision (before the implementation below):** proceed with canonical metadata ordering and a regression through its actual write path; review the database-only retry boundary separately. The subsequent implementation and rollout sections record the delivered patch. Full-session acceptance remains open.

## Implementation — step 1 (canonical order), deployed

- **Fix (market-data only).** [`ai_signal_delivery_write.py`](../../services/market-data/src/services/ai_signal_delivery_write.py)
  reads `(id, stock_id, horizon)` for the decided rows with a plain SELECT (no row locks), then
  issues the per-row UPDATEs ordered by stock id, then `GENERATION_HORIZON_ORDER` rank, with
  horizon name and row id as tie-breakers. `check_signal_alerts()` calls it in place of the old
  `_ai_delivery.items()` loop. Same values, same single commit, same skip of vanished rows.
  Email selection, sending, consent and `SignalAlert` state are unchanged. No retry was added
  (step 2 is still a separate review).
- **Why it is sufficient against this cycle.** A generator holds rows of ONE stock, acquired
  in rank order. When delivery waits on a generator's row, every row it holds for that stock
  ranks lower, and the generator has already passed those ranks. Rows of other stocks are never
  wanted by that generator.
- **Unknown horizon.** It sorts last within its stock instead of raising, because raising would
  drop the whole run's delivery evidence. A drift test reads the literal
  `generate_all_signals()` returns and fails if the generator's order ever differs (for
  example, when INTRADAY is added).
- **Tests.**
  - market-data
    [`test_signal_delivery_lock_order.py`](../../services/market-data/tests/test_signal_delivery_lock_order.py)
    checks the order, the drift, and that the sender writes only through the helper.
  - signal-engine
    [`test_signal_delivery_lock_order_db.py`](../../services/signal-engine/tests/test_signal_delivery_lock_order_db.py)
    has a SQLite write/skip test, plus a **PostgreSQL** race. The race uses the shipped
    `_bulk_persist` upsert SQL and the live F03 observer against the real helper. Delivery
    already holds stock 1's rows and is proven blocked by the generator via
    `pg_blocking_pids()`. **Canonical order: both commit, all 4 generated rows and all 8
    delivery rows written, 4 occurrences observed. Reversed order (negative control):
    40P01**, and the victim's batch is all-or-nothing.
  - PostgreSQL pair run 3× against a throwaway local database, all passing; `make test` all
    services passed.
- **Probe compatibility.** `reproduce_pg16.py` extracts the old `_ai_delivery.items()` loop, so
  it reproduces the pre-fix baseline at `e8851a43` only. The regression above replaces it for
  the fixed code.
- **Not yet covered.**
  - Concurrent refresh plus live-GET writers, and multi-stock generator interleavings beyond one
    generator. Both writers use the same per-stock order, so no new cycle is expected; this is
    not separately tested.
  - The "email accepted before DB failure" acceptance belongs to the retry/receipt work.
- **Rollout completed.** market-data only under the host lock, pinned to `8dc84150`; detailed verification and the operational cutover are below. F04 remains held.


## Production rollout — October 27 PDT / October 28 UTC

**Deployed `8dc8415041e4788c05c6f3d579d45e88e5e1cf0b`, market-data only**, through `scripts/deploy_remote.sh` under the host lock. New sender container started at **2026-10-28 00:28:08 UTC / October 27 17:28:08 PDT**. Previous image tagged `stockai-market-data:rollback-before-8dc84150`; old logs and identities retained at `/home/ec2-user/deploy-evidence/2026-10-27-delivery-lock-order/`.

Fresh pre-release checks: **10 targeted market-data tests passed**; **4 database tests passed** (two SQLite, two actual PostgreSQL 16 race tests with F03 active, including the reversed-order negative control). The isolated regression container was removed. The author's full `make test` pass is recorded above; this deployment independently reran the affected checks, not all services.

Post-deploy verification: **15/15 healthy; all 12 backends' `src` and `shared` hashes match the release; only market-data restarted**. All other images, start times and source hashes are unchanged, including signal-engine and ml-prediction. The helper was imported from the running image and its order checked against 64 actual signal-row identities in a READ ONLY transaction. The real sender's loaded source calls it; no insertion-order metadata loop remains. An empty-decision no-op was checked without writes. **No alert job or test email was manually invoked**.

The initial quiet-window check saw zero retained PostgreSQL deadlocks after restart; the normal startup alert job had not yet completed at that first read. That is an immediate health/source check, not full-session contention acceptance or proof of zero future deadlocks. Deployment ended with about **25 GB free** and sampled I/O wait **3.2%**. [Tests, source/image/start identities and verification script](evidence/2026-10-27-delivery-lock-order-deployment/).

**Passive follow-up at 00:31:42 UTC:** the normal startup `check_signal_alerts` job completed with `status=ok` at **00:29:57 UTC**, duration 43 seconds, and **401 signal rows** had delivery metadata committed since restart. These are metadata rows, not 401 sent emails. Retained PostgreSQL logs still contained zero deadlocks since the sender restart, and the F03 cron entry remained installed. No job or send was manually invoked. [Follow-up](evidence/2026-10-27-delivery-lock-order-deployment/passive-followup.json).

**F03 evidence cutover:** `/home/ec2-user/f03-observation/release-cutovers/market-data-8dc84150.json` records the sender's new image/start plus the unchanged generator and ML identities. The observer contract/counters remain `f03-observe-v1`; this is a new **operational release segment**, not a new confidence-counter definition. Keep earlier captures. The next daily log window begins before this cutover, so split timestamped deadlocks at the recorded boundary and do not call its aggregate refresh-skip count a pure post-fix rate. First full candidate market sessions remain October 28 because the rollout preceded both opens; they count only after coverage is reviewed.

**Still open:** active-session before/after deadlock and persistence evidence, database-only retry/receipt handling, and F04's evaluation-version/allocation decision. No retry, receipt schema or F04 trainer change shipped.
