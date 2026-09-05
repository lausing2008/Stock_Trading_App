# PHASE C — EXECUTION REALISM (PAPER TRADING AUDIT)

**Date:** 2026-09-05
**Prompt:** `docs/recomm_or_audit/AI Stock Trading Platform — Independent Trading Audit Prompt (REVISED 2026-09-04).md`, Phase C
**Scope:** C.0b (what existing data can answer), C.1, C.2, C.3, C.4. **C.0a (bid/ask/MFE/MAE
capture) is a BUILD, not an audit, and was not attempted** — per the prompt's own instruction not
to present a flat 10bps assumption as a measurement.

---

## Headline

**Execution mechanics are sound. Entry *selection* is the defect — and it is now quantified.**

The single most useful result: entries classify cleanly into quality tiers with a monotonic
return ordering, and **38% of all signals are in the worst tier**.

---

## C.2 Entry timing — the core finding

Classified all 4,770 post-fix BUY signals (per the prompt's instruction to use `signal_outcomes`,
not the 116 closed trades):

| Entry class | n | Avg return | Win rate |
|---|---|---|---|
| **OPTIMAL** (mild move, room left) | 695 | **−0.33%** | **52.2%** |
| LATE (already moved) | 1,816 | −0.70% | 45.0% |
| EARLY (pullback, not extended) | 465 | −0.92% | 43.2% |
| **CHASE** (parabolic) | **1,794** | **−3.94%** | **32.9%** |

**OPTIMAL is the only bucket with a win rate above 50%.** The ordering is exactly what theory
predicts, which is itself evidence the classification is measuring something real.

**38% of signals (1,794) are CHASE** — and they are catastrophically worse than everything else.
This is the concrete, quantified form of the late-entry defect diagnosed in
`WHY_SIGNALS_FIRE_LATE.md`.

Note EARLY underperforms OPTIMAL: buying a deep pullback is *not* better than buying a mild one.
"Buy the dip" is only right up to a point — a stock down >5% from its high with negative 10-day
momentum is often falling for a reason.

### The shipped filter is validated

`AUD-CHASE-ROC10` (shipped earlier today) blocks `roc_10 >= 10`, which targets precisely the
CHASE bucket:

| Gate | n kept | Avg return |
|---|---|---|
| All signals | 4,770 | −1.89% |
| **AUD-CHASE-ROC10 (shipped)** | 2,976 | **−0.65%** |
| OPTIMAL-only (tighter) | 1,160 | −0.57% |

The shipped filter captures nearly all the available benefit (−1.89 → −0.65) while keeping
**2.5× more signals** than the tighter OPTIMAL-only gate would. That trade-off looks correct.

---

## C.1 / C.0b Execution realism — no live defects

### Exit-reason distribution

| Exit reason | n | Avg return | Avg hold | % profitable |
|---|---|---|---|---|
| stop_hit | 59 | −2.85% | 5.6d | 23.7% |
| breakeven_stop | 33 | −0.30% | 4.5d | 12.1% |
| **trailing_stop** | 11 | **+4.63%** | 14.2d | **100.0%** |
| **target_reached** | 7 | **+12.22%** | 5.7d | **100.0%** |
| momentum_exit | 4 | −0.48% | 2.0d | 0.0% |
| hold_stall_timeout | 1 | +3.09% | 21.0d | 100.0% |
| signal_exit | 1 | −4.46% | 7.0d | 0.0% |

**`trailing_stop` and `target_reached` are 100% profitable** (18 of 116 trades). The exit
machinery works when a trade gets far enough for it to engage — the problem is that only 18
trades did.

### Stop execution correctness — clean

Of 59 `stop_hit` trades, 7 filled below the stop (gapped through), worst gap −7.83%. That is
normal, realistic gap behaviour, not a modelling error.

**Investigated and cleared:** 14 `stop_hit` trades exited *profitably*, some filling up to
**+27.6% above the stop price** — which is impossible for a real stop. Checked the dates:
**all 14 predate the 2026-08-31 `AUD262` exit-reason fix, and there have been ZERO since.**
Stale history, not a live bug — exactly as the prompt's "verified non-issues" section states.
Not re-litigated.

**Also not re-litigated** (per prompt): `breakeven_stop`'s small negative average, and the
GROWTH −12% stop being intentional design.

---

## C.3 Exit analysis — a real pattern with a small dollar impact

Using `highest_price` as the MFE proxy (MAE unavailable — blocked on C.0a):

| Metric | Value |
|---|---|
| Avg peak unrealized gain | **+6.09%** |
| Avg realized return | **−0.39%** |
| **Avg giveback** | **6.47 points** |
| Trades that ran +5% then closed at a loss | **17** |

A 6.47-point giveback looks alarming, and the obvious remedy is a "move stop to breakeven after
+5%" rule.

**Modelled it — and it does not help.** Those 17 trades lost only **−$347 combined**. Forcing
them all to break even changes total P&L from −$8,029 to **−$7,682**: a 4% improvement.

The giveback is real in percentage terms but immaterial in dollars, because **the large losses
never ran up first** — they went against the position from the start. Recommending a
breakeven-ratchet on the strength of the percentage figure alone would have been wrong.

---

## C.4 Overtrading — not a problem

| Metric | Value |
|---|---|
| Total trades | 124 |
| Active trading days | 39 |
| **Trades per active day** | **3.2** |
| **Avg holding period** | **6.4 days** |
| Held ≤1 day | 18.5% |

3.2 trades/day across 5 portfolios with a 6.4-day average hold is measured, not frantic. No
evidence of churn. **No action needed.**

---

## Conclusions

1. **Execution is not the problem.** Stops fire correctly, gaps are handled realistically,
   exit-reason labelling is accurate post-2026-08-31, and trade frequency is sane.
2. **Entry selection is the problem, and it is now quantified**: 38% of signals are CHASE
   entries returning −3.94% at a 32.9% win rate.
3. **The shipped anti-chasing filter targets exactly that bucket** and is validated by this
   independent classification (−1.89% → −0.65%).
4. **Do not add a breakeven-ratchet rule.** Modelled at +$347 of $8,029 — the giveback pattern
   is real but the money isn't in it.
5. **The exit machinery is good when reached** — `trailing_stop` and `target_reached` are 100%
   profitable across 18 trades. More trades reaching them is an *entry-quality* outcome, not an
   exit-logic change.

## Limitations

- MAE, bid/ask, spread, and true slippage are **not captured** (C.0a). Every execution-realism
  claim here is bounded by that; a real slippage analysis remains impossible.
- 116 closed trades is a small sample for exit analysis. The entry classification uses the much
  larger `signal_outcomes` (n=4,770) precisely because the prompt directs it.
- Post-fix window only (2026-08-04+), one market regime.
