# Broker lifecycle verification — PostgreSQL races, dispatch controls, closure disposition

Run 2026-10-02 against `c5ca572b`, closing the open item the SF remediation review left as
acceptance #4: *"Run PostgreSQL races through dispatcher and close/cancel paths. If close wins
before claim, provider calls must be zero; if claim wins, closure must handle potentially live
broker exposure explicitly."*

**M25 remains disabled.** Nothing here asks for it to be turned on, and §1 is the reason it must
not be.

## How this was established

Nine scenarios against a real **PostgreSQL 15.18** (production runs 15), one session per thread,
with explicit barriers so the interleaving is forced rather than hoped for:
`services/market-data/tests/_broker_lifecycle_pg_probe.py`, output captured in
[`evidence/2026-10-02-broker-lifecycle-pg-races.json`](evidence/2026-10-02-broker-lifecycle-pg-races.json).

The earlier SQLite probe could not do this. SQLite serialises writers at the database level, so
the case the dispatcher actually meets — two connections contending for the SAME ROW, one UPDATE
blocking on the other's lock and then re-evaluating its WHERE clause — never occurs there. The
SQLite result was a sequential predicate check, which is why the review called SF-01's lifecycle
acceptance still open.

Every scenario that reaches `submit_pending` pins `now` to just after the fixture's entry time.
Without that pin the new expiry control blocks each dispatch on wall-clock age and every
scenario reports zero provider calls — **passing for a reason unrelated to the property under
test**. Two scenarios (R6, R8) did exactly that on the first run and were corrected.

## 1. The dispatcher has no production caller — the decisive M25 finding

`submit_pending()` is referenced **only** by tests and probes. No scheduler job, no endpoint, no
engine call site. The same is true of `needs_reconciliation()` and `reconcile_submission()`.

The consequence is not "a feature is incomplete". `_open_paper_trade` branches on the flag: when
it is ON, the inline `_place_broker_entry` call is **skipped** in favour of recording an intent.
So turning M25 on today would stop every real broker entry order, silently — intents would
accumulate in `pending` and nothing would ever submit them. The local trades would open
normally, which is exactly what makes it quiet.

This is a build task, deliberately **not** done here: a job that places real orders is not
something to add as a side effect of a verification pass.

## 2. Race results

| # | Scenario | Result |
|---|---|---|
| R1 | Close commits before the claim | **0 provider calls**; intent left `pending` |
| R2 | Claim blocks on an UNCOMMITTED close, then re-evaluates | blocked 1.00 s on the row lock, then **lost the race; 0 provider calls** |
| R3 | Two dispatchers, one row | exactly **1** provider call, one `lost_race`, attempts = 1 |
| R4 | Claim wins, close lands mid-flight | closure now records the disposition (§3) — see the scope note below |
| R5 | A `submitting` row a dead worker left behind | never re-claimed; **0** calls; listed for reconciliation |
| R6 | Attempt cap under repeated failure | **3** calls at a cap of 3 |
| R7 | Six-hour-old intent | blocked, attempts **unburned** |
| R8 | Portfolio unlinked from its broker after the intent | blocked |
| R9 | Price drift ±2% / unavailable quote | blocked; ±0.5% and ±1.0% proceed |

**R2 is the acceptance.** It is also what finally gives SF-01 a behavioural lifecycle test:
reverting `begin_submission`'s predicate to the pre-SF-01 `id + state` makes R2 fail — the stale
claim succeeds and the provider is called on a closed position.

**What R4 is and is not.** R4 calls `record_closure_disposition` and then closes the fixture; it
establishes the disposition's content, not an end-to-end broker cancellation through all four
close paths. Two tests in `test_liquidate_portfolio.py` do drive it through the REAL liquidation
path, which covers one of the four. Cancellation is not covered anywhere: closure records that an
order may be live, and does not cancel it. Likewise `retains_reserved_exposure=True` is a
property of the trade row — it is **not** evidence that every exposure calculator retains a
closed-but-unreconciled intent, which needs a subsequent entry exercised against the real
reservation path.

## 3. Gaps found and closed

**Closure said nothing about a live order (R4).** When the dispatcher won, the row committed to
`submitting` with the call in flight; a closure landing immediately after set `stage='closed'`
and recorded nothing. The position then read as flat locally while a real order could be live at
the broker — and the closure, the last thing to touch the row, left no trace that anyone needed
to look. `record_closure_disposition()` now stamps state, client order id and the reconciliation
requirement, wired into all four close paths: the scheduled exit, the manual exit, the admin
reset and the conditional-order exit.

**The broker link was not part of eligibility (R8).** An intent recorded while a broker was
connected stayed claimable after the user disconnected it, because eligibility asked only about
the TRADE. A real order placed into a disconnected account is the clearest case of acting on
withdrawn authority. The link is now a correlated subquery inside the one shared predicate, so
it applies to discovery and to the compare-and-set alike.

**No intent expiry, no price-drift control (R7, R9).** Both named by the review as M25
prerequisites; neither existed. `dispatch_block_reason()` now checks both **before** the claim,
so a blocked intent does not burn one of its three attempts. It **fails closed** — an
unmeasurable age or an unavailable quote blocks the order, on the same reasoning
AUD-B01-PREFLIGHTFAILCLOSED already settled for the buying-power check: a control that could not
be evaluated has not passed. `quote` is a REQUIRED argument rather than an optional one, so
forgetting the price check is a loud `TypeError` at the call site instead of looking exactly like
having no prices. It immediately caught its own existing caller.

**Mixed timestamp awareness.** `conditional_orders` builds timestamps with
`datetime.now(timezone.utc)` while the column is naive, so an in-memory trade can carry tzinfo.
Subtracting those raises `TypeError` — in a control that blocks real orders that would surface as
a crash in the dispatch loop rather than a refusal. Both sides are normalised.

## 4. Policy constants — these are decisions, not corrections

**Signed off 2026-10-03 as PROVISIONAL SANDBOX VALUES** — approved to proceed with, explicitly
not validated production limits, and nothing activates on them while M25 is off. The lifecycle
review's counter-examples stand and are the reason for that status: a 1% move can consume most
of a tight stop's risk budget, and a 15-minute-old breakout may already be invalid. Neither is
"conservative" in any absolute sense.

| Constant | Value | Reasoning |
|---|---|---|
| `INTENT_MAX_AGE_SECONDS` | 900 | the entry scan runs every 5 minutes; an intent older than three cycles was not produced by the conditions now in front of us |
| `MAX_ENTRY_PRICE_DRIFT_PCT` | 1.0 | the position was SIZED against `entry_price`; a fill far from it is a different position than the one the risk checks passed |

Both are the conservative end of a defensible range, not a tuned result. Neither has been
calibrated against outcome data, because no outcome data exists for a dispatcher that has never
run.

## 5. Blast radius of these changes today

Zero, by construction. `broker_submission_state` is NULL for every production trade: `mark_pending`
only runs when the flag is ON, and the legacy path sets only `broker_submission_path='legacy'`.
The closure guard is therefore inert across the entire live book, and the dispatch controls sit
in a function nothing calls. That is asserted directly, not assumed —
`test_closing_an_uninvolved_trade_records_nothing` covers every state a live trade can be in.

## 6. Test posture

- `tests/test_broker_lifecycle_controls.py` — 32 deterministic tests of the controls and the
  disposition, in the suite that runs on every commit.
- `tests/test_liquidate_portfolio.py` — two tests driving the guard through the **real**
  liquidation path. The first one written asserted the wrong actor: liquidation closes each
  position through `_close_one_paper_trade`, the same function the manual exit uses, so the actor
  is `manual_exit`, not the bulk `admin_reset` loop. The test caught the wrong assumption.
- Eight sabotages, each caught by exactly the scenario that owns it: broker-link predicate
  removed (R8), expiry removed (R7), drift removed (R9), disposition made a no-op (R4),
  SF-01's predicate reverted (R2), expiry boundary made exclusive, disposition fired for every
  trade, drift measured in dollars instead of percent.
- `make test: all services passed` (5,121 market-data). T401 ratchet green at baseline.

## 7. What remains open

1. **No dispatcher job** (§1) — the blocker for M25, and a build task needing its own go-ahead.
2. **No reconciliation surface.** `needs_reconciliation()` has no endpoint, so an `unknown` row
   would never reach a person. The data is correct and unreadable.
3. **Cancellation against the broker.** Closure now RECORDS that an order may be live; it does
   not cancel it. Cancellation needs a broker call with its own unknown-outcome handling — the
   same problem this module exists to solve, one layer along.
4. **Policy constants unsigned** (§4).

Until all four are closed, M25 stays off.
