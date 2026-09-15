# The Path to a Profitable System

**Written 2026-09-15**, in response to: *"I would like to proactively analyse all the movements
like news, market, stocks, options, margins to predict the trend and buy low and sell high or use
options to earn passive money or protect my capital... Just trade like an expert. React and take
actions for the changes, it has to be very accurate."*

---

## Your vision is already ~90% built. That is not the problem.

The platform already does, today, on schedule:

| you asked for | exists |
|---|---|
| analyse news | news-intelligence service, hot-news gate |
| analyse market | market regime, breadth, VIX, fear/greed |
| analyse stocks | 16,916 scored signals, K-Score ranking |
| analyse options | options flow, GEX, max pain, IV rank, 29-symbol chain archive |
| analyse margins | margin/liquidation risk in paper_portfolio |
| react to changes | **15 alert detectors**, 77 scheduled jobs |
| options for income/protection | Options Game Plan (covered call + protective put) |
| take action | paper trading engine with stops, targets, trailing, circuit breakers |

**Adding a 16th detector will not make it profitable.** Here is what will.

---

## THE blocker, measured on 16,916 resolved signals

**The confidence score does not discriminate.**

| confidence bucket | n | 5-day hit rate | avg 5d return |
|---|---|---|---|
| <50 | 10,738 | **40.1%** | −0.005 |
| 50–59 | 2,630 | **38.9%** | −0.007 |
| 60–69 | 1,620 | **40.8%** | −0.007 |
| **70+** | 1,928 | **41.0%** | **−0.012** |

**A 2.1 percentage-point spread across the entire confidence scale, and every bucket is
negative.** A signal the system is 70% confident in performs the same as one it is 45% confident
in — and slightly *worse* on return.

This reproduces in live paper trading:

| confidence at entry | n | win % | P&L |
|---|---|---|---|
| 50–59 | 24 | 41.7% | **+2,458** |
| 60–69 | 24 | 29.2% | −3,430 |
| **70+** | 35 | 34.3% | **−5,591** |

**The highest-confidence trades lost the most money.**

### This is a known, diagnosed, already-half-fixed problem

`docs/audits/2026-09-02-six-part-platform-audit-1-ai-signal.md` found it two weeks ago:

> `confidence = round(abs(fused - 0.5) * 200, 2)` — a pure, deterministic restatement of the
> input probability, **never touching real historical accuracy.**

The calibration machinery that would fix it **already exists and already runs**
(`_build_confidence_calibration`) — but its output is used only as a *display annotation*, never
fed back into the stored confidence.

**The fix was deferred** because calibrating needs trustworthy ground truth, and the ground-truth
table was recording the wrong moment (a signal that was BUY at 10am and HOLD by 4pm was scored on
its 4pm state). **That bug was fixed in the same audit.**

### So why isn't it fixed already? Data.

| | count |
|---|---|
| signals with clean frozen entry state | 2,632 (since 2026-09-03) |
| **resolved outcomes with clean ground truth** | **184** |

**184 is not enough to calibrate a confidence model.** At the current resolution rate this
reaches a usable sample (~1,000+) in **roughly 3 weeks — early-to-mid October.**

That is the single highest-value fix available, and it is **blocked on time, not on work.**

---

## On "it has to be very accurate" — the most important thing I can tell you

**Accuracy is the wrong target, and chasing it will lose you money.**

Your own data proves it:

| | win rate | avg win | avg loss | result |
|---|---|---|---|---|
| Current system | 31.9% | +$407 | −$287 | **−$7,786** |

The win rate is 31.9% and the payoff ratio is **1.42:1**. For that to be profitable you need a
win rate above **41.3%** (`1 / (1 + 1.42)`). You are **9.4 points short** — not 40 points short.

**A 45% win rate with a 2:1 payoff makes money. An 80% win rate with a 0.2:1 payoff loses it.**
The master prompt says this explicitly, and it is right:

> Do NOT optimize primarily for win rate. A strategy with 58% win rate, 2.1 Profit Factor and 10%
> max drawdown may be superior to one with 72% win rate, 1.2 Profit Factor and 30% drawdown.

**Your gap to profitability is ~9 percentage points of win rate, OR a better payoff ratio.** Both
are reachable. "Very accurate" — in the sense of usually being right about direction — is not,
by anyone, and a system that claims it is lying.

---

## The three levers, ranked by measured value

### Lever 1 — SWING's stop sits inside its own noise (largest single loss bucket)

| | stop distance | avg favourable excursion | realised loss/stop | raw stop-out rate |
|---|---|---|---|---|
| GROWTH | 11.37% | +8.00% | −1.60% | 46.6% |
| **SWING** | **5.31%** | **+4.26%** | **−3.90%** | **52.5%** |

**32 SWING stop-outs × −3.90% = −$11,539** — larger than the entire account loss.

SWING's stop and its typical upside are the same magnitude, so normal noise stops it out before
the thesis resolves. GROWTH survives on asymmetry, not better entries.

**Blocked on:** the strategy backtester has **no stop-loss support**, so this cannot be tested
today. That makes the backtester gap the real prerequisite.

### Lever 2 — Options income, the one path that does NOT need directional accuracy

You asked for *"options to earn passive money or protect my capital."* **This is the strongest
idea in your message**, precisely because covered calls and cash-secured puts earn premium
regardless of whether the directional call is right.

With a 40% directional hit rate, a strategy that **does not depend on direction** is structurally
better suited to this platform than one that does.

The Options Game Plan already computes `covered_call` and `protective_put` legs. What is missing
is the **systematic income engine** (§26) that runs it as a strategy with position management and
an outcome scoreboard.

**This is the most promising build in the entire master prompt**, and it is nearly free — the
option chain archive (29 symbols, 724 days each) was just backfilled for exactly this kind of
work.

### Lever 3 — Confidence calibration (highest value, unblocks in ~3 weeks)

Feed `_build_confidence_calibration` back into the stored confidence. **Blocked until ~October
on clean resolved outcomes.** When it lands, every downstream gate that filters on confidence
starts filtering on something real.

---

## Recommended sequence

| phase | work | why now | blocked? |
|---|---|---|---|
| **1** | Backtester stop-loss / take-profit / sizing | Prerequisite for testing Lever 1 | no — buildable today |
| **2** | Test + fix SWING stop width | Largest loss bucket (−$11,539) | needs Phase 1 |
| **3** | Options income engine (§26) | Only lever independent of direction | no — buildable today |
| **4** | Confidence calibration feedback | Highest value, fixes the root cause | **~3 weeks of data** |
| **5** | §4 Event unification | Consolidates 15 detectors, enables per-source measurement | no |
| 6+ | §9 moat, §32 state machine, §42 source value | Real gaps, not binding constraints | no |

**Everything else in the 44-section master prompt is already built, or is not the reason the
system loses money.**

---

## What I will not promise

- That any of this makes the system "very accurate." It will not. Nothing is.
- That calibration fixes profitability on its own. It removes a known defect; the result must
  then be measured.
- That the SWING hypothesis is correct. It has a clear mechanism and a large loss bucket behind
  it, but n=61 and this repo has retracted findings at n=42.

**What I will promise:** every number in this document was measured against production, and
anything I could not measure is labelled as unmeasured.
