# WHERE THIS APP ACTUALLY STANDS — A BALANCED READ

**Date:** 2026-09-05
**Why this exists:** a full day of auditing produced a long list of defects, and read end-to-end
it gives a badly distorted impression. Every audit went looking for what was broken; none of
them measured what works. This is the correction.

---

## What has actually been built

| | |
|---|---|
| Commits | **1,551** |
| Production Python | **83,428 lines** |
| Test code | **66,849 lines across 386 test files** |
| Microservices | **12**, all healthy in production |
| Frontend pages | **62** |
| Price bars stored | **1,469,435** |
| Signals generated | **44,414** |
| Outcomes tracked | **16,732** |

A **0.8:1 test-to-production ratio** is better than most commercial codebases. The 12 services
run 24/7 on EC2 with health checks, scheduled jobs, rate-limit discipline, and a data-quality
framework with 44 automated checks. That framework is *why* this session could find defects at
all — most systems this size cannot tell you what's broken.

**This is not a broken app. It is a working platform with a measurement problem in one
component.**

## What demonstrably works

- **Data pipeline** — 1.47M bars, US + HK, multi-timeframe, split-adjusted (one bad symbol found
  and fixed today out of 193).
- **Alerting infrastructure** — 241 alerts genuinely delivered; 15 BUY alerts went out on the
  last trading day. The plumbing works.
- **Outcome tracking** — 16,732 forward-return records. Most retail tools never measure
  themselves at all. This is the single most valuable asset in the codebase.
- **Self-diagnosis** — 44 DQ checks, currently all passing. Several defects found today were
  surfaced *by the app's own instrumentation*.
- **Options/dark-pool integration** — real Unusual Whales data, honest framing that refuses
  "smart money" overclaiming, correct 4-way options direction logic.
- **Paper trading** — a full engine with game plans, stops, position sizing, and broker sync.

## What genuinely does not work yet

**One thing: signal entry timing.** Not "a lot of pieces." Everything else either works, or is
a measurement gap that hides whether something works.

The evidence, honestly stated:
- Post-fix signals: −0.76% alpha vs buy-and-hold
- Placebo test: entering the same stocks 14 days *earlier* was ~6 points better
- Root cause: every conviction pillar is a momentum measure, so requiring several to agree
  structurally guarantees late entry

That is **one defect with one cause**, not a system-wide failure. It happens to be the most
important one, which is why it dominated the day.

---

## The finding that reframes everything: it is a risk-management problem, not a stock-picking one

Paper trading, 116 closed trades:

| Metric | Value |
|---|---|
| Win rate | 31.9% |
| **Average win** | **+$408** |
| **Average loss** | **−$293** |
| Gross wins | +$15,091 |
| Gross losses | −$23,119 |
| Net | **−$8,029** |

**Look at the win/loss sizes. The average win is 39% LARGER than the average loss.** That is a
genuinely good trait — it means when the system is right, it is right in a meaningful way. Most
losing systems have the opposite profile.

So why the net loss? **Concentration in a handful of catastrophic trades:**

| Symbol | Trades | P&L |
|---|---|---|
| 2382.HK | 3 | **−$5,143** |
| 0992.HK | 1 | −$1,659 |
| FCEL | 5 | −$1,312 |
| 0981.HK | 2 | −$1,305 |
| SNOW | 1 | −$986 (−19% on one trade) |

**11 trades lost more than $500 each, totalling −$15,875 — nearly double the entire net loss.**
The other 105 trades are collectively profitable.

### Modelled: what a real stop would have done

Capping every loss at −8%:

| | P&L |
|---|---|
| Actual | **−$8,029** |
| With an 8% hard stop | **−$5,412** |

**A third of the loss recovered by one mechanical rule**, with no change to signal quality at
all. Only 8 trades ever exceeded −8%; the worst hit −19%.

This is the highest-value, lowest-risk change available, and it is independent of every signal
problem in the audits. A stop does not need the entry to be good.

---

## The honest bottom line

**What the audits got right:** the signal entry timing is genuinely weak, and the alerts have
not yet demonstrated predictive edge. Those findings stand.

**What the audits missed by never looking:** the platform around those signals is substantial,
well-tested, self-measuring, and operationally sound. And the trading results are *far* closer
to working than "no edge" implies — the win/loss ratio is already favourable, and the losses
concentrate in a small number of unstopped trades.

**What that means practically:** this is not six months of wasted work needing a rebuild. It is
a working system with an identified, addressable weakness and one obvious unimplemented safety
rule.

## Where the leverage actually is, in order

1. **Enforce a hard stop loss.** Modelled: recovers ~$2,600 of $8,029. Mechanical, needs no
   signal improvement, biggest single win available.
2. **Cap position size / exposure per symbol.** 2382.HK alone lost $5,143 across 3 trades — a
   per-symbol loss limit would have stopped that bleed after the first.
3. **Let the anti-chasing filter run.** Shipped today, honestly validated out-of-sample at
   ~+0.33 points. Small but real, and it directly targets the late-entry defect.
4. **Wait for measurement.** The GEX corroboration and dark-pool baseline tracking shipped today
   will, in 3-4 weeks, answer questions that are currently unanswerable. Do not build on those
   alerts before then.
5. **Only then revisit signal generation.** It needs a non-momentum pillar, which is a design
   change, not a tuning exercise — and it is the hardest item on this list, not the first.

## A note on how the audits read

Every one of them was scoped to find defects, so every one returned defects. That is what they
were asked to do, and the findings are real and independently verified. But a day of reading
only those documents gives a false picture of the whole. The app is in materially better shape
than that reading suggests — and it is in better shape *today* than yesterday, because those
defects are now found, fixed, or instrumented rather than silently costing money.
