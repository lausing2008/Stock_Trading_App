# §F.3 (NON-BLOCKED HALF) — BACKTEST BIAS AUDIT

**Date:** 2026-09-05
**Prompt:** REVISED 2026-09-04 audit prompt, §F.3
**Scope:** §F.3 is *mostly* blocked — the walk-forward harness does not exist and must be built.
But its own note carves out work that is **not** blocked:

> *"existing backtests should still be audited **now** for look-ahead bias, survivorship bias,
> data snooping, overfitting, parameter-optimisation bias, and multiple-testing problems — that
> part is Phase A work and is not blocked."*

That audit had never been done. This is it.

---

## Summary

**The backtest engine is well-built.** Look-ahead is correctly eliminated by construction and
documented; multiple-testing correction already exists. Two real gaps found, one of which is
currently **unmeasurable rather than absent** — a distinction worth keeping honest.

| Bias | Verdict |
|---|---|
| Look-ahead (same-bar fill) | **CLEAN** — 1-bar lag by construction |
| Look-ahead (equity curve) | **CLEAN** — `position.shift(1)` |
| Transaction costs | **CLEAN** — slippage + fee applied on both sides |
| Multiple testing | **CLEAN** — promotion-margin test (AUD263) |
| Parameter-optimisation bias | **PARTIALLY MITIGATED** |
| **Survivorship bias** | **UNMEASURABLE** — universe contains zero delisted stocks |
| **Walk-forward validation** | **ABSENT** — the blocking gap |

---

## CLEAN: look-ahead bias is eliminated by construction

`services/strategy-engine/src/backtest/engine.py` opens with an explicit statement of its own
timing contract:

> *"Signal detected at bar i-1 close, fill at bar i close (1-bar lag eliminates same-bar
> look-ahead). Equity curve uses `position.shift(1)` so the fill bar's return is excluded."*

Verified in the code, not just the docstring (`engine.py:59-69`):

```python
for i in range(1, len(feat)):
    if not in_pos and entries.iloc[i - 1]:          # signal from the PRIOR bar
        entry_p = feat["close"].iloc[i] * (1 + self.slippage + self.fee)   # fill on THIS bar
```

Entry and exit both read `entries.iloc[i-1]` / `exits.iloc[i-1]` and fill at `close.iloc[i]`.
**No same-bar leakage.** The `position.shift(1)` on the equity curve independently prevents the
fill bar's own return from being counted.

This is a genuinely careful implementation — the exact defect class that Phase A found elsewhere
(the live-bar contamination) is correctly handled here.

## CLEAN: transaction costs are applied symmetrically

Slippage and fees are applied *against* the trader on both sides — `* (1 + slippage + fee)` on
entry, `* (1 - slippage - fee)` on exit (`engine.py:61, 66, 74`). No cost-free fills.

**Caveat carried from Phase C:** the slippage *value* is a flat assumption, not a measurement
(§C.0a / §F.5). The mechanism is correct; the input is unvalidated.

## CLEAN: multiple-testing correction exists

`AUD263-TUNESTRATEGY-NO-MULTIPLE-COMPARISONS-CORRECTION` replaced a bare `ev_lift <= 0` floor
with `_passes_promotion_margin()` (`calibration.py:2209`), a real two-part test that accounts for
the validation slice's own return dispersion.

This matters more than it sounds: a tuner that evaluates many parameter combinations and promotes
on "any positive lift" will promote noise reliably. The platform already fixed this.

---

## UNMEASURABLE: survivorship bias

`strategy-engine` contains **zero references to `delisted`** — the backtester does not filter or
even consider delisting status.

Normally that is a clear finding. Here it is not, and the honest answer is *unmeasurable*:

```
SELECT COUNT(*) FILTER (WHERE delisted), COUNT(*) FROM stocks;
→ 0 delisted, 193 total
```

**The universe currently contains no delisted stocks at all**, so the omission has no present
effect. But it is a latent defect: the moment a tracked stock delists, backtests will silently
include it up to its final bar and exclude the delisting outcome — inflating results exactly
when it matters most.

This is consistent with CLAUDE.md's own standing limitation ("survivorship bias in ML training
data — requires external data source") and with the `aud14-survivorship` work already shipped for
*detection*. The gap is that the backtester does not consume that detection.

**Recommended:** have the backtester exclude, or explicitly mark, symbols whose `delisted` flag
is set — before the first delisting arrives, not after.

## PARTIALLY MITIGATED: parameter-optimisation bias

Optuna-tuned parameters (`_TA_WEIGHTS`, `_STYLE_PARAMS` via `trade_params.json`) are fitted on
historical outcomes and then used live. Mitigations that genuinely exist:

- The promotion-margin test above, which is the main defence.
- Per-horizon and per-market segmentation (`AUD-MINRR-MARKETBLIND`), so one market's data cannot
  set another's thresholds.

**What is missing is the out-of-sample half** — which is precisely §F.3's blocked portion. This
session demonstrated the risk concretely and repeatedly: **three separate findings reversed when
validated out-of-sample or on a wider sample** (the ignition band, `insider_score`, the
"defensive skill" beta artifact). Every one looked convincing in-sample.

That is the strongest available argument for building the walk-forward harness: not theory, but
three measured reversals in a single day.

---

## ABSENT: walk-forward validation

Still not implemented. CLAUDE.md lists it as a known limitation ("deferred, 2+ weeks of work"),
and §F.3 calls it *"arguably the highest-value item in §F, because it partially unblocks F.1 and
F.4 as well."*

**This audit agrees, and adds evidence:** it is also a **required gate** for live automation
(Phase D, D.4). Without it, live autonomy stays blocked regardless of what expectancy does.

---

## Conclusions

1. **The backtest engine itself is trustworthy** on the axes that can be checked now. Look-ahead
   is eliminated by construction and documented honestly; costs are applied symmetrically;
   multiple-testing correction exists.
2. **Survivorship bias is a latent, not active, defect** — zero delisted stocks in the universe
   today. Fix it before the first delisting, not after.
3. **Parameter-optimisation bias is real and only partially mitigated.** The in-sample/OOS gap
   is not theoretical here — three findings reversed under OOS validation today.
4. **Walk-forward validation remains the single highest-value unbuilt item**, now supported by
   three independent lines of evidence: §F.3's own reasoning, D.4's gate requirement, and this
   session's measured reversals.

## What this does NOT establish

- Whether *specific past backtest results* were inflated — that needs the walk-forward harness.
- Data-snooping across the wider research process (how many hypotheses were tested before one
  worked) — not reconstructable from the codebase.
