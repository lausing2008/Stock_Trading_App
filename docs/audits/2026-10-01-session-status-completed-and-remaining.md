# Session status — what is done, what is deployed, what is left (2026-10-01)

Written against the register in
[`docs/features/2026-09-30-measurement-framework-and-improvement-backlog.md`](../features/2026-09-30-measurement-framework-and-improvement-backlog.md)
(M01–M25), which is the authoritative list of outstanding work. This document only records
**state**; it does not re-argue any decision.

Three states are used, and the distinction matters more than usual here:
**deployed** (running in production) · **committed** (in `prod`, not yet in a container) ·
**open** (not built).

## Shipped and deployed

| Item | What | Register |
|---|---|---|
| AUD-RECOVERY-LIFECYCLE | Recovery grant is spent by an ENTRY, not an attempt. Reserve (atomic, 15 min) → consume (7 days) only on a real `_open_paper_trade`. | M01 |
| AUD-CONVGATE-IDENTITY | A cached conviction result must be about the signal being evaluated. Bound to `sig.ts`; a stale record is treated as *missing*, never as permission. Producer writes `gate_passed`. | M03 |
| AUD-ANTICHASE-FUNNEL | Three counters — reached / rejected / **authoritative** rejected — split by decision source, because `_should_enter()` is a shadow comparison when the DE is primary. | M04 |
| EC-03 | Migration readiness: failures recorded, `/health` reports a `migrations` block naming the degraded capability, and the price-alert retry asks before running. | M24 (partial) |
| MU-02 **v1** | Phase- and event-scoped earnings dedup, plural fetcher, bounded candidate window. | M19 (partial) |
| MU-01 instrumentation | `earnings.history_fetch` / `earnings.history_map` record the mapping outcome. | M19 (partial) |

## Committed, NOT deployed

| Item | What | Register |
|---|---|---|
| MU-02 **v2** | Issuer binding, period extraction for reconciliation (incl. full-year), recorded exclusion reasons, abstention on overlapping events, abstention measurement + unresolved-set key. | M19 |

**The v1/v2 distinction is deliberate and should be preserved**: v1 is running and its live
behaviour is still unverified; v2 has never run in production.

## Measured, not built — findings that changed a belief

| Finding | Result |
|---|---|
| Dark-pool relative threshold | Rejects **15 of 9,707 evaluable** prints (~0.15%; 15/9,710 of all absolute-pass prints, with **3** no-baseline shown separately). For **55 of 57** symbols `5 × median` sits *below* the $1M absolute floor, so the bar cannot bind. (M05) |
| Confidence calibration | Do not promote. My "flat bands" reading was a **pooling artifact**; sliced as production slices there are 59 supported slices with wide dispersion. (M06) |
| Options-flow direction | "Bearish is anti-predictive" **withdrawn** — equal symbol/date weighting reverses the 5d gap. (M07) |
| GEX corroboration | My raw-return comparison was **backwards**; thesis-signed, corroborated is −1.87% vs +2.28%. Gate stays off. (M09) |
| Prebreakout | Blocker changed identity: not "too few events" but **too few names** — 8 names, AI 42%. (M08) |
| Signal-outage manifest | "69" does not reproduce: **65** actionable, of which **18** consumed *and* still current — **all SELL**. (M21) |
| Inferred fiscal period | New DQ item. MU's Q4 is stored as "Q3 2026". Traced: not a predicate, not a key, not in prompts, emitted but **unconsumed**. |

## Open — the register, unchanged

**P0/P1 engineering:** M13 ML `n_outcome_rows` attrition · M14 OOS-suppression inventory ·
M20 outbox, technical-alert retries, immutable event IDs · M24 readiness beyond the one job ·
M25 broker/order and options-accounting reconciliation.

**Collecting data, no code angle:** M08 prebreakout diversity · M10 squeeze ignition ·
M11 short-squeeze corroboration · M12 regime robustness (one bear sample) · M15 portfolio
concentration · M16 broker fill re-poll (occurrence-gated).

**Holds:** M06 confidence feedback · M07 flow inversion · M09 GEX gate · M17 eight-cell ablation ·
M18 watchlist rerun.

**Design:** M22 Jev · M23 event-linked news resolution.

## Awaiting an explicit decision — nothing done

- **M02 — recovery markers.** Portfolios 2 and 5 still hold markers written by the old bug. The
  register's recommendation is to clear **only portfolio 2**, with compare-and-delete against the
  inspected value, and to leave portfolio 5 pending a recovery-strategy review. **Not done.**
- **M21 — recovery digest.** Manifest built, read-only. No `last_signal` reset, no send.
- **M19 verification.** Needs the next instrumented sync (`07:00 ET` window) and a real earnings
  release to exercise phase delivery.

## Known unresolved risk, recorded rather than closed

A retrospective article **published today** about a past period passes issuer, freshness and
period checks and is classified as `results`. Freshness excludes old articles, not new articles
about old events. A test asserts this gap *exists* so it cannot be assumed closed; closing it
needs authoritative release identity (M20), not another heuristic.

## Process note

Across this session the sabotage runs caught **six weak tests of my own** before any reviewer did:
an ordering assertion that could not see an earlier write, a reimplementation of the logic under
test, two fixtures too narrow to drive the failure, and two assertions that pinned numbers in
source text and tripped the T401 ratchet. The ratchet baseline is unchanged at **180**.
