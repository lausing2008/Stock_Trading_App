# Deep Audit Series (2026-09-07): Decision-Making Engine — 1 of 6

**Domain:** `services/decision-engine/` (port 8009) + the entry-decision path that consumes it
(`paper_trading_engine.py`).

**Method:** per `docs/AUDIT_DOMAIN_SERIES_TEMPLATE.md` — grounded in live production data before
dispatch, subagent given verified facts plus explicit "already refuted, do not re-derive"
negatives, then **every consequential claim independently re-verified by me** before being
recorded here. That last step corrected one detail (see Finding 1).

**Result: 3 confirmed findings, all config-plumbing divergences.** The decision LOGIC itself
audited clean — no falsy-zero, no unit mismatch, no dead gate. What is broken is that
deliberately-tuned parameters never reach the code that enforces them.

---

## Finding 1 — HIGH — HK position-sizing reduction (T222-F) has never applied

**File:** `paper_trading_engine.py:4566` (merge), `:4572-4575` (HK override guard), values `:858-871`

```python
cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {}), **portfolio.config}   # :4566
if cfg.get("market") == "HK":
    for _k, _v in _HK_MARKET_OVERRIDES.items():
        if _k not in (portfolio.config or {}):     # ← this guard is the bug
            cfg[_k] = _v
```

The guard skips an HK override whenever the portfolio row *happens to carry that key* — even
when the stored value is just the generic US default the UI wrote out, not a deliberate choice.

`_HK_MARKET_OVERRIDES` sets `max_position_pct: 0.07` and `risk_per_trade_pct: 0.007`, added by
T222-F precisely because *"HK ATR is large, a 2× ATR stop equals a huge % move."* Both HK
portfolios store those keys at the US defaults `0.1` / `0.01`, so **the reduction has never
taken effect.**

**Independently verified by me** (not taken from the subagent):

```
name                  | stores_maxpos | maxpos_val | stores_risk | risk_val
HK GROWTH Portfolio   | t             | 0.1        | t           | 0.01     ← should be 0.07 / 0.007
HK SWING Portfolio    | t             | 0.1        | t           | 0.01     ← should be 0.07 / 0.007
```

Realized HK positions as % of initial capital — clustering at the **US 10% cap**, not HK's 7%:

| Symbol | Entry | % of capital |
|---|---|---|
| 0005.HK | 2026-07-16 | **12.69%** |
| 0939.HK | 2026-08-17 | **11.28%** |
| 0669.HK | 2026-08-07 | **10.06%** |
| 0981.HK | 2026-06-25 | 10.01% |

**Impact:** ~43% oversizing on both the position cap and the per-trade risk budget, on exactly
the market the override exists to de-risk. This is the only finding in this domain with
demonstrated realized impact.

**CORRECTION to the subagent's report:** it claimed the same guard also defeats
`trail_atr_mult: 1.5` and `min_ta_score: 0.65`. I checked — **`min_ta_score` is NOT stored on
either HK row**, so that override *does* apply correctly. Only `max_position_pct` and
`risk_per_trade_pct` are actually defeated. Recorded because the wrong version would send a
future fix chasing two parameters that are fine.

---

## Finding 2 — MEDIUM (latent) — `min_kscore` 48.0 overrides SWING's stricter 52.0

**File:** `paper_trading_engine.py:4566`; SWING value `:837`; gate `:5619`

Same merge, different mechanism: here `**portfolio.config` is simply last and wins outright.
`_STYLE_OVERRIDES["SWING"]` sets `min_kscore: 52.0`, but all SWING portfolios store `48.0`
(50.0 for ETrade Sandbox) — the generic default — so the SWING tightening never applies. The
loosened value is then forwarded to DE at `:3553`, so both gates use it consistently.

**Impact is latent, not realized.** Zero closed SWING trades have entered in the 48–52 kscore
band (ETrade 0/15, HK SWING 0/4, US SWING 0/40), so no measurable damage has occurred. A real
config-vs-intent divergence worth correcting — but it must **not** be characterized as having
caused losses.

---

## Finding 3 — MEDIUM — `max_entry_gap_pct` never reaches decision-engine

**File:** DE read at `hard_rejects.py:432`; caller omission in `paper_trading_engine.py:3477-3701`

The T171 gap-up filter is deliberately per-style — GROWTH `0.04`, **SWING `0.03`**, LONG `0.05`
(`:825/838/851`). `_call_decision_engine()` threads ~30 keys into `config_overrides` but
`max_entry_gap_pct` is **not among them** (verified: 0 occurrences in that block), so
`hard_rejects.py:432` falls through to its own hardcoded `cfg.get("max_entry_gap_pct", 0.04)`.

Because `decision_engine_mode` defaults to `"primary"`, **DE is the authoritative gate** — so
SWING's tightening is inert on the live path.

**Failure scenario:** a SWING BUY gaps up 3.5% above its signal-time close. The fallback gate
(`:2008`) would reject it as chasing; DE reads 0.04, passes it, trade opens. Given `stop_hit` is
the dominant exit (59 trades, avg −2.85%), entering further extended on a 3% stop is directly
adverse.

**Other omitted keys — checked, lower/no impact:**
- `max_daily_loss_pct` — passed as a top-level request field and merged at `routes.py:72-73`. **Not a bug.**
- `max_consecutive_losses` — HK GROWTH stores 7, DE defaults to 3 → DE is *stricter* than intended, fails safe.
- `max_sector_positions`, `equity_floor_pct`, `research_gating_enabled`, `max_breakout_extension_pct` — stored values currently match DE defaults, so no live divergence. Latent drift: a UI change would silently not reach DE.

---

## CHECKED AND FOUND CLEAN

Recorded so a future audit doesn't re-tread this ground:

- **All ~25 gates in `hard_rejects.py` traced individually.** Comparison directions correct,
  including the two easy-to-invert ones: `max_confidence_decline` correctly treats the threshold
  as negative (`:304`); `min_volume_z` correctly fails **open** on a missing value rather than
  defaulting to 0 (`:347-348`). No gate is shadowed by an earlier return.
- **Falsy-zero sweep: clean.** Previously-fixed instances have held — `sizer.py:127-131`,
  `scorer.py:263`, `routes.py:224-225` all use explicit `is not None`. **No new instances.**
- **Unit consistency verified on both sides of every scale-sensitive comparison.** No
  fraction/percent mismatch found. `weekly_net_pnl_pct` looks asymmetric but is correct.
- **DE-outage fallback path correct** — returns `None` on any exception/non-200, caller falls
  back with `gate_source="fallback"`, never raises into the scan loop. (A stale 4-tuple type
  annotation at `:3449` is cosmetic only.)
- **Calibrated logistic-regression entry path (PT-3) is dormant** — `/data/models/entry_weights.json`
  does not exist in production, so both DE's mirror and `_should_enter()` fail safe to the
  additive threshold. Formulas verified term-for-term identical.
- **`min_entry_score` is correctly scaled.** Re-derived independently: `compute_score()` layers
  sum to roughly −8..+12, so `min_entry_score: 4-6` is sane. This confirms the pre-dispatch
  grounding and re-refutes the prior series' unit-confusion conclusion.
- **`aget_regime()`** has no stale-closure bug; `_NEUTRAL` fallback correctly defaults to the
  conservative `"choppy"`.

---

## The through-line

All three findings are the **same class**: a deliberately-tuned parameter that never reaches the
code enforcing it, because of config-merge precedence or a missing key in the DE request body.
None is a logic error. That suggests the highest-value fix is not three patches but **a
systematic guard** — e.g. asserting that every key in `_ENTRY_GATE_KEYS` / `_HK_MARKET_OVERRIDES`
actually arrives at DE, so the next added parameter cannot silently fail to plumb through.

**Not fixed — reported only, per the agreed audit protocol.**
