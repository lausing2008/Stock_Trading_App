# S0-3 — two-model ensemble quality: fix and forced-fallback replay

Date: **2026-10-28**. Branch: `f04-s0-3-two-model-quality`. Status: **implemented and replayed; not deployed**.
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
| Cells changed (old → new) | **54**, all mixed pairs: quality e.g. 0.325 → 0.65, ML weight 0 → 0.15 |
| Signal changes | 11: SWING SELL→WAIT 1 and BUY→HOLD 1; LONG SELL→WAIT 1 and BUY→HOLD 1; GROWTH HOLD→BUY 2, BUY→HOLD 1, WAIT→SELL 1, SELL→WAIT 1, WAIT→HOLD 2 |
| New BUYs / newly actionable | 2 / **0** (both new BUYs fail the conviction gate) |
| Lost BUYs / no longer actionable | 3 / 2 |
| SELL changes | 1 new, 3 lost |

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
