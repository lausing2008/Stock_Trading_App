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

---

# UPDATE 2026-09-15 — Lever 1 is NOT testable retrospectively. Two attempts, one root cause.

I chose Lever 1 (the −$11,539 SWING stop bucket) and tried to test it. **Both attempts failed
the same validity control, and the reason turns out to be architectural.**

## Attempt 1 — a hand-written daily-bar simulation

Control (replay each trade with its OWN stop; must reproduce the actual result):

| | actual | simulated | error |
|---|---|---|---|
| SWING | −25.1 pp | +2.8 pp | 28 pp |
| GROWTH | −15.0 pp | +40.7 pp | 56 pp |

**Cause:** the real engine has **10 exit paths** and takes **partial scale-outs**; the
simulation had 4 paths and full exits. A re-implementation drifts from the real logic.

## Attempt 2 — replay the REAL `_monitor_positions()`

Built `services/market-data/src/backtest/exit_harness.py` to re-implement nothing and drive the
actual production function with historical prices. **It failed worse:**

| | actual | replay | error |
|---|---|---|---|
| SWING | −25.1 pp | **−197.4 pp** | **172.3 pp** |
| GROWTH | −15.0 pp | **−216.7 pp** | **201.7 pp** |

Exit-reason agreement: **13%**.

## The root cause — and it is the Master Prompt's §3

`_monitor_positions()` does not decide exits from price. It reads **five live data sources that
cannot be rewound**:

| source | references |
|---|---|
| `Signal` | 25 |
| `Ranking` / kscore | 24 |
| ATR | 32 |
| OBV | 22 |
| market regime | 15 |

Replaying a June trade feeds it **September's** signals, rankings, ATR, OBV and regime. No amount
of price-path fidelity fixes that.

**This is §3 "POINT-IN-TIME CORRECTNESS" of the master prompt, and it is now a measured gap
rather than a theoretical one.** Until the inputs an exit decision consumed are *recorded at
decision time*, **no retrospective config experiment on exits is possible** — not by replay, not
by simulation, not by any method.

## What this changes in the plan

**§3 point-in-time correctness is promoted.** It was previously listed as "partially built, not
systematically enforced." It is in fact the **blocker on all retrospective strategy testing**,
which makes it foundational rather than housekeeping.

**Lever 1 needs a forward A/B test instead.** Run a second SWING portfolio with a wider stop
alongside the current one, on the same signals, and compare realised results. It needs no replay,
uses the real engine against real live state, and is the only method available today that can
answer the question. Cost is calendar time, not engineering time.

## Revised sequence

| phase | work | status |
|---|---|---|
| **1** | **Forward A/B: SWING wide-stop portfolio** | the only way to test Lever 1 — needs a go/no-go |
| **2** | **§3 record decision inputs at decision time** | unblocks ALL future retrospective testing |
| 3 | Options income engine (§26) | unchanged — independent of direction |
| 4 | Confidence calibration | unchanged — ~3 weeks of data |
| 5 | §4 event unification | unchanged |

## The honest summary

I set out to test the largest loss bucket in the account and **could not**. That is a negative
result, but it is a real one: it identifies why the platform cannot currently learn from its own
history, and it promotes a section of the master prompt I had previously ranked as low priority.

**Two failed attempts against the same control is stronger evidence than one successful
backtest would have been** — a passing result from either attempt would have been believed, and
both were wrong by 28–202 percentage points.

---

# UPDATE 2 — the harness DOES work on recent trades. Fidelity decays with age.

The user suggested *"try testing with this month's data and see."* That test settled it.

## Two methodology errors of mine, found by controls

**Error 1 — probing the daily LOW.** I fed each day's low to the engine as an observable price.
A real stop *would* fill on an intraday low, so this looked correct. But the live engine reads a
**5-minute price cache ~78 times a session** and essentially never observes the true daily low,
so the replay triggered stops reality never saw.

| probe | SWING error |
|---|---|
| LOW + CLOSE | 186.6 pp |
| **CLOSE only** | **32.8 pp** |

A **5.7× improvement** from one variable. It also fixed a second symptom I had flagged —
`highest_price` had been stuck at entry, so breakeven/trailing never armed.

**Error 2 — assuming the failure was uniform.** It was not.

## Fidelity decays sharply with trade age

| entry month | n (SWING/GROWTH) | SWING error | GROWTH error |
|---|---|---|---|
| 2026-06 (oldest) | 25 / 21 | 59.2 pp | 60.2 pp |
| 2026-07 | 18 / 24 | 45.6 pp | 55.0 pp |
| 2026-08 | 14 / 10 | 25.2 pp | 62.6 pp |
| **2026-09 (newest)** | **4 / 3** | **6.0 pp** | **0.4 pp** |

**September replays almost exactly.** The further back a trade sits, the more the live state the
exit logic reads has moved on — precisely the staleness the `as_of` work targets, now measured
rather than argued.

**Caveat, stated plainly: September is n=4 and n=3.** That is a suggestive trend across four
months, not proof. GROWTH's August error (62.6) also breaks monotonicity, so age is clearly not
the only factor.

## What this changes

**The harness is not broken — its usable window is bounded.** Rather than "exits are not
retrospectively testable" (the T390 conclusion, now superseded twice), the honest statement is:

> Exit-config experiments are testable on RECENT trades, and fidelity degrades with age.

That is a materially better position, and it makes the SWING stop question answerable without
waiting weeks for a forward A/B — provided the sample is recent enough to trust.

## Next: M5 replay

`prices` holds **1.4M M5 bars from 2026-06-15**, which is the granularity the live engine
actually samples. Replaying at 30-minute intervals (13 observations a session instead of one
close) should capture the intraday path that arms breakeven and trailing — the remaining known
source of pessimism in the daily replay. That control is running.
