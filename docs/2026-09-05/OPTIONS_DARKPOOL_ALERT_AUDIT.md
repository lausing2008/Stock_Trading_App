# DEEP AUDIT: OPTIONS FLOW & DARK POOL ALERTS

**Date:** 2026-09-05
**Question:** what are these alerts actually telling me, and how can they help?

---

## Short answer

| | Options Flow | Dark Pool |
|---|---|---|
| **What it measures** | Large, urgent options orders sweeping market makers — with a real bid/ask split showing aggressive buying vs. selling | A large block that printed off-exchange (≥$1M) |
| **Is the measurement real?** | **Yes** — UW's own scanner over the full options tape | **Yes** — real FINRA-reported prints |
| **Is the *interpretation* sound?** | **Yes** — the 4-way direction logic is correct options reasoning | **N/A — it makes no directional claim** |
| **Has it shown edge yet?** | **Unproven** — 2 days of resolved data, no directional discrimination visible | **Unproven** — 49 resolved, 40.8% up |
| **Biggest problem** | Too new to judge | **Fires on ~85% of the watched universe every day** |

Neither is broken. Both report genuinely real facts. But **neither has yet demonstrated it can
help you make money**, and the dark pool alert has a selectivity defect that makes it close to
useless in its current configuration.

---

## 1. Options Flow Alert — what it's telling you

**Mechanism** (`check_options_flow_alerts`, scheduler.py:4509): UW's rule-based scanner detects
repeated same-contract trades within milliseconds — typically one large order sweeping across
multiple market makers. The alert reports the contract, expiry, strike, size, and direction.

### The direction logic is genuinely correct

This is the part most retail tools get wrong. `_options_flow_alert_direction()` (scheduler.py:4436):

| Option type | Aggressor side | Direction | Why |
|---|---|---|---|
| Call | Ask (buying) | **Bullish** | Aggressive buying of upside |
| Put | Ask (buying) | **Bearish** | Buying downside protection / betting on a drop |
| Put | Bid (selling) | **Bullish** | Selling puts = betting it will NOT fall |
| Call | Bid (selling) | **Bearish** | Selling calls = betting it will NOT rise |

This is **not** the naive "call = bullish, put = bearish." Selling puts being bullish and selling
calls being bearish is how a real options trader reads the tape. The implementation matches its
own documentation exactly.

### What the data shows so far — and why you can't trust it yet

1,539 alerts since 2026-09-01. Resolved 1-day outcomes:

| Direction | n | Avg 1d return | Price rose | "Win rate" |
|---|---|---|---|---|
| Bullish | 683 | +0.021% | **79.9%** | 61.5% |
| Bearish | 761 | +0.022% | **79.4%** | 20.6% |

**Read this carefully.** Price rose after ~80% of alerts in **both** directions, and the average
return is essentially identical (+0.021% vs +0.022%). The alert is not discriminating direction
at all — bullish and bearish alerts were followed by the same price behavior.

The `is_correct` field is **honest** (a bearish alert correctly scores as wrong when price rises,
which is why its win rate is 20.6%) — the scoring is not the problem. The problem is the signal.

**But this is NOT yet a verdict**, for a specific reason: only 2 days have resolved
(2026-09-01 and 09-02), and **1,145 of 1,444 came from a single day**. The "80% up" is
overwhelmingly one day's market drift, not a property of the alert. `return_5d` has **zero**
resolved rows. There is not enough data here to conclude anything, in either direction.

**Verdict: too new to judge. Re-run this analysis in 3–4 weeks.** The mechanism is sound and the
direction logic is correct; whether it predicts anything is genuinely unknown.

---

## 2. Dark Pool Alert — what it's telling you

**Mechanism** (`check_dark_pool_alerts`, scheduler.py:4754): reports a block trade ≥$1,000,000
that executed off-exchange, via UW's `/api/darkpool/{ticker}`.

### The framing is admirably honest

The code explicitly refuses to claim what the block *means*:

> this reports a MEASURED fact — a large block genuinely printed off-exchange … never a claim
> about WHY it happened or that the stock will move as a result. Institutional block trades
> cross dark pools for many reasons (index rebalancing, portfolio hedging, block-crossing to
> avoid market impact) that have nothing to do with a directional view — this is explicitly
> NOT framed as "smart money is bullish/bearish."

This is correct and worth respecting. A dark pool print tells you **size moved**, not which way
anyone thinks the stock is going. Anyone selling you "follow the smart money" on dark pool prints
is overclaiming; this app doesn't.

### The real defect: it fires on almost everything

| Fired date | Alerts | Distinct symbols |
|---|---|---|
| 2026-09-02 | 49 | 49 |
| 2026-09-03 | 46 | 46 |
| 2026-09-04 | 49 | 49 |
| 2026-09-05 | 40 | 40 |

The watched universe (`_bounded_options_flow_symbols`) is **55 symbols**. So this fires on
**~85–89% of everything it watches, every single day, one alert per symbol.**

A $1M block is simply not unusual for a large-cap. For AAPL or NVDA it is routine, sub-second
background activity. The threshold is not selective, so the alert carries almost no information:
"a big trade happened in a big stock today" is true nearly always.

Performance on 49 resolved outcomes: **+0.005% avg 1-day, 40.8% of the time price rose.** That is
noise, consistent with the alert being uninformative — as its own honest framing would predict.

**Verdict: real data, honest framing, but not actionable as configured.** The volume problem is
the fixable part.

---

## 3. Bug found: `DarkPoolPrint` table is never written

`shared/db/models.py` defines a `DarkPoolPrint` model, and the table exists in production — with
**0 rows**, while 184 alerts have fired.

Nothing in the codebase writes it. Every consumer (`check_dark_pool_alerts`, the API route, the
UI) reads live from UW's API instead. The table is dead.

**Impact:** nothing is broken today, but it means there is **no local history of dark pool
prints** — so no backtest of "do large blocks predict anything?" is possible, and every view is
limited to whatever UW's API returns right now (last 50 prints). Any future attempt to measure
this alert's value has no data to measure against.

---

## 4. How these can actually help you

Given the audit, here is the honest assessment against your stated goals.

### What they genuinely give you

1. **Options flow direction is real information you cannot get elsewhere in this app.** Whether
   large money is aggressively *buying* or *selling* a contract — and the correct 4-way reading
   of that — is a genuine capability. It just isn't validated yet.
2. **Expiry and strike tell you the market's implied timeframe.** An alert on a 2-week expiry vs.
   a 6-month LEAP are completely different statements about conviction and horizon. This is
   directly relevant to your QQQ LEAPS strategy — it is a read on what timeframe large players
   are positioning for.
3. **Dark pool prints tell you where institutional size is transacting**, which is real context
   even without a direction — but only if the alert is selective enough to be surprising.

### How to actually use them (as of today)

- **Use options flow as context, not as a trigger.** When you are already considering a position,
  a large ask-side call sweep at your strike/expiry is corroboration. It is not, on this
  evidence, a reason to enter by itself.
- **Ignore dark pool alerts as currently tuned.** At 85% daily coverage they contain no
  information. They become useful only after the selectivity fix below.
- **Watch the expiry clustering.** If several alerts on one symbol cluster at the same expiry,
  that is a more meaningful statement than any single alert.
- **Do not treat either as a directional prediction.** Neither has demonstrated predictive
  power, and the code itself never claims they do.

### Recommended fixes, in priority order

1. **Make the dark pool threshold relative, not absolute** (highest value). $1M is meaningless
   for AAPL and enormous for a small cap. Scale it — e.g. print size as a multiple of the
   symbol's median daily dark-pool volume, or as a % of average daily volume. Target roughly
   5–10% of the universe firing per day rather than 85%.
2. **Persist `DarkPoolPrint` rows** so the alert can ever be backtested. Without this, question 4
   ("does this help?") is permanently unanswerable.
3. **Re-run the options-flow performance analysis in 3–4 weeks**, once `return_5d` has resolved
   across more than two trading days. The direction logic deserves a fair test.
4. **Consider splitting the options-flow win-rate display by direction** — the existing
   `calibrated_win_rate` gate (≥30 resolved per direction) already anticipates this, and the
   bullish/bearish asymmetry above is exactly why it matters.

## Caveats on this audit

- Options flow: 2 resolved days, heavily concentrated in one. **Not a verdict on the alert.**
- Dark pool: 49 resolved outcomes. Enough to show the volume problem, not enough to judge
  predictive value.
- Both features launched 2026-09-01/02. Everything above should be revisited with a month of data.
