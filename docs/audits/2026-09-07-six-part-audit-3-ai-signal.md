# Deep Audit Series (2026-09-07): AI Signal Engine — 3 of 6

**Domain:** `services/signal-engine/` — `generators/signals.py` (TA/ML fusion, style profiles,
compression gates, `_decide_style`) and `api/calibration.py` (threshold calibration + watchdog).

**Result: 2 confirmed findings.** One is actively harmful in production *right now*.

---

## Finding 1 — HIGH — The self-healing watchdog LOOSENED the threshold it was trying to tighten, then locked itself out

**File:** `calibration.py:2631-2636`; read side `signals.py:1898-1908`

```python
current_val = float(current_adj) if current_adj else (
    float(redis_client.get(f"stockai:signal_thresholds:{style}") or 0) or floor_threshold
)
new_val = min(current_val + 0.03, floor_threshold + 0.12)
```

The watchdog **seeds** from the calibrated Redis value but takes its **name and ceiling** from a
different base — `floor_threshold = _STYLE_PROFILES[style]["buy_threshold"]["bull"]`
(`:2544-2546`, `:2591`). It is called a *floor* and documented as one, but is never applied as a
floor — it appears only inside a `min()` as a ceiling.

When the calibrated value sits **below** the bull baseline, every `+0.03` step still lands below
it, so the watchdog writes a threshold **looser than the one already in force**. And because
`_get_dynamic_buy_threshold()` reads `stockai:watchdog:*` with strict priority over
`stockai:signal_thresholds:*` (`signals.py:1898-1901`), that looser value wins.

### Verified by me against live Redis + replayed arithmetic

| style | bull base | calibrated | watchdog | tighten_count | effective delta |
|---|---|---|---|---|---|
| SHORT | 0.63 | 0.55 | — | 0 | **−0.08 (loose)** |
| **SWING** | 0.72 | 0.56 | **0.65** | **3** | **−0.07 (loose)** ← deadlocked |
| LONG | 0.60 | 0.55 | — | 0 | **−0.05 (loose)** |
| GROWTH | 0.60 | 0.65 | 0.68 | 1 | +0.08 (correctly tighter) |

Replaying SWING's three tightens from its calibrated 0.56 reproduces the live value exactly:
`0.56 → 0.59 → 0.62 → 0.65`.

Because T232-CAL2 applies the value as a **delta from the bull baseline across every regime**,
those three "tightenings" produced a **7-point loosening in all regimes**:

```
bull      0.72 -> 0.65
high_vol  0.74 -> 0.67
bear      0.76 -> 0.69
```

**The watchdog reacted to a sub-38% win rate by making SWING fire BUY more readily.**

**And it has now disabled its own escape hatch.** `tighten_count` is at `_MAX_TIGHTEN=3`, so
`:2626` short-circuits to `max_tighten_reached_manual_review_needed`. The relax branch cannot
help either — it requires `signals_7d == 0`, and SWING emitted 262 BUYs in 7 days. Only a fresh
`outcomes_calibrate_apply` (which calls `_clear_watchdog_override`, `:1469`) can break it.

GROWTH shows the mechanism working correctly when the calibrated value sits *above* the bull
base. **SHORT and LONG are both in the vulnerable state and will hit this the next time their
win rate dips.**

**Correction to the subagent's report:** it attributed the loosening partly to the
`floor_threshold + 0.12` ceiling. That ceiling is 0.84 for SWING and never binds — the loosening
comes entirely from the **seed**, not the cap. The fix must address which value is used as the
starting point, and add the missing floor.

---

## Finding 2 — MEDIUM — The compression cap can flip a bearish signal back to bullish

**File:** `signals.py:2556-2565`

```python
orig_dist = fused_before_filters - 0.5      # snapshot from :2067
curr_dist = fused - 0.5
if orig_dist != 0 and abs(curr_dist) < abs(orig_dist) * max_ratio:
    fused = 0.5 + float(np.sign(orig_dist)) * abs(orig_dist) * max_ratio
```

The restore is unconditionally in the direction of `orig_dist` — a snapshot taken ~490 lines
earlier, before ~25 subsequent additive adjustments. **The guard compares only magnitudes, never
signs.** If the intervening adjustments moved `fused` across 0.50, the cap does not restore
compression — it **reverses the sign of the signal** and reinstates it at `max_ratio` strength.

**Failure scenario:** a LONG signal fuses to 0.58 (`orig_dist = +0.08`). Downstream,
`analyst_momentum = strong_downgrade` (−0.08, `:2500`) plus a `kscore < 35` penalty (−0.06,
`:2524`) drive it to 0.48 — correctly bearish on two independent inputs. The cap evaluates
`abs(−0.02) < 0.08 × 0.65 = 0.052` → true, and rewrites `fused = 0.552`. The signal is silently
returned to the bullish side and `confidence` is restated as 10.4 *bullish* rather than 4.0
bearish.

**Measured blast radius (verified by me):** the cap fires on **1,129 of 4,120 signals (27%)** —
a hot path — but only **12 became BUY** (11 SHORT, 1 LONG). The sign-flip subset needs an
adjustment large enough to cross 0.50, which is narrower still. **MEDIUM, not HIGH:** the
mechanism is definitively wrong, the current impact is small, and it would widen immediately if
any boost magnitude were increased.

Correct guard: require sign agreement (`np.sign(curr_dist) == np.sign(orig_dist)`), or skip the
cap when the sign has flipped.

---

## CHECKED AND FOUND CLEAN

- **`squeeze_boost` does not exist.** `grep -rn "squeeze_boost"` across `services/`, `shared/`
  and `frontend/src` returns **zero** matches — its absence from all 4,120 signals is correct,
  no code ever writes it. The real logic is `short_interest_flag` (`:2469-2481`), which fires.
  `_check_uw_short_interest_disagreement` (`:1776`) IS reachable (called at `:2770`) and is
  visibility-only by design.
- **The ML-absent path degrades correctly.** `fused = ta_prob` (`:2046`) is a clean pass-through
  on the same 0-1 scale, with `ml_weight=0.0` recorded honestly. No implicit re-weighting.
  Confirmed live: all four horizons share `avg_ta = 0.4852`; LONG/GROWTH's higher `avg_fused`
  (0.545/0.574) comes from their **TA-side profile boosts** — LONG is the only style with
  `kscore_boost: True` (+0.08, `:2520`), GROWTH gets `_growth_ta_adjustment` (+0.10) — **not**
  from missing ML. So LONG's 47.6% BUY rate is a threshold-calibration question, not an
  ML-absence artifact. (Note Finding 1 is actively loosening LONG's threshold by −0.05.)
- **Compression stacking order is correct.** The five gates intended to be un-overridable
  (weekly BUY `:2607`, weekly overbought `:2629`, HSI bear `:2641`, HK southbound `:2652`, HK
  liquidity `:2662`) are all applied *after* the cap at `:2561`, so none can be undone by it.
- **`_get_dynamic_buy_threshold()` clamp cannot invert regime ordering.** The delta is uniform
  and the `(0.55, 0.85)` clamp is monotonic, so ordering is preserved in all 7 regimes × 4
  styles. A clamp can compress two regimes to equality but never invert them. Out-of-bounds
  values correctly return `None` (fall back to profile) rather than being silently clamped.
- **BUY/SELL thresholds cannot cross.** `_DYNAMIC_SELL_THRESHOLD_BOUNDS` maxes at 0.45; the
  lowest `hold_threshold` is GROWTH bull 0.45; `_DYNAMIC_BUY_THRESHOLD_BOUNDS` floors at 0.55.
  `sell_t ≤ 0.45 ≤ hold_t < buy_t` always holds. GROWTH's live `SELL=0.30` is well inside range.
- **Watchdog cannot oscillate** — tighten/relax are mutually exclusive (`elif`, `:2653`) and
  relax requires `signals_7d == 0`. It can only **ratchet**, which is Finding 1.
- **Falsy-zero sweep clean** — `_get_style_tuned_param`, `_redis_get_float`, and the `ml_weight`
  chain all use explicit `is not None`. The `or` chain at `:2632` is a *deliberate* guard against
  a corrupt zero Redis value and is correct in isolation.
- **`_MIN_SAMPLES=15` / `<0.38` gate** correctly guarded, with `_win_start` properly widened by
  each style's own `_OUTCOME_HOLD_DAYS` (T243-TUNE-WINDOW) so LONG's 28-day hold is handled.
- **`_decide_style` regime vocabulary complete** — all 7 keys present on every profile, so the
  `.get(reg, bull_base)` fallback at `:1893` never silently fires.
- **Confidence is correctly signed post-fix** (my own grounding): win rate rises monotonically
  32.8% → 37.9% → 39.4% → 43.0% → **50.5%** across confidence bands. AUD232-BUY-FROM-TOP holds.

---

## The through-line

Domain 1 was config that never arrived; Domain 2 was work that never completed. **Domain 3 is a
self-correcting mechanism correcting in the wrong direction** — and then disabling the very
counter that would have let it try again. Both findings share a shape: a value used as if it
meant something it doesn't (`floor_threshold` used as a ceiling; a stale pre-adjustment snapshot
used as the current direction).

**Not fixed — reported only, per the agreed protocol.**
