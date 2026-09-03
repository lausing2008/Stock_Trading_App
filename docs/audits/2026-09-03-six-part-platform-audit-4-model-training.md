## Deep Audit Series (2026-09-03): Model Training — 4 of 6

**Scope**: `services/ml-prediction/src/training/trainer.py` (~1560 lines) — `train_model()`,
`_load_outcome_features()`, `predict_latest()`, `predict_latest_ensemble()`,
`predict_latest_ensemble_three()`, `validate_walkforward()`, plus `meta_trainer.py` in the same
directory. Sequential platform audit series (AI Signal → Decision-Making → Paper Trading →
**this domain** → Short Squeeze Alerts → Options Trading & Alerts), per
`docs/AUDIT_DOMAIN_SERIES_TEMPLATE.md`.

**Carryover context**: a 2026-09-03 mid-Domain-2 interruption (a live user question about
3690.HK's WAIT→SELL signal) surfaced a genuine, unrelated ML anomaly while answering it — noted
then, investigated properly here. The carryover note's own numbers were **re-verified live, and
one was corrected** before dispatching the audit subagent (see Ground truth below).

### Ground truth (queried directly against production before dispatching)

249 total model artifacts exist (`/data/models/{random_forest,xgboost}/*.joblib` on
`stockai-ml-prediction-1`, excluding `_short` variants). Model file mtimes range 2026-05-22 to
2026-09-02 — models are retrained regularly, not simply stale.

- **4 models** show `auc=1.0` (or near it) AND `recall=0.0` — perfect discrimination claimed,
  but the model never once correctly predicted a positive case. Example: `3690.HK`'s
  `random_forest` model: `auc=1.0, recall=0.0, precision=0.0, cv_auc_mean=0.6219,
  overfit_gap=-0.3781, n_test=27`.
- **40 models (16%)** have `overfit_gap < -0.2` — test-AUC dramatically HIGHER than CV-AUC, the
  OPPOSITE direction from the pre-existing `ML-FIX-4` check (`overfit_gap_val > 0.10`, which
  only fires when CV≫test, the "memorized training data" direction). No corresponding check
  existed for the opposite direction.
- **Corrected from the carryover note**: the note's claim of `n_outcome_rows: 0` for the
  3690.HK model was based on checking the wrong dict key (`metrics.get("n_outcome_rows")`
  instead of the real top-level `bundle["n_outcome_rows"]`, confirmed by reading
  `trainer.py:839-852` directly). Re-checked correctly: of 249 models, 89 are missing the key
  entirely (predate the Tier 87 feature or something else strips it — not fully resolved, see
  below), 155 have `n_outcome_rows==0`, and only 5 ever got real outcome-augmented rows (values
  observed: 5-6, barely above the `len(X_out) >= 5` survival floor).

### Headline findings

1. **CRITICAL, independently re-verified, FIXED — models with zero true positives ever
   observed (dead recall) could still clear the existing coin-flip suppression gate.**
   `oos_suppressed` (SA-9) only checked `cv_auc_mean < 0.52` — a model whose `recall` and
   `precision` are BOTH exactly `0.0` on its own held-out test slice (i.e., it has never once
   correctly predicted a BUY) could still have a `cv_auc_mean` comfortably above 0.52 and serve
   live. Independently re-verified via direct `joblib.load`: `9961.HK`'s `random_forest` model
   (`auc=1.0, recall=0.0, precision=0.0, cv_auc_mean=0.716, oos_suppressed=False`) AND its
   **xgboost sibling for the same symbol** (`auc=0.875, recall=0.0, precision=0.0,
   cv_auc_mean=0.683, oos_suppressed=False`) — meaning the ensemble for 9961.HK/SWING had TWO
   independently-trained models, both live-serving, neither ever having predicted a true
   positive. A full sweep across all 249 artifacts (done after the subagent's report, closing
   its own explicitly-flagged open item) found **41 of 249 (16.5%)** currently in this exact
   dead-recall-but-not-suppressed state.
   **Root cause**: a tiny final test split (`n_test` as low as 21 rows observed) lets the
   headline `auc` metric hit 1.0 by chance ranking even when the model's actual
   `buy_threshold`-gated decision never fires a true positive. `recall`/`precision` were always
   computed and stored in the metrics dict but never read by any downstream consumer —
   confirmed via grep across `trainer.py` and `signal-engine/src/generators/signals.py`.
   **Fixed**: extracted the suppression decision into a new pure function,
   `_compute_oos_suppression()` (`trainer.py`, after `_blend_weights`), now checking 3
   independent conditions (any one sufficient): `cv_auc_mean < 0.52` (unchanged, SA-9),
   `recall==0.0 and precision==0.0` (new), `abs(overfit_gap) > 0.10` (new, see finding 2).
   `oos_suppressed` already propagates correctly through the entire existing downstream chain
   (`predict_latest` → `predict_latest_ensemble` → `predict_latest_ensemble_three` →
   signal-engine's `_fetch_ml_data`) — confirmed by the subagent's trace, no changes needed
   there.

2. **HIGH, independently re-verified, FIXED (same code change as finding 1) — a large negative
   `overfit_gap` (test-AUC ≫ CV-AUC) had no corresponding suppression check.** Independently
   re-verified: of the 40 flagged models, exactly 14 were already caught by the pre-existing
   `cv_auc_mean < 0.52` check; 26 (65%) were not. `n_test` across all 40 ranges 21-42 (mean
   28.2) — the identical small-sample regime that produces finding 1's pathology. Answers the
   carryover note's own open questions: a gap this large on this few samples is a small-sample
   metric artifact, not evidence the model genuinely generalizes better than its CV suggests;
   a **symmetric** magnitude check (mirroring the pre-existing 0.10 threshold, rather than a
   one-off recall check alone) is the more complete fix since it catches the negative-gap
   population regardless of whether recall happens to also be degenerate. **Fixed** as the 3rd
   condition in `_compute_oos_suppression()` above. The pre-existing `ML-FIX-4` log-only warning
   (still fires independently, unchanged) is left in place as a separate diagnostic message —
   not removed, since it predates and is distinct from the new suppression decision.

**Combined real-world impact of both fixes, computed directly against all 249 live artifacts**:
**114 of 249 models (46%)** would newly become `oos_suppressed` once retrained under this fix —
a substantial fraction, disclosed explicitly here rather than understated. This is the correct,
intended effect of closing a real gap (nearly half of all trained models had a genuine quality
problem invisible to the one existing check), not a regression — but it means a large share of
symbols will see their ML fusion weight held at neutral (0.5) until models are retrained with
enough real data to clear the new bar. **Deliberately NOT retrained/redeployed as part of this
audit pass** — the fix changes training-time logic only; existing `.joblib` artifacts on disk
still carry their OLD `oos_suppressed` value until each symbol's regular retrain cycle runs
again with the new code. No forced mass-retrain was triggered.

### Checked and found CLEAN

- **Falsy-zero AUC bug class within ml-prediction itself**: already fixed 3× (`T237-ML1B`,
  `AUD-ML1B-3MODEL`, `AUD-ML1B-NUDGEGATE`) — all 3 verified present and correctly using
  `is not None` presence checks. No further un-fixed instance of this bug class found in
  `trainer.py` or `meta_trainer.py`.
- **`oos_suppressed` propagation chain**: traced end-to-end through
  `predict_latest`/`predict_latest_ensemble`/`predict_latest_ensemble_three` into
  signal-engine's `_fetch_ml_data`/`_apply_style_signal` — flag correctly read and applied at
  every hop (0.6× compression), consistent with its own SA-27 design.
- **`meta_trainer.py`**: extensively self-audited already (`T247-ML-META-FEATURE-ORDER`,
  `T242-METAMODEL-NANFILL`, `AUD301-METASCALER-LEAKAGE`,
  `SELFIMPROVE-PROMOTION-GATES-INCOMPLETE` — all present and correct). Its own separate AUC
  gate (`if auc < 0.55: return None`) is a genuinely independent safety net; the meta-model does
  NOT share findings 1/2's pathology since it trains on `signal_outcomes` directly (real trade
  labels) at much larger `n` (20,000-row cap, 50-row floor) than any single-symbol base model.
- **Unusual Whales wiring**: confirmed absent from ml-prediction's feature set and training
  pipeline. `_load_options_snapshots()` reads `options_flow_snapshots`, populated exclusively
  by `market-data`'s `options_flow_snapshot.py` via **yfinance's option chain** — a completely
  separate path from `unusual_whales.py` (which feeds GEX/dark-pool/congress data, none of
  which `trainer.py` touches). Grepped the entire `services/ml-prediction/src/` tree for
  `unusual_whales`, `dark_pool`, `gex_snapshot`, `squeeze_score`, `congress_score`,
  `insider_score` — zero hits. Consistent with Domains 1-3's finding that UW is either
  free-tier-substituted or deliberately unwired everywhere checked so far.
- **Point-in-time fundamentals joining** (`_load_fund_snapshots`, T228/T234): consistent with
  the file's own PIT discipline, no lookahead found.
- **Train/embargo/purge splitting** (`T232-ML4`): correctly computes `gap=horizon` in
  `TimeSeriesSplit` and inserts embargo rows at split boundaries — no lookahead leakage in the
  splitting itself.
- **Threshold-selection/reporting split** (`T232-ML2`): correctly separates the
  threshold-selection half from the reported-metrics half of the test slice — findings 1/2
  persist even in the genuinely-held-out reporting half, confirming this is a sample-size
  problem, not a leakage problem.
- **`_load_outcome_features()`'s RangeIndex/DatetimeIndex dedup fix (T232-ML3)**: read
  carefully, confirmed correct — no regression of the bug it originally fixed.

### What was NOT independently verified / left open

- **The `n_outcome_rows` drop-off root cause (89 missing / 155 zero / only 5 populated) is only
  partially traced, not fully isolated.** The gating chain (`len(outcomes) < 20` →
  `len(prices) < 100` → `build_features()` non-empty → date-index intersection → final
  `len(X_out) >= 5`) is fully read and understood, and the first gate is confirmed NOT the
  primary bottleneck (291 of 621 real `(stock_id, horizon)` combinations in `signal_outcomes`
  clear the ≥20 floor — a healthy number). Which of the remaining 4 gates is the dominant
  silent-drop point was NOT isolated within this audit's scope — it needs live instrumentation
  of `_load_outcome_features()` against 3-5 real qualifying symbols (not resolvable by further
  static reading alone). One plausible-but-unconfirmed contributor flagged by the subagent: an
  outcome's `signal_date` falling early in a symbol's price history (before rolling 200-day
  SMA/ATR windows stabilize) would silently fail to survive `build_features()`'s own NaN-feature
  mask, dropping it from `X_full.index` before ever reaching the date-intersection step —
  plausible given the `earliest = min(outcome_dates) - timedelta(days=400)` price-fetch window
  is close to (but should exceed) typical 200-bar rolling windows. **Explicitly deferred to a
  future session** — this needs its own instrumented investigation, not a quick fix.
- The 89-models-missing-`n_outcome_rows`-key population was not cross-referenced against
  specific retrain dates to confirm whether it's purely "trained before 2026-06-21" or something
  else entirely (e.g. a code path that doesn't call `train_model()` at all for those symbols).

### What to check if this needs re-verifying

```bash
# Confirm the fix is present and correctly wired:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-ml-prediction-1 grep -n '_compute_oos_suppression' /app/src/training/trainer.py"

# Re-sweep all model artifacts for the dead-recall-not-suppressed / large-overfit-gap-not-suppressed
# populations (numbers will shrink over time as symbols retrain under the new code):
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 'docker exec stockai-ml-prediction-1 python3 -c "
import joblib, glob
paths = [p for p in (glob.glob(\"/data/models/random_forest/*.joblib\") + glob.glob(\"/data/models/xgboost/*.joblib\")) if \"_short\" not in p]
still_unsuppressed = 0
for p in paths:
    obj = joblib.load(p)
    m = obj.get(\"metrics\", {})
    gap = m.get(\"overfit_gap\")
    dead_recall = m.get(\"recall\")==0.0 and m.get(\"precision\")==0.0
    big_gap = gap is not None and abs(gap) > 0.10
    if (dead_recall or big_gap) and not obj.get(\"oos_suppressed\"):
        still_unsuppressed += 1
print(f\"still not caught (should shrink toward 0 as symbols retrain): {still_unsuppressed}\")
"'

# Re-run the n_outcome_rows investigation (deferred item above) once instrumented:
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
   \"SELECT stock_id, horizon, COUNT(*) FROM signal_outcomes WHERE signal_direction='BUY' AND is_correct IS NOT NULL AND signal_date >= CURRENT_DATE - INTERVAL '365 days' GROUP BY stock_id, horizon HAVING COUNT(*) >= 20 ORDER BY COUNT(*) DESC LIMIT 10;\""
```
