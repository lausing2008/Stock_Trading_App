## Feature Reference: AUD232-059 — meta_trainer.py's Per-Row Feature Recomputation Deduplicated (Fixed 2026-07-21)

**The gap**: `train_meta_model()` (`services/ml-prediction/src/training/meta_trainer.py`)
called `build_features()` (and `compute_label_threshold()`) **fresh for every
`signal_outcome` row** — re-slicing the price DataFrame up to that row's `signal_date` and
recomputing the entire rolling-window indicator pipeline (SMA/RSI/MACD/ATR/etc.) from
scratch each time. For a symbol with N outcome rows, that's N full recomputations over
heavily-overlapping windows instead of one.

**Fix**: call `build_features()` **once per symbol** on the full price history, then index
into the result per row instead of recomputing. This is safe for two reasons, both verified
directly (not assumed):
1. `build_features()`'s indicators are all trailing rolling-window computations — a row's
   value depends only on data up to and including that row, never on data trailing after it,
   so computing on the full `df` vs. a truncated `df_upto` slice gives numerically identical
   values for any given date.
2. `horizon` (which genuinely varies per row within the same symbol — SHORT/SWING/LONG/
   GROWTH have different day counts) only affects `build_features()`'s discarded
   `fwd_ret`/`y_dir` outputs and `compute_label_threshold()`'s result — itself only consumed
   by `build_features()`'s non-inference-mode dead-zone mask, never reached here since
   `inference_mode=True` is always passed at this call site. Both were genuinely unused
   busywork on top of the duplication itself; the now-dead per-row `compute_label_threshold()`
   call and its now-unused import were both removed.

```python
X_feat_full, _, _ = build_features(df, horizon=10, macro_df=macro_df, inference_mode=True)
feat_ts = pd.to_datetime(df["ts"]).reset_index(drop=True)
for row in sym_rows_sorted:
    signal_date = pd.Timestamp(row.signal_date)
    eligible_idx = feat_ts[feat_ts <= signal_date].index  # look-ahead safe
    row_idx = eligible_idx[-1]
    latest = X_feat_full.loc[row_idx]  # instead of a fresh build_features() call per row
```

**Tests**: `services/ml-prediction/tests/test_meta_trainer_feature_dedup.py`, 6 cases — the
core numerical-parity claim is proven directly against the REAL `build_features()` (not a
hand-copied reimplementation of the old logic): a full-history call at a given date produces
bit-identical values to the old truncated-slice call at that same date, checked at one date
and across 4 different dates within one symbol; a dedicated test confirms `horizon` genuinely
has zero effect on `X` in inference mode (three different horizon values produce an
identical DataFrame via `pd.testing.assert_frame_equal`); a test confirms
`build_features()`'s boolean-mask filtering preserves original row-position index values
rather than resetting to a fresh range (the property the fix's per-row lookup depends on);
and 2 source-text regression checks guard the actual `meta_trainer.py` code — `build_features()`
must be called exactly once per symbol, strictly before the per-row loop begins, and
`compute_label_threshold()` must no longer be called at all.

**Why `train_meta_model()` itself isn't exercised end-to-end**: it requires a real Postgres
session for a `LEFT JOIN LATERAL` raw-SQL query with no SQLite equivalent (confirmed via its
own `db=None` test-injection seam's docstring, but `LATERAL` joins aren't supported by
SQLite) — testing is scoped to proving the underlying `build_features()` parity property
directly instead, matching the proportionate-testing precedent already used elsewhere in this
codebase for functions too DB-coupled to fully exercise locally (e.g. `_monitor_positions()`'s
source-text-only tests).

**Adversarial verification**: reverted the fix by moving `build_features()` back inside the
per-row loop (restoring the exact original duplication) and confirmed the
build-features-called-once source-text test correctly failed before re-reverting.

Full 19-test ml-prediction suite green (9 across the two meta_trainer test files combined);
frontend typecheck clean (no frontend files touched).

**What to check if this looks wrong**:
```bash
docker exec stockai-ml-prediction-1 grep -n "X_feat_full, _, _ = build_features" /app/src/training/meta_trainer.py
```
Should show exactly one match, positioned before the `for row in sym_rows_sorted:` loop. If a
meta-model retrain's AUC looks suspiciously different from before this fix, that would be a
real red flag worth investigating directly — the 6 tests above prove the feature VALUES are
identical, but a live retrain comparison was not additionally run as part of this fix (the
numerical-parity tests were judged sufficient evidence, since they test the actual property
the fix depends on using the real function, not a mock).

---


## Feature Reference: T237-ML2b — eps_revision_direction Reintroduced Point-in-Time-Correctly (2026-08-18)

**Continues the next-improvements survey**, item 2 of the 3 remaining verified candidates
(FMP analyst estimates / K-Score weight validation were the other two). Verified against real
code before building: `eps_revision_direction` was removed under `T237-ML2` (2026-07-something,
per the module docstring's own history) for a real look-ahead-bias reason — the original
implementation broadcast TODAY's live analyst-recommendation trend to every historical training
row with no date bound, the exact bias class already fixed for `recommendation_mean` itself via
a point-in-time (PIT) snapshot join, but missed for this derived feature.

**Root cause of the original bug, confirmed via `git log -p`**: the removed implementation ran
its own live `SELECT recommendation_mean FROM fundamentals_snapshot WHERE symbol=:sym ORDER BY
snapshot_date DESC LIMIT 8` query — unconditionally, with no date bound — then stored the
result into `fund_data["eps_revision_direction"]`, which the generic `FUNDAMENTAL_COLUMNS`
broadcast loop then applied identically to EVERY row in the training set, regardless of that
row's own historical date.

**The reimplementation**: `FundamentalsSnapshot` rows already flow into `build_features()` via
`fund_snapshots` (a list of per-snapshot dicts, already used for `recommendation_mean`'s own
correct PIT join). A new computation on `_snap_df` (already sorted by `snapshot_date`) applies
`.rolling(window=8, min_periods=2).apply(lambda w: w.iloc[0] - w.iloc[-1])` directly on the
`recommendation_mean` SERIES ITSELF — producing, for every snapshot row, the delta between its
own value and the value up to 8 snapshots prior. This per-snapshot delta series is then
`merge_asof(..., direction="backward")`'d onto the training rows exactly like every other PIT
column — so a training row's snapshot can only ever be at or before its OWN date, never the
future. Thresholded to the SAME `±0.15` bands as the live `T220-F` signal in `signals.py`
(lower `recommendation_mean` = more bullish, so a positive delta means analysts upgraded).

**A genuine, previously-undiscovered second gap found while reimplementing this**: the ORIGINAL
implementation's `fund_data["_symbol"] = symbol` stash (still present as dead code in
`trainer.py`'s inference call site, with a comment saying "so build_features can look up
earnings revision direction") was leftover from the removed live-query version — nothing wired
`fund_snapshots` into the `inference_mode=True` call site at all, meaning even a correctly
point-in-time-safe reimplementation would have silently returned `NaN` at LIVE prediction time
forever (this feature has no broadcast-from-`fund_data` equivalent the way other PIT columns
do — it was ALWAYS computed from a rolling snapshot window, never a single field). Fixed by
adding a `_load_fund_snapshots(symbol)` call to the inference call site (`trainer.py`, mirroring
the existing training call site's own identical call) and computing `eps_revision_direction`
UNCONDITIONALLY whenever `fund_snapshots` is available — not gated behind `not inference_mode`
the way the OTHER PIT columns correctly are, since a live-prediction row IS "today," so using
the full snapshot history through today is genuinely current information, not lookahead (the
other PIT columns don't need this special case since their broadcast-from-`fund_data` path is
already correct at inference time).

**`FUNDAMENTAL_COLUMNS`** gained `eps_revision_direction` back (it flows automatically into
`FEATURE_COLUMNS` via `*FUNDAMENTAL_COLUMNS`) — the broadcast loop sets it to `NaN` as a safe
default (no raw `fund_data` field exists for it), then the new snapshot-based computation
overwrites it with the real value whenever `fund_snapshots` is non-empty.

**Tests**: `services/ml-prediction/tests/test_eps_revision_direction.py` (11 cases) —
`build_features()` only depends on `numpy`/`pandas` (real, installed), so it imports and runs
directly under pytest with no stub workaround, matching `test_features.py`'s own established
precedent. Covers: no-snapshots degrades to `NaN` not a crash, upgrade/downgrade/flat trend
classification, the exact `±0.15` threshold band boundary, the `<2`-snapshots-insufficient
case, the 8-snapshot window cap (a 9th, older snapshot must NOT be included in the delta —
matching `signals.py`'s own live `LIMIT 8` semantics exactly), and — the two tests this whole
reimplementation exists for — an EARLY training row never reflecting a LATER
upgrade/downgrade that hadn't happened yet as of that row's own date, and the mirror case
confirming a LATER row correctly DOES see an earlier-completed downgrade (proving the fix
doesn't just always degrade to `NaN`/`0`). A dedicated test also confirms `inference_mode=True`
computes the feature too, not just training mode — the exact second gap found and fixed above.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. Removing the `window=8` cap (using the full snapshot history instead) — caught by the
   dedicated window-cap test, with a real value mismatch (`1.0` instead of the correct `0.0`)
   proving an out-of-window snapshot leaked into the delta.
2. Widening the `±0.15` threshold to `0.0` — caught by the dedicated small-delta-stays-flat
   test.
3. Swapping `merge_asof(direction="backward")` for `direction="forward"` (the exact class of
   bug this whole reimplementation exists to prevent) — caught broadly across 7 of 11 tests,
   confirming this property is well-covered from multiple angles, not just one narrow check.

Full 72-test ml-prediction suite green (up from 61); `pyflakes` clean on every touched file
(confirmed via `git stash` that the 4 pre-existing warnings — 2x unused `json` import in
`builder.py`, unused `db.Signal`/`..features.SECTOR_COLUMNS` imports in `trainer.py` — predate
this change, only line numbers shifted).

**Tracker**: `improvements.tsx` — new entry `T237-ML2b-EPS-REVISION-REINTRODUCED`.

**What to check if this looks wrong**:
```bash
# Confirm the feature is present in a real trained model's feature columns:
docker exec stockai-ml-prediction-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.features import FEATURE_COLUMNS
print('eps_revision_direction' in FEATURE_COLUMNS)
"

# Confirm the inference-time fund_snapshots wiring is present (the second gap fixed here):
docker exec stockai-ml-prediction-1 grep -n "infer_fund_snapshots\|_load_fund_snapshots(symbol)" /app/src/training/trainer.py

# Spot-check the computed value against real production data for a specific symbol:
docker exec stockai-ml-prediction-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.training.trainer import _load_fund_snapshots
snaps = _load_fund_snapshots('AAPL')
print(f'{len(snaps)} snapshots loaded')
print(snaps[-3:] if len(snaps) >= 3 else snaps)
"
```
If a retrained model's `eps_revision_direction` importance looks suspiciously flat/always-NaN,
check whether `fundamentals_snapshot` actually has enough real weekly history for that symbol
yet (`min_periods=2` requires at least 2 real snapshot rows) — a symbol added to this app
recently may simply not have accumulated enough snapshot history for this feature to ever
produce a non-NaN value, which is correct, expected behavior, not a bug.

---


## Feature Reference: AUD291-SILENT-EXCEPTIONS-MLPRED — All ~29 Genuinely-Silent Exception Blocks in ml-prediction Now Log (2026-08-26)

**Continues this session's own tracker-review discipline** — the user asked to check tiers
217/232/234/241/242/288/291 and, after establishing most `todo`/`in-progress` items in those
tiers are DELIBERATE architecture/business decisions (not forgotten bugs), 2 genuinely
buildable AUD291 items were confirmed and built.

**The fix**: went through every `except Exception` block in `trainer.py`/`tuner.py`/
`meta_trainer.py` individually (matching this repo's own established discipline — never a
blanket find-and-replace) — 6 already logged, 2 (both model-save atomic-write blocks, RACE-001)
already correctly `raise`, and ~29 were genuinely silent. Fixed all 29 with a per-site judgment
call: symbol-scoped `log.warning` for real per-symbol enrichment-fetch failures (fundamentals/
macro/sector/outcome-feature loads, in both train and inference paths), `log.debug` for
per-window skips inside `validate_walkforward()`'s potentially-long loop (matching this file's
own convention of not spamming warning-level logs for expected-frequency events).

**The single highest-value fix**: `predict_latest_ensemble_three()`'s meta-model prediction
call in `trainer.py` — its OWN pre-existing comment already documents a real bug
(`T237-ML-META3`: a bare, unimported module name that raised `ModuleNotFoundError` on EVERY
call, completely silently, for a long time) that this exact except block's silence is what hid
in the first place. The fix adds a dedicated log line specifically so a regression of that
class can never go unnoticed again.

**Verification**: full 91-test ml-prediction suite green (zero regressions); pyflakes clean
(all 3 remaining warnings confirmed pre-existing via `git stash`).

**What to check if this looks wrong**:
```bash
docker exec stockai-ml-prediction-1 grep -c "log.warning\|log.debug" /app/src/training/trainer.py /app/src/training/tuner.py /app/src/training/meta_trainer.py
```

---

