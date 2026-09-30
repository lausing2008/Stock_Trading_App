# Signal-outage recovery manifest — read-only (2026-09-30)

**Nothing was sent. Nothing was reset. No production row was written.** This document is evidence
for a decision; it does not make one, and it does not authorise a send.

Built at the reviewer's direction: *"preparing the read-only recovery manifest is the right next
step… It should distinguish historical missed events from current conditions, identify duplicates
and recoverable evidence, and propose a digest without sending anything or resetting state."*

Evidence: [manifest script](evidence/2026-09-30-signal-outage-recovery-manifest.py) (SELECTs only),
[results](evidence/2026-09-30-signal-outage-recovery-manifest.json).

## The headline

**18 recoverable events, for one recipient, across 15 symbols — and every one of them is a SELL.**

Not one BUY is both consumed and still valid today. That is the single fact a decision should turn
on, and it was not knowable before this reconstruction.

## What "69" actually counts — it does not reproduce exactly

The figure carried through every previous document was **69 consumed transitions**, never
independently recounted. Reconstructing from `signals` history against `signal_alerts` state:

| Measure | Count |
|---|---|
| Subscriptions total | 362 |
| Missed transitions of any kind | **202** |
| ...across subscriptions | 160 |
| ...across symbols | 111 |
| **Actionable** (ended in BUY or SELL) | **65** |
| — of which BUY | 29 |
| — of which SELL | 36 |

**65, not 69.** Close enough to suggest 69 counted actionable transitions on a slightly different
window, and far enough that it should not be reported as reproduced. The gap is unexplained; the
honest statement is that 69 was never verifiable and 65 is what the surviving evidence supports.

Two measurement corrections were needed to get even this far, and both changed the answer
materially:

- **Dates must be converted to America/New_York before grouping.** `signals.ts` is naive UTC, so a
  UTC `date()` puts every evening run on the next calendar day — measured: 168 rows dated
  2026-09-30 that are really 2026-09-29 evening ET. Grouping in UTC splits one trading day across
  two buckets and manufactures transitions that never happened. See
  `docs/incidents/utc-vs-et-date-boundary.md`. First pass, in UTC: **306** events. Corrected: 202.
- **Weekend runs must be excluded.** The scheduler wrote full 720-row signal sets on Saturday
  2026-09-20 and Sunday 2026-09-27, plus a 4-row partial on 2026-09-26. Those are scheduler
  artifacts, not market events.

**The outage was two trading days, not four.** Derived from send history rather than assumed:
`signal_alerts.last_sent_at` has sends on 09-24 and again on 09-29, with **09-25 (Fri) and 09-28
(Mon)** empty. The "four days" of earlier documents is the calendar span 09-25 → 09-28, which
includes a weekend.

## Historical miss vs current condition — the classification that matters

Of the 65 actionable events:

| State | Count | What it means | Action |
|---|---|---|---|
| **Consumed** — `last_signal` advanced, no send | **37** | The real harm. The system believes the reader was told. | Candidate for recovery |
| **Still pending** — `last_signal` never advanced | **9** | Will re-fire on its own on the next run. | **None. Leave alone.** |
| Moved on further since | 19 | The signal has changed again; the missed event is history. | None |

Then, of the 37 consumed:

| | Count |
|---|---|
| Consumed **and still the current signal** | **18** |
| Consumed but the signal has since changed | 19 |

**Only 18 of the original 65 are both genuinely lost and still true today.** The other 47 need
nothing: 9 will fire by themselves, and 38 describe a market state that no longer exists — telling
someone about those now would be reporting stale news as if it were current, which is the specific
failure the previous reviews warned against.

## The recoverable set

All 18 are **SELL** — exits, not entries.

| | |
|---|---|
| Recipient | 1 (`stockai2028@gmail.com`) — 64 of 65 actionable events belong to this one account |
| Distinct symbols | 15 |
| Duplicates across horizons | SOUN ×3 (SWING/GROWTH/SHORT), XAR ×2 (SWING/GROWTH) |
| Markets | 13 US, 5 HK |
| Event dates | 09-25 ×6, 09-28 ×12 |
| Undeliverable | 1 actionable event has a NULL email and cannot be recovered at all |
| Inactive accounts | 0 |

```
1810.HK SWING   WAIT->SELL 09-25      AXON  SWING   WAIT->SELL 09-25
1879.HK SWING   WAIT->SELL 09-28      CLBT  SWING   WAIT->SELL 09-28
3896.HK SWING   WAIT->SELL 09-28      DIVO  SWING   WAIT->SELL 09-28
6651.HK SWING   WAIT->SELL 09-28      IBM   LONG    WAIT->SELL 09-28
9880.HK SWING   WAIT->SELL 09-25      IMVT  SWING   WAIT->SELL 09-28
AMADY   SWING   WAIT->SELL 09-25      LMT   SWING   WAIT->SELL 09-25
SOUN    SWING   WAIT->SELL 09-28      SOUN  GROWTH  WAIT->SELL 09-28
SOUN    SHORT   WAIT->SELL 09-28      UPST  GROWTH  HOLD->SELL 09-25
XAR     SWING   WAIT->SELL 09-28      XAR   GROWTH  WAIT->SELL 09-28
```

## Why "all SELL" changes the decision

Earlier work established a direction-dependent rule: **failing open is right for an EXIT and wrong
for a new BUY.** Withholding an exit costs a reader the chance to reduce risk; emitting a BUY on a
check that never ran asks them to commit money.

The recoverable set is entirely exits. That makes the case for a digest *stronger* on the harm
side — these are risk-reduction signals the reader never saw — and simultaneously removes the
objection that a recovery send would manufacture stale buy recommendations. There are none to
manufacture.

It also means the digest does not need to pass today's BUY gates (freshness, consensus, conviction,
decision-engine veto), because it is not proposing entries.

## Proposed digest — NOT SENT, and not to be sent without explicit approval

One email, to one recipient, with a unique incident key so it cannot be confused with a normal
alert or re-sent by any existing job.

- **Subject:** states plainly that it is a recovery notice for a delivery outage, not a new signal.
- **Body:** the 15 symbols above, each showing the transition, **the date it occurred**, and **the
  signal as of today** — the two stated separately, never merged into one present-tense sentence
  (EC-02's lesson).
- **An explicit outage statement:** these were generated on 09-25 and 09-28 and were not delivered
  because of a defect on our side.
- **A staleness warning:** each is 2–5 trading days old. The reader must not treat a 09-25 exit
  signal as an instruction to act now at a price they cannot get.
- **No BUYs**, because there are none in the recoverable set.
- **Excluded and named:** the 9 still-pending events (they will arrive on their own), the 19 that
  moved on, and the 1 undeliverable subscription.

### What must NOT be done

- **No bulk `last_signal` reset.** Besides discarding real state, the transition logic recognises
  `None→BUY` but **not** `None→SELL`, so clearing the field would not reliably produce an alert for
  a set that is entirely SELL. It would silently do nothing for most of them.
- **No re-use of the normal send path**, which would stamp `last_sent_at` and make a recovery
  indistinguishable from an ordinary alert in the record.

## Limits of this manifest

- It reconstructs from `signals` history and current `signal_alerts` state. **No give-up log
  survives** — containers have been recreated many times since. So "consumed" is inferred from
  `last_signal` matching the post-transition value, not read from a log of the event.
- That inference cannot distinguish a transition consumed by the outage from one consumed
  legitimately by an earlier successful send whose `last_sent_at` is older than it appears.
  Cross-checking `last_sent_at` bounds this but does not eliminate it.
- **65 ≠ 69**, and the difference is unexplained.
- Signal *quality* is out of scope entirely. Earlier measurement found the SELL side of this
  platform's signals outperformed the BUY side, but none of that makes any individual SELL here
  correct. This manifest establishes what was not delivered, not what was right.
