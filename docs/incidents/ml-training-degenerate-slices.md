# ML Training — Degenerate Data Slices That Kill a Whole Run

## AUD-MLCV-SINGLECLASSFOLD — a single-class slice killed the entire training run, not just the fold (Fixed 2026-09-07)

**Found by:** running the AUD-MLRETRAIN-UNKNOWNAGE retrain. 5 of 86 jobs died. The failures were
the discovery mechanism, not a side effect — nothing else on the platform would have surfaced
this, because the affected symbols simply ended up with no model and no alert.

**Two different-looking errors, one root cause:**

```
WMT / GEV / EE / AAON   random_forest → IndexError: index 1 is out of bounds for axis 1 with size 1
GEV                     xgboost       → Invalid classes inferred from unique values of `y`
```

**Root cause.** sklearn will happily `fit()` a model on a training slice containing only **one
class**, but its `predict_proba()` then returns shape `(n, 1)` instead of `(n, 2)`. So
`trainer.py:660`'s

```python
preds_proba = cv_model.predict_proba(X_cv_val_s)[:, 1]
```

raises `IndexError` — and that kills the **entire `train_model()` call**, not just the offending
CV fold. XGBoost rejects the same input up front with a different message instead.

The loop already guarded the **validation** slice one line below:

```python
if len(np.unique(y_cv_val)) > 1:      # protects roc_auc_score
    cv_aucs.append(roc_auc_score(y_cv_val, preds_proba))
```

...but never guarded the **training** slice — the one that actually produces the degenerate
`predict_proba`. Neither library message names the symbol or the real cause, which is what made
this take a traceback to pin down.

**Confirmed by instrumenting the real code path, not by inference:** AAON failed on its very
**first** CV fold with an all-positive `[1]` training set, while AAPL completed 6 CV fits with
zero single-class folds.

**Why it is reachable with ordinary data:** an early `TimeSeriesSplit` fold is small, and on a
symbol in a sustained one-directional run every label in that window lands on the same side of
the forward-return threshold. This is not an exotic edge case.

### The fix

1. **CV loop** — skip the fold and log it. Skipping is *correct rather than lossy*: a
   single-class fold teaches nothing about ranking two classes and its AUC is undefined anyway.
   A symbol where *every* fold is degenerate now ends with empty `cv_aucs` → `cv_auc_mean` stays
   `None` → the existing suppression gate handles it, instead of the symbol having no model.
2. **Final fit** — raise early with a message naming the symbol, the class, the row count, and
   the likely cause. No binary classifier can exist there, so failing fast beats failing
   obscurely inside a library.

### Test-quality lesson (worth more than the fix)

3 adversarial sabotage cycles were run. **The second initially PASSED.** Swapping `y_cv_tr` for
`y_cv_val` reintroduces the exact bug, but the test only asserted the substring
`np.unique(y_cv_tr)` appeared *somewhere in the loop* — and that name occurs elsewhere. The test
was tightened to assert on the **skip-guard statement itself** (`if len(np.unique(y_cv_tr)) < 2:`)
and to explicitly reject the `y_cv_val` form.

**Generalisable:** a source-text assertion that checks only for a *substring* is weak whenever
that substring legitimately appears elsewhere in the same scope. Assert on the full statement.

### What to check when touching any training loop here

1. **Guard the slice that feeds `fit()`, not only the one that feeds the metric.** They fail
   differently and the fit-side failure is the one that takes down the whole run.
2. **A library that accepts degenerate input silently is more dangerous than one that rejects
   it.** sklearn's single-class fit "succeeds" and defers the failure to an unrelated line.
3. **Skipping a degenerate fold is usually right; skipping the whole symbol is usually wrong.**
   Prefer degrading to the existing suppression path over producing no model at all.

---


---

## Detail relocated from CLAUDE.md's index (2026-09-10)

**T382-CLAUDEMD-REINDEX.** The lines below lived in `.claude/CLAUDE.md`'s Topic File Index,
which is read at the start of EVERY session and re-paid on every prompt-cache rebuild. They
were verified to be **new content, not duplicates** of this file — a sampled check found only
1-2 of 6 claims from each oversized index entry already present here — so they are moved rather
than deleted, and the index keeps a short pointer.

Preserved verbatim. Formatting is unchanged from the index entry, including its emphasis, so
nothing is lost to a reflow.

AUD-MLCV-SINGLECLASSFOLD — a single-class TRAINING slice made sklearn's `predict_proba` return shape `(n,1)`, so `[:, 1]` killed the ENTIRE `train_model()` call, not just the fold (the validation slice was guarded, the training slice never was). Read before touching any training loop: guard the slice that feeds `fit()`, not only the one that feeds the metric. Also carries a test-quality lesson — a source-text assertion on a mere substring is weak when that substring appears elsewhere in scope.
