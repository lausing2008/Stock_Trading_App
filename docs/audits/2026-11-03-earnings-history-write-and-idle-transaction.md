# Earnings results not recorded since October; one idle-transaction kill (2026-11-03)

Read-only investigation. Nothing was changed in production. Two findings from the Nov 3
evidence save (`~/stockai-evidence/2026-11-03`).

## 1. Earnings history write fails on every run: results are not being recorded

### Symptom

- PostgreSQL logs 95–741 `uq_earnings_stock_report_date` violations a day, every day since the
  log begins (Oct 20). Nov 3: 741.
- Since the week of Oct 19, **1 of 48** earnings events dated in that window has its actual EPS
  recorded (AMZN, Oct 29). July and August were almost fully recorded.
- 49 stocks hold a pending row (`eps_actual IS NULL`) dated in the past, among them GOOG
  (Oct 28), COIN, HOOD, ABBV, CCJ, SNDK, VRTX and UBER.
- The provider has the results. For example, the Nov 4 01:15 UTC run fetched VRTX's
  2026-09-30 quarter (EPS 4.76), but VRTX's Nov 2 row is still pending.
- The coverage ledger records it on every run, but nothing alerts on it:
  - Every daily history pass since the ledger began (Oct 4) shows 31–146 `partial` outcomes.
  - On Nov 3, 138 of 146 partials read exactly `{"IntegrityError": 1, "PendingRollbackError": 3}`.
  - The scheduler logs the job as `executed successfully`.

### Cause

`services/event-intelligence/src/services/earnings.py`, history loop (introduced in `fb064b32`,
AUD264, 2026-08-06):

```python
existing_pending = s.execute(
    select(EarningsEvent).where(
        EarningsEvent.stock_id == stock_id,
        EarningsEvent.eps_actual.is_(None),
    )
).scalars().first()
```

1. **The lookup is not scoped to the period being written.** It returns the stock's *upcoming*
   pending row for every history row. The loop walks history oldest first. So the first row,
   a quarter already stored at its real announcement date, tries to move the upcoming pending
   row onto that old date. The update is
   `UPDATE earnings_events SET report_date='2025-11-04' … WHERE id = 27775`, and the unique
   constraint rejects it.
2. **One failed flush poisons the session.** There is no savepoint per row. Each later row
   raises `PendingRollbackError` and is counted in `dropped`, including the newly released
   quarter. Then `s.commit()` raises, and the outer `except` logs it at DEBUG only.
3. **Why August worked.** On Aug 6, the same release backfilled stored rows to their real dates
   (`b654cd12`, `d671037e`). In that one pass the old dates did not exist yet, so the move
   succeeded (writing an old quarter's actuals onto the pending row), and later rows took the
   insert path. Once the calendar path created the next pending row, every following pass
   collides on its first row.
4. **Where the move does succeed,** it is silent corruption: an upcoming event is rewritten as
   a past quarter. Where it fails, nothing is written. Both outcomes are wrong.

### Downstream effects

- **Measured:** `check_earnings_impact_poll` reports `checked: 0` on every run on Nov 3.
- **Not checked, likely:** anything reading a fresh `eps_actual` sees no reports this season:
  - earnings surprise alerts;
  - the post-earnings impact report;
  - the T370 direction scoreboard;
  - the earnings calendar's actual column.
- **Unrelated:** GOOGL left the daily sync after Oct 30 because it was set `active = false`
  (223 → 199 active symbols on Oct 31). GOOG is active and affected.

### Fix direction (not built)

- Match the pending row to the period being written, not to "any pending row". For example,
  the pending row whose date lies within the announcement window of this `period_end`.
  Otherwise leave it for the calendar path.
- Give each row a `begin_nested()` savepoint, so one bad row cannot drop the others.
- Raise the `partial` outcome above DEBUG, or alert when a pass's `dropped` is non-zero.
- After deploying, re-run the daily history sync to recover the missing quarters, and check
  for pending rows earlier passes moved onto old dates.
- Test on real PostgreSQL. SQLite will not reproduce the flush-poisoning behaviour faithfully.

## 2. Idle-in-transaction kill at 00:55 UTC (market-data)

- `shared/db/ddl_guard.py` sets `idle_in_transaction_session_timeout = 300 s` on every app
  connection. PostgreSQL ended one at 00:55:03 UTC. market-data noticed at 01:08:55 when it
  tried to reset the connection (`Exception during reset or similar`, `_finalize_fairy`).
- So a session went idle mid-transaction by 00:50:03 at the latest and was released about
  19 minutes later. `_finalize_fairy` also runs when a connection is garbage-collected rather
  than closed, so this is consistent with a session that was never closed.
- **Timing lead (unconfirmed):**
  - Kills at 12:55 UTC on Oct 21, 23 and 26, and at 00:55 UTC on Nov 4, are each 5 minutes
    after a morning digest: US at 08:50 ET (EDT), HK at 08:50 HKT.
  - `send_morning_digest` itself uses `with SessionLocal()`.
  - A static search found no unclosed `SessionLocal()`, `get_session()` or `engine.connect()`
    in market-data or `shared/`.
- A separate cluster, 18:01–18:58 UTC on Oct 23, 26 and 28, falls inside the US session and
  is not explained by the digest.
- **Next step (not built):** `pool_watch` already records each checkout's origin but logs only
  under pool pressure. Logging any connection held longer than about 240 s at check-in or
  close would name the holder the next time this happens.
- Impact so far: one stale connection discarded; no failed request has been attributed to it.
