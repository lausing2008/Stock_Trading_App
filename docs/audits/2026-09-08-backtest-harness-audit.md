# Deep Audit (2026-09-08): Backtest Harness — 4 findings fixed, 1 retraction (tier 367)

The last genuinely un-audited domain. It matters because every threshold and weight this
platform tunes is validated against this harness — if it lies, everything downstream inherits it.

**Read this before touching `services/market-data/src/backtest/` or quoting any backtest number.**

---

## The headline is counterintuitive: the harness does NOT overstate performance

It **understates** it, by ~5.7pp on GROWTH — and its lookahead discipline is genuinely the
best-built part of this codebase. What was broken is the **configuration plumbing** around a
sound core.

That distinction matters for how you read old results: the risk was never inflated confidence.
It was that HK had been un-tunable for months without anyone knowing, the only risk-side
promotion check was inert, and two published conclusions were wrong.

---

## The through-line — one failure wearing three hats

`AUD-BT-HKCFGDEFAULT`, `AUD-BT-HKLUNCHBREAK` and `AUD-BT-ALERTHORIZON` are the same defect:

> **A required input silently defaulting to a plausible wrong value.**

```
cfg["market"]           -> "US"       (should have been "HK")
signal_data["horizon"]  -> "SWING"    (should have been the replayed style)
```

In every case the **live** caller supplies the value explicitly and the **harness** caller forgot
— and because the default was itself *valid*, nothing raised, nothing logged, and the output
looked entirely reasonable. This is the platform's signature failure mode, now seen in a fourth
domain.

---

## Findings

### AUD-BT-HKCFGDEFAULT — HIGH — HK had been un-tunable for months

Nine call sites built their replay config as:

```python
base_cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}
```

and passed `market` as a **separate argument that never reached the dict**. Since
`_DEFAULT_CONFIG["market"] = "US"` and `_STYLE_OVERRIDES` carries no `market` key, every HK
replay ran as US. Consequences, all silent:

- `_is_market_hours("US", as_of=<HK midday>)` — HK midday is **00:00 ET**, so the *first* hard
  reject fired on 100% of HK candidates.
- `_default_min_rr_ratio(..., "US")` bypassed the per-market R:R floor that
  `AUD-MINRR-MARKETBLIND` exists to enforce.
- The time-of-day gate resolved in the wrong timezone.
- `_HK_MARKET_OVERRIDES` (min_entry_score 6, min_confidence 65, min_ta_score 0.65) were skipped
  by **8 of the 9** sites.

**Production evidence:** every HK row in `tune_history` carried NULL train/validation/n with
`gate_failures: ["harness_skipped:..."]`, across months of Sunday runs — despite HK having
**418–752 resolved BUY outcomes per style**. Indistinguishable in the DB from "HK has no data."

**Fixed** with `resolve_backtest_config(style, market)`, which delegates to the live
`resolve_entry_config()` precedence (user choice > HK override > style override > default) rather
than being a fourth hand-rolled merge that can drift again.

### AUD-BT-HKLUNCHBREAK — HIGH — every HK replay landed in the lunch break

`_entry_as_of` built `12:00` local for both markets. But HKEX has a **split session**, and
`_is_market_hours` tests:

```
(09:30 <= t < 12:00)  or  (13:00 <= t < 16:00)
```

`12:00` HKT is the **exclusive** end of the morning session and the afternoon has not opened — so
it fell in **neither** window. Verified live: `_is_market_hours("HK", as_of=<12:00 HKT>)` →
`False`.

**This is independent of the config bug.** Fixing either one alone still yields zero HK entries,
which is exactly why both had to be found. HK now uses `11:00` HKT; US keeps `12:00` ET.

The docstring claimed the instant was "comfortably clear of both the market-hours boundary" —
true for US, **false for HK**, and that false claim is why nobody re-checked.

### AUD-BT-WORSTTRADESCALE — HIGH — the only risk-side promotion check was inert

`GateReplayResult.returns` is populated by `returns.append(float(pct_return))` from
`SignalOutcome.return_10d` — a **fraction** (`-0.5456` = −54.56%). The tolerance is
`DEFAULT_MAX_WORST_TRADE_REGRESSION_PCT = 10.0` **percentage points**. A difference of two
fractions is bounded by ~1.0 in practice, so:

```
regression <= 10.0     # UNCONDITIONALLY TRUE
```

Rule #3 — the only risk-side check the promotion gate has, with rule #4 already recorded as
`not_yet_available` — never fired and **could not**. Every promotion rested on EV lift alone.

**Production evidence, one row holding both scales** (`tune_history`, 2026-09-06):

```
train_ev_pct           =  0.7961    <- a real percent
approx_worst_trade_pct = -0.5456    <- a fraction, i.e. -54.56%
```

Observed worst-trade values were `-0.5456 / -0.4366 / -0.1742 / -0.1314 / -0.0091 / -0.0017`, so
the largest achievable |regression| was **~0.7 against a 10.0 tolerance**.

The constant's own comment asserted *"these are already pct returns"* — the false premise that
made it invisible. Meanwhile the **sibling** `_passes_promotion_margin` in `gate_harness.py`
gets it right, with an explicit `* 100  # returns are stored as fractions`. Two functions in one
promotion path disagreed about the scale of the same list.

Fixed at the comparison **and** at persistence, so `tune_history` stops storing 100×-too-small
values under a `*_pct` column name.

### AUD-BT-ALERTHORIZON — HIGH — BT-4 replayed every style as SWING

`replay_alert_gate` built `signal_data` with `signal`, `confidence`, `bullish_probability`,
`reasons` — **no `horizon`**. `_is_conviction_buy` reads
`style = signal_data.get("horizon", "SWING")`, so every style replayed under SWING's rules.
GROWTH lost both exemptions: layer 4a (GROWTH needs only `trend_above_sma50`; SWING requires
`sma50_above_sma200 AND trend_above_sma50`) and layer 4b (GROWTH's RSI band 50–85 vs SWING's
45–72).

**The tell was already in the published output** and nobody noticed: §8's top rejection reason,
*"Uptrend structure not aligned (SMA50/SMA200/price)"*, is the **non-GROWTH branch's** message.

---

## AUD-BT-HOLDMODELGAP — the retraction (not a code bug)

The harness scores every entry with `SignalOutcome.return_{5d,10d,20d}` — a **fixed hold to a
calendar horizon, exiting at close**: no stop, no trailing stop, no partial take-profit, zero
slippage. The live engine exits on stops/targets/trailing at **~6.8 days** average with 10bps
each way.

Measured against real closed paper trades on the **same signals**:

| style | n | real avg | harness `return_10d` | gap |
|---|---|---|---|---|
| GROWTH | 50 | **+0.26%** | **−5.45%** | **5.70pp** |
| SWING | 41 | +0.17% | −0.10% | 0.28pp |

By exit reason — this localises the entire divergence:

| exit_reason | n | real | harness | hold |
|---|---|---|---|---|
| `breakeven_stop` | 30 | −0.30% | **−5.99%** | 4.7d |
| `target_reached` | 7 | **+12.22%** | −1.28% | 5.7d |
| `stop_hit` | 42 | −2.30% | −2.52% | 6.1d |
| `trailing_stop` | 9 | +4.70% | +2.95% | 16.3d |

The harness **holds through drawdowns the engine exits**, and **holds past targets the engine
banks**.

**What this retracts.** The 2026-09-06 scoping doc's headline BT-1 conclusion — *"GROWTH's
replayed edge is negative… evidence that today's GROWTH gates admit a losing population"* — is
**not supported**. The same signals returned **positive** under real exit logic. The defensible
version is narrower: *under a fixed 10-day hold with no risk management, GROWTH's entries are
negative* — a statement about the hold model, not the gates.

**Why it also matters for tuning.** 5.7pp is **11× `_MIN_PROMOTION_EV_LIFT_PCT` (0.5)**, and the
bias is **non-uniform** — it varies with the exit-reason mix, which the gate config under test
itself changes. So the harness is not merely pessimistic-but-consistent.

> **Rule going forward:** treat `avg_return_pct` as a **relative ranking signal between
> candidates**, never as an estimate of realised performance. Do not let it drive automated
> promotion without simulating exits.

The limitation *was* disclosed in `portfolio_backtest.py`'s docstring. Its **magnitude** was
documented nowhere — which is how the conclusion got written.

---

## Checked and found CLEAN — do not re-derive these

**Lookahead: the core replay is honest.** The strongest part of the domain.

- `_historical_atr` — `Price.ts < as_of`, strictly before (conservatively one day *early*).
- `_historical_kscore` — `Ranking.as_of <= signal_date`, deliberately **not** the live engine's
  unbounded `func.max(Ranking.as_of)`. The comment explicitly names the leak it avoids.
- `_historical_confidence_delta` — strict `Signal.ts < signal_date`.
- `sig.reasons` is the last-of-day snapshot; the T+1 entry model makes it legitimately known at
  decision time. **Looks like lookahead, is not.**
- `entry_price` = **T+1 close**, with `_OUTCOME_CENSOR_GRACE_DAYS` bounding gap resumption.
- **No `.iloc[-1]` live-bar defect anywhere in the backtest tree** — that documented class did
  not propagate here.
- `strategy-engine/src/backtest/engine.py` — 1-bar entry lag, `position.shift(1)`, fees +
  slippage both directions. The best-built engine in the codebase.

**Wall-clock: BUG233's fix held.** `as_of` threads correctly through `_is_market_hours` and all
three reads inside `_should_enter`. No sibling path reintroduces a bare `datetime.now()` for a
simulated decision. The two HK findings are *configuration* failures reaching the same symptom —
not a regression of that fix.

**Survivorship: structurally correct.** `_fetch_matched_signals` applies **no** `delisted` filter,
so delisted names stay in the replay universe — the right default. Prod now has 1 delisted stock
(SKHYV, up from 0 — the `AUD-ING7` fix is working) with 0 outcome rows, so no present effect.

**Promotion margins are conservative, not permissive.** `_passes_promotion_margin` converts
fractions correctly. `_passes_return_promotion_margin` compares a portfolio-level lift against
per-trade SD — checked whether that was a category error, and it errs **strict** (measured
per-trade SD 9.22pp on n=2,654, so it demands ≥4.6pp lift).

**Also clean:** `_resolvable_window_end`'s per-style lag before splitting; the drawdown-breaker
and open-risk caps are genuine in-loop state-dependent gates, not post-hoc filters; `ev_gate.py`
scales correctly; `_VAR_EPS`/`_VOL_EPS` float-noise guards intact. **A fail-open / falsy-zero
grep sweep across all 8 backtest modules returned zero hits.**

---

## Two items recorded, deliberately NOT fixed

**Research-summary lookahead in `_should_enter`'s replay — latent.** `_should_enter` makes a
**live HTTP call** to `/research/{symbol}/summary` (±2 entry score). That endpoint returns only
the most-recent TTL-fresh report with no `as_of`, so replaying a June decision would read
*today's* research. **Currently inert:** all 68 rows in `research_report_cache` are dated
2026-07-29 and past TTL, so it 404s and contributes 0. A point-in-time alternative already
exists (`SignalOutcome.research_rec`, populated on 3,958 outcomes), and the Phase 2c path already
passes `research_rec: None` with the right comment. **If research generation resumes, this
becomes live lookahead.**

**Win-rate definition drift and optimistic barrier fills — MEDIUM, display-only / shadow-only.**
`walk_forward_scorer_sweep` and `portfolio_backtest` use `r > 0` while every other path requires
clearing `_OUTCOME_WIN_HURDLE_PCT = 0.005` — a systematic **3.1–3.3pp** inflation (SWING/US
44.80% vs 41.48%; GROWTH/US 44.99% vs 41.66%). Promotion uses `avg_return_pct`, so this is
display-only. Separately `multi_tranche_engine` checks `bar_high >= upper` **before**
`bar_low <= lower`, so a bar touching both barriers always scores a WIN, and exits fill *at* the
barrier — correct for a limit target, wrong for a stop that gaps through. That feeds
`position_scaling_gate`, which is **shadow-mode only** (logs a verdict, never acts).
