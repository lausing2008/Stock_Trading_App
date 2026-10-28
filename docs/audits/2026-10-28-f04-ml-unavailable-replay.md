# F04 Stage 0 fix — suppressed models carry no ML weight: implementation and frozen-input replay

Date: **2026-10-28 (UTC)**. Status: **implemented and replayed; NOT deployed — held for review**.
Code is on branch `f04-ml-unavailable` (not on `prod`). F04's split change and model artifacts
are untouched. Finding: [Stage 0 report](2026-10-28-f04-stage0-inventory-and-no-model-trace.md).
Evidence: [`evidence/2026-10-28-f04-ml-unavailable-replay/`](evidence/2026-10-28-f04-ml-unavailable-replay/)
([manifest with hashes](evidence/2026-10-28-f04-ml-unavailable-replay/manifest.json)).

## Result in one paragraph

All 796 expected cells (199 symbols × 4 horizons) are accounted for:
- **792 compared** in all three passes; **4 failed**, all one symbol (SSNLF: "No price data", no
  models); **0 missing**; **0 replay misses**.
- **Control:** replaying the recording with the OLD code reproduced the recorded old pass exactly
  in all 792 cells.
- **What changed.** 30 signals and 315 confidences (3.8% and 39.8% of cells).
  - **Every change** is in a cell whose models are all suppressed and previously carried weight:
    141 at 0.45 and 174 at 0.5.
  - **No cell with an eligible model changed.**
- **New BUYs: 5. New SELLs: 7. Lost BUYs or SELLs: 0.**
  - All 5 new BUYs **fail** the conviction gate, so **no entry became newly actionable**.
  - No changed symbol is held in the 3 open paper positions.
- **Policy point for review.** 7 unchanged BUY rows move from gate tier **full → near**. They
  stay actionable, but they show that the new gate is stricter for one input class (see Gate).

## What was implemented

| Where | Change |
|---|---|
| ml-prediction `trainer.py` | `_no_eligible_model()`: zero unsuppressed members → `ml_available: false`, `metrics: {}`, no probabilities or weights, agreement `no_eligible_model`, `oos_suppressed: true`. 3-model: suppressed members are excluded from probability, agreement, threshold, **reported quality** (now the contributors' own mean) and `model_probabilities`/model name; `suppressed_members` lists them. 2-model: same eligibility rules; its averaged-quality issue (S0-3) deliberately unchanged and still tracked. `/ml/predict` → `predict_single_served()` (a suppressed model answers ML-unavailable). Fully eligible output byte-identical to before. |
| signal-engine `signals.py` | `ml_available` false — or, from an older ml-prediction, `oos_suppressed` true — is treated as ML-unavailable: probability None, quality 0.0 marked `no_eligible_model`, zero weight, `ml_unavailable_reason` recorded per horizon. **SA-27 policy:** compress exactly when the explicit `ml_oos_suppressed` flag is set; the unavailable path sets it. No AUC value is read on that path. Docstring corrected: there is no cross-style fallback. |
| market-data `scheduler.py` | Conviction gate: `ml_unavailable_reason == no_eligible_model` is recorded as the soft `"ML probability unavailable…"` failure, not the never-trained soft pass. |
| Stage 0 evidence | `summary.json` missing-mode count corrected (373), with the correction recorded in the file; capture script fixed. |

**Tests.** ml-prediction 477, signal-engine 1,003 (+13 new e2e), market-data 5,961 (including the 4 new gate tests), all
passing.
- **New regression tests** run the REAL ensemble code with only member predictions stubbed. They
  cover all-suppressed, mixed, missing and fully eligible models. The fully eligible case is
  compared against a frozen copy of the pre-patch functions.
- **End-to-end path:** response → real `_fetch_ml_data` → real `_apply_style_signal`.
- **Gate:** the real `_is_conviction_buy`.
- **Changed tests.** Four existing tests that pinned the old fallback were rewritten, each with
  its reason recorded.
- **Sabotage checks** (each fix reverted in turn; tests fail as expected):
  - remove the gate branch → 2 tests fail;
  - ignore unavailability in signal-engine → 2 fail;
  - restore the ensemble fallback to suppressed members → 3 fail.

## Replay method

`generate_all_signals` was run for every active symbol, three times, on production (read-only),
in throwaway containers built from the deployed signal-engine image.

1. **Members.** Each model family's own prediction for every symbol, style and family (2,388:
   1,430 found, 958 with no artifact) was captured once inside the running service
   ([`capture_members.py`](evidence/2026-10-28-f04-ml-unavailable-replay/capture_members.py)).
   The service's own token was used in-process, so no credentials left the service.
2. **record** (OLD generator = deployed = HEAD `64a87954…`, OLD ensembles). Every HTTP response
   (1,952), Redis read and SQL row was recorded.
3. **control** (OLD code, recording only, `--network none`). This is the harness check.
4. **replay** (PATCHED generator and ensembles, recording only, `--network none`).

ML calls in every pass were answered from the same frozen members, so **only code differs**
between record and replay.
- **Clock:** not frozen. Every cell's input contract is compared between passes: 0 differences.
- **Run window:** 05:48–05:52 UTC, during the HK session and with US closed.
- **Script:** [`replay.py`](evidence/2026-10-28-f04-ml-unavailable-replay/replay.py).
- **Comparison:** [`compare.py`](evidence/2026-10-28-f04-ml-unavailable-replay/compare.py), using
  the real `_is_conviction_buy` from HEAD (old) and the working tree (new).

## Coverage

| Pass | Expected | Completed | Failed | Missing | Replay misses |
|---|---:|---:|---:|---:|---:|
| record | 796 | 792 | 4 | 0 | 0 |
| control | 796 | 792 | 4 | 0 | 0 |
| replay | 796 | 792 | 4 | 0 | 0 |

The 4 failures are SSNLF on every horizon: the generator raises "No price data". SSNLF has no
model in any family (Stage 0: TA-only on all horizons). The failure is upstream of ML, the same in
all passes, and not clustered by horizon or by model availability among the affected cells, so it
does not bias the comparison. **Control == record in all 792 cells.**

**Update 2026-10-28: SSNLF is a discontinued listing** (confirmed by the user). So the 4 failures are
expected, and the replay compared **all 792 cells of the 198 live symbols**. The platform still has it
`active`, not `delisted`, on three watchlists, with no open paper positions. It has no daily bars; ingestion
writes one 5-minute row a day of a frozen quote (65.21, volume 0, through 2026-10-26), and its last signal
was 2026-10-23. Delisting detection never fired because it requires two consecutive yfinance
ticker-missing errors, and Yahoo keeps returning the stale quote instead. Marking it delisted, and
detecting frozen zero-volume quotes, are separate follow-ups and are not part of this fix.

## Results on the 792 compared cells

| | SHORT | SWING | LONG | GROWTH | All |
|---|---:|---:|---:|---:|---:|
| ML unavailable (all suppressed) | 151 | 153 | 162 | 149 | 615 |
| Eligible ML | 24 | 23 | 13 | 26 | 86 |
| No model (TA-only, unchanged path) | 23 | 22 | 23 | 23 | 91 |
| Signal changed | 7 | 1 | 4 | 18 | **30** |
| Confidence changed | 68 | 80 | 85 | 82 | **315** |
| Mean change in confidence where changed (signed) | +6.0 | +1.8 | +2.0 | +7.6 | |
| Largest absolute change in confidence | 15.3 | 9.9 | 5.0 | 25.0 | |

**Signal transitions:**
- SHORT: HOLD→WAIT 3, WAIT→HOLD 2, WAIT→SELL 2.
- SWING: HOLD→WAIT 1.
- LONG: WAIT→SELL 2, HOLD→BUY 2.
- GROWTH: HOLD→WAIT 10, WAIT→SELL 3, WAIT→HOLD 2, HOLD→BUY 3.

**Attribution:** 30 of 30 signal changes and 315 of 315 confidence changes are in ML-unavailable
cells whose models previously had weight > 0 (141 at 0.45, 174 at 0.5). Cells with an eligible
model, and TA-only cells, are identical.

### New BUY signals (end to end, independent of the gate) — 5, none actionable

| Symbol | Horizon | Old | New | Old ML (p, weight) | Gate (new) |
|---|---|---|---|---|---|
| TXN | LONG | HOLD 34.2 | BUY 38.4 | 0.45, 0.15 | failed |
| XLE | LONG | HOLD 35.2 | BUY 40.2 | 0.45, 0.15 | failed |
| ANF | GROWTH | HOLD 36.6 | BUY 52.5 | 0.50, 0.36 | failed |
| MNDY | GROWTH | HOLD 23.4 | BUY 44.6 | 0.45, 0.49 | failed |
| NOW | GROWTH | HOLD 29.1 | BUY 54.2 | 0.45, 0.44 | failed |

**Lost BUYs: 0.** A new BUY is still a BUY signal on the page and in the signal record even
though the conviction gate blocks it. Anything that consumes a BUY without that gate would see
it.

### SELL changes — 7 new, 0 lost

| Symbol | Horizon | Old | New | Old ML (p, weight) |
|---|---|---|---|---|
| BULL | SHORT | WAIT 41.5 | SELL 49.8 | 0.50, 0.17 |
| QUCY | SHORT | WAIT 32.1 | SELL 42.8 | 0.45, 0.30 |
| HWM | LONG | WAIT 28.0 | SELL 31.0 | 0.45, 0.11 |
| TMDX | LONG | WAIT 27.3 | SELL 32.2 | 0.50, 0.15 |
| 0005.HK | GROWTH | WAIT 24.8 | SELL 42.7 | 0.45, 0.49 |
| 0700.HK | GROWTH | WAIT 39.4 | SELL 42.1 | 0.50, 0.06 |
| BULL | GROWTH | WAIT 32.5 | SELL 42.7 | 0.50, 0.24 |

- **Direction.** The suppressed models' weighted 0.45/0.5 was pulling a bearish TA *toward*
  neutral, so removing it lets some signals reach SELL. The removal works in both directions; it
  is not only a bearish bias being taken out.
- **No SELL was lost**, so no risk-reducing exit disappeared in this snapshot.
- **Exits.** None of the changed symbols is held in the 3 open paper positions.

### Gate eligibility

| Old tier → new tier (BUY rows) | SHORT | SWING | LONG | GROWTH |
|---|---:|---:|---:|---:|
| failed → failed | 2 | – | 8 | 10 |
| full → full | – | 1 | – | – |
| **full → near** | 1 | – | 3 | 3 |
| not BUY → failed (new BUYs) | – | – | 2 | 3 |

- **Newly actionable: 0. No longer actionable: 0.**
- **Policy point — needs a decision before release.** The 7 full → near rows are AI (GROWTH,
  LONG), MSFT (GROWTH, LONG) and V (GROWTH, LONG, SHORT).
  - Signal and confidence are identical. Their suppressed models had stored AUC < 0.50, so they
    already carried **zero** weight.
  - The old gate gave that a soft **pass** ("model AUC below random — zero weight… soft pass").
    The new gate records "no eligible model" as a soft **failure**.
  - For the previously **weighted** suppressed rows the tier is preserved exactly (tested). For
    the previously **zero-weight** class it is one soft notch stricter: full → near here, and
    near → failed for any such BUY that also has another soft failure. None occurred in this
    snapshot.
  - **Options:**
    - **(a)** keep it — it follows "missing ML must not satisfy a new-entry gate";
    - **(b)** restore the old soft pass for that class, which would need a stored-quality signal
      the unavailable response deliberately no longer carries.
  - My recommendation is (a). The ML layer is not confirmed in either case, and "near" remains
    actionable.

## Demonstrated changes vs historical influence

The Stage 0 figures and this replay measure different things:
- **Historical influence (Stage 0, persisted signals, 3 days):** 435 signals carried a 0.45
  suppressed-ML probability with weight, and 74 SELLs had suppressed-but-weighted ML. That
  measures *exposure*: how often the defect was present.
- **Demonstrated change (this replay, one snapshot at 05:48 UTC):** 30 signal changes in 792
  cells, 5 new BUYs, 7 new SELLs, 0 lost BUYs or SELLs, 0 newly actionable entries. That
  measures *effect*: what the fix changes on identical inputs.
- **Gap between them.** The influence counts cannot be read as the number of signals the fix
  would change. Most influenced rows keep their signal and only change confidence. Conversely,
  the snapshot is one moment, and other market states will produce different counts.

## Release checklist status

| Check | Status |
|---|---|
| All-suppressed: no ML probability, quality contribution or agreement nudge | Tests (3-model, 2-model, single); replay: 615 cells `no_eligible_model`, weight 0 |
| Mixed ensembles use eligible contributors consistently | Tests (probability, agreement, quality, names); replay: eligible cells unchanged |
| Fully eligible outputs unchanged | Golden comparison in tests; replay: 0 of 86 eligible cells changed |
| Gate preserves the previous tier for equivalent inputs | **Yes for previously weighted suppressed rows; one notch stricter for previously zero-weight rows (7 full → near) — decision above** |
| SA-27 explicit policy for unavailable ML | Tests: identical output for any AUC; flag-driven only |
| New/lost BUYs, SELL changes, confidence, eligibility, exits | Above |
| Frozen evidence and hashes | `manifest.json`: members, recording, three pass outputs, control, inputs |

## Limits

- **Single snapshot,** taken outside US hours. Counts will differ in other market states.
- **Gate soft layers.** Tiers use the base soft layers; calibrated edge extensions were not loaded,
  identically for both passes.
- **Not modelled:** downstream consumers beyond the conviction gate and open paper positions
  (decision-engine scoring, emails).

## Reproduce

Files in `full/` are gzipped; `gunzip` reproduces the raw hashes in the manifest. Then:

`python3 docs/audits/evidence/2026-10-28-f04-ml-unavailable-replay/compare.py <full dir> <out.json> docs/audits/evidence/2026-10-28-f04-stage0/active_symbols.json`
