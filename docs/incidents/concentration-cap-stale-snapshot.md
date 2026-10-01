# M15-DEFECT-CONCURRENT-CAP — individually valid entries jointly exceeded the sector cap

**Found 2026-10-01** by synthetic-position testing (M15). **Fixed the same day.** No production
deployment is implied by either; this was found and fixed in the repository.

| Item | Status |
|---|---|
| Atomic reservation primitive | Verified on PostgreSQL |
| Organic / conditional integration | Verified: both production writers raced through their real paths |
| Crash reclaim vs in-flight commit | Verified: `committing` is never swept |
| Fresh marks | **Open** — mechanism built, OFF, now measured in shadow |
| Pending broker exposure, FX, assignment | **Open** |

## The defect

Entry candidates are sized against `prefetched_open`, a snapshot of open positions captured
**once** before the candidate loop (AUD19-PERF2, deliberately, to avoid a database query per
candidate). Within one scan cycle every candidate therefore evaluated the sector cap against the
same stale view, and **none saw the others**.

Reproduced with synthetic positions against the real entry path: two candidates, each sized at
~10% of equity against a **15% sector cap**, both passed and opened — **20.02% of equity in one
sector**. Each entry was individually valid. The cap was not.

## Execution modes affected

| Mode | Path | Affected |
|---|---|---|
| Organic paper entries | `_scan_for_entries` → `_open_paper_trade` | **Yes** |
| Conditional orders ("buy" action) | `conditional_orders.py` → `_open_paper_trade` | **Yes** — and worse: an independent writer with no shared view of the scan's in-flight entries |
| Portfolio backtest | `backtest/portfolio_backtest.py::_size_position` | **No** — a separate, declared subset that does not call this path (and so would not reproduce the defect either) |
| Live broker automation | — | Not applicable; no automated live-capital path exists |

Severity is bounded by the caps themselves — the breach is of a concentration limit, not of
cash or position count — but concentration limits are precisely the control that is supposed to
hold when several candidates look good at once, which is exactly when they fire together.

## Why re-querying would not have fixed it

A fresh read per candidate narrows the race window; it does not close it. Two writers — an entry
scan and a conditional order firing in the same moment — can both read, both find room, and both
write. **Check-then-act is only safe when the check and the act are one atomic step.**

## The fix: atomic exposure reservation

`shared/common/exposure.py` + `portfolio_exposure_reservations`. Inside a **row lock on the
portfolio**, `reserve()` reads committed exposure *fresh*, adds every active reservation, and
claims the proposed entry's value — all in one step. Lifecycle mirrors the recovery-grant
reserve→consume pattern already in this codebase:

`reserved` (claimed, no position yet) → `consumed` (position exists and now carries it, so the
reservation stops counting) / `released` (entry rejected; exposure returns immediately) /
`expired` (worker died; reclaimed at read time *and* by a sweep, so a crash cannot freeze a
portfolio).

**A first attempt at this fix did not work, and the witness caught it.** `reserve()` originally
took `committed_value` as an argument from the caller — who computed it from the same stale
`prefetched_open`. The defect reproduced unchanged. Committed exposure must be read *inside* the
lock; the caller now supplies only the valuation rule (`price_for`), not the row set.

## Verification

**The defect witness is preserved**, with the same fixture and the corrected expectation, so a
regression fails with the original symptom rather than vanishing:
`test_the_witness_fixture_no_longer_breaches_the_sector_cap`.

| Guarantee | Evidence |
|---|---|
| Concurrent entries cannot jointly exceed the cap | **PostgreSQL, 8 threads × 4% against a 15% cap → exactly 3 granted**, total 12,000, no errors |
| An aborted transaction claims nothing | PostgreSQL: 0 rows after rollback |
| One proposed entry reserves once | 6 concurrent same-intent attempts → 1 row, 1 `reserved` + 5 `already_reserved` |
| A crashed worker does not block forever | Blocks while live; freed at read time *and* by sweep; entries resume |
| No double counting | reserved 10,000 → consumed → reserved 0, committed 10,000 |
| Rejected entries release immediately | Released, not left to time out |
| Terminal reservations cannot be reused | Consumed cannot be released; expired cannot be consumed |
| Reconciliation surfaces orphans | A consumed reservation with no open position is reported |
| Fails closed on an UNVALUABLE position | Refuses the entry. **Unreachable from the live path today** — see below |
| Mixed-writer integration | Organic entry raced against the real `conditional_orders._execute_buy`: organic opened, conditional refused with `sector_cap`, combined within cap |
| Reservation carried through trade creation | Every reservation terminal, every consumed one pointing at a real open trade, none left `reserved` |
| Crash reclaim cannot free in-flight capacity | A `committing` reservation swept an hour past TTL: still held, new entry still refused, surfaced for review |

**Two of these cannot be established under the SQLite configuration used here** — which is a
statement about the driver and the locking available, not about SQLite. Under the default
pysqlite configuration the driver does not emit `BEGIN` for DML, so a released SAVEPOINT is
already durable and `rollback()` does not undo it (the documented `isolation_level=None` plus an
explicit `BEGIN` would restore it); and there is no PostgreSQL-equivalent row lock, writers
being serialised at the database level instead. Those two guarantees live in the **required**
PostgreSQL CI job, and the SQLite suite records the limitation rather than asserting something
it has not tested.

Five sabotage runs caught: committed read from a stale snapshot again; active reservations not
counted; consume leaving the reservation counting; expired reservations still blocking; an
unvaluable position no longer failing closed.

## Still open — the other four M15 findings

Deliberately **not** fixed here; each needs the eventual execution model rather than a competing
one invented now. See `docs/audits/2026-10-01-portfolio-concentration-synthetic-tests.md`.

1. **Pending/unfilled orders remain invisible** — needs outstanding entry commitments, with
   partial fills transferring exposure from reserved to held without double counting, and
   cancellations releasing only confirmed unfilled quantity.
2. **Missing marks fall back to entry price.** Two different kinds of not-knowing, which must
   not be conflated:
   - **Unvaluable** — no number can be produced, so the cap cannot be computed and the entry
     fails closed. **This is unreachable from the live entry path today**, because the
     production caller substitutes `entry_price` whenever a live mark is missing and so never
     reports a position as unvaluable. It guards a future caller that reports honestly.
   - **Fallback** — a number IS produced, from a substitute source. It is a number, and it is
     not current exposure; it can understate or overstate. **This is the case that actually
     occurs, and it is currently permitted to decide a cap.**

   `require_fresh_marks` → `exposure_stale_mark` is built and **off by default**. Shadow
   telemetry (`paper.exposure_stale_mark_shadow`) now records how often it *would* have refused
   an entry, so enabling it can rest on a measured block rate rather than a guess.
3. **No FX conversion** — single-currency assumption now enforced in one place; mixed-currency
   portfolios need timestamped conversion first.
4. **No order/assignment representation** — a prerequisite for broader broker/options
   automation; assignment's stock, cash and collateral consequences must be modelled explicitly.
