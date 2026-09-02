## Recurring Issue: BUG-SA33-UNREACHABLETHRESHOLD — A Design Doc's Own Fix Was Mathematically Unable to Achieve Its Stated Goal (Fixed 2026-07-27)

**Found via adversarial review of uncommitted work**, not a live incident — a separate,
uncommitted "SA-33 early-recovery entry timing fix" (`services/signal-engine/src/generators/
signals.py`, with a matching `docs/SIGNAL_FIX_SA33_2026-07-25.md` design doc) was reviewed
before being trusted/shipped, per this repo's own standing "verify before trusting a claim"
discipline — applied here to a design doc's own math, not just a tracker's `defaultStatus`.

**The claim**: Finding 1 of that fix said a 0.25 partial credit on the TREND pillar's
`above_sma50` term would let the pillar reach its own 0.5 "active" threshold (used by
`independent_pillars_active`, which gates/boosts the fused signal probability) "when combined
with a supertrend cross-up or ADX bullish signal" — i.e., either one alone, plus the credit,
should be enough.

**Why it was false**: during genuine early recovery (`above_sma50=False` by definition — the
whole point of the fix), `sma50_above_sma200` and `golden_cross_event` are BOTH structurally
`False` too (both require `sma50 > sma200`, which by definition hasn't happened yet in early
recovery) — so `_sma_golden_score` and `_gc_score` (2 of the pillar's 5 weighted terms) are
always `0.0` in exactly this scenario. Ran the actual arithmetic: with the original 0.25
credit, the BEST achievable `p_trend` — even with BOTH a supertrend cross-up (0.10 weight) AND
an ADX bullish trend (0.20 weight) confirming simultaneously — was `0.25*0.30 + 1.0*0.20 +
1.0*0.10 = 0.375`. Never 0.5. The "either one alone" framing was worse than just imprecise —
solving for the credit needed to satisfy it showed it was mathematically IMPOSSIBLE at any
credit ≤ 1.0 (the pillar's own 0-1 scale): the weaker single signal (supertrend cross-up alone)
would need a credit of `(0.5-0.10)/0.30 = 1.33`, off the scale entirely.

**A secondary comment-accuracy bug in the same fix**: the code comment and module docstring
both said the 0.25 credit "lifts the trend pillar from 0 to ~0.25" — the real CONTRIBUTION to
`p_trend` is `0.25 * 0.30 = 0.075` (the credit is a sub-score, not a direct pillar-scale value);
the design doc's own "Fix" section got this part right, but the inline code comments didn't.

**Fix applied**: raised the early-recovery credit from 0.25 to 0.70 — verified reachable:
`0.70*0.30 + 1.0*0.20 + 1.0*0.10 = 0.51`, just past the active threshold, when BOTH a
supertrend cross-up AND an ADX bullish trend confirm together. This is a STRICTER bar than the
original "either one" claim (which was never actually achievable), but a real, achievable,
conservative one — verified the pillar still correctly stays inactive with the credit ALONE
(`0.70*0.30=0.21`) or with only ONE of the two confirming signals (`0.41` with ADX alone, `0.31`
with supertrend alone), preserving the fix's own stated invariant that the credit "cannot push
the pillar above 0.5 on its own." Findings 2 (RS-compression recovery exemption) and 3 (weekly-
gate recovery exception) were independently checked and found logically sound — both gate on
`stoch_rsi_cross_up`, a real, correctly-defined crossover event with no equivalent reachability
problem — left unchanged.

**Design invariant reinforced**: a design doc's own "this reaches the threshold" claim is a
factual assertion that can be — and here was — simply wrong, independent of whether the
supporting code compiles, passes a lint check, or "looks reasonable" on a read-through. Before
trusting ANY claim of the form "value X combined with condition Y reaches threshold Z," run the
actual arithmetic against the real formula and the real structural constraints of the scenario
being described (here: which OTHER terms are ALSO forced to zero in that same scenario) —
"looks plausible" is not the same bar as "is arithmetically true," and a claim can fail even
when every individual code change is syntactically correct and internally consistent.

**Tests**: `services/signal-engine/tests/test_sa33_early_recovery.py` (15 cases) — Finding 1
covers the corrected reachability property directly: alone stays below 0.5, with only ONE of
the two confirming signals stays below 0.5 (both directions tested independently), with BOTH
reaches ≥0.5, an exact hand-computed arithmetic check (`0.51 ± 0.01`), and confirms a stock
genuinely above SMA50 (not in early recovery at all) still gets full credit. Findings 2/3 each
get 4 cases covering the real exemption/exception plus both single-condition-only cases (guards
against either condition alone silently triggering the bypass). A real, engineered price
fixture (`_early_recovery_df()` — a long decline followed by a short, modest bounce that
reclaims SMA20 but not SMA50) is used for Finding 1's price-shape-dependent flags, verified via
direct assertions on the real computed SMA values rather than assumed; supertrend/ADX are
monkeypatched directly (both are simple module-level functions) to construct exact confirming/
non-confirming scenarios without fighting synthetic price generation for indicators that don't
depend on the SMA20/50 relationship at all.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted: reverting the credit
from 0.70 back to the original broken 0.25 (2 tests correctly failed, including the exact
arithmetic check, landing on `0.38` — matching the hand-computed `0.375` within test
tolerance); removing the RSI-band condition from Finding 2's `rs_recovery_floor` guard (1 test
caught it); removing the volume-confirmation condition from Finding 3's
`_weekly_gate_recovery_exception` guard (1 test caught it). Full 96-test signal-engine suite
(up from 81) green modulo the 4 pre-existing, unrelated `test_analyst_momentum.py` failures
already documented elsewhere in this file. `pyflakes` clean (the one remaining warning, an
unused `macd_line` at line 768, confirmed pre-existing and unrelated).

**Not yet deployed at review time** — this whole SA-33 fix (both the original 3 findings and
this correction) was found completely uncommitted in the working tree and NOT present in the
running `stockai-signal-engine-1` container (`grep` for "SA-33"/"early_recovery_trend" inside
the container returned zero matches) before this review — meaning the original, broken 0.25
version was never actually live in production. The corrected 0.70 version is what shipped.

**What to check if this looks wrong**:
```bash
docker exec stockai-signal-engine-1 grep -n "_above_sma50_score = 1.0 if above_sma50" /app/src/generators/signals.py
# Should show: ... else (0.70 if _early_recovery_trend else 0.0)) — NOT 0.25.

# Live-check the arithmetic against a real symbol currently in early recovery:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, ts, reasons->>'pillar_trend' AS pillar_trend, reasons->>'early_recovery_trend' AS early_recovery, reasons->>'trend_above_sma50' AS above_sma50 FROM signals WHERE reasons->>'early_recovery_trend' = 'true' ORDER BY ts DESC LIMIT 10;"
```

---

