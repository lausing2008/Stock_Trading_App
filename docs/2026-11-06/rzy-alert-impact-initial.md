# RZY alert impact: initial bounded check, Nov 2–6

Production access was read-only (explicit `BEGIN READ ONLY`, 20-second statement timeout).
No application changes, push, or deployment. This extends attribution commit `aee67f15`.

## Findings

Among same-day, incrementally observed 5m events, 97 had a material revision to their signal
candle after the scan cutoff. Only 48 had that revision **after event persistence**, avoiding
an obvious scan/ingest race in the other 49. These are exposure candidates, not proof that
all 48 used a partial candle: later ordinary provider corrections also qualify.

| Session | After scan cutoff | After event persistence |
|---|---:|---:|
| Nov 2 | 32 | 5 |
| Nov 3 | 7 | 7 |
| Nov 4 | 39 | 21 |
| Nov 5 | 4 | 2 |
| Nov 6 | 15 | 13 |

For the 48 persisted before correction, live occurrence records and latest event payloads show:
- 45 exist in the current stream generation with exactly identical tracked figures.
- 3 have a latest occurrence generation older than the current stream generation:
  NVDA EXPIRED (Nov 2 11:30 ET), CM EXPIRED (Nov 4 11:40 ET), and AVGO ARMED
  (Nov 4 12:30 ET). None appears in the exported email-alert path.
- These three absences are not attributed to a specific revision. In particular, CM survived
  from generation 57 through 60 before disappearing by generation 67.

One candidate was recorded and accepted by the mail provider before the correction:
**ANF REVERSAL_WATCH, Nov 4 09:35 ET signal**. Recorded 09:38:26 ET, accepted 09:39:13 ET,
corrected 09:40:27 ET. The candle's high changed 161.30 → 164.60 and close
161.195 → 164.17. The event persists from generation 94 through current generation 110,
with every queued tracked figure exactly unchanged. Acceptance is not confirmed inbox delivery.

BEPC TRIGGERED (Nov 6 16:00 ET) is the other emailed cutoff-based candidate. Its correction
at 16:02:30 preceded persistence at 16:02:33, so its consumed values are ambiguous. Its queued
tracked figures also match the current event exactly.

## Limits and next evidence needed

This is not a completed causal replay or proof that all alerts were unaffected. It covers
5m incremental events with corrections to the signal candle, including the closing candle;
it excludes earlier bars affecting later events, 15m/1h streams, and morning replay events.
Current-generation comparison cannot reconstruct every intermediate rebuild. Archived event
versions are pruned. Non-tracked payload fields were not compared.

To finish step 2, reconstruct the actual pre/post-rebuild inputs and compare the first affected
rebuild (or use retained immediate-generation snapshots), including indirect and higher-timeframe
impacts and morning rebuilds. Saved revision history alone does not establish the exact database
snapshot each scanner consumed. Missing historical snapshots may limit causal attribution.

Option B remains a correctness proposal, not implemented here. A preliminary concern about
unchanged fetches was resolved by reading the implementation: `record_writes` advances
`last_written_at` when an unchanged same-provider resend crosses `_owned_from`; for intraday bars
that boundary is completion. The opening module comment does not describe this exception.

## Evidence and validation

Inputs: existing local `scratchpad/rzy/rzy-session-2026-11-02` through `2026-11-06` exports.
`rzy-step2-evidence/find_candidates.py` reproduces the cutoff-based candidate selection using
that original local path. `query.sql` reads the 97 candidate occurrence IDs using indexed joins;
`candidate-outcomes.csv` is its successful complete output, and `results.json` records comparisons.
Exact figure comparison ignores `failed_rules`, which is additional first-observation metadata,
and checks the queued figures when available, otherwise the original figures.

Two preliminary broader queries timed out and their partial outputs were discarded from the
saved evidence. The final bounded query completed successfully and returned all 97 IDs.
Counts were reconciled across the five exports and database results. No runtime tests were
needed because application code was unchanged.
