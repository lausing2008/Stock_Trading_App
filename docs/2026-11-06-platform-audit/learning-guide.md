# StockAI learning wiki

Use the platform to build a conditional trade plan, then check whether that plan is still valid at the available price. A green score is a reason to investigate, not permission to chase.

All prices, symbols, probabilities and position sizes in examples below are hypothetical educational inputs. They are not current recommendations or validated strategy parameters. The proposed Trade Readiness card in the architecture plan is not yet part of this documentation-only release.

## Start here

Your daily sequence is **market → candidate → setup → trigger → price/risk → review**.

1. Choose US or HK and the intended trading style. Check market regime, scheduled earnings and major events.
2. Use Rankings/watchlists to find candidates. A high K-Score identifies a stock worth researching; it does not identify a good entry minute.
3. Open stock detail. Read the AI Signal's style, reasons, freshness and data basis. A forming daily bar can change before close.
4. Identify a known level and a setup: base breakout, first pullback, or early recovery. Mark what would invalidate it.
5. Check participation using a clearly identified RVOL measure and inspect the chart's selected VP/VWAP range.
6. Wait for your declared trigger. If price moves beyond your planned ceiling, record a missed opportunity rather than moving the target to justify entry.
7. Calculate risk and portfolio exposure. Save the plan/journal entry before reviewing its outcome.

No entry is a valid decision when evidence, price or risk is unsuitable. A rising stock can remain a poor new trade.

## Where to find things

Paths below identify existing app pages, not links to a newly published site. Open them through your platform's normal navigation; access can depend on your account and deployed release.

| Task | Existing page/tool |
|---|---|
| General education | `/learn` |
| Discover candidates | `/rankings`, `/watchlist` |
| Chart, AI Signal and stock-specific context | `/stock/[symbol]` — substitute the selected symbol |
| Market context and HMM | `/regime`, `/market-pulse-dashboard` |
| RZY structures and explanations | `/little-rzy`, stock chart RZY overlay/panel |
| Headlines and research | `/news`, `/research`, `/stock-intelligence` |
| Squeeze candidates and education | `/short-squeeze`, `/squeeze-playbook` |
| Options | `/options-flow`, `/options-calculator`, `/options-income` and stock options panels |
| Options education | `/options-dark-pool-tutorial`, `/options-income-guide` |
| Save/review decisions | `/board`, `/journal`, `/paper-portfolio` |
| Signal measurement | `/signal-quality`, `/signal-accuracy`; check the displayed population and target |

The current `/learn` self-tuning description is broader than the inspected implementation: several legacy calibration writers are research-only. This wiki describes that limitation rather than assuming every historical tuning mechanism is active.

## Understand the four different questions

| Display/concept | What it tells you | What it does not tell you |
|---|---|---|
| K-Score | Relative attractiveness under a ranking formula | That now is a timely entry |
| AI strength/confidence score | How far the fused signal sits from neutral | A literal chance of making money |
| Historical calibrated probability/rate | Frequency of a specifically defined event in an eligible historical population | Your personal fill/exit outcome or a guarantee |
| Planned reward/risk | Distance to target relative to stop, ideally after costs | Probability of reaching the target |
| Data quality/readiness | Whether required inputs are present, fresh and suitable | Whether the trade has positive expectancy |

For example, the current strength formula maps a fused score of 0.80 to `abs(.80-.50)×200 = 60`. That is **60 strength**, not a 60% win rate. A claimed 70% probability only has meaning with a target such as “price exceeds this hurdle after ten sessions,” and evidence that comparable 70% forecasts succeed near that rate. [Calibration explanation](https://scikit-learn.org/stable/modules/calibration.html).

SHORT is a **daily trading style**, not a command to short stock and not the separate INTRADAY horizon. SWING, LONG and GROWTH have different policies. Model forecast horizon, displayed outcome window and maximum managed holding period can differ; inspect each rather than assuming one “days” number covers all three. INTRADAY currently has contracts/storage but production generation is disabled in inspected source.

## AI Signal: start with reasons, finish with price

AI Signals combines technical context and qualified ML contributions with style/regime rules. BUY means the applicable signal rule passed; it does not establish that the current quote still offers the original reward/risk. HOLD/WAIT are not synonymous with “bad company.” SELL can describe bearish evidence or an exit context; opening a short position needs separate borrow and risk checks.

**Example:** ABC receives a bullish swing signal around $50. By the time you open the alert it is $52. The planned stop is $49.40 and the structural target is $52.80. At $50.20 the gross reward/risk is `2.60/.80 = 3.25`; at $52 it is `.80/2.60 ≈ .31`. The same bullish thesis can be a poor late entry.

Check whether the signal is generated from completed or forming daily data, whether ML is available, whether important inputs are missing, and whether the displayed performance measures first signals, accepted alerts or fills. These populations should not be interchanged.

## RVOL: is participation unusual for this time?

RVOL means relative volume. The denominator matters more than the badge color.

Suppose a stock has traded 600,000 shares by 10:30. Its normal cumulative volume by 10:30 is 300,000, while its normal whole-day volume is 2,000,000:

- Same-time RVOL = `600,000 / 300,000 = 2.0`.
- Full-day volume ratio = `600,000 / 2,000,000 = 0.30`.

Both calculations can be true. Only the first directly answers whether today's activity is unusual **by 10:30**. The current platform contains both conventions and approximate elapsed-session alert thresholds; do not compare them as identical scores.

Use volume to qualify a price response. Rising participation plus a successful reclaim can support a rally hypothesis. Large volume while price fails resistance can be distribution, liquidation or two-sided event trading. Volume alone does not identify buyers' motives. Missing historical bars can exaggerate RVOL, so an unknown/low-coverage baseline is not a strong signal.

## Volume Profile and Fixed Range VP

Volume Profile groups estimated volume by price. Ordinary volume bars group volume by time.

| Term | Meaning | Practical use |
|---|---|---|
| POC | Price bucket with most estimated volume | A reference area for prior acceptance; not guaranteed support |
| VAH / VAL | Upper/lower boundary of the selected value area, usually 70% of estimated volume | Context for moves outside or back into prior value |
| HVN | High-volume node | Prior activity/congestion zone |
| LVN | Low-volume node | Lower historical activity; possible fast movement, not a guaranteed path |

**Range VP** profiles a loaded range. **Fixed Range VP** lets you select the move you want to study. **Session VP** uses a session slice. The current backend value-area alert uses its own rolling daily range; it need not match your selected chart range.

To study a breakout, select the already-known base or preceding impulse using the chart's Fixed Range VP tool, then inspect the selected dates and pivot snapping. Keep that range fixed while judging the trade. Do not keep dragging the endpoints until the picture supports a desired conclusion.

**Example:** a base spans $48–$50 with POC $49.20 and VAH $49.80. Price breaks above $50, then tests $49.90 and closes back above $50. That is a hypothesis of acceptance above prior value. A drop back below $49.80 with persistent selling contradicts it. The profile supplies context; the completed reclaim supplies the possible trigger.

The platform spreads OHLC bar volume across each bar's high–low range. It does not reconstruct actual transactions at every price, buyer/seller aggression or institutional orders. Bucket count, timeframe and chosen range affect the picture. “Fixed Range VP” and “Vol Profile” are related tools, not independent confirmations.

## VWAP and anchored VWAP

VWAP is volume-weighted average price. In this chart it uses bar typical prices as an approximation. Session VWAP should restart each session; anchored VWAP starts at a declared event or bar.

**Current limitation:** the ordinary chart VWAP accumulates from the first bar supplied to the calculation; it does not explicitly reset per session. Check the loaded range before interpreting “above VWAP” as “above today's average.” The architecture plan proposes separate labels/calculations.

Anchored VWAP can answer “is price above the volume-weighted average since this earnings event or known breakout?” Choose the anchor before assessing the result. Selecting the perfect low afterward creates hindsight.

**Example:** the explicitly selected event-anchored VWAP is $50.10. Price pulls back to $50.15 and closes above a $50.30 reclaim level with sound participation. That can support a first-pullback plan. Price at $53 with the same $50.10 anchor may be strong but extended. Reclaim failure or a close below your structural invalidation cancels the specific plan.

## Fair Value Gaps: zones, not promises

A bullish FVG is a three-candle pattern where the first candle's high is below the third candle's low. A bearish FVG is the mirror. The pattern becomes known when the third candle is available; its middle-candle chart position is not an earlier trading opportunity.

Despite the name, an FVG is neither an estimate of business fair value nor proof that nobody traded at those prices. The middle candle may have traded through them. There is no guarantee that price will return to the zone or bounce.

**Example:** first candle high $100, third candle low $102. The bullish zone is $100–$102. A proposed midpoint entry at $101 and stop $99.80 risks $1.20. A $103 target offers $2, or 1.67R before costs. If real resistance is at $101.80, there is only $0.80 room: do not invent a higher target just because the tool can draw 1.5R.

The basic current FVG plan generates a target from the requested R multiple. That is arithmetic, not independent evidence the target is reachable. Compare it with actual structure and the nearest opposing level.

The audit reproduced a fill-status defect for disconnected touches later bridged. Until repaired, independently inspect the bars before treating an “unfilled” gap as fresh. Distinguish untouched, partly traded, fully filled and invalidated; those are different observations.

## HMM: market context with uncertainty

The Hidden Markov Model estimates a latent market state from VIX, SPY five-day return and IWM relative to its EMA200. The platform maps four learned states to bull, neutral, choppy and bear and uses bear pressure in risk context.

**Example:** bull 0.40, neutral 0.35, choppy 0.15, bear 0.10 is mixed evidence, even if the headline label is “bull.” Bull 0.85 is stronger model state membership, not an 85% chance that your chosen stock rises.

Use regime as context for aggressiveness and competing scenarios. If HMM and the simpler market regime disagree, inspect dates and inputs, then prefer a smaller research commitment or waiting under your existing risk plan. Do not average the two percentages into a new win probability. US proxies should not silently become a specific HK regime assessment.

The audit found labeling and artifact-validation issues to repair. Read model/data age and uncertainty; an unavailable model is not evidence of a favorable market.

## Little RZY: a structured pullback study

This implementation is named `LITTLE_RZY_INSPIRED_V1`. It is a deterministic research interpretation, not a claim of equivalence to every external RZY teaching or a validated proprietary strategy.

It uses confirmed pivots, impulse/retracement structure, a line interaction and directional completed-bar confirmation. Current defaults prefer early sequences, reject chasing beyond Bollinger context, impose a structural stop and require planned reward/risk. `ARMED` means the setup is waiting; `TRIGGERED` starts pending-entry handling, not proof of a fill. Expired, invalidated or cancelled setups are not executed trades.

**Example:** price makes an impulse, a controlled pullback forms, and the structure becomes armed. A completed bar touches the structure line, closes on the favorable side and breaks the previous bar's high. If that close is already beyond the allowed band, no-chase can block entry despite a bullish candle. That block is useful information, not a reason to disable the filter impulsively.

Pivots require future confirmation bars relative to the pivot itself. With the default seven right bars, a 5m pivot cannot be confirmed until at least 35 minutes later. A beautiful historical pattern can therefore have been unavailable at the apparent low.

The corrected October historical study cited in the audit had negative mean net R and did not demonstrate an edge. Use the tool to study consistent decisions and causal replay. A chart reconstruction, simulated next open and actually available live entry are different evidence levels.

## News and event intelligence

Ask four questions: **what happened, when could I know, what was expected, and how did price react?**

A positive headline is not automatically positive surprise. Earnings can beat last year's result but disappoint current expectations. A negative headline may already be priced in. The platform's sentiment/materiality classification helps triage; it is not a causal return forecast.

**Example:** earnings news first reaches the platform at 09:38, after price has already risen 9%. You cannot backtest an entry at 09:30 as if the classifier knew the story. Wait for a defined setup or pass. If price later holds a consolidation and reclaims its top with qualified volume, that is a new observable setup—not proof that the headline guarantees continuation.

Check publication versus observation time, source, revisions, upcoming earnings/macro events and potential binary outcomes. Insider/congress disclosures are often observations of earlier transactions, not real-time buying signals. AI research text should link to evidence and distinguish facts from inference; eloquence is not conviction.

## Short squeeze: watch crowding, then price and liquidity

A squeeze candidate has conditions that could force short sellers to buy back shares. Crowding alone does not specify when or whether that happens.

| Input | What to ask |
|---|---|
| Short % of float | How much of the float is reported short, and what is the reporting date? |
| Days to cover | How large is short interest relative to average daily volume? Higher means more average-volume days to cover |
| Borrow fee/availability | Which provider/universe and timestamp? Is inventory actually current? |
| Price + RVOL | Is there an observable early trigger or an already extended rally? |
| Spread, halts, float | Could an entry or exit execute near the assumed price? |

Daily short-sale volume is not short interest; a trade marked short can be closed the same day. [FINRA explains the distinction](https://syndication.finra.org/content/short-interest-what-it-what-it-not).

**Current caveat:** the platform's score rewards lower DTC and labels a short-interest/available-shares ratio as utilization. The audit recommends revisiting these semantics and validating provider fields. Do not read the score as a probability of a squeeze.

**Example:** a crowded stock rises 2% from a well-defined base on sound same-time volume, then retests. That is a candidate for a controlled plan. The same stock up 25%, far above its trigger and with a wide spread, can be a worse new entry despite a more exciting squeeze score. Never assume a stop eliminates halt or gap risk.

## Options: choose the underlying plan before the contract

A correct stock forecast can lose money in options because of premium paid, time decay, implied-volatility changes and trading costs. [OIC explains option price behavior](https://www.optionseducation.org/referencelibrary/faq/option-price-behavior).

| Structure | Why consider it? | Main tradeoff |
|---|---|---|
| Stock | Direct exposure to the underlying thesis | More capital for equivalent share exposure; downside remains |
| Long call | Defined premium risk and upside exposure | Time/IV/spread costs; can lose all premium |
| Protective put with stock | Protect a stock holding under specified terms | Insurance premium reduces returns |
| Covered call | Receive premium on shares you are willing to sell | Caps upside; substantial stock downside remains |
| Cash-secured put | Accept assignment at an effective purchase price | Stock-like downside after assignment; collateral is committed |
| No trade | Preserve capital when price or evidence is poor | Forgone upside if the thesis works |

High IV rank means high relative to a historical range; it does not establish overpriced premium or safe income. Low IV is not automatically cheap. Check the event calendar, expiry, quote age, bid/ask spread, open interest, contract identity/deliverables and settlement/assignment risks. Open interest alone does not guarantee executable liquidity.

**Long-call example at expiration:** stock is $100; you buy a $100 strike call for $5 per share. Assuming a standard 100-share contract, premium is $500 before costs. Stock finishes at $103: the call is worth $3, so you lose $200 despite getting direction right. Expiration breakeven is $105 before costs. Before expiration, time and IV also affect value; intrinsic value is not a full interim option quote.

**Covered-call example at expiration:** buy 100 shares at $100 and sell a $105 call for $2. If stock finishes above $105 and assignment occurs, gross profit is `$500 share gain + $200 premium = $700`, before costs/dividends. At $80, the call can expire worthless but the combined position loses `$2,000 - $200 = $1,800`. Premium is not free yield.

The research shortlist favors quote quality; it is not proven to find the best returns. The October 31 study did not justify promoting it as a best-pick predictor. Compare the option with stock alone under the same underlying entry and horizon, and with doing nothing. Unsupported or adjusted contract terms should remain blocked from automatic payoff assumptions.

## A complete early-rally example

**Hypothetical swing setup:** ABC has a known base below $50, supportive but mixed market context, and no unresolved data blocker. Its first breakout pulls back and a completed bar reclaims $50.20. A valid same-time volume baseline confirms unusual participation. The prior structural target is $52.80; invalidation is $49.40.

Predeclare entry at $50.20 with a ceiling of $50.40. At the intended entry, gross risk is $0.80/share and gross reward is $2.60/share. For illustration assume total round-trip costs of $0.06/share in either exit outcome: modeled loss is $0.86 and target gain is $2.54, net planned R/R about 2.95.

With a **hypothetical** $100 loss budget, `floor(100/.86) = 116` shares before capital, concentration, liquidity and lot-size caps. That needs about $5,823.20 of stock capital at the intended entry. If available capital is lower, size is lower. This is not a recommended risk percentage; a gap can exceed the modeled $100 loss.

| What happens next | Consistent response |
|---|---|
| Price is $50.25, still within plan; all inputs remain valid | Recheck live executable price and portfolio limits before any action |
| Alert arrives at $51.10 | Outside the predeclared ceiling: wait/pass; do not silently widen it |
| Price loses $49.40 before entry | Invalidate this setup |
| RVOL baseline has missing bars | Treat participation as unknown; do not call 3× volume confirmed |
| A major adverse announcement arrives | Reassess/expire the plan under the declared event rule |
| No entry occurs before expiry | Record a nonfill/expired setup, not a losing trade |
| Price gaps through the stop after entry | Record actual available exit; loss can exceed initial R |

If the move continues without you, that is an opportunity cost to measure across many cases, not evidence this single disciplined decision was wrong.

## Resolve conflicting signals

| Situation | Interpretation |
|---|---|
| AI BUY, RZY not triggered | Directional context exists; the RZY entry condition has not occurred |
| High RVOL, price failing resistance | Activity without favorable price acceptance; investigate exhaustion/adverse event |
| Bullish HMM, stock below invalidation | Broad context cannot override the stock's failed setup |
| FVG support, major resistance immediately overhead | Potential support with insufficient upside room |
| High K-Score, huge recent rally | Attractive candidate can still be an extended entry |
| Positive news, weak price response | Expectations/positioning may differ; the headline alone is insufficient |
| Good stock setup, wide option spread/high premium | Stock or no trade may express the plan more efficiently |

Do not average conflicting indicators until the answer becomes green. Identify which question each answers and let the declared risk/entry policy decide.

## Review performance honestly

Win rate is only part of expectancy:

`expectancy = P(win) × average win − P(loss) × average loss`, using consistent units and net costs.

A hypothetical 40% win rate with +2R winners and −1R losers has +0.20R expectancy before costs. A 70% win rate with +0.30R winners and −1R losers has −0.09R. A high win rate can hide poor economics. Stops, nonfills, partial exits, gaps and capital usage affect the real distribution.

Keep this journal for each unique setup:

| Before the decision | After maturity/exit |
|---|---|
| Symbol, market, style, strategy/version and timestamp | Actual entry/exit or explicit nonfill/cancel/unresolved state |
| Setup, trigger, entry ceiling and expiry | Spread/slippage/fees and actual net result |
| Structural stop/targets and planned R | Maximum adverse/favorable excursion under a fixed window |
| Evidence timestamps/basis and decisive blockers | Whether the decision followed the original plan |
| Event and portfolio risks | Separate selection, timing, execution and exit mistakes |

Review by strategy and horizon, across enough independent dates, including losing periods and rejected setups. Repeated alerts are not independent opportunities. A result drawn from first-fire signals is not a track record of your personal executions. An updated chart should not erase the evidence you actually saw.

## Quick practice

1. **A stock trades 40% of normal daily volume by 10:00. Is it quiet?** Unknown without normal volume by 10:00 and baseline coverage.
2. **AI strength is 80. Is the trade 80% likely to win?** No. Check the separately named and qualified probability, if available.
3. **The chart marks a pivot at 10:00 that needs seven later 5m bars. Could you trade that confirmed pivot at 10:00?** No; confirmation is later.
4. **A 1.5R target appears automatically in the FVG tool. Is it a validated target?** No; inspect actual structure and costs.
5. **You bought a call and the stock rose. Must the call profit?** No; premium, time, IV and spreads matter.
6. **A setup expired without a fill, then rallied. Should you book a paper win?** No; record the missed opportunity separately.

Use the [audit](audit.md) to understand current limitations and the [architecture plan](architecture.md) to distinguish planned improvements from features already available.
