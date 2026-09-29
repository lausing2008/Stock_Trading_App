# Post-deployment audit — 2 defects in the deployed code, one of them a conclusion I got wrong

An independent audit of the **deployed** code at `b5332a3`, run after the R01–R10 remediation
and its own pre-deployment fixes went live. Evidence and probes:
[`evidence/2026-09-28-postdeployment-evidence.json`](evidence/2026-09-28-postdeployment-evidence.json),
[`evidence/2026-09-28-postdeployment-reproductions.py`](evidence/2026-09-28-postdeployment-reproductions.py).
Fixed in `fa6095b`.

## 1. Settlement used a value the check never vouched for

`_settlement_close` did this, in order:

1. **read** the close for the expiry session → `99.90`
2. **corroborate** — which opens its *own* session and judges the state as it is *then*
3. **return the value from step 1**

The daily ingest's re-fetch window rewrites exactly that bar between 1 and 2 — atomically with
the later bar that makes `superseded_by_later_session` true. So the sequence runs: read 99.90,
ingest refreshes the bar to 100.10 and inserts the next session, corroborator sees that later
session and reports "final", settlement books **99.90**.

On a $100 strike that is the difference between **assigned** and **expired worthless**, and
`settle_expired_positions` closes the position permanently on it.

### This is a conclusion I got wrong at the previous audit

The pre-deployment audit raised settlement finality and I recorded it under *"Not a defect —
checked rather than assumed."* The reasoning was:

> `superseded_by_later_session` is sound because the daily ingest re-fetches from
> `head.date() - 7 days` and upserts with `DO UPDATE` on `close` — so any bar with a successor
> inside that week has been rewritten from finalised history.

Every word of that is true, and it does not support the conclusion. **The bar is final; our own
read happened before the rewrite.** I verified a property of the database and treated it as a
property of a value already taken out of it. I even wrote a test pinning the re-fetch window —
guarding the half of the argument that was sound, which is why nothing caught the half that
was not.

**The fix** restores one invariant: *the value returned is the value that was corroborated.*
Corroborate, re-read, settle only when they agree exactly. If the bar moved, corroborate the
new one instead. Bounded at two passes — a bar still moving after a full round defers, leaving
the position open for the next run, the same recoverable outcome this function already uses for
missing data. It cannot spin: the re-fetch window closes and the value stops changing.

## 2. The label date was still an estimate

The pre-deployment fix replaced calendar days with **business** days. That equals counting
**bars** only while every business day has one — and `df` holds whatever rows the price table
actually has. Gaps (holidays beyond the flat allowance, suspensions, dead-zone filtering, a
delisting pause) push the real target further out.

The audit's probe removes three sessions from a twenty-bar window:

| | |
|---|---|
| estimate said the label resolves | 2026-09-29 |
| the tenth bar forward actually is | **2026-10-01** |
| training cutoff | 2026-09-30 |

So the row is admitted while its label is built from a price past the boundary — the leak R01
exists to close, one approximation further along.

**There is no estimate now.** The label is `close.shift(-horizon)` over the frame, so the target
is the row `horizon` positions ahead *in that frame* — exactly knowable, because the frame is in
hand. When it cannot be resolved (the target bar has not printed, or the signal's own bar is not
in the frame) the row is **dropped**, not guessed. `label_end_date()` is deleted rather than
kept as a fallback nobody audits: it was dominated by a measurement everywhere it was used, and
it was wrong twice before it was removed.

`meta_trainer` carried the identical unit mismatch — unfixed last round because that pass
treated the two files as one change. It now reads its own bar index and skips a record whose
target bar has not printed.

## What the audit confirmed as correct

Worth recording, because these were the deploy's own claims:

* `mark_evidence` column and `uq_options_income_intent` index present, and the index
  `indisvalid` / `indisready` / `indisunique` all true.
* The historical price lookup returns real per-date closes — and the evidence file checks the
  *bar date* behind each, catching that `2026-08-29` correctly resolves to the `2026-08-28`
  bar rather than merely returning a plausible number.
* Revocation correct on all six controlled inputs, including same-second and missing-`iat`.
* Meta blend flag unset — the blend is off, failing closed.
* Drift 0 of 12.

## Verification

9 sabotages, all confirmed red then restored green. **Two survived the first pass**, and both
for the same reason — the assertion described the code's shape instead of its behaviour:

* *"an unresolvable row is kept instead of dropped"* passed because the check looked for
  `continue` inside a fragment, and the sabotage appended the row immediately before it.
* *"the observed exit date stops widening availability"* had no test at all.

Both now run the real availability block against controlled frames.

All 12 backend suites green, AUD-T401 ratchet still 181.

## The pattern across three audits

Each round found the previous round's fix incomplete in the same way — **a guess left where a
measurement was available**, and a test that pinned the guess:

| Round | The guess | What replaced it |
|---|---|---|
| R01 | `signal_date + horizon` in calendar days | business days |
| pre-deploy | business days | *(still an estimate)* |
| post-deploy | business days | the actual bar, or drop the row |
| R04 | the clock proves ingestion finished | corroborate the session |
| pre-deploy | corroborate the session | *(declared sound — wrongly)* |
| post-deploy | corroborate the session | corroborate **the value** |
