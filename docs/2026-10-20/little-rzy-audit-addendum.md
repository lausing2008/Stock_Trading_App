# Little RZY audit continuation — 2026-10-20 Pacific

**Current conclusion: ten prioritized findings across the two audit reports — three high priority and seven medium priority. No fixes made.** The [original eight findings](../2026-10-19/little-rzy-independent-audit.md) still apply to their unchanged code. This supplement reviews the newly added evaluation framework at **`cfb40e0b`** and documents two additional matched-control defects.

## Scope update

Since the original audit of `f51fdb2c`, commit `cfb40e0b` added `rzy_evaluation.py`, the evaluation runner, tests and a first real-data results document. Hash comparison confirmed that `little_rzy.py`, `rzy_data.py`, `rzy_workflow.py` and `rzy_backtest.py` still match the earlier audited versions. This continuation does not repeat the historical production observations as current October 20 measurements.

The new evaluator uses `_FullHistory` with effectively unlimited position/structure retention. Therefore **RZA-03's pruned-accounting defect applies to the existing `rzy_backtest.py` and its CLI, not automatically to this new evaluator's reported 693 trades**. It also splits source bars into contiguous segments, avoiding RZA-04's direct-replay gap problem in this runner. Neither change fixes the original API/CLI paths.

The previous statement that no real-data comparisons had been run is now historical. [The new run](../2026-10-19/little-rzy-evaluation-results.md) reports all five comparisons using stored real bars and explicit hypothetical execution/borrow assumptions. That is useful descriptive research; it does not establish verified live fills, prospective performance or chronological held-out validation.

Validation for this supplement: **5 evaluation tests passed in 30.91 seconds**, plus the local reproductions below. Earlier audit: 60 backend and 18 frontend tests passed. These results are separate runs, not a full-platform rerun. No production evaluation was run for this supplement.

## RZA-09 — medium: control borrow continues after an early exit

**Location:** [simulate_control](../../services/market-data/src/services/rzy_evaluation.py#L104), especially line 118.

The loop may exit at a stop/target on bar `k`, but short borrow is calculated through `bars[last].closed_at`, where `last` is the holding cap. It does not use the actual exit bar. The simulated exit price is correct for the early stop/target, but its borrow cost covers additional time when that control is already flat.

**Reproduced:** zero commission/slippage/spread to isolate borrow, default assumed annual borrow 0.5%, initial risk 1, short fill 100, protective stop 101 hit on the entry bar. Actual held time under the simulator's bar-close accounting is **300 seconds**, but the five-bar holding cap charges **1,500 seconds**. Reported control R is **−1.000023782343988**; using the actual exit bar gives **−1.0000047564687975**. The difference is small for this intraday example but grows with longer holding caps. This does not quantify the effect on the committed real-data headline.

This depresses short-control results and can inflate the measured strategy-minus-control edge. Also, controls hardcode `ASSUMPTIONS['borrow_rate_annual']`, while `research_bars` permits a custom borrow argument; custom-rate research needs a single explicit cost contract.

**Recommended follow-up, not implemented:** retain the actual exit index/time and apply the same explicit borrow rate and elapsed-time convention as the matched strategy trade. Regression: early short stop, early target and time-cap exit with identical cap lengths must accrue only their actual holding periods.

## RZA-10 — medium: truncated controls violate the matching window

**Locations:** [simulate_control cap](../../services/market-data/src/services/rzy_evaluation.py#L104) and [matched_controls candidate pool](../../services/market-data/src/services/rzy_evaluation.py#L123).

The candidate pool includes every trigger index before the last bar, regardless of the real trade's holding length. `last = min(len(bars)-1, t+holding)` silently shortens controls near the end of a segment. Those controls are scored as completed trades, even if neither bracket was reached, without a censoring flag.

**Reproduced:** request a **ten-bar** matched control; the selected trigger leaves **one bar** available. The helper returns a finite scored control of **+0.20R** rather than excluding or identifying the shortened sample. The report says controls use the real trade's holding length; this sample does not.

The changed exposure can alter outcomes and costs in either direction. Actual strategy positions unresolved at a segment end are separately counted/excluded, so truncated controls also receive different boundary treatment.

**Recommended follow-up, not implemented:** require the complete planned control horizon to be observable before selecting the trigger, or define and report a consistent censoring policy for both arms. Regression: incomplete-window controls must not silently contribute to the ordinary full-window mean.

## Remaining methodological/evidence boundaries

- `evaluate_streams` replays each complete segment and reports monthly slices; it does **not** implement chronological training/held-out or walk-forward partitions. The older `compare()` has partitions but still has RZA-03. A month-by-month descriptive breakdown does not close the spec's out-of-sample requirement.
- Matched controls use the real trade's observed holding length and may select times later in the same segment. This is an explicitly retrospective diagnostic, not a causal trading rule or prospective forecast.
- The new framework documents independent-trade bootstrap assumptions, overlapping streams, multiple comparisons, survivors-only universe and hypothetical execution/borrow. Those caveats remain material. No statistical change to the headline is asserted without corrected calculations and an appropriate rerun.
- The committed results refer to raw outputs under EC2 `/tmp/rzy-eval/`; the runner report includes configuration/version and coverage, but no immutable input snapshot or complete source/content hashes. Provider restatements can prevent exact reproduction later. Archive inputs, output artifacts and code identity before treating this as a reproducible research baseline.
- Original findings remain: mixed generation history, stale aggregate eligibility, old backtest accounting, replay input/gap validation, cancellation email classification, stale-native fallback and disappearing chart triggers. Exact anchor confirmation disclosure and paper-ledger provenance also remain incomplete.
- Actual digest receipt and current post-session rebuild counts were not measured in this continuation. Earlier read-only production counts belong to the timestamp in the original evidence, not to an assumed successful Tuesday maintenance run.

## Reproduction and disposition

Run locally from the repository root:

```sh
python docs/2026-10-20/little-rzy-evaluation-audit-probe.py
```

The [saved evidence](little-rzy-evaluation-audit-evidence.json) contains both results. This probe imports the shipped pure evaluation functions and uses synthetic local bars; it does not connect to a database, send a message or mutate application code. Existing evaluation tests were run with `python -m pytest -q tests/test_rzy_evaluation.py` from `services/market-data`.

Recommended order: address the original high-priority correctness issues first; then repair control accounting/boundary matching before interpreting revised edge estimates, and complete chronological and prospective acceptance. This audit changed only reports, reproduction/evidence artifacts and the documentation index. No fixes, commits, pushes, deployments or production writes were performed.
