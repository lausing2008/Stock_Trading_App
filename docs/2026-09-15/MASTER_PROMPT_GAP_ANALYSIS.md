# Gap Analysis — "AI Market Intelligence & Adaptive Trading System" Master Prompt

**Reviewed:** 2026-09-15
**Document:** `Improvements/Claude Code Master Prompt — AI Market Intelligence & Adaptive Trading System.md` (2,889 lines, 44 sections)
**User's ask:** *"I would like our system have this capability to detect and act on the changes. Let's verify what we are missing... Please help me to create an accurate trading system which can help me to gain money seriously."*

---

## The headline finding, before any gap list

**Every claim below is measured against production, not inferred from the document.**

The platform is **far more built than the document assumes**. Of 44 sections, roughly **38 already
exist in some form** — 182 Python files across 12 services, 73 DB tables, 77 scheduled jobs, and
**15 alert detectors already running**.

**But the system is currently losing money on paper trades:**

| | closed | win % | avg win | avg loss | total P&L | **profit factor** |
|---|---|---|---|---|---|---|
| **All paper trading** | 119 | 31.9% | +$407 | −$287 | **−$7,786** | **0.67** |

By style:

| style | n | win % | P&L |
|---|---|---|---|
| GROWTH | 58 | 36.2% | **+1,038** |
| SWING | 61 | 27.9% | **−8,824** |

By portfolio:

| portfolio | closed | win % | P&L | PF |
|---|---|---|---|---|
| HK GROWTH | 15 | 46.7% | **+2,315** | **1.36** |
| US SWING | 42 | 38.1% | −702 | 0.83 |
| GROWTH Paper | 43 | 32.6% | −1,277 | 0.71 |
| ETrade Sandbox | 15 | 6.7% | −1,512 | 0.08 |
| HK SWING | 4 | 0% | **−6,611** | — |

And every alert family sits **at or below a coin flip**:

| family | n | resolved | hit rate |
|---|---|---|---|
| signal (5d) | 16,916 | 16,916 | **40.1%** |
| dark pool (1d) | 447 | 372 | **44.9%** |
| options flow (1d) | 1,649 | 1,621 | **39.5%** |
| squeeze (1d) | 381 | 361 | **40.2%** |
| prebreakout (1d) | 33 | 32 | 43.8% |

### What this means for the document

The master prompt proposes **~20 new engines** layered on top of these signals. **Adding
intelligence on top of inputs that are below chance will not produce profit — it will produce
more confident losses.**

A 0.67 profit factor with a 31.9% win rate is not a "needs more features" problem. The average
win (+$407) is 1.42× the average loss (−$287), which means **the edge is in the exit/sizing, and
the entry rate is too low to overcome it**. That is a calibration problem, not a coverage problem.

**The single highest-value action in this entire document is not in this document:** find out why
SWING loses money and GROWTH does not.

---

## Section-by-section: what exists, what doesn't

### ✅ Already built (verified in code)

| § | Engine | Where |
|---|---|---|
| 5 | Market regime | `scheduler.py`, `macro_reaction.py`, `rl_agent.py` |
| 6 | Sector intelligence | `sector_trajectory.py`, `brinson_attribution.py` |
| 10 | Fair value | `research-engine/scoring.py` |
| 11 | Uncertainty | confidence/calibration paths in scheduler + email |
| 13 | Options intelligence | `options_flow_snapshot.py`, `compute_options_pressure_score()` |
| 14 | GEX / dealer hedging | `gex_snapshot.py`, `institutional.py` |
| 15 | Options expiration | `max_pain`, `unusual_whales.py` |
| 16 | Short squeeze | `risk_agent.py`, `scorer.py`, squeeze alerts |
| 17 | Margin / liquidation | `paper_portfolio.py` |
| 19 | Multi-horizon prediction | horizon-scoped signals + outcomes |
| 20 | Regime-aware ML | regime features in the model fleet |
| 21 | Feature groups | `FEATURE_GROUP` in ml-prediction |
| 22 | Feature ablation | ablation harness exists |
| 23 | AI trade decision | decision-engine (`hard_rejects.py`, `scorer.py`) |
| 26 | Passive income | covered call / cash-secured put paths |
| 27 | Capital protection | circuit breakers, drawdown halts |
| 29 | Dynamic position sizing | `risk_per_trade_pct`, ATR sizing |
| 30 | Thesis | thesis fields on signals/trades |
| 33 | Alert prioritization | priority scoring in alerting |
| 35 | AI challenges own trade | `risk_agent.py` (T258 what-could-go-wrong) |
| 37 | Trade journal / learning | outcome tables + learning loop |
| 38 | Model calibration | calibration persisted + fed back |
| 39 | Walk-forward | `trainer.py` true walk-forward |
| 41 | Model promotion gate | promotion criteria in ml-prediction |

### ❌ Genuinely missing

| § | Engine | Assessment |
|---|---|---|
| **4** | **EventChangeDetectionEngine** | **The real gap — see below** |
| 9 | Quantitative moat | Not built. Fundamental scoring exists; moat scoring does not |
| 32 | Signal state machine | No lifecycle state on signals (NEW → WATCHING → ACTIVE → INVALIDATED) |
| 42 | Data source value measurement | No per-source contribution tracking |

### ⚠️ Partially built

| § | Engine | What's missing |
|---|---|---|
| 40 | Realistic backtesting | Engine exists; **no stop-loss/take-profit, no position sizing** (see the separate backtest audit) |
| 3 | Point-in-time correctness | Some paths respect it; not systematically enforced |

---

## §4 — the one that matters, and it's smaller than it looks

The document asks for a central `EventChangeDetectionEngine` emitting typed events with
`importance / direction / magnitude / confidence / novelty / persistence`.

**This is not a greenfield build. 15 detectors already run on schedule:**

```
check_dark_pool_alerts          check_options_flow_alerts      check_short_squeeze_alerts
check_early_earnings_news_alerts check_portfolio_drawdown_alerts check_signal_alerts
check_earnings_beat_screener_alerts check_prebreakout_alerts    check_squeeze_ignition_alerts
check_earnings_impact_alerts    check_price_alerts             check_technical_alerts
check_gamma_unwind_alerts       check_sector_rotation_alerts    check_macro_reaction_alerts
```

**What's missing is the unification**, not the detection. Today there are **11+ separate
alert/outcome tables**, each with its own schema. There is no single stream where a consumer can
ask *"what changed for AAPL today, across all sources, ranked by significance?"*

**That is a real and useful gap** — it is the difference between 15 independent alert emitters and
one intelligence layer. But it is **1 table + 1 adapter layer**, not 20 engines.

---

## My honest assessment of the document

**What it gets right:**
- The objective function is correct: *"Do NOT optimize primarily for win rate"*, optimize profit
  factor and risk-adjusted return. Given the measured 31.9% win rate with a 1.42 win/loss ratio,
  this is exactly the right frame.
- §4's change-detection framing is genuinely the missing architectural piece.
- §22 (feature ablation) and §42 (data source value) are the right instincts — *measure whether a
  data source earns its place*.
- It explicitly says **"Do NOT promise perfect prediction."** Good.

**Where I'd push back:**

1. **It is a specification, not a plan.** 44 sections with no dependency order and no
   measurement gate. Building §5–§31 before fixing the 0.67 profit factor adds surface area to
   something that currently loses money.

2. **It assumes the platform is a "static stock screener."** It isn't — ~38 of 44 sections
   already exist. Following it literally would mean rebuilding working systems.

3. **No section says "first, find out why the current system loses money."** That is the actual
   problem. SWING is −$8,824 over 61 trades; GROWTH is +$1,038 over 58. **The same engine, two
   styles, opposite outcomes** — that difference is worth more than any new engine.

4. **More signals ≠ more profit when the signals are below chance.** Every family measures
   39–45%. The composite-scoring approach in the dark-pool document was already deferred for
   exactly this reason.

---

## Recommended phasing

### Phase 0 — Diagnose before building (highest value, ~1 week)

**Nothing in the master prompt. This is the prerequisite.**

1. **Why does SWING lose and GROWTH win?** 61 vs 58 trades, −$8,824 vs +$1,038. Same platform,
   opposite results. Candidate causes: stop placement (HK SWING stopped out 4/4, two on day 0),
   entry timing, holding period, or style-specific gate calibration.
2. **Why did HK SWING lose $6,611 on 4 trades?** All four were stop-outs at −5.5% to −5.7%, two
   on day 0. Either the stops are too tight for HK volatility, or entries are chasing.
3. **Fix `realized_pnl` vs `pnl`.** Two columns, one populated (`pnl`), one mostly zero
   (`realized_pnl`, broker-fill only). A P&L query against the wrong one reports **zero losses in
   126 trades** — I made exactly that error during this review before catching it.

**Gate:** do not build new engines until SWING's loss is explained.

### Phase 1 — §4 Event & Change Detection (the real gap, ~2 weeks)

One `market_events` table + adapters from the 15 existing detectors. Typed fields per the doc
(`importance`, `direction`, `magnitude`, `confidence`, `novelty`, `persistence`). **Value: a
single "what changed" stream, and per-source hit-rate measurement for free.**

This also delivers **§42 (data source value)** as a by-product — once events share a schema,
"which source actually predicts anything" becomes a query.

### Phase 2 — §32 Signal state machine (~1 week)

Signals have no lifecycle. Adding NEW → WATCHING → ACTIVE → INVALIDATED → EXPIRED makes
"react when conditions change" (objective #10) possible — currently a signal is a point-in-time
row with no notion of invalidation.

### Phase 3 — Close the backtest gaps (~1 week)

Stop-loss/take-profit and position sizing in the strategy backtester (from the separate audit).
**Without these the backtester cannot model the exact thing Phase 0 is investigating.**

### Phase 4 — §9 Quantitative moat, and only then the rest

Genuinely missing, genuinely useful for LONG-horizon selection — but it is a *selection quality*
improvement, and selection quality is not currently the binding constraint.

### Deferred indefinitely

§5–§8, §10–§31 as *new* work: already built. Improve them where measurement says they are weak,
not because a document lists them.

---

## On "help me gain money seriously"

The most useful thing I can say:

**The platform's problem is not missing features. It is that the features it has are at 40%
accuracy and the paper account is down $7,786 with a 0.67 profit factor.**

Building 20 more engines on that foundation will produce a more sophisticated system that loses
money more confidently. The document's own objective function agrees — it says optimize profit
factor, and profit factor is currently **0.67**, where **1.0 is break-even**.

**The fastest path to a profitable system is Phase 0: find the difference between SWING (−$8,824)
and GROWTH (+$1,038), and apply it.** That is a measurement task, not a construction task, and it
is worth more than §5 through §31 combined.

---

# PHASE 0 EXECUTED — why SWING loses and GROWTH wins

Run 2026-09-15, since this is the prerequisite for everything else.

## The answer is the protective-exit ladder, not the stop distance

**The obvious hypothesis is wrong.** SWING does not lose because its stops are too wide — its
stops are *tighter*:

| | planned stop distance | avg target | avg R:R |
|---|---|---|---|
| GROWTH | **11.37%** | 34.39% | 3.48 |
| SWING | **5.31%** | 11.81% | 2.43 |

Yet SWING loses **2.4× more per stop-out**:

| | stop_hit n | realised avg | P&L from stops |
|---|---|---|---|
| GROWTH | 27 | **−1.60%** | −3,933 |
| SWING | 32 | **−3.90%** | **−11,539** |

**GROWTH has wider stops and smaller realised losses.** That is only possible if GROWTH exits
*before* the hard stop is reached.

## The mechanism

| | max favourable excursion | protective exits (breakeven + trailing) | raw stop-outs |
|---|---|---|---|
| **GROWTH** | **+8.00%** | **51.7%** (30/58) | 46.6% |
| **SWING** | **+4.26%** | **26.2%** (16/61) | 52.5% |

**GROWTH trades go up ~2× as far before turning.** That gives the breakeven stop time to arm,
converting what would have been a full stop-loss into a ~flat exit:

- GROWTH `breakeven_stop`: 21 trades, **−0.47%** avg → +285 P&L
- GROWTH `trailing_stop`: 9 trades, **+4.73%** avg → **+4,872 P&L**
- SWING `breakeven_stop`: 13 trades, −0.02% avg → +19 P&L
- SWING `trailing_stop`: 3 trades, +5.34% avg → +802 P&L

**GROWTH's entire profit comes from 9 trailing-stop exits (+$4,872).** SWING gets only 3.

## My first hypothesis was WRONG — and the real answer is sharper

I initially concluded *"SWING's trail trigger is unreachable, lower it."* **Checking the config
refuted that before it was written into a recommendation:**

| | breakeven trigger | trail trigger | stop distance |
|---|---|---|---|
| GROWTH | +4.0% | +7.0% | 11.37% |
| **SWING** | **+1.5%** | **+3.0%** | **5.31%** |

**SWING already has far tighter triggers than GROWTH.** Its breakeven arms at +1.5% against a
+4.26% average favourable excursion — that is well within reach. Lowering it further would have
been the wrong fix, aimed at a mechanism that is already tuned.

## The actual mechanism: SWING's stop sits inside its own noise

The numbers only reconcile one way:

- SWING stop distance: **5.31%**
- SWING average favourable excursion: **+4.26%**
- SWING realised loss per stop-out: **−3.90%**

**The stop (5.31%) and the typical upside (4.26%) are the same order of magnitude.** A SWING
trade has roughly as much room to lose as it typically gains, so a normal adverse wiggle reaches
the stop before the trade resolves. 52.5% of SWING trades end in a raw stop-out.

GROWTH avoids this not by better entries but by **asymmetry**: an 11.37% stop against +8.00%
typical excursion, giving the trade room to breathe, plus a 3.48 average R:R versus SWING's 2.43.

**So the binding constraint is the stop-to-volatility ratio, not the trigger ladder.** SWING's
stop is too tight *relative to the instrument's own movement* — the opposite of the usual
"stops too wide" diagnosis, and the opposite of my first hypothesis.

## What to do about it — as a hypothesis, not a conclusion

Two candidate directions, and they are genuinely opposed:

**(A) Widen SWING's stop** toward its ATR, so a normal wiggle does not stop the trade out. Risk:
the realised loss per stop-out grows from −3.90%, so this only wins if the stop-out *rate* falls
faster than the loss size grows.

**(B) Raise SWING's entry bar** so it only takes trades with more expected room. SWING already
has `min_confidence 50 / min_kscore 52` vs GROWTH's 45/48 — it is *already* stricter and still
loses, which weakens this option.

**I would test (A) first**, because the arithmetic points at it: 32 stop-outs × −3.90% is the
single largest loss bucket in the entire account (−$11,539 of the −$7,786 total).

**This must be backtested before it is believed.** And that is the concrete link to Phase 3:
**the strategy backtester has no stop-loss support at all**, so today the harness *cannot model
this change*. Fixing that is the prerequisite for testing the single most valuable hypothesis
this review produced.

## Honest caveat on sample size

119 closed trades, 61 SWING / 58 GROWTH. That is enough to see a −$11,539 loss bucket clearly,
but **not** enough to conclude a style is broken — this platform has retracted findings at n=42
before. Treat the mechanism as well-evidenced and the *fix* as unproven.
