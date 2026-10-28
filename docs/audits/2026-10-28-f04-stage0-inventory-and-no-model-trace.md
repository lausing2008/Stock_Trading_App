# F04 Stage 0 — model artifact inventory and the no-qualified-model trace

Date: **2026-10-28 (UTC)**. Status: **Stage 0 complete; read-only throughout**. No training, tuning,
suppression resweep, artifact write, serving change or deployment. F04 remains held.
Plans: [F04 comparison and rollout](../features/2026-10-27-f04-evaluation-version-comparison-and-rollout-plan.md),
[completion and release plan](../features/2026-10-27-rzy-ai-completion-and-release-plan.md).
Evidence: [`evidence/2026-10-28-f04-stage0/`](evidence/2026-10-28-f04-stage0/).

## Headline

**Suppressed models are not neutral in live signals.** When every model for a stock and horizon
is suppressed, which is the case for 77% of active cells, the ensemble does not withdraw. Each
member returns 0.5, so the agreement test (`p > 0.5`) reads *unanimous bear*. When the stored
AUCs exceed 0.57, the ensemble nudges the probability to **0.45**, and its stored AUC goes to
signal-engine as a "measured" quality score that earns ML fusion weight.

**Effect in production over the last 3 days:**
- 435 signals carried ML probability 0.45. All 435 were suppressed and all had non-zero weight.
- Average applied weight: 0.14 in SWING, 0.61 in GROWTH.
- 74 SELL signals had suppressed-but-weighted ML (38 of them at 0.45); no BUY did.

**Size of the effect, in the real code:** a bullish TA of 0.62 fuses to **0.4955** at the
profile's maximum cap, and to 0.5567 at a production-like 0.15 cap, versus 0.62 with no ML at
all.

This is the plan's "no eligible contributor" case, now established rather than assumed. It is a
live serving defect independent of F04's split.

## Method

- **Capture.** [`capture_inventory.py`](evidence/2026-10-28-f04-stage0/capture_inventory.py)
  ran under the host deploy lock, in a throwaway container from the **deployed ml-prediction
  image** `sha256:3fa7c7cb…`:
  - `--network none`, 1 GB memory, 0.5 CPU;
  - model volume mounted **read-only**, output written to `~/f04-stage0/out` outside the model
    root.
- **Hash binding.** Each artifact's bytes were read once; the SHA-256 and the loaded metadata come
  from those same bytes. Directory listings before and after the capture were diffed.
- **Rules used, extracted unchanged:**
  - `_compute_oos_suppression` and `_HORIZON_BY_STYLE`, from the image's own trainer (sha
    `fc7d772a…`);
  - `_threshold_report_bounds`, from the held F04 branch at `f5a87189` (sha `558d1600…`);
  - M14 `build_inventory` / `evidence_sufficiency`, loaded by file path so the trainer was never
    imported.
- **Active universe.** 199 symbols (active, not delisted), from a read-only query, giving 796
  stock/horizon cells.
- **Routing.** Mirrors `predict_latest_ensemble_three` member selection from **stored flags**. It
  is cross-checked against observed signals below. Runtime failures (feature build, unreadable
  members) are not modelled.
- **Trace.** [`trace_no_qualified_model.py`](evidence/2026-10-28-f04-stage0/trace_no_qualified_model.py)
  executes the real `predict_latest_ensemble_three` and signal-engine `_calculate_ml_weight`,
  extracted unchanged. Only each member's `predict_latest` result is a fixture, shaped as a
  suppressed bundle returns it. Output:
  [`trace-output.jsonl`](evidence/2026-10-28-f04-stage0/trace-output.jsonl).
- **Observed provenance.** Read-only query over the last 3 days of signals:
  [`signal-provenance.txt`](evidence/2026-10-28-f04-stage0/signal-provenance.txt).

Capture 05:07:08 → 05:08:28 UTC: **1,433 artifacts read, 0 unreadable, 0 changed, added or
removed during the capture.** 133 non-model files (`*_params.json`) were listed and skipped.

## Inventory

| | Count |
|---|---:|
| Artifacts (xgboost / lightgbm / random_forest) | 1,433 (703 / 29 / 701) |
| Suppressed / unsuppressed | 1,330 / **103** |
| `evaluation_valid` True / False / missing | 1,272 / 126 / 35 |
| Threshold mode: holdout / in-sample fallback / **not recorded** | 934 / 126 / **373** |
| …of which not recorded but `evaluation_valid: true` | 338 |
| Suppression reason if the rule is re-applied: cv_auc < 0.52 / overfit gap / dead recall / not out of sample | 600 / 371 / 233 / 126 |
| Stored flag differs from today's rule | 0 |
| Protocol-v2 qualified | **0** (no artifact carries a protocol version) |

**M14 invariants hold.** 0 invalid-but-unsuppressed, 0 resweep would unsuppress an invalid model,
inventory complete, validity coverage 97.6%. Four unsuppressed artifacts have neither `cv_auc_mean`
nor `overfit_gap`.

> `summary.json`'s `threshold_mode` tally compares against the string `"None"` and reports 0 for
> missing modes. That is a defect in the capture script's summary, not in the data: the per-artifact
> `artifacts.json` is correct (373 missing), and the table above is computed from it.

## F04 impact, refreshed

Under the held split, a horizon-row purge between the threshold half and the report half:

- **590 of 934** holdout artifacts fall below the 10-row reporting floor. The earlier impact
  study found 590 of 932.
- **40** of them are currently unsuppressed (earlier: 38).
- Class support after the purge is **unknown**: it cannot be derived from stored metadata.

**The two floors.** M14 evidence sufficiency (`n_test ≥ 50` plus diagnostics) is met by **no
artifact**: median `n_test` is **23**, maximum 142, and only 2 of 1,433 reach 50.
- Under the current 70/10/10/10 layout, M14 sufficiency is unreachable for almost the whole fleet.
- Arm C (20% test) would roughly double `n_test` and still leave most cells below 50.
- The study should report M14 sufficiency per arm. It should not expect the larger slice to make
  models M14-sufficient.

## Routing over 199 active symbols × 4 horizons (796 cells)

| Class (from stored flags) | SHORT | SWING | LONG | GROWTH | All |
|---|---:|---:|---:|---:|---:|
| ≥ 1 unsuppressed contributor | 24 | 23 | 13 | 26 | **86** |
| All suppressed → 0.45 bear nudge | 25 | 37 | 38 | 41 | **141** |
| All suppressed → neutral 0.5 | 126 | 116 | 124 | 108 | **474** |
| No xgboost artifact → TA-only | 24 | 23 | 24 | 24 | **95** |

- Only **11%** of cells have a contributor the current rule would accept; under strict v2,
  **none** do.
- Of the 615 all-suppressed cells, **333** carry a stored quality ≥ 0.50 and therefore receive
  ML weight.
- All 95 TA-only cells have no artifact of any family, so no models sit unused.
- 126 SWING cells are served from legacy `{symbol}.joblib` files. These are still retrained
  (latest 2026-10-27), because `_artifact_path` keeps writing to the legacy name.

**Cross-check against observed signals.**

| SWING | Routing predicts | Observed (3 days) |
|---|---:|---:|
| All suppressed | 77% | 75% (446 / 596) |
| 0.45 nudge | 18.6% | 17.6% (105 / 596) |

The stored-flag mirror describes what production actually does.

## Findings from the trace

| # | Finding | Evidence |
|---|---|---|
| **S0-1** | **All-suppressed ensembles emit a bearish 0.45.** Members return 0.5; `0.5 > 0.5` is False, so the result is `unanimous_bear` and a −0.05 nudge applies whenever every member's auc/cv_auc > 0.57. The stored AUC is reported as `mean_model_test_auc` (restored from the zeroed values), and signal-engine marks it "measured". | Trace: TA 0.62 → 0.4925 fused / 0.4955 after SA-27 (cap 0.75); 0.5945 / 0.5567 (cap 0.15). Production: 435 rows at 0.45, all suppressed, all weighted; 141 cells. |
| **S0-2** | **A suppressed model still dampens TA toward neutral.** When all members are suppressed without the nudge, ML is 0.5 but its stored quality earns weight, and SA-27 then compresses further. ML is counted as present when it should be unavailable. | Trace: single suppressed xgb, TA 0.62 → 0.518. 333 of 615 all-suppressed cells have quality ≥ 0.50. |
| **S0-3** | **The two-model endpoint understates a mixed ensemble's quality.** With one member suppressed, its weight is zeroed and then averaged over both members (unsuppressed rf 0.62 is reported as 0.31, so ML weight is 0). This is inconsistent with the three-model path. | Code reading of `predict_latest_ensemble`. Reached only when `/predict_ensemble_three` returns a non-404 error, so rare. |
| **S0-4** | **No cross-style fallback exists**, despite `_fetch_ml_data`'s docstring ("falls back to the SWING artifact"). A missing style model ends in TA-only. This matches the plan's rule (a wrong-horizon substitute is not coverage); only the docstring is stale. | Code: every endpoint is called with the same style; `_artifact_path` has no style fallback. |
| **S0-5** | **M14 sufficiency is unreachable under the current split** (median `n_test` 23). | Inventory. |
| **S0-6** | **338 legacy passes have no threshold mode recorded.** They were written before R02 recorded `threshold_evaluation_mode`, but carry `evaluation_valid: true`. Under v2 they are unknown, not passed. | Inventory. |

## What this changes in the plan

- **F04 step 5/6 (serving resolution and fallback) is the most urgent piece**, and it can be
  separated from the split change. The fix the plan already specifies: an empty qualified set →
  explicit ML-unavailable, **zero ML weight, no agreement nudge**. That removes S0-1 and S0-2
  without retraining anything.
- **It is a live behaviour change** to signal output. 77% of cells would move from
  "suppressed-but-weighted" to "TA-only".
  - SELL and WAIT counts can shift; BUY can increase where the 0.45 pull was holding TA down.
  - That needs its own approval, a before/after replay on frozen inputs, and a new
    outcome-cohort boundary. It is **not** done here.
- **Strict-v2 serving today would remove ML from every cell** (0 qualified). The canary has to
  start from qualified candidates trained into the isolated root; tagging the legacy fleet is not
  a way to get there.
- **Study design:** report M14 sufficiency per arm, and do not count the 338 mode-less passes as
  holdout evidence.

## Limits

- Routing is reconstructed from stored flags. Live member failures and per-request feature-build
  errors are not modelled. The observed-signal cross-check covers SWING rates only; other
  horizons' per-style agreement is not persisted.
- The observed rows show weighted suppressed ML on recorded signals. They do not show what those
  signals would have been without it; that needs a counterfactual replay.
- Class support after the F04 purge is unknown from metadata.

## Reproduce

```bash
# Inputs (outside the model root): the script, the held branch trainer, active symbols (read-only query)
git show f5a87189:services/ml-prediction/src/training/trainer.py > in/f04_trainer.py
# Under the host lock, on EC2:
docker run --rm --network none --memory 1g --cpus 0.5 \
  -v stockai_datavol:/data:ro -v ~/f04-stage0/in:/in:ro -v ~/f04-stage0/out:/out \
  -e IMAGE_ID=<image> -e F04_COMMIT=f5a87189… --entrypoint python <ml-prediction image> \
  /in/capture_inventory.py /data/models /in /out
python3 docs/audits/evidence/2026-10-28-f04-stage0/trace_no_qualified_model.py   # local, no services
```

Evidence SHA-256: `artifacts.json` c1919db0…, `routing_cells.json` 6084aa8d…, `summary.json`
e4019bed…, `capture_inventory.py` e12b9b33… (as run), `active_symbols.json` 5290b226….
