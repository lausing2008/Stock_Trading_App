# Pre-deployment audit of the R01–R10 remediation — 7 defects found in the fix itself

An independent review of the pending work at `0e72d3a`, run **before** it was deployed. Evidence
and controlled probes:
[`evidence/2026-09-28-predeployment-evidence.json`](evidence/2026-09-28-predeployment-evidence.json),
[`evidence/2026-09-28-predeployment-reproductions.py`](evidence/2026-09-28-predeployment-reproductions.py).
Fixed in `e77f7a2`.

The probes assert that each defect is **present**, so they are reproductions rather than
regression tests. All seven reproduced against the audited source (hashes recorded in the
evidence file and re-verified before touching anything); after the fix, six no longer reproduce
and the seventh was shown not to be a defect.

## Why this one matters more than its size

Every finding is in code written *to close* an audit finding, and shipped with tests that
passed. Two of those tests passed against code that **could not execute**:

* `test_a_historical_snapshot_does_not_reach_for_the_live_price` asserted that a clock guard
  appeared before a `SELECT` in the source — while that `SELECT` named a column which does not
  exist, so it raised on every call and the branch never ran.
* `test_a_failed_index_creation_does_not_block_startup` asserted `try:`/`except` around a
  handler whose `conn.rollback()` closed its own transaction — inside a function that returns
  before reaching it in production.

That is the AUD-T401 lesson at full strength: **a source-text assertion cannot distinguish
correct code from unrunnable code.** Both tests are now behavioural, against a real database.

## The seven

### Dead on arrival

| | |
|---|---|
| **The R08 price query** | `SELECT close FROM prices WHERE symbol = :sym AND timeframe = '1d'`. `prices` has no `symbol` column (it carries `stock_id` and joins through `stocks`), and the enum label is `D1`. Raised on every call. The today-path returns before reaching it and the caller's `except` swallowed the rest, so it looked fine. It also fell through to **today's live price for a historical date**, labelled `"live"` — the mixed-time valuation R08 exists to prevent. |
| **Both DDL statements** | Written inside `_seed_admin()`, which returns early when `admin_password` is unset — **and it is unset in production**. Neither `uq_options_income_intent` nor `mark_evidence` would ever have been created. The handler also called `conn.rollback()` inside `with engine.begin()`, which closes the transaction: every later statement would have raised, and the admin seeding already done in it would have been discarded. |

Now `_apply_isolated_ddl()`, called unconditionally from `init_db`, each statement in its own
transaction so a failure rolls back nothing but itself.

### Guards that stopped short — the same shape the R05/R07 findings were about

* **market-data's own `get_current_user` ignored the revocation marker.** It rejects disabled
  and deleted accounts from the live row, so the headline case held; what it missed is that a
  token issued before an admin **password reset** kept working on most of the platform. R07
  enforced the marker in `shared/common/jwt_auth.py` and this service's validator went around
  it. One marker, both validators now.
* **R06 locked `open_income_positions` and not `settle_expired_positions`** — which mutates the
  same `current_cash`.
* **R03 purged train→validation and stopped.** The early-stop rows choose the boosting rounds,
  so their labels must also predate the final slice.

### Wrong arithmetic

* **R01 measured the horizon in calendar days when it is in BARS.** Ten bars from 2026-09-14 is
  2026-09-28; the old expression said 2026-09-24, so rows whose labels resolve *after* the
  cutoff were admitted — the leak R01 exists to close, left open by a unit mismatch. New
  `label_end_date()` counts business days and adds a holiday allowance, erring late.
* **`X_dates_for_split` was misaligned after the dedup.** It re-derived dates with
  `df["ts"].iloc[X.index]` *after* `X = X[_keep].reset_index(drop=True)` — so it returned the
  first *n* rows of `df`, not the surviving ones. The R01 training cutoff and every range in
  `slice_date_ranges` were wrong whenever a row was dropped. Dates are now captured once, where
  `X.index` is still positional, and masked in lockstep, with a length check failing closed.

### A conflict between two of my own fixes

`resweep_oos_suppression()` re-applies `_compute_oos_suppression()` to stored metrics, and that
function knew nothing about R02's two new suppression reasons. **The nightly sweep would have
unsuppressed exactly the models R02 had just suppressed for leakage**, undoing the fix on its
first scheduled run. Evaluation validity is now a condition inside that single shared rule.
`None` means *unknown*, not valid, so pre-R02 artifacts are untouched and the measured 4-of-130
impact still holds.

## Not a defect — checked rather than assumed

The probe showing a stored settlement close (99.90 → assigned) against a hypothetical final
close (100.10 → not assigned) demonstrates the *arithmetic* consequence, not that the stored
close is stale. `superseded_by_later_session` is sound because the daily ingest re-fetches from
`head.date() - 7 days` on every incremental run and upserts with `on_conflict_do_update`
overwriting `close` — so any bar with a successor inside that week has been rewritten from the
provider's finalised history.

**That coupling runs across two files and is invisible from either**, so it is now pinned by a
test: shrink the re-fetch window and the corroboration becomes a guess with nothing in
`options_income_engine.py` changing.

## Verification

15 sabotages, all confirmed red then restored green. **Three survived the first pass** — the
settlement lock, the resweep condition and the early-stop purge each had *no test at all*. They
were written, then re-run red. A fix without a failing test behind it is a fix on trust.

All 12 backend suites green (6,929 tests), frontend 304 green, typecheck clean, AUD-T401 ratchet
still 181.
