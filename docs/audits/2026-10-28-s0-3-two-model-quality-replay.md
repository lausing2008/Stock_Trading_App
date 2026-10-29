# S0-3 — two-model ensemble quality: fix and forced-fallback replay

Date: **2026-10-28**. Branch: `f04-s0-3-two-model-quality`. Status: **deployed 2026-10-29 (`fa1a3e22`), released separately from fencing**.
Finding: [Stage 0 S0-3](2026-10-28-f04-stage0-inventory-and-no-model-trace.md).
Evidence: [`evidence/2026-10-28-s0-3-two-model-replay/`](evidence/2026-10-28-s0-3-two-model-replay/)
([manifest](evidence/2026-10-28-s0-3-two-model-replay/manifest.json)).

## The defect and the fix

`predict_latest_ensemble` (the `/ml/predict_ensemble` fallback) blended only the eligible member
correctly, but **reported quality averaged over both members with the suppressed one zeroed**.
- **Example:** an eligible model at AUC 0.65 was reported as 0.325. That is below signal-engine's
  0.50 cut-off, so its fusion weight was 0.
- **Fix:** `mean_model_test_auc` and `cv_auc_mean` are the eligible contributors' own, the same rule
  as the three-model path. Fully eligible pairs are unchanged: the golden comparison against the
  frozen pre-patch functions still passes.
- **Tests:** two existing tests that pinned the averaged value are rewritten with the reason
  recorded. ml-prediction suite: 477 passed.
- **When it runs:** this endpoint runs in production only when `/ml/predict_ensemble_three` returns
  a non-404 error.

## Replay — the fallback forced on every cell

- **Inputs:** the **same frozen recording** as the ML-unavailable replay (HTTP, Redis, SQL and the
  2,388 member predictions; recording SHA-256 `4767e2db…`, verified).
- **Code:** the deployed generator.
- **Forcing:** `FORCE_TWO_MODEL=1` makes the simulated three-model endpoint return 500, so
  signal-engine falls back to `/ml/predict_ensemble` exactly as after a real failure.
- **Passes:** old (prod ensemble), control (prod again) and new (S0-3), all `--network none`.

| | Result |
|---|---|
| Coverage | 796 expected; **792 compared in all three passes**; 4 identical SSNLF no-price-data failures; 0 missing; 0 replay misses |
| Control | Reproduced the old pass in all 792 cells |
| Fallback in effect | Every ML cell went through the two-model function (`ensemble_xgb_rf` 83, `xgboost` 1, `none_eligible` 617) |
| Decision-output changes (old → new) | **54** cells, all mixed pairs: signal, confidence or probability changes, and ML weight changes in every one (quality e.g. 0.325 → 0.65, weight 0 → 0.15) |
| Quality-only changes | **15** further cells: only `reasons.ml_test_auc` changes. The surviving contributor's corrected quality is still below the weight cut-off, so weight and output are unchanged |
| Any recorded payload change | **69** cells (54 + 15), excluding the per-run `input_contract` metadata present on every row |
| Signal changes | 11: SWING SELL→WAIT 1 and BUY→HOLD 1; LONG SELL→WAIT 1 and BUY→HOLD 1; GROWTH HOLD→BUY 2, BUY→HOLD 1, WAIT→SELL 1, SELL→WAIT 1, WAIT→HOLD 2 |
| New BUYs / newly actionable (base gate) | 2 / **0** (both new BUYs fail the base conviction gate) |
| Lost BUYs / no longer actionable (base gate) | 3 / 2 |
| SELL changes | 1 new, 3 lost |

**Gate counts are base-gate replay results.** The comparison evaluates `_is_conviction_buy` with
`_load_conviction_edges` stubbed to `{}`, identically in every pass. The actionability counts
therefore use the base soft-layer edges, not the calibrated per-regime overrides stored in
production on 2026-10-28. They are not a verified replay of the production gate configuration.

**Correction (2026-10-28, after independent review).** This report first said "54 cells changed"
without separating decision output from quality-only metadata, and did not label the gate counts
as base-gate. Both are corrected above; the figures are re-derived from `old.jsonl.gz` and
`new.jsonl.gz` and match the [review](2026-10-28-f04-fencing-s03-independent-review.md). No code or
replay output changed.

**Consistency with the primary path.** The deployed three-model path already uses an eligible
survivor's own quality. So the corrected fallback should give the primary path's signals, and the
replay confirms it.
- **Before:** the fallback disagreed with the primary path on **49** mixed pairs.
- **After:** **0**. Six still show confidence ±0.01 from rounding the reported quality to 4 decimals;
  their signals and probabilities are identical.

**Remaining fallback/primary differences, pre-existing and not S0-3:**
- **12 fully eligible pairs.** The two functions blend differently by design: AUC-weighted here,
  fixed weights plus an agreement nudge in the three-model path.
- **2 cells (MU and NET SWING).** The only eligible model is lightgbm, which the two-model function
  never loads, so the fallback reports ML-unavailable.

**What the signal changes mean.** They are the fallback path's own changes on frozen inputs. In
production these signals change only when the three-model endpoint has failed, and then the
fallback now agrees with what the primary path would have produced.

## Deployment (2026-10-29)

- **Approval:** released separately from the fencing branch, with this corrected report, after the
  [independent review](2026-10-28-f04-fencing-s03-independent-review.md) found no code blocker.
- **Merged:** `fa1a3e22` on `prod`. The diff against the running services touched only
  `services/ml-prediction/src/training/trainer.py` (19 lines) and two tests.
- **Verified before deploy:** `make test` on a clean worktree at `fa1a3e22`: every backend suite
  passed (ml-prediction 477, market-data 5,967, signal-engine 1,003, all others green).
- **Rollback image:** `stockai-ml-prediction:rollback-before-fa1a3e22` (`49df3f924a55`), the image
  that was serving before this deploy.
- **Deployed:** `scripts/deploy.sh ml-prediction` (host lock, `--no-deps`); healthy after the
  settle. All 15 containers healthy. `check_deploy_drift.sh` from a clean worktree at `fa1a3e22`:
  OK for all 12 backend services. The running `trainer.py` hash matches the commit.
- **Live check (read-only, inside the running service):** the 3 SWING xgboost/random_forest pairs
  with exactly one suppressed member each report one eligible contributor and that member's own
  test AUC as the pair's quality.
- This establishes the fallback reports correct quality. It is not evidence of improved returns,
  and the fallback still runs only when the three-model endpoint fails.
