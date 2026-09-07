# Deep Audit Series (2026-09-07): ML Training Engine — 2 of 6

**Domain:** `services/ml-prediction/` — `trainer.py`, `features/builder.py`, `meta_trainer.py`,
and the `predict_latest*()` inference path.

**Result: 3 confirmed findings, one CRITICAL.** Unlike Domain 1 (config plumbing), these are
structural: an entire nightly job that never finishes, a feature that has been silently inert,
and no mechanism to re-evaluate model quality flags.

---

## Finding 1 — CRITICAL — The ML pillar is DEAD for 2 of 4 trading styles

**File:** `routes.py:393-431` (`train_all_horizons`), dispatched from `scheduler.py:643`;
artifact resolution at `trainer.py:255-268`.

`train_all_horizons` enqueues `symbols × 4 styles × 2 models` onto FastAPI `BackgroundTasks`,
which runs **serially in one queue**. At 173 active symbols that is **1,384 training runs** at a
measured ~326 runs/30min — roughly 2 hours. The scheduler re-posts the job every post-close, and
the loop order is `SHORT, SWING, LONG, GROWTH`. **The queue never survives long enough to reach
LONG or GROWTH.**

`_artifact_path()` compounds it: **only SWING has a legacy `{symbol}.joblib` fallback**
(`:263-267`). LONG/GROWTH resolve to `{symbol}_long.joblib` / `{symbol}_growth.joblib`, which
never get written, so `predict_latest()` raises `FileNotFoundError` at `:978`.

### Independently verified by me (not taken from the subagent)

Artifact filename suffixes — **zero `_long` files anywhere**:
```
lightgbm       {'legacy(none)': 29}
random_forest  {'legacy(none)': 111, 'short': 151}
xgboost        {'legacy(none)': 128, 'swing': 10, 'short': 151, 'growth': 1}
```

Bundle `style` field across all 580 artifacts — **`LONG` entirely absent**:
```
{'SWING': 245, 'SHORT': 302, '?': 32, 'GROWTH': 1}
```

Age by style — only SHORT gets a full fresh pass:
```
SHORT   n=302  min=0   median=0   max=0
SWING   n=245  min=0   median=5   max=70
GROWTH  n=1    (66 days old)
?       n=28   (all 82 days old)
```

**The decisive end-to-end check — live signals, last 7 days:**

| Horizon | signals | with ML weight | avg ml_weight |
|---|---|---|---|
| SHORT | 1,030 | 463 | 0.139 |
| SWING | 1,030 | 427 | 0.088 |
| **GROWTH** | **1,030** | **0** | **0.000** |
| **LONG** | **1,030** | **0** | **0.000** |

**2,060 live signals over 7 days carry zero ML contribution.** Half the platform's trading styles
run on TA alone, silently — no error, no alert. GROWTH is the style with the *highest* configured
`ml_weight_cap` (0.60), so the gap is largest exactly where ML was meant to matter most.

**Confidence: CONFIRMED** (artifact counts, bundle metadata, and live signal data all agree).

---

## Finding 2 — HIGH — Outcome-augmented training (Tier 87) is ~89% inert

**File:** `trainer.py:613-620`

`_load_outcome_features()` works — it returns 37-43 rows for well-covered symbols. The T232-ML3
de-duplication then removes every outcome row whose date falls inside the main training window.
Because `signal_outcomes` are by construction *closed* (their forward window has expired), their
dates are always ≥~2 weeks old and therefore **almost always inside `X`**. The `len(X_out) >= 5`
floor at `:616` then skips the augmentation entirely.

Only outcomes dated after `X`'s end (~2026-08-21, from label-horizon truncation) can survive — a
narrow trailing sliver.

### Verified by me

| n_outcome_rows | artifacts |
|---|---|
| **0** | **490** |
| 5-18 (nonzero) | 58 (median **6**) |

Against 37-43 rows actually loaded per symbol. So the Tier 87 feature — feeding real live-trading
labels back into training at 2× weight — is a no-op for 89% of models and a token handful for the
rest. Models train almost entirely on synthetic forward-return labels.

**It fails silently by design:** there are zero `outcome_augment_failed` log lines. The metric
reports ~0, which reads as *"no outcome data available"* rather than *"the merge is broken."*

**A correction to the subagent's framing:** it reported the dedup drops **100%**. It does not —
58 artifacts have nonzero values. "Effectively inert (89% zero, median 6 where present)" is the
accurate characterisation.

**A correction to MY OWN earlier work:** my pre-dispatch grounding reported `n_outcome_rows`
absent from all 580 artifacts. That was wrong — I looked inside `metrics`, but it is a
**top-level** bundle key. The subagent's 548/490/58 figures are right and mine were not. This
also means the AUD-ML2 audit's "deferred: root-cause n_outcome_rows" item is now **answered**:
the killer is the caller's dedup at `:613`, not any of the five gates inside
`_load_outcome_features()`.

---

## Finding 3 — MEDIUM — `oos_suppressed` is never re-evaluated on existing artifacts

**File:** written once at `trainer.py:896`; every other reference is a read
(`:1080`, `:1193-1206`, `:1297`, `:1446-1450`, `routes.py:557`, `signal-engine/signals.py:421`).

There is **no re-evaluation path** — no backfill route, no scheduler job, no startup sweep. A
model keeps its stale flag until its own retrain overwrites the file.

Normally this would self-heal nightly. **Finding 1 means it does not:** LONG/GROWTH never
retrain and SWING is largely served by legacy artifacts, so a stale `oos_suppressed=False` can
persist indefinitely.

**Verified live:** 11 artifacts have `test_auc` of exactly 0.0 or 1.0 with `oos_suppressed=False`;
re-running the current logic against their stored metrics, **all 11 would suppress today** (10
via `gap>0.10`, 1 via dead recall). They were trained 2026-07-05 to 2026-09-02, before the
AUD-ML2 fix. `test_auc == 0.0` means *perfectly inverted ranking* — 27 models have it, 5
unsuppressed. Separately, **45 artifacts are >30 days old AND unsuppressed**, oldest 82 days.

The only staleness signal is `_MODEL_STALE_DAYS = 30` (`:970`), a log line at predict time that
says nothing about suppression correctness.

---

## CHECKED AND FOUND CLEAN

- **`_compute_oos_suppression()`** (`:368-405`) — all three conditions correct; uses proper
  equality (no falsy-zero); `abs()` makes the gap check symmetric. Actively firing in live logs.
- **Falsy-zero AUC guards HELD** — the three prior fixes (T237-ML1B, AUD-ML1B-3MODEL,
  AUD-ML1B-NUDGEGATE) are intact at `:1205-1206` and `:1446-1450`.
- **Feature-count mismatch is handled correctly** — inference pins to the bundle's own
  `feature_columns` via `X.reindex(columns=saved_cols)` (`:986`), so a 44-feature model is not
  misaligned against the current 64-column `FEATURE_COLUMNS`.
- **Live-bar exclusion at inference** is present and symmetric with training (`:1019-1025` vs
  `:517-521`).
- **Atomic artifact write (RACE-001)** — `mkstemp` + `os.replace`, correct cleanup, no torn reads.
- **Outcome-label date handling** — correctly normalizes `Price.ts` (DateTime) to dates before
  intersecting with `signal_date` (Date); no HK-offset misalignment.
- **`_blend_weights()`** (`:345-364`) — AUD-C1 equal-class-mass rescale correct, guards `cls_sum > 0`.
- **Container restart ruled out as the cause of Finding 1** — `RestartCount=0`, `OOMKilled=false`;
  the 2026-09-04 run truncated with no restart.

**Caveat:** `docker logs` only covers from 2026-09-04 (container restarted 09-07 21:13). Findings
1 and 3 are anchored on `trained_at` and filesystem mtimes, which are restart-independent.

---

## The through-line

Domain 1 was config that never arrived. Domain 2 is **work that never completes and quality
signals that never refresh** — and the three findings compound: the retrain never reaches
LONG/GROWTH (F1), so their flags never refresh (F3), while the feature meant to teach models from
real outcomes was never running anyway (F2). Fixing F1 alone would partially self-heal F3.

**Not fixed — reported only, per the agreed protocol.**
