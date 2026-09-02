## Design Reference: AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK — Confirmed Real, Deliberately Not Yet Built

**The gap**: `_calibrated_win_rate()` (`services/signal-engine/src/api/routes.py`/
`signals_shared.py`) already computes and SURFACES a real, measured historical win rate per
confidence-band/horizon/direction/market combination — written into
`reasons["calibrated_win_rate"]`/`["calibrated_win_rate_count"]` purely for DISPLAY on the
stock page. Nothing in the actual BUY/SELL decision or the headline confidence NUMBER a user
sees ever reads this value back. Real production data (re-queried 2026-08-18, NOT the audit's
own fabricated "13.3%" statistic) shows genuine inversions where a HIGHER raw-confidence band
has a LOWER measured win rate — e.g. SHORT/SELL: 50-64 band wins 38.4% of the time vs. 65-79
band winning only 21.7%; GROWTH/SELL shows the same pattern (40.8% vs 25.7%).

**Why this was NOT built this session, deliberately**: the audit's own proposed fix (a naive
`confidence * (win_rate / 0.35)` linear scale, applied inline with no train/validation
discipline) would violate this codebase's own established convention that ANY live-decision-
affecting parameter change needs the same chronological train/validation split +
validation-beats-baseline promotion gate every other tuning mechanism in this repo already
enforces (`gate_harness.py`'s `_passes_promotion_margin`, `outcomes_calibrate_apply`,
`tune_strategy`, etc.) — silently reusing display-only calibration data as a live confidence
multiplier, with no held-out validation, is exactly the kind of unvalidated live-decision
change this repo's own audit history has repeatedly found and fixed elsewhere (e.g.
`AUD283-MLWEIGHT-RATCHET`, same session as the earlier `AUD288` items).

**What a real fix needs, before any code is written**:
1. Confirm sample sizes are large enough per band to trust — several bands in the real data
   are `n<50`, some `n<10` (too thin to safely calibrate against without real risk of
   overfitting pure noise).
2. Decide the actual mechanism via a real walk-forward validated sweep — a scoring-time
   penalty vs. a genuine confidence-calibration curve fit (e.g. Platt scaling), not a
   hand-picked linear formula chosen without evidence.

**How to see the underlying (real) pattern today**:
```bash
docker exec stockai-signal-engine-1 curl -s 'http://localhost:8005/signals/confidence-calibration' \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
```
This is the exact display-only endpoint whose data the audit correctly flagged as unused for
live decisions — re-query before acting on the specific numbers above, since real production
values shift as more outcomes resolve.


---


## Feature Reference: AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK — Calibration Now Persisted +
## a Validated, OFF-by-Default Score Layer (Built 2026-08-19)

**The gap**: `_calibrated_win_rate()` (signal-engine's `signals_shared.py`) has always computed
a real, measured historical win rate per `(horizon, direction, market, confidence-band)` — but
it was only ever consumed by 2 code paths, BOTH of which write into a response dict AFTER
their own DB commit already ran. `_scan_for_entries()` (`paper_trading_engine.py`, the real
trading engine) only ever reads `Signal.reasons` from the DB — so the value was computed
correctly and displayed to real users on the stock page, but had never once reached anything a
live entry decision could see.

**Fix, part 1 — durable persistence**: `_bulk_persist()` (`services/signal-engine/src/api/
routes.py` — the ONE function that durably persists `Signal.reasons`, since it's the real
5x/day scheduled path `_scan_for_entries()` reads from) now fetches `_get_confidence_
calibration(s)` ONCE per symbol (matching the sibling T220-G sector-rotation fetch's own
established shape — not once per style, which would be a redundant 4x round-trip) and, for
every BUY/SELL signal in that symbol's batch, writes `ai.reasons["calibrated_win_rate"]`/
`["calibrated_win_rate_count"]` BEFORE the per-style `INSERT ... ON CONFLICT ... DO UPDATE`
upsert serializes and persists `reasons` via `json.dumps(_json_safe(ai.reasons))`. A `None`
result from `_calibrated_win_rate()` (below the sample floor, or horizon/direction not
supplied) correctly writes nothing — never a fabricated 0.0/None pair.

**Fix, part 2 — a new, OFF-by-default score layer**: `_should_enter()` gained a new score
layer, placed right after the existing T172-B catalyst-intelligence block:
```python
if cfg.get("calibration_feedback_enabled") and reasons.get("calibrated_win_rate") is not None:
    _cal_wr = float(reasons["calibrated_win_rate"])
    if _cal_wr >= 0.55:
        score += 1
    elif _cal_wr <= 0.35:
        score -= 1
```
Gated behind `cfg["calibration_feedback_enabled"]` (default `False` — a pure no-op for every
existing portfolio unless explicitly turned on), because this is a NEW score adjustment, not a
tuned value of an existing one — it needed the same walk-forward validation discipline every
other new sizing/scoring parameter in this codebase gets before being trusted live (matching
this session's own `AUD283-MLWEIGHT-RATCHET` precedent). Deliberately does NOT assume "higher
confidence implies higher win rate" — real production calibration data (2026-08-19) shows
genuine non-monotonic inversions (e.g. SWING|BUY|HK: 30.9% win rate at the 40-55 band vs. 13.4%
at 55-70), so the layer reads whichever band's OWN measured number applies to THIS signal, not
a generic confidence-scaled adjustment. Trusts `calibrated_win_rate`'s presence without a
second sample-floor check, since `_calibrated_win_rate()` itself already enforces
`_CONF_CAL_MIN_COUNT` (30) before ever returning a non-`None` value.

**Validation — a new walk-forward sweep, before this is ever turned on for real trading**:
`walk_forward_calibration_feedback()` (`gate_harness.py`) — unlike `walk_forward_min_entry_
score()`/`walk_forward_extended_gate()`, this isn't a search over a continuous parameter (the
score layer's own thresholds are fixed constants) — it's a binary ON-vs-OFF comparison. Cheap
train-slice check first (does turning it ON beat OFF on the older 70% at all — if not, return
early without spending the validation slice), then the real promotion decision: does ON beat
OFF on the held-out newer 30% by the SAME `_passes_promotion_margin()` (min absolute EV-lift
AND min lift-vs-dispersion ratio) every other walk-forward function in this module already
enforces (`BUG233-BACKTESTHARNESS-COINFLIP`'s own fix, reused verbatim, not re-derived). Both
`off_cfg`/`on_cfg` are built from the SAME `base_cfg`, differing only in the one flag under
test — a controlled comparison, not confounded by any other cfg difference. New admin-only
research endpoint `GET /paper-portfolio/backtest/calibration-feedback` (`paper_portfolio.py`),
matching the existing `/backtest/min-entry-score` endpoint's exact shape — never writes to any
portfolio's live config; turning the flag on for real trading still requires an explicit,
separate config change.

**Tests**: `services/signal-engine/tests/test_bulk_persist_calibration_enrichment.py` (9 cases,
source-text regression — `_bulk_persist()` can't be imported directly in this test environment,
`conftest.py` stubs `common`/`db` wholesale) — the once-per-symbol fetch placement (guarding
against the real trap of a SECOND, unrelated `for style_key, ai in all_sig.items():` loop
earlier in the same function that an initial version of this test's own `.index()` call
matched first instead of the intended one), the BUY/SELL-only gate, the `None`-reasons guard,
the only-write-on-a-real-value guard, and strict ordering before the upsert.
`services/market-data/tests/test_walk_forward_calibration_feedback.py` (15 cases, same
source-text-extraction technique for both `gate_harness.py` and `paper_trading_engine.py`) —
the sweep's chronological split, the train-slice cheap-reject-before-validation-spend property,
reuse of the shared promotion-margin gate (not a bare comparison), the controlled ON/OFF cfg
pairing, and the score layer's own flag-gate/threshold/non-monotonicity properties.

**Adversarial verification** — 6 sabotage/revert cycles total, all caught correctly and
reverted (confirmed byte-identical via `diff`/`md5sum` before moving on): removing the
`ai.reasons is None` guard in `_bulk_persist()` (caught); disabling the BUY/SELL condition
entirely (caught by 3 tests); moving the calibration fetch to inside the per-style loop instead
of before it (caught by the ordering test); removing the train-slice cheap-reject early return
in the sweep (caught); swapping the sweep's promotion-margin gate for a bare `>` comparison —
the exact `BUG233-BACKTESTHARNESS-COINFLIP` bug class this margin exists to prevent — (caught);
removing the `cfg.get("calibration_feedback_enabled")` flag check from the score layer entirely
(caught by 2 tests, confirming the layer would otherwise fire unconditionally for every
existing portfolio the moment `calibrated_win_rate` became non-`None`).

Full 1,924-test market-data suite and 349-in-scope-test signal-engine suite (excluding the 2
pre-existing, unrelated failure groups already documented elsewhere in this file —
`test_signal_generator.py`'s `_decide` import-collection error and 4 `test_analyst_momentum.py`
failures, both reconfirmed via `git stash` to predate this change) green. `pyflakes` clean on
all 4 touched files (confirmed via `git stash` that the sole remaining diff, an f-string
warning's shifted line number, predates this change).

**What to check if this looks wrong**:
```bash
docker exec stockai-signal-engine-1 grep -n '_cal_map_bp = _get_confidence_calibration' /app/src/api/routes.py
docker exec stockai-market-data-1 grep -n 'calibration_feedback_enabled' /app/src/services/paper_trading_engine.py

# Confirm calibrated_win_rate is now actually landing in newly-persisted signals (won't
# backfill old rows — only signals generated after this deploy will carry it):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*) FILTER (WHERE reasons->>'calibrated_win_rate' IS NOT NULL), COUNT(*) FROM signals WHERE ts > now() - interval '1 day' AND signal IN ('BUY', 'SELL');"

# Run the new sweep against real production data for a specific style/market (needs an admin JWT):
docker exec stockai-market-data-1 curl -s \
  'http://localhost:8001/paper-portfolio/backtest/calibration-feedback?style=SWING&market=HK&window_days=90' \
  -H "Authorization: Bearer <admin token>" | python3 -m json.tool
```
The score layer will correctly stay a no-op for EVERY real portfolio until a real,
`promoted: true` sweep result justifies setting `calibration_feedback_enabled: True` on that
portfolio's own config — an empty/unchanged result from the sweep endpoint is not a bug, it
means real production data does not yet show a validated edge for this specific style/market
combination.

---

