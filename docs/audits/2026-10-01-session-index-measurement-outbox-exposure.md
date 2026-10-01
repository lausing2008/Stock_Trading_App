# Session index — measurement contract, durable delivery, exposure reservation (2026-10-01)

START HERE for the 2026-10-01 session. Everything below is committed on `prod`.

## What was asked, in order

1. Create calendar check-backs for the remaining work and the data-gated items.
2. Review and implement `2026-09-29-jev-integration-and-ab-testing-design.md` and
   `2026-09-30-measurement-framework-and-improvement-backlog.md`.
3. M20 (durable delivery), then M15 (synthetic concentration tests).
4. Document, update the tracker, deploy.

## What shipped

| Area | Where |
|---|---|
| Measurement contract + M01–M25 register | [measurement-contract-and-register.md](../features/measurement-contract-and-register.md) |
| Durable notification delivery + earnings integration | [notification-outbox.md](../features/notification-outbox.md) |
| Concentration defect and its fix | [concentration-cap-stale-snapshot.md](../incidents/concentration-cap-stale-snapshot.md) |
| M15 measurement and the five findings | [2026-10-01-portfolio-concentration-synthetic-tests.md](2026-10-01-portfolio-concentration-synthetic-tests.md) |
| Calendar check-backs | [2026-10-01-checkback-calendar.md](2026-10-01-checkback-calendar.md) |

## The one defect found

**M15-DEFECT-CONCURRENT-CAP.** Two individually valid entries opened **20.02% of equity in one
sector against a 15% cap**, because every candidate in a scan cycle was sized against the same
pre-loop snapshot. Affected the organic scan *and* conditional orders — independent writers with
no shared view. Fixed with atomic exposure reservation under a portfolio row lock. It was found
by writing the tests the register had been asking for, not by anything failing loudly.

## Claims I made that did not survive review

Recorded because the pattern matters more than any single correction.

1. **"The outbox closes the retrospective-article risk."** It does not. An outbox delivers
   whatever event the classifier *selected*, reliably, including a wrong selection. Event
   identification is M19/M23 and needs its own acceptance criteria.
2. **"Jev and the ablation grid are blocked on M20."** They are not. Shadow and paper arms need
   durable decisions, assignments and outcomes — not email.
3. **"A missing mark understates exposure by 50%."** That is a property of the fixture. The
   absolute shortfall is unbounded; the percentage of current market value stays below 100%.
4. **"Exits are not gated by entry caps."** Verified only for `_monitor_positions`. Source
   inspection of one function cannot establish it for manual exits, liquidation,
   conditional-order exits or broker closes.
5. **"`committing` protects in-flight capacity."** It does — but by the **row lock**, not by
   visibility. A second worker reads the row as `reserved`, because `begin_commit` runs inside
   the entry's own uncommitted transaction.

## A pre-existing defect that correction #5 exposed

`_place_broker_entry` runs inside `_open_paper_trade`, and **`_open_paper_trade` contains no
`session.commit()`**. A broker order is therefore submitted from an uncommitted transaction. A
crash after acceptance leaves a real accepted order, no local trade row, and capacity that
expires. Its own comment claims the trade is "already committed by the caller before this
function runs" — incorrect for this path.

Pre-existing, not caused by the reservation work; the reservation made the boundary visible.
Now the concrete head of **M25**, to be closed with the outbox's existing
reserve→dispatch→unknown→reconcile shape rather than a competing mechanism.

## Tests of mine that were wrong

- **A sabotage survived.** Removing the in-flight exclusion from `claim()` broke nothing,
  because the worker always quarantined first — untested defence-in-depth.
- **A probe captured a list by reference**, so a later scenario's sends appeared in an earlier
  one's result.
- **A function slice ran 1,300 lines past its target**, which would have reported the entry
  scan's caps as sitting in the exit path.
- **A mixed-writer test was vacuous** — the conditional order was rejected by an earlier gate,
  so nothing raced.
- **A fixture leaked a reservation** into the next scenario and made a valid reservation look
  like a `sector_cap` refusal.

## What is deliberately NOT done

The earnings outbox rollout flag is **off** and no scheduler job calls its producer or worker.
`require_fresh_marks` is **off** and now measured in shadow. Pending-order exposure, FX and
options assignment remain open and must share the eventual execution model. M02 recovery markers
and the M21 digest remain untouched and unauthorised.

## Verification

4,804 tests pass; 17 PostgreSQL concurrency tests pass as a **required** CI job. Nineteen
sabotage runs across the session, all caught except the one named above, which was then covered.

## Deployed 2026-10-01

| Check | Result |
|---|---|
| EC2 `HEAD` | `b0a46ea4` (from `f7609926`) |
| Backend rebuild | **All 12** rebuilt, recreated, healthy — `shared/` changed, so a full-fleet rebuild was required |
| Deploy drift | **0 of 12** |
| New tables | `notification_outbox` and `portfolio_exposure_reservations` both created by `create_all()` |
| Earnings outbox rollout flag | **absent = off**, unchanged |
| `require_fresh_marks` | **off**, unchanged |
| Containers | 15 healthy, **0 unhealthy** |
| Exposure module against production | Read-only probe over 11 active portfolios: queries, `committed_value` and `reconcile` all work against the live schema |
| Frontend | Rebuilt with `DOCKER_BUILDKIT=0`; Tier 404 present in the built bundle |
| Public site | `lausing.com` 200 |

**THE RESERVATION PATH HAS NOT YET RUN IN PRODUCTION.** The deploy happened outside US market
hours, so `_open_paper_trade` was never reached — there were no entry attempts to exercise it.
Zero tracebacks and zero exposure-related errors since restart, and the module itself queries the
live schema correctly, but *that is not the same as a live entry having been sized through it*.

The first real exercise is the next US session. What to look for then: `paper.entry` and
`paper.skip_sector_cap` appearing normally (the reservation **fails closed**, so a broken table
would stop entries rather than over-concentrate), and `paper.exposure_stale_mark_shadow` lines
beginning to accumulate. **The fresh-mark observation window starts at confirmed emission, not
at this deploy.**
