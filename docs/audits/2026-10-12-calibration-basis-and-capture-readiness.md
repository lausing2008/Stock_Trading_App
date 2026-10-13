# Backlog closure: ML-weight calibration basis, and what the option ledger is actually waiting for

**2026-10-12.** Closing the three remaining AQ items. Two of the three turned out not to be what
the record said they were, so each is stated with what was checked rather than what was carried
forward.

---

## AQ06 / AQ07 (AI halves) — already closed; verified, not re-done

The backlog listed both as open. They are not. `classify_selected_signal_freshness` is called
from the live subscription path (`scheduler.py:8310`), and the delivery fold
(`alert_delivery_pool.fold_recipient`) is called at `scheduler.py:5932`. The eligibility floors
are applied to the accepted subset separately from the generated baseline in
`signals_shared.py`.

This is the stale-tracker class this repository already documents: an entry that was true when
written and was then closed by the fourth correctness slice without the backlog being updated.
Recorded here so the next reader does not re-derive it a third time.

---

## AQ04 residual — the ML fusion weight was fitted on unadjusted returns

### What was wrong

`services/signal-engine/src/api/calibration.py` measured every observation's forward return
straight off `Price.close`, outside the adjustment-evidence contract the outcome evaluator had
already adopted. Two functions did it: `ml_weight_validation` (reporting) and
`calibrate_ml_weight` — and the second is not a report. It **writes the global ML fusion weight**
via `set_ml_weight_global_cap`. A split inside a 14–28 day hold window reads as a crash: a 2:1
split on a flat stock produces a −50% observation, and that observation moves how much the model
is trusted in production.

### The rule, and why it is not "drop everything unverified"

`resolve_window` returns no prices whenever evidence does not span the window, which collapses
two different situations into one empty result:

- a corporate action is **recorded inside the window** and could not be applied — the raw return
  is known to be wrong; and
- **nothing is recorded** for the window at all — the raw return is unverified, which is not the
  same as wrong.

Treating those alike either discards most of the sample or admits a definitely-corrupt return.
So `classify_observation_window` (in `shared/common/outcome_price_basis.py`, beside the contract
rather than in the caller, because the outcome evaluator asks the same question) refuses the
first and keeps the second **labelled**. Both counts travel on every response, including the
`no_observations` early return, where "nothing was measurable" and "everything was dropped as
unadjustable" would otherwise look identical.

The asymmetry is deliberate: "no action recorded" is not evidence that no action occurred. That
is exactly why the result stays `unverified` and cannot reach a published rate. It is good
enough to fit a weight on and not good enough to quote.

### A defect in the fix itself, found by chasing a sabotage

The first version classified over `[signal_date, target_exit_date]` — the **calendar** target.
The exit actually fills on the first session **at or after** that date, and those differ by up to
a long weekend. A split in that gap falls outside the window being checked and is admitted as a
real return. The window is now the real holding period, `[signal_date, exit_date_actual]`, and
a dedicated test covers exactly the two signals whose 14-day target lands on the weekend before
the split.

### Measurement note on the test, which was wrong twice before it was right

- The first assertion checked `observations_used == 0`. Wrong: the stock is flat, so windows
  entirely **after** the split are honest and must survive. Only windows that *span* it are
  corrupt. The test of the fix is whether a −50% reading reached the curve, not how many rows
  remain.
- The second placed the split in the oldest rows. The returned curve's `avg_return_pct` is
  measured on the held-out newest 30%, so the defect corrupted weight **selection** while leaving
  the reported curve clean — the test would have passed on a number the defect never touched.

Four sabotages caught: reverting to raw closes; downgrading a known-unappliable action to merely
unverified; discarding every unverified window instead of labelling it; and classifying over the
calendar target instead of the filling session.

---

## AQ11 — it is not waiting for a market session

### What the record said

> "The live prospective trace is still open: the ledger is empty and 2026-10-11 is Sunday, so no
> quote can meet the one-minute evidence window."

That reads as a calendar problem. It is not, and a Monday does not fix it.

### What `actionable_gate` actually requires, and what exists

| Required fact | Source in this platform |
|---|---|
| live two-sided quote **with displayed ask size** | none — `option_chain_history` is a DAILY archive with `nbbo_bid`/`nbbo_ask` and **no size columns**; UW's chain endpoint is `as_of`-historical |
| `deliverable_verified` | none |
| `account_permissions_verified` | none — no options permission level is recorded anywhere |
| `available_options_buying_power` | none — the identifier appears only inside the ledger module that requires it |

`long_option_playbook.build()` already says this out loud: it hardcodes
`live_two_sided_quote_and_ask_size_unavailable`, `deliverable_unverified`,
`options_permission_unverified` and `account_risk_and_buying_power_unverified` as blockers on
**every** plan it produces.

And separately: **`enqueue_if_eligible` has no production caller.** Nothing in the scheduler, no
route, no job. Same shape as the M25 blocker already recorded here, where enabling a flag would
have silently stopped every broker entry.

### What was built instead of a producer

Not a producer. Building one would mean supplying `account_permissions_verified` and
`available_options_buying_power` from nothing, which is fabricating the evidence the gate exists
to require. The gap is account integration and a live sized quote feed, and both are decisions
about cost and scope rather than code to write tonight — UW option chains already consume ~87–89%
of the daily quota.

What was built is the thing that makes the absence **legible**: `option_prospective_capture` in
the M24 capability matrix (`shared/db/capability_checks.py`), with real schema checks rather than
asserted booleans. An empty ledger and an unbuildable ledger look identical from the outside, and
that identity is precisely what kept AQ11 reading as "waiting for a session". It now reports:

```
option_prospective_capture -> not_ready
  not_ready | live sized option quote    | option_chain_history is missing nbbo_bid_size, nbbo_ask_size
  not_ready | account options perms/BP   | users is missing options_permission_level, available_options_buying_power
  ready     | prospective capture ledger | table 'option_strategy_captures' present
```

This is the first capability in that matrix that is **not schema-provisionable** — a fully
migrated database does not make it ready. The readiness test therefore no longer asserts
"healthy database ⇒ everything ready"; it names this capability explicitly, so a second
capability going permanently red cannot hide behind a pattern match. Two sabotages caught:
pointing the data-source checks at columns that do exist, and removing the reasons from the
blocked description.

**Still out of scope and unchanged:** multi-leg economics, assignment, broker fills. No capture
was manufactured. Nothing was sent.

---

## Deployment verification (commit `28c04853`)

All 13 services rebuilt and recreated; 15/15 containers healthy; **0/12 drifted or errored**
from `scripts/check_deploy_drift.sh`, which compares each container's full `/app/src` AND
`/app/shared` tree against the local tree at HEAD. The frontend is outside that denominator by
design — tier 433 was confirmed in the SERVED chunk
(`/app/.next/static/chunks/pages/improvements-c809c5db0e949b4f.js`), never the webpack cache,
which the script's own closing note warns about.

An earlier, narrower check is superseded by that run but worth keeping for what it establishes
on its own: the three changed `shared/` files hash identically across all twelve backends and
match local (`5aa8955a…`), and signal-engine's `calibration.py` matches byte-for-byte
(`a07ddf41…`).

| Check | Result |
|---|---|
| `idle_in_transaction_session_timeout` on a live app connection | `5min` |
| client backends carrying it | 26 |
| blocked sessions | 0 |
| idle-in-transaction > 60s | 0 |
| migration warnings / "not proceeding unserialized" | 0 |
| `option_prospective_capture` | `not_ready`, both data-source requirements failing with named evidence |
| pages `/`, `/improvements`, `/quality-value` | 200 |
| tier 433 in the served static chunk | present |

**The deploy did not exercise the lock under contention, and the report should not imply it
did.** Zero services logged a wait. `shared/` changed, so every image rebuilt from a cold cache
and the twelve startups were naturally minutes apart — the opposite of the cached, fast-build
deploy that produced the convoy. The contention path is covered by the five sabotages, not by
this run.

So it was tested directly against the production database instead, across two real sessions,
using the shipped lock key: while session A held it, a separate session was **refused**
(`granted: false`, one holder visible in `pg_locks`); after A finished the same call was
**granted**; and no advisory lock remained afterwards. An earlier attempt at this check was
invalid and is recorded as such — both calls ran in one session, and Postgres advisory locks are
re-entrant within a session, so it returned `true` for a reason that had nothing to do with the
lock working.

The AQ11 readiness finding also reproduces against the **production** database rather than only
a test fixture: `option_chain_history` has no size columns and `users` has no options permission
or buying-power columns there either.
