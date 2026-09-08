# Deep Audit Series (2026-09-07): Entry Timing — 4 of 6

**Domain:** when a signal becomes a position — the entry scan, gap/extension gates, anti-chase
filters, and the signal→entry handoff.

**Result: 3 confirmed findings, one CRITICAL.** All three are gates that exist, are configured,
and are threaded — but **cannot block anything**. Two are mathematically incapable of firing;
one is simply absent from the path that decides.

---

## The measurement that frames this domain

Entry gap vs the signal-date close predicts outcome **monotonically** (post-fix BUYs, n=4,095):

| gap band | n | win % | avg return |
|---|---|---|---|
| ≤0% | 2,203 | 39.4% | −2.20% |
| **0–1%** | **665** | **44.5%** | **−0.92%** |
| 1–3% | 735 | 33.2% | −2.12% |
| **>3%** | **492** | **31.9%** | **−3.80%** |

A 12.6-point win-rate spread. **Chasing is not systemic** — mean gap is *negative* (−0.33%) —
it is a **tail problem**: 492 entries gapped >3%, 241 >5%, worst +20.5%.

---

## Finding 1 — CRITICAL — The anti-chase filter is absent from the gate that actually decides

**File:** present at `paper_trading_engine.py:2106-2111` (`_should_enter()`, the **fallback**);
**absent** from `services/decision-engine/` entirely — `grep -rn "roc_10"` returns **zero** matches.

`decision_engine_mode` defaults to `"primary"`, so decision-engine's verdict opens the trade and
`_should_enter()` is used only for shadow-logging. AUD-CHASE-ROC10-PAPERPORT (2026-09-05) was
ported into the fallback **only**. The same applies to the AUD-GAPCHASE-EARNINGSVOL secondary
condition at `:2083`.

**Why the other gates can't catch it:** T171 measures the gap against the **signal-time close**
and T196 against the **prior settled close**. A large run-up that happened *days before the
signal* is invisible to both. `roc_10` is the only gate that sees a 9-day-old melt-up — and it
never runs.

**Worked example:** ANF gapped **+35.7% on 2026-08-26** on earnings (108.90 → 147.75), chopped
for a week, then entered on 2026-09-04 at $145.54 with `roc_10 = 33.0%` — 3.3× the 10% limit.
Its *measured* T171 gap was **+0.37%**, so every price-reference gate passed it.

### Verified by me against production (numbers differ from the subagent's — mine are worse)

| cohort | n | avg return | win % |
|---|---|---|---|
| `roc_10 < 10%` | 69 | −0.32% | 34.8% |
| **`roc_10 ≥ 10%`** | **24** | **−2.39%** | **16.7%** |

A **2.07-point return gap and 18-point win-rate gap**. (The subagent reported −1.83% vs −0.50%;
re-running it myself gives a larger effect.)

**The port would take effect immediately:** `roc_10` is populated on **1,273 of 1,273** BUY
signals in the last 7 days — no fail-open gap.

---

## Finding 2 — HIGH — The breakout-extension guard is mathematically incapable of firing

**File:** `paper_trading_engine.py:2514` (derivation), `:2170-2177` (fallback guard),
`hard_rejects.py:570-579` (DE guard)

`_build_game_plan_for_style()` is called with `current_price = live_price` (`:5872`), so:

```
breakout = live_price * breakout_pct
ext_pct  = (live_price / breakout - 1) * 100 = (1 / breakout_pct - 1) * 100
```

**A constant with no market input.** Verified across all four styles at four price points:

| style | breakout_pct | ext_pct (any price) |
|---|---|---|
| SHORT | 1.010 | **−0.99%** |
| SWING | 1.020 | **−1.96%** |
| LONG | 1.030 | **−2.91%** |
| GROWTH | 1.035 | **−3.38%** |

Threshold is **+6%**. The maximum attainable value is **−0.99%** — the guard can never fire.

This is a reference-price-drift bug: the guard *intends* to compare price against a
**signal-calibrated** breakout level, but receives one recomputed from the live price at scan
time. A stock trading 20% above the level the signal was calibrated to is reported as "3.38%
below breakout" and admitted. Its config key is dutifully threaded to DE (`:3595`) carrying a
value that can never bind.

**Evidence:** zero `"above breakout"` rejections in 30 days of logs.

---

## Finding 3 — HIGH — The price-zone scoring layer is a constant +2; its −3 penalty is unreachable

**File:** `paper_trading_engine.py:2183-2194`

Same root cause. Since `breakout = live_price * breakout_pct` with `breakout_pct > 1.0`, the
first branch `entry2 <= live_price <= breakout` is **always true** (`entry2 = live_price ×
0.94–0.985` is always below; `breakout` always above). The remaining branches — including
`score -= 3` for *"extended above breakout — chasing risk"* — are **unreachable**.

This layer is meant to be the main entry-price-quality discriminator, swinging 5 points (+2 to
−3) against a `min_entry_score` of 4–6. Instead it hands **every** candidate a free +2
regardless of entry quality, systematically inflating scores toward the entry threshold.

**Evidence:** all 111 fallback-path trades took the "optimal entry zone" branch;
`deep_pullback`, `just above breakout` and `chasing risk` each recorded **0**.

---

## Answers to the domain's open questions

1. **Why 492 entries gapped >3%** — the gates measure the **wrong window**. T171 (signal-time
   close) and T196 (prior settled close) are both blind to a run-up that predates the signal.
   ANF is the worked example: +35.7% nine days before entry, measured gap +0.37%.
2. **Reference prices are otherwise coherent** — T196 correctly caps its reference bar at
   `date.today() - 1` (AUD-LIVEBAR-T196, `:5375`), excluding today's live bar. The only
   incoherent one is the breakout level (Findings 2/3).
3. **T+1 entry uses a live intraday quote**, not the next open/close (entries spread 10:00–14:41
   ET). `entry_price = live_price * (1 + slippage)`, so gate and fill see the same number.
4. **Prebreakout is NOT re-detecting momentum** — it gates on `detect_price_compression()` (BB
   width AND ATR both in the bottom 20% of a 126-day lookback, plus dried-up volume) and a ≥15%
   short-float floor. Structurally anti-momentum, genuinely different from the signal path.
   **But it is email-only — never wired into paper-trading entries.**
5. **Stop placement is clean** — `stop_distance = live_price - stop` with `stop` derived from
   `live_price`, so a gap-up widens the stop proportionally rather than compressing it.

### On the prebreakout 0% win rate — do not over-read it

26 alerts, 10 resolved, **0% win at 5d**. But they span only **5 symbols in a 7-day window**
(UPST ×4, QBTS ×2, AI ×2, POET, RGTI) — an effective independent sample of ~2-3, not 10. This
is the same clustering trap that produced two **retracted** findings in the 2026-09-05 cycle
("the system has defensive skill", "insider_score predicts returns"), both of which reversed
once the sample widened. 0% is discouraging; it is **not yet** evidence the pillar fails.

---

## CHECKED AND FOUND CLEAN

- Stop/sizing reference prices (`:4358`, `:4470`) — sized off entry price, no compression
- `_fetch_live_prices()` (`:1797`) — single batched download, $0.50 floor
- T196 drift-gate reference-bar capping (`:5375`) — correctly excludes today's live bar
- Signal-staleness gate (`:5729`) — drift-proof after AUD-SIGNALAGEMIRROR
- Market-hours / T185 time-of-day / macro-blackout / earnings-proximity gates — present and
  matched on both paths
- `min_volume_z` hard gate — live and firing (4 skips in 30 days)
- Compression detector (`price_compression.py`) — percentile and warmup logic correct
- DFNS `roc_10 = 995.6%` — real data (a genuine 10× melt-up), not a unit or falsy-zero artifact
- No falsy-zero defects in the entry-timing gates

**Latent risk, not filed as a finding:** DE enforces `max_signal_age_hours`, `min_volume_z` and
`max_price_drift_pct` from **hardcoded literals** rather than threaded config, and none appear
in `_ENTRY_GATE_KEYS` or `_DE_THREADED_KEYS`. They agree numerically today, so nothing
mis-fires — but this is the exact drift mechanism that silently disabled SWING's tighter
`max_entry_gap_pct` (Domain 1).

---

## The through-line

Domain 1: config that never arrived. Domain 2: work that never completed. Domain 3: a
self-correcting mechanism correcting the wrong way. **Domain 4: gates that exist, are
configured, are threaded — and cannot fire.** Two are self-referential arithmetic; one is on the
wrong code path. All three would pass a code review, and none shows up as an error.

**Not fixed — reported only, per the agreed protocol.**
