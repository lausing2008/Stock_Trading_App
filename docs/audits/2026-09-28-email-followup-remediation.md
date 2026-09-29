# Email remediation follow-up — EF-01…EF-05, three of them mine

Remediates [`2026-09-28-email-remediation-followup-review.md`](2026-09-28-email-remediation-followup-review.md),
a review of the EA-01…EA-12 work at `c2703484`. Evidence:
[`evidence/2026-09-28-email-remediation-followup.json`](evidence/2026-09-28-email-remediation-followup.json),
[probes](evidence/2026-09-28-email-remediation-followup.py). Fixed in `0ec2ceba`.

## The shape of this one

Five findings. **Three are regressions I introduced while fixing EA-01 and EA-06**, and one is
a weakness in the very test I wrote to make EA-05 durable. The EA work was right about the
defects and wrong about parts of its own repair, which is the honest summary.

| | Finding | Whose |
|---|---|---|
| **EF-01** | `except ... as` deletes its name on exit; reading `_send_exc` later raised `UnboundLocalError` **inside the error path**, which the outer handler caught — **aborting the whole batch**. One failing subscription starved every recipient after it | mine, from EA-01 |
| **EF-02** | `prices.get(_ua.symbol, _ua.threshold)` displayed the **configured threshold as the current price**, and retry symbols were absent from the fetch so the default always won | mine, from EA-06 |
| **EF-03** | `price_alerts` holds technical conditions too. A **successfully delivered** one-shot MACD alert matched the retry query exactly like a failed price alert | mine, from EA-06 |
| **EF-04** | The enforcement ratchet matched **raw source**, so a type named only in a comment counted as enforced | mine, from EA-05 |
| **EF-05** | `(ask_prem or 0.0)` turned an **absent** side into a measured zero → `("ask", 1.0)`, maximum confidence from one reported number | pre-existing, missed by EA-09 |

## Production exposure, checked before fixing

EF-02 and EF-03 were **live**. At the time of the review:

* rows the retry query would have picked up: **0** (the 24-hour window excluded the 60 legacy
  rows)
* `alert.retrying_undelivered` events since deploy: **0**

So nothing was mis-sent. That is luck about timing, not a property of the code — a technical
alert triggering in that window would have been re-emailed through the price renderer with the
raw enum and a fabricated price.

## What changed

**EF-01.** The message is captured as a plain string inside the handler, initialised before the
`try`. Acceptance verified directly: first recipient raises, second still delivers, the first
transition stays pending, the batch completes.

**EF-02.** Retry symbols join the price fetch. With no quote the notification **waits** rather
than inventing one — it is already undelivered, so waiting costs nothing and the 24-hour window
still bounds it. Delayed mail now says it is delayed and gives the original trigger time.

**EF-03.** The retry is restricted to the crossing families this job can render; technical
sends record delivery under the same contract; and a migration closes out legacy rows. That
last part matters: `last_sent_at` only began recording delivery on 2026-09-28, so every earlier
triggered row has it null whether or not its mail went out. The review's own guidance is not to
read every legacy null as a failure, so those rows are stamped from their trigger time —
marking them not-retryable and saying their delivery is unknown, rather than asserting success.

**EF-04.** Comments and docstrings are stripped, and — more importantly — the inventory is
**labelled as an inventory**. An AST check now requires every gate call to be the negated
condition of a branch that skips, so a call whose result is discarded fails, and seven tests
exercise the gate's decision directly.

A full job run would be better still and does not compose here: the review's probe supplies
real module objects while this service's conftest stubs the same modules suite-wide. That limit
is written into the test rather than papered over.

**EF-05.** Missing returns `"unknown"` and yields no candidate; a genuine measured zero still
classifies. The dark-pool template stopped calling an inferred aggressor side a "measured fact"
while keeping its existing disclaimers.

## Residuals from the same review

* The plan validator now requires the target above the **breakout** entry — only the two
  pullback entries were checked — and no longer carries a comment promising a reward/risk floor
  it never enforced.
* **Sector rotation, EA-07's sibling**, no longer advances its emerging set before delivery.
* The **flow digest** carries the unsubscribe footer for the type it now advertises.

## Verification

8 sabotages, all confirmed red then green.

**Two pre-existing tests caught a regression in my first sector-rotation attempt.** Moving the
state write below the send loop put it after the `if not newly_emerging: return` guard, so a
sector *fading out* stopped being recorded — and a faded sector that is never removed can never
re-alert. Resolved with the always-resync-minus-failures pattern `check_short_squeeze_alerts`
already uses: a fade-out is a fact independent of delivery; a new arrival is the thing that must
survive to be retried.

**Eighth prose collision**, and it fooled both the test and my own verification: the comment
explaining the fix *quotes the guard it describes*, so a raw `index()` found the prose first.

T401 ratchet **lowered 181 → 180** — a threshold-pinning assertion became behavioural, and the
gain is locked in rather than left spendable.

## Still open, and named as such

The review's own list, unchanged by this work: the transactional outbox, lease ownership and
delivered-versus-accepted state (EA-12); immutable event IDs with separate watch and actionable
TTLs (EA-10); separating candidate diagnostics from delivered-alert outcomes from executed
trades (EA-11); and EA-08's deeper point that freshness still tests *price availability* rather
than the age of the stored signal and every decision input.

**The 69 consumed transitions are not recovered.** The review's recommendation — do not bulk
clear `last_signal`; build a manifest and a reviewed recovery digest instead — is right, and it
also caught a flaw in the reset I had floated: the transition logic specially recognises
`None→BUY` but not `None→SELL`, so clearing the field would not reliably produce an alert
anyway.
