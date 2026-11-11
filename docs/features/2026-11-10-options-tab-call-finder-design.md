# Options tab redesign and Call Finder — design (November 10, 2026)

**Status: built and tested locally on November 10, not committed or deployed.** Decisions taken: the proposed defaults (half-move headline with max-at-target as the aggressive alternative, one implied σ default target, advanced tier). See [Build](#build-november-10). Validation steps 1–2 remain open. Companion review: [options, short squeeze and dark pool review](../audits/2026-11-10-options-squeeze-dark-pool-review.md).

## What was asked

Make the Options tab on the stock page easier to use, find the best contract in the chain, and highlight its expiration, strike, delta, gamma, open interest and IV with reasons. Give one near-term call and one long-dated (LEAPS) call.

## What "best" can honestly mean

Three facts from this project's own evidence limit what the tab may claim:

1. **No contract-selection rule tested so far picked better contracts.** The October 31 replay (285,702 trades, 152 symbols) found that the quote-quality lead beat the nearest-strike preset only by about its cheaper spread. In the held-out quarter no choice made reliable money, and long calls had a negative median everywhere. (That report is on the `safety/prod-before-reconcile-20261108` branch, not on `prod`; see the review.)
2. **Option prices are fair on average.** Under the market's own implied distribution, every strike has roughly the same expected return: zero, minus trading costs. A "most profitable contract" exists only relative to a view the market does not share.
3. **The chain is yesterday's settled end-of-day quote**, with no quote time or size. It can shortlist; it cannot confirm a fill.

So the tab cannot name a contract that is most profitable unconditionally. It can do this:

> **Given your view (a target price by a date), show the liquid contract that pays best if you are only half right, with the numbers and the reasons, and show what it loses if you are wrong.**

The pick is called a **research candidate**, not a recommendation, until a replay and a forward record show the rule beats the alternatives (see [Validation](#validation-before-any-stronger-wording)).

## How the pick is made

### Why not "highest return at target" or a scenario average

Illustrative numbers only (a $100 stock, 40% IV, Black–Scholes, 4% spread; not market data). A 45-day call held 14 days, with the target set one standard deviation up ($107.83):

| Strike | Delta | Ask | Break-even | Return at target | At half the move | Flat | One σ down | Average of the four |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 90 | 0.80 | 12.32 | +2.3% | +46% | +18% | −10% | −56% | −0.3% |
| **95** | **0.68** | **8.78** | **+3.8%** | **+56%** | **+19%** | **−14%** | **−64%** | −1.1% |
| 100 | 0.54 | 5.95 | +6.0% | +64% | +18% | −21% | −72% | −2.8% |
| 105 | 0.40 | 3.83 | +8.8% | +71% | +15% | −29% | −80% | −5.6% |
| 110 | 0.28 | 2.35 | +12.4% | +75% | +9% | −38% | −85% | −9.6% |
| 115 | 0.19 | 1.37 | +16.4% | +76% | +1% | −47% | −90% | −14.8% |

- **Return at target** keeps rising with the strike, so maximising it always picks the cheapest far out-of-the-money call the filters allow. That call needs the full move, on time.
- **An average over symmetric scenarios** is negative for every strike and always favours the deepest in-the-money one. That is fair pricing showing through, not a ranking.
- **Return at half the move** has a real maximum inside the chain: here the 95 strike, delta 0.68. Across 25%, 40% and 70% IV the maximum stays at delta 0.67–0.79 for a near-term call and 0.83–0.86 for a one-year call. That agrees with common practice: in-the-money, delta about 0.70, near term; delta about 0.80, as a stock substitute, for LEAPS.

**Strike rule: the contract with the highest return if the stock makes half of your target move by your date.** It rewards being roughly right, not exactly right. The full-target, flat and down outcomes are shown beside it. The contract with the highest return at the full target is shown as the **aggressive alternative**, labelled as needing the whole move.

### Expiry is chosen by a time rule, not by return

In the same model the best expiry is always the shortest one allowed, because the scenario assumes the move arrives on time. A scenario cannot choose the expiry, so a rule does:

| Pick | Expiry rule | Reason |
|---|---|---|
| **Near-term call** | The nearest expiry with at least **2× your holding period and at least 30 days** to run, at most 90 days. If two qualify within a week of each other, the one with more call open interest | You sell with at least half the life left, before decay is steepest, and have room if the move is late |
| **LEAPS call** | Among expiries **365 days or more** out, the one with the most call open interest; ties go to the later date. If none exists, the longest expiry of 270 days or more, labelled "long-dated, not a full LEAPS" | Long-dated liquidity concentrates in January expiries; more time is the point of a LEAPS |

### Eligibility (hard gates; every rejection is counted and shown)

| Gate | Near-term | LEAPS |
|---|---|---|
| Contract terms | Standard root, current expiry, not an index | Same |
| Quote | Bid above zero, ask above bid | Same |
| Spread, % of mid | At most 10%; if nothing passes, at most 20%, labelled "wide quotes" | At most 15%; same fallback |
| Open interest | At least 100; unknown fails | At least 50; unknown fails |
| IV | Known, between 0 and 300% | Same |
| Delta guard rails | 0.40 to 0.85 | 0.60 to 0.90 |

Delta is the provider's where present. The provider sends greeks only for contracts that traded that day, so otherwise delta and gamma are computed from the contract's own IV and marked **est.**

### Inputs

- **Stock price:** the chain session's own close, used only when that session's put–call parity agrees with it (the existing R03 check).
- **Holding period:** from the selected horizon (7, 14 or 28 days) for the near-term call; 180 days for the LEAPS. Editable.
- **Target:** defaults to one IV-implied standard deviation up over the holding period. That is a neutral yardstick, not a forecast, and the card says so. One click swaps in the analyst target or a typed price.
- **Model:** Black–Scholes with the contract's own IV held constant, a stated flat interest rate, no dividends, and exit at the mid less half the current spread. All four assumptions are printed under the table.

## What the card shows

Two columns, **Near-term call** and **LEAPS call**. Each has:

**The contract:** expiry and days left, strike, ask, break-even price and % move needed, cost per contract.

**The six numbers asked for, each with its reason** (text generated from the values, never free-form):

| Field | What the reason says |
|---|---|
| Expiration | Days to run against your holding period; daily time decay in dollars and as % of the premium |
| Strike | In or out of the money by how much; share of the premium that is time value |
| Delta | Dollars gained per $1 move now; shares-equivalent exposure; leverage against 100 shares |
| Gamma | What delta becomes after the target move; high gamma near term means the position speeds up and slows down fast |
| Open interest | OI and spread as a round-trip cost in dollars; where it ranks among that expiry's calls |
| IV | The contract's IV against the stock's 20-day realised volatility and its one-year IV rank; what a 5-point IV drop would cost (vega) |

**If you're right / if you're wrong:** return at the target, at half the move, flat, one σ down, and "target reached but twice as late". Also the model-implied chance of finishing above break-even at expiry, labelled as the market's pricing, not a prediction.

**Warnings**, shown above the numbers when they apply:
- earnings or ex-dividend date inside the holding period (dates are not issuer-confirmed);
- IV rank above 60: the call is expensive against its own year, with a one-line note that a spread would cut the IV exposure;
- stock below its 200-day average (LEAPS only): this project's LEAPS backtest found a 0.70-delta call needs a trend (TQQQ −73.78% over 336 days);
- greeks estimated, wide-quote fallback used, or chain session older than one trading day.

**Nothing qualifies:** the card says which gate removed the most contracts, and shows no pick.

## Tab layout

The tab is currently one long column of nine blocks, with the chain collapsed at the bottom. Proposed order, with a sticky row of four section links:

1. **Find a contract**
   - *At a glance:* price and its session, IV, IV rank, IV against realised volatility, implied move to the next monthly expiry, next earnings and ex-dividend dates.
   - *Call Finder* (above).
   - *Chain:* see below.
2. **Strategies:** the existing four-strategy playbooks and the gain/loss table, unchanged.
3. **Volatility and past performance:** the IV-against-HV chart and the three-week performance table, unchanged.
4. **Flow and positioning:** call/put dominance, options flow and dealer positioning, unchanged.

**Chain changes**
- Add **Delta** and **Gamma** columns (already returned by the API, not displayed today), plus mid and spread %.
- Highlight the two picked rows with a badge; clicking a pick opens its expiry and scrolls to the row.
- Expiry buttons show days to expiry and are grouped: weekly, monthly, LEAPS.
- Show strikes within 15% of the price by default, with "show all strikes".
- Show the chain whenever a chain exists. Today it is hidden unless the separate flow summary loaded.
- A missing bid or ask shows "—", not 0.00.

## Build plan

| Step | What | Notes |
|---|---|---|
| 1 | `services/option_pricing.py`: price, delta, gamma, theta, vega, probability above a level | Pure functions; tested against published reference values |
| 2 | `services/option_call_finder.py`: gates, expiry rule, strike rule, reasons, warnings, rejection counts | Pure; takes rows and inputs, no database or network |
| 3 | `GET /{symbol}/options-call-finder` | One database read of the latest archived session (all expiries); no provider request. Same access tier as the Game Plan. Cached per session and inputs |
| 4 | Frontend: glance strip, Call Finder card, chain columns and highlight, section links | Existing cards move, not change |
| 5 | Tests | Gates, both expiry rules, interior strike optimum, estimated-greek labelling, nothing-qualifies, parity failure, route with real builder; frontend render and null handling |

Step 3 reads the same archive rows the chain endpoint already reads, so provider usage does not change.

## Validation before any stronger wording

1. **Replay.** Add the finder's rule as a fourth arm in the candidate outcome study (against the preset, the quote-quality lead and a random pick), with the held-out period fixed before the run. Report monthly first.
2. **Forward record.** Append each served pick to the desk record, so real picks can be scored later.
3. Only if both show an advantage, with intervals that exclude zero in the held-out data, may the wording change from "research candidate". Otherwise the card stays as a transparent calculator of your own view.

Both depend on the study and desk-record code, which is preserved on the `safety/prod-before-reconcile-20261108` branch and is not on `prod`.

## Limits

- End-of-day quotes from the previous session; no live bid/ask, quote time or size. Confirm at the broker.
- Constant-IV model: a fall in IV after an event (earnings) makes real results worse than shown.
- No dividends or early exercise in the model; no commissions (the broker is commission-free) or price impact.
- Calls only in this version. Puts and spreads would reuse the same engine.
- The half-move rule is a convention chosen for robustness. It has not been validated on this platform's data.

## Decisions needed

1. **Strike rule:** best return at half the move as the headline pick, with max-at-target as the aggressive alternative (proposed), or the reverse.
2. **Default target:** one implied standard deviation up (proposed), or the analyst target.
3. **Access:** same advanced tier as the Game Plan (proposed), or all users.
4. **Bring the study and desk-record work from the safety branch onto `prod`,** so validation steps 1 and 2 can run.

## Build (November 10)

| Part | Where | Notes |
|---|---|---|
| Pricing | `services/market-data/src/services/option_pricing.py` | Call price, delta, gamma, theta, vega and the model probability above a level. Checked against Hull's worked example (S=42, K=40, r=10%, σ=20%, T=0.5 gives 4.76), put–call parity, and finite differences for the greeks |
| Finder | `services/market-data/src/services/option_call_finder.py` | Pure. Gates, both expiry rules, the half-move strike rule, the aggressive alternative, generated reasons, scenarios and warnings, with rejection counts. Method `call_finder_v1` |
| Route | `GET /stocks/{symbol}/options-call-finder?holding_days=&target_price=` | Advanced tier. One archive read of the latest session (every expiry), no provider request beyond the cached IV rank. Refuses without a parity-checked session close, or for an index product. Contract terms are checked against each OCC identity |
| Frontend | `CallFinderCard.tsx`, the Options tab and `OptionsChainTable.tsx` | Glance strip; two pick columns with the six figures and reasons; scenarios; aggressive alternative behind a button; warnings and assumptions; target input with "use analyst target" |
| Tab layout | Options tab | Section links (Find a contract, Chain, Strategies, Volatility, Flow); the chain now sits under the finder; expiry buttons show days, LEAPS and the pick; the chain shows strikes within 15% of the price by default (picks always shown) and badges the two picks |

**Worked check on a model-priced chain** ($100 stock, 40% IV, 4% spread, 14-day hold):
- The near-term pick is the 35-day 95 call, delta 0.69: +21% at half the move and +63% at target.
- The aggressive alternative is the 102.5 call: +77% at target.
- The LEAPS pick is delta 0.75.

These numbers match the worked example above.

**Tests:**
- `test_option_pricing.py`: 5 tests.
- `test_option_call_finder.py`: 16 tests, covering the rules, gates, fallbacks, warnings and the route end to end.
- `CallFinderCard.test.tsx`: 5 tests.
- `options-missing-inputs.test.tsx`: the chain additions.

Sabotage of the half-move rule, the ask fill and the open-interest gate each fails its tests.

**Not built:** the replay arm and the forward record (validation steps 1–2). Both need the study and desk-record code that is still on the `safety/prod-before-reconcile-20261108` branch.
