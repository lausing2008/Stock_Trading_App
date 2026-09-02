## AUD232-METAMODEL-MEDIUM-GROUP — Meta-Model NaN-Preserving Fix (2026-08-26)

**Closes AUD232-057/058**, deferred since 2026-07-11 on a technical premise that turned out to
be factually wrong. `train_meta_model()`/`predict_meta()` (`services/ml-prediction/src/
training/meta_trainer.py`) zero-filled every NaN before feeding `StandardScaler`, including the
genuinely-NaN-by-design `FUNDAMENTAL_COLUMNS`/`WEEKLY_COLUMNS` — `builder.py`'s own comment
already says these are "NaN-allowed — XGBoost handles natively." Zero-filling makes "we don't
know this stock's revenue_growth" indistinguishable from "revenue_growth is exactly 0.0."

**The original deferral reasoning was wrong on 2 independent facts, both verified empirically
before touching any code (not assumed)**:
1. "sklearn's StandardScaler cannot accept NaN input" — false. `StandardScaler.fit_transform()`
   on NaN input does not raise; it computes mean/std ignoring NaN and propagates NaN through in
   the output. Confirmed directly: `[[1,nan],[2,3],[3,4]]` → `[[-1.22, nan], [0, -1], [1.22, 1]]`.
2. "trainer.py's base models do not scale features at all before XGBoost" — false. `trainer.py`
   DOES run `StandardScaler` on its own base models (`train_model()`, lines ~564/622) and DOES
   feed it real NaN from `build_features()`'s sparse fundamental/weekly columns. XGBoost's own
   `fit()`/`predict_proba()` also both accept NaN directly (confirmed empirically). Meta-
   model's zero-fill was a genuine, unforced divergence from an already-established pipeline
   convention — never a technical necessity requiring dropping `StandardScaler` or adding an
   imputer, as the deferred note had proposed.

**Fix**: 2 real code changes, plus 1 bug caught while making them:
1. `train_meta_model()`'s `X_raw` construction: `else 0.0` → `else np.nan`.
2. `predict_meta()`'s mirrored `vec` construction: same change.
3. **A real bug found and fixed while writing #1** — the constant-column filter used a bare
   `X_raw.std(axis=0) > 1e-8`. With real NaN now present, `np.std` on any column containing even
   one NaN returns NaN (not a real number), and `NaN > 1e-8` is always `False` — this would have
   silently dropped EVERY sparse fundamental/weekly column from the model entirely, exactly the
   opposite of this fix's own goal. Verified directly: `.std()` on `[[1,nan,5],[2,3,5],[3,4,5]]`
   → `[0.816, nan, 0.0]`; `np.nanstd()` on the same → `[0.816, 0.5, 0.0]`. Switched to
   `np.nanstd()`.

**Tests**: 3 new cases in `services/ml-prediction/tests/test_meta_trainer.py` — one drives the
REAL `predict_meta()` end-to-end via the file's existing `importlib`-loaded harness (real
`build_features()` computation, no feature-pipeline mocking) and confirms every
`FUNDAMENTAL_COLUMNS` slot in the actual vector handed to `scaler.transform()` is real NaN
(`predict_meta()` never supplies `fund_data`, so this is the real, reachable production shape);
2 source-text regression checks guard `train_meta_model()`'s own two fixed lines (that function
can't be exercised end-to-end — needs a real Postgres `LATERAL` join with no SQLite equivalent).

**Adversarial verification** — 3 sabotage/restore cycles, all caught: train-side zero-fill
reintroduced (caught by the source-text regression test), `nanstd`→`std` reversion (caught by
its own dedicated test), predict-side zero-fill reintroduced (caught by the end-to-end test).
Each restore confirmed byte-identical via `md5sum` before moving on. Full 94-test ml-prediction
suite green (up from 91); `pyflakes` clean (the sole remaining warning — unused `sqlalchemy.
select` import — confirmed pre-existing via `git stash`).

**No retrain/promotion-gate comparison was performed as part of this fix** — the bundle FORMAT
is unchanged (still the same `scaler`/`model`/`non_const`/`feature_columns` keys `predict_meta()`
already reads), so the next scheduled retrain trains on real NaN going forward and is evaluated
automatically by the already-existing `SELFIMPROVE-PROMOTION-GATES-INCOMPLETE` AUC-vs-previous-
bundle gate — no separate migration step needed.

**What to check if this looks wrong**:
```bash
docker exec stockai-ml-prediction-1 grep -n "np.nanstd(X_raw" /app/src/training/meta_trainer.py
docker exec stockai-ml-prediction-1 grep -n "else np.nan for v in r\[0\]" /app/src/training/meta_trainer.py
# Confirm the next scheduled retrain's AUC (compare against previous_auc in the log line):
docker logs stockai-ml-prediction-1 --since 24h | grep "meta_trainer.trained\|meta_trainer.promot"
```

---

