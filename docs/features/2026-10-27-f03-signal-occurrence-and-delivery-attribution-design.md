# F03 — Signal occurrences, delivery receipts and outcome attribution (design)

Date: **2026-10-27**. Status: **DESIGN FOR REVIEW — nothing built.** Source finding:
[F03 in the RZY/AI review](../audits/2026-10-26-little-rzy-ai-signals-full-review.md). Also
specifies the frozen completed-close snapshot deferred from F02.

## 1. The defect, in the current code

One `signals` row exists per `(stock_id, horizon, date_trunc('day', ts))`
(`uq_signals_stock_horizon_day`). Each refresh — about five a day — overwrites `signal`,
`confidence` and `reasons` in place (`api/routes.py` upsert). The row carries three different
things that are each correct alone and wrong together:

| Field | Written | Meaning |
|---|---|---|
| `first_buy_sell_*` | once, by `COALESCE`, at the day's first BUY/SELL | what `signal_outcomes` scores (`outcomes.py:evaluate_signal_outcomes`) |
| `signal`, `confidence`, `reasons` | every refresh | what the alert path reads (`_stored_signal_for_style`) and what the email shows |
| `alert_delivery_*` | after each alert run, on whatever row ID the selector returned | merged state plus a recipient counter |

`_build_confidence_calibration()` joins the **first-fire** outcome to the row's **merged**
delivery state. So:

- **Cross-direction:** SELL first at 10:00, never mailed; BUY at 13:00, mailed. The SELL outcome
  enters the `sender_accepted` cohort although no one received a SELL.
- **Same direction, different strength:** BUY 58 at 10:00 (first fire, scored); BUY 81 at 13:00
  (mailed). The delivered cohort's confidence band says 55–70; the email said 81.
- **Recipient inflation:** `alert_delivered_recipient_count` is incremented per acceptance, so a
  retry or a second run that re-sends counts again. It is not a distinct-recipient count.
- **No completed-close record (F02 remainder):** nothing freezes what the signal was once the
  session's bar closed; the final `reasons` of the day is whatever the last refresh wrote.

## 2. Goals and non-goals

**Goals.** (1) An outcome is attributed to exactly the state that was generated, delivered or
closed — never to a neighbour on the same row. (2) Distinct recipients are countable without
inflation by retries. (3) Every cohort (`generated_first_fire`, `delivered`, `completed_close`)
is a separate population with its own denominator. (4) Additive: no existing column changes
meaning, no reader breaks during rollout.

**Non-goals.** Exactly-once email (the outbox contract already records why it cannot be
promised; this design inherits `unknown`). Inferring exact delivery identity for legacy rows —
they stay `legacy_row_level` and are excluded from the delivered cohort. Changing signal
generation, alert gating or the subscription model.

## 3. Identity

**Occurrence** = one continuous run of a single direction (BUY or SELL) for one
`(stock_id, horizon)` within one daily row. It opens at the first generation in that direction
and closes when a later generation on the same row publishes a different signal (including
HOLD), or when the row's day ends. A SELL→BUY→SELL day therefore has three occurrences.
HOLD is not an occurrence (nothing is scored or mailed for it).

- Key: `occurrence_key = f"{signal_id}:{seq}"`, `seq` 1, 2, 3… within the row. Assigned inside
  the same transaction as the upsert that opened it, under the row's lock (`SELECT … FOR UPDATE`
  on the `signals` row), so two concurrent refreshes cannot both open `seq = 2`.

**Snapshot** = an immutable copy of one generation's published state, attached to an
occurrence. Written only at three moments, which bounds the volume:

| `kind` | When | Purpose |
|---|---|---|
| `opened` | the occurrence opens | the generated cohort (supersedes `first_buy_sell_*` for new rows) |
| `delivered` | immediately **before** an alert email is rendered from it | the delivered cohort; the email is rendered from the snapshot, not from the live row |
| `completed_close` | the first generation whose `input_contract.basis == session_closed` for that session | F02's frozen completed-close state; may be HOLD, so keyed by session, not occurrence |

A snapshot stores: `confidence`, `bullish_probability`, the price levels shown (entry, stop,
target where present in `reasons`), the full frozen `reasons`, the `input_contract`, `captured_at`,
and `content_hash` (SHA-256 of the canonical JSON of those fields). Two `delivered` snapshots of
the same occurrence with the same hash are the same snapshot (dedup by unique constraint).

## 4. Tables (all new; nothing altered)

```
signal_occurrences
  id                bigserial PK
  signal_id         bigint  FK signals(id) ON DELETE SET NULL   -- daily row it lived on
  stock_id, horizon, direction ('BUY'|'SELL')
  session_date      date    -- the VENUE's session date, never a UTC .date()
  seq               int
  opened_at, closed_at (null while open), close_reason ('direction_change'|'day_end')
  contract_version  varchar(32)
  UNIQUE (signal_id, seq)

signal_snapshots
  id, occurrence_id FK (nullable for completed_close), kind, captured_at,
  confidence, bullish_probability, levels json, reasons json, input_contract json,
  content_hash char(64)
  UNIQUE (occurrence_id, kind, content_hash)

signal_session_closes                      -- F02's completed-close record
  stock_id, horizon, session_date, snapshot_id FK
  UNIQUE (stock_id, horizon, session_date) -- first session_closed generation wins; never updated

signal_delivery_receipts
  id
  receipt_key       varchar(255) UNIQUE    -- f"{snapshot_id}:{user_id}:{channel}"
  occurrence_id, snapshot_id, user_id, alert_id, channel
  state             'queued_not_sent'|'sender_accepted'|'failed'|'gave_up'|'unknown'|'suppressed'
  attempts int, first_attempt_at, last_attempt_at, accepted_at, provider_message_id
  contract_version

occurrence_outcomes
  id, snapshot_id FK, cohort ('generated_first_fire'|'delivered'|'completed_close'),
  window ('primary'|'5d'|…), entry_date, entry_price, exit_date, exit_price,
  pct_return, is_correct, evidence_status, evidence_basis, evaluator_version
  UNIQUE (snapshot_id, cohort, window)
```

`receipt_key` uses the same shape as an outbox `event_id`, so when AI signal alerts move to the
M20 outbox the receipt and the outbox row share one identity rather than needing a mapping.

## 5. Write paths

**Signal upsert (signal-engine).** In the same transaction as the existing upsert: lock the row,
read the latest open occurrence; if the new published signal is BUY/SELL and differs from it (or
none is open), close the old one and open `seq + 1` with an `opened` snapshot; if it is HOLD,
close any open occurrence. If `input_contract.basis == session_closed` and no
`signal_session_closes` row exists for that session, insert one (`ON CONFLICT DO NOTHING`).
The existing columns, including `first_buy_sell_*`, keep being written unchanged.

**Alert send (market-data `check_signal_alerts`).** Per recipient, before rendering: resolve the
open occurrence for the selected row and insert-or-reuse its `delivered` snapshot (unique on
hash), then upsert the receipt by `receipt_key`. Render from the snapshot. Settle the receipt
from the send result. The existing row-level `alert_delivery_*` update continues unchanged
during dual-write.

## 6. Retry and failure handling

| Situation | Receipt behaviour |
|---|---|
| Same transition re-sent by a later run (EA-01 retry, quota retry) | same `receipt_key`; `attempts += 1`; no new row |
| Gmail quota (T239) | `queued_not_sent`, attempts counted, transition not consumed (unchanged policy) |
| Code-level send exception (EA-01) | `queued_not_sent`; never `failed`; the transition stays pending |
| 5-retry SMTP give-up (DP-1) | `gave_up`; terminal |
| Process dies after provider acceptance, before commit | next run finds `last_attempt_at` set and no settle: `unknown` (as in M20), never guessed |
| Live signal changes between runs while a send is pending | new occurrence/snapshot → new `receipt_key`; the old receipt stays as it was |

State merge is monotonic: `sender_accepted` is never downgraded (same rule as AQ06's
`_merge_ai_delivery`). **Distinct recipients** = `COUNT(DISTINCT user_id) WHERE state =
'sender_accepted'` per occurrence or snapshot — retries cannot inflate it. `unknown` is reported
beside every delivery rate, never folded into either side.

## 7. Outcome attribution

- `generated_first_fire`: scores each occurrence's `opened` snapshot — so a SELL→BUY day yields
  two scored occurrences, not one. Entry convention unchanged (next session after
  `session_date`).
- `delivered`: scores each `delivered` snapshot with **entry anchored to `accepted_at`**, not the
  signal date — the first session close at or after acceptance. This is the only causal anchor
  for "what a recipient could have done".
- `completed_close`: scores `signal_session_closes` snapshots that are BUY/SELL, entry at the
  next session. This is the population for completed-session calibration (F02).

The existing `signal_outcomes` table and its evaluator stay untouched and keep their meaning
(first-fire, row-level, legacy). Calibration's delivered subset moves to `occurrence_outcomes`
`cohort = 'delivered'`; rows before cutover report `delivery_attribution: legacy_row_level` and
contribute no delivered observations.

## 8. Migration and rollout

1. **Schema:** four new tables in `shared/db/models.py` (created by `create_all`, which does
   create missing tables) plus a migration-ledger entry. No `ALTER` of `signals`. Because this
   touches `shared/`, all 12 backend images must be rebuilt together
   ([AUD-REGISTER-POSTBUILD-DRIFT](../incidents/docker-deploy-staleness.md)).
2. **Dual-write (≥ 5 sessions, US and HK):** both write paths populate the new tables alongside
   the existing columns. Readers unchanged.
3. **Parity check:** a read-only report per session: occurrences per row, rows with >1 occurrence
   (the cases F03 is about), delivered snapshots whose confidence differs from the first fire,
   receipts with `attempts > 1`, distinct vs legacy recipient counts. These are the first real
   measurements of how often the defect occurs.
4. **Reader cutover:** calibration's delivered subset and any delivered-rate display read
   `occurrence_outcomes`. Legacy fields stay populated until a separate removal decision.
5. **No backfill.** Historical rows cannot be attributed exactly and are not inferred.

## 9. Acceptance tests (PostgreSQL, real write paths)

- SELL 10:00 unmailed → BUY 13:00 mailed: two occurrences; the SELL outcome is in
  `generated_first_fire` only; the BUY outcome is in `delivered` with entry after `accepted_at`.
- BUY 58 → BUY 81 mailed, same direction: one occurrence, two snapshots; the delivered outcome
  carries confidence 81.
- Same transition sent, quota-retried twice, then accepted: one receipt, `attempts = 3`, one
  distinct recipient.
- Two concurrent refreshes opening an occurrence: exactly one `seq = 2` (row lock).
- Two `session_closed` generations for one session: one `signal_session_closes` row, the first.
- Crash between acceptance and commit (simulated): receipt resolves to `unknown`.
- Legacy row (pre-cutover): zero delivered-cohort contribution; reported as `legacy_row_level`.

## 10. Decisions needed before building

1. **Occurrence boundary on confidence.** This design opens a new occurrence only on a direction
   change; confidence changes within a direction become extra `delivered` snapshots. The
   alternative — a new occurrence per material confidence change — multiplies scored rows and
   needs a threshold that would itself be a tuning choice. Recommended: direction only.
2. **Receipts table vs routing AI alerts through the M20 outbox now.** Recommended: receipts
   table now (smaller change to a live sender), shared `receipt_key`/`event_id` shape for a later
   move.
3. **Delivered-cohort entry anchor.** Recommended: first session close at or after
   `accepted_at`. A next-open anchor is more realistic for a human acting on an email but needs
   open prices the evaluator does not use today.
4. **Volume.** Not yet measured. Before building, a read-only count of direction changes per
   daily row over the last 30 days sizes `signal_occurrences` and the dual-write cost.
