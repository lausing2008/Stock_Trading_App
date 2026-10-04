# Intelligence reports: interpretation and decision support

Date: October 3, 2026. **Status: proposed product requirements and implementation plan.** This document adds no runtime code, flags, jobs, emails or trading authority.

Companions: [report templates](2026-10-02-intelligence-report-templates.md), [LLM narration architecture](2026-10-03-evidence-grounded-report-narration-design.md), [momentum horizons and monitoring](2026-10-03-momentum-horizons-and-monitoring-tasks.md), [measurement framework](2026-09-30-measurement-framework-and-improvement-backlog.md).

## 1. Problem and intended outcome

The user's feedback: the reports show statistics and numbers but do not identify what deserves attention, what could happen next, or what may affect market and stock direction. Accurate tables are necessary but insufficient. The reader should not have to assemble an investment thesis from dozens of disconnected fields.

After the first screen, the reader should understand:

1. What the evidence currently supports, for which instrument and horizon.
2. Which developments matter most and through what plausible mechanism.
3. What competing scenarios could follow.
4. What observations or upcoming events would change the assessment.
5. What cannot be concluded from the available evidence.

This is an interpretation layer over the existing report evidence. It does not establish a profitable edge, supply execution permission, or replace the existing signal, risk, paper-trading or broker engines. Better writing must be evaluated separately from better forecasts and returns.

## 2. Required reading order

| Section | Required content |
|---|---|
| **Assessment** | Concise conclusion, instrument, snapshot cutoff and horizon; distinguish observed structure from conditional outlook and validated model forecast. Mixed or insufficient evidence is a valid answer. |
| **Top drivers** | Up to three material developments; evidence, change from a comparable baseline, possible transmission mechanism, contrary evidence and why each merits attention. |
| **Scenarios** | Bull/base/bear or other appropriate alternatives; measurable activation conditions, invalidation, horizon and unresolved dependencies. No unsupported probability or price target. |
| **What to watch** | Prioritized events and observations, specific trigger definitions, expected availability and what the observation would change. |
| **Limitations** | The few gaps or conflicts that actually constrain this assessment. Critical identity or comparability failures remain visible at the top. |
| **Supporting evidence** | Primary metric tables, full calculations, cited documents, methodology, missing dimensions and version history. Expand detail without hiding contrary evidence. |

The opening should normally fit a desktop viewport without opening the evidence tables. Start with a short paragraph and three driver bullets, not every populated field. On mobile, keep the same order and use stacked scenario cards.

Avoid repeating all source figures in multiple sections. A claim should link to a metric or evidence record. Show one section heading with temporal subheadings, rather than repeating “Key figures” or “Analysis limitations” for every group.

All relative wording is anchored to the **saved snapshot**, not the viewing date. Replace “today” and “now” with an explicit cutoff or “at this snapshot.” Distinguish report generation, last check, latest market session and each input's availability. Reusing a report does not refresh its inputs.

## 3. Four distinct report templates

### Market Outlook

**Main question:** Is the observed market structure strengthening or weakening, how broad is participation, and what could change it?

- Compare benchmark structure with participation and leadership. Distinguish watchlist breadth from index-constituent breadth, and coverage from participation.
- Explain whether leadership is broad or concentrated; qualify sectors represented by very few stocks. Do not turn watchlist sample means into claims about all institutional capital.
- Incorporate available volatility, rates, macro releases and liquidity measures with their timestamps and limitations. Do not replace unavailable series with an unlabeled proxy.
- Present the next relevant catalyst and conditional responses. Existing price structure alone does not establish a multi-horizon forecast.

**Opening template:** “At [cutoff], [benchmark] shows [observed structure]. Participation is [measurement and universe], while [leadership/counterevidence]. This supports [bounded interpretation]. Over [horizon], watch [condition/event]; the outlook would weaken if [invalidation]. [Missing input] prevents a stronger conclusion.”

### Stock Outlook

For a detailed market-to-stock transmission framework, see §9. Market drivers should inform company analysis through measured exposure, not through an assumption that every stock follows the benchmark.

**Main question:** What is specific to this company, what may reflect the sector/market, and what evidence would support continuation or reversal?

- Compare stock returns with aligned sector/benchmark returns over the same sessions; label simple relative returns separately from any estimated factor attribution.
- Explain company catalysts, comparable results/guidance, news and valuation where supported. Mere timing or correlation does not prove the cause of a price move.
- Connect price structure and available options information to competing hypotheses. Options activity alone does not establish buyer intent or dealer positioning.
- Keep the existing engine's signal, horizon and uncalibrated score distinct from the report's assessment. A disagreement should be explained, not silently resolved in favor of BUY or SELL.

**Opening template:** “[Company] shows [observed structure], with [relative performance] over [window]. The main supported developments are [drivers]. Evidence favors [conditional interpretation] only if [confirmation]; [counterevidence] would undermine it. [Missing information] remains unresolved.”

### Pre-Earnings

**Main question:** What expectations must the company meet, what assumptions are vulnerable, and which combinations of results and guidance would matter?

- Freeze comparable consensus and prior guidance, including target period, units, basis and availability. If unavailable, describe preparation rather than claim an actionable earnings forecast.
- Identify the metrics that would test the thesis and concrete questions for management.
- Separate an early preparation snapshot from the immediate pre-release baseline. Do not present an October price as the December pre-announcement reference without qualification.
- Describe alternative result/guidance combinations and observable confirmation. Any options-implied move must retain its quote time, expiry, construction and limitations.

**Opening template:** “This is [early preparation/frozen pre-release baseline] for [verified or scheduled event]. The key expectation is [comparable baseline or unavailable]. The most consequential questions are [questions]. A constructive case requires [conditions]; [alternative] would weaken it. Event-specific direction is [unresolved/conditional], not inferred from the current BUY label.”

### Post-Earnings

**Main question:** What changed in the business and expectations, how did the market respond, and do those messages agree?

- State actuals versus comparable frozen expectations; if only current estimates exist, do not call them verified pre-release consensus.
- Present current guidance independently of guidance change. A raise requires previous comparable guidance for the **same target period**.
- Explain material margin, cash-flow, working-capital or operating changes only with comparable evidence. A double beat does not prove organic growth; operating cash flow above net income does not alone establish earnings quality.
- Separate results, forward outlook and observed price reaction. “Priced in,” profit-taking, and positioning are hypotheses unless stronger evidence supports attribution.
- Describe return baseline/endpoints exactly. Close-to-close returns spanning a release are not necessarily isolated announcement reactions.

**Opening template:** “[Issuer] reported [verified facts]. Relative to [baseline and limitations], [comparison]. Current guidance indicates [sourced outlook], while whether it was raised is [supported/unknown]. Price moved [measured window], which [agrees/disagrees] with [stated interpretation]. Watch [follow-through conditions]; [counterevidence] could change that assessment.”

## 4. Interpretation contract and integration

Reuse the existing research-engine report generators, adapters, evidence records, store and API, together with the shared report contract. Add a versioned interpretation module within that flow; do not create a parallel trading engine or another provider ingestion stack.

Pipeline:

`saved report + evidence → eligibility/comparability checks → material findings → conditional assessment → deterministic summary → optional validated LLM narration`

Proposed interpretation fields:

| Field | Purpose |
|---|---|
| `report_id`, `interpretation_version`, `policy_version`, `cutoff_at` | Reproduce the exact analytical inputs and policy. |
| `assessment` | Statement class, horizon, conclusion, supporting/contrary evidence IDs and unresolved dependencies. |
| `drivers[]` | Observed change, baseline, relevance rationale, proposed mechanism, alternative explanation and evidence IDs. |
| `scenarios[]` | Conditions, invalidation, horizon, measurable threshold references and unavailable inputs. |
| `watch_items[]` | Observable, source, trigger, evaluation cadence/session, expiry, and the assessment it would change. |
| `limitations[]` | Missing/conflicting input and precisely which conclusion it prevents. |

Select findings through explicit, versioned rules. Prioritize new material changes, source quality, target-period relevance, freshness and decision relevance; expose the selection reason. Repeated articles describing one event count as one development, not independent confirmation. Initial rankings are editorial policies, not estimated financial impact or calibrated probabilities. Incomplete data should reduce claim scope rather than silently count as neutral evidence.

Dependencies must be enforced: missing units/basis block beat claims; missing comparable prior guidance blocks “raised”; unresolved announcement identity blocks reaction attribution. Statement type and causal confidence must be visible. Evidence that price and news changed together permits a hypothesis, not certainty about causation.

Monitoring suggestions are proposed tasks only. Creating subscriptions, sending alerts or enabling trading remains a separate action through existing controls. A stored watch item should define its expiry and rearm behavior before it becomes an actual monitoring rule.

## 5. Deterministic and LLM responsibilities

The deterministic layer owns numbers, comparison eligibility, timestamps, evidence selection, trigger definitions and all trading/risk constraints. It must deliver a useful assessment without an LLM whenever the rules support one.

The optional LLM explains supplied findings, summarizes management statements with citations, discusses supported alternative explanations and improves readability. It cannot invent missing data, select arbitrary support levels, create forecast probabilities, strengthen a tentative association into a cause, or issue account-qualified trade instructions.

Use the existing [narration design](2026-10-03-evidence-grounded-report-narration-design.md) for packet, provider, validation, budgets, flags and persistence. Structured claim prerequisites are the primary guard; verb matching is supplementary. Validation failure falls back to the deterministic report. Do not force post-earnings narration merely because it is the initial candidate: each report's eligibility depends on its actual evidence packet.

## 6. Illustrative MU opening

This example uses numbers from the user's supplied screenshot. It is an illustration of report wording, **not a current market assessment or an independently verified forecast**.

> **Longer-window strength, recent weakness; near-term direction unresolved.**
>
> At this snapshot, MU is above its 20- and 50-bar averages. It gained 12.18% over 20 bars, but declined 2.05% over the latest bar and 0.68% over five bars. This describes recent weakness within a stronger longer-window price structure; it does not establish whether the pullback will reverse or deepen.
>
> **What matters next:** whether that structure holds, how semiconductor peers behave over the same sessions, and how newly issued guidance compares with expectations that were actually available before the release.
>
> **Limit:** without comparable pre-release expectations, management commentary and relevant positioning evidence, this report cannot confidently explain the decline or call it a buying opportunity.

The live implementation must derive wording from the selected snapshot and its real limitations. Do not hardcode this example or continue reporting a missing input after it has been validated and incorporated.

## 7. Delivery sequence and acceptance

1. **Interpretation contract and deterministic opening.** Implement the first-screen structure across four report types using available evidence. Partial reports remain useful; missing optional data must not block all interpretation.
2. **Material driver integration.** Join the most valuable supported news, guidance, sector and market inputs; make conflicts and attribution limits visible. Fix identity/units defects before using affected inputs.
3. **Offline N1 evaluation, then shadow narration.** Use frozen packets and the existing narration rollout plan. No flag activation or publication is implied by this document.
4. **Browser acceptance and controlled publication.** Verify snapshot-relative wording, evidence links, old versions, correction pointers, desktop/mobile layout and deterministic fallback.
5. **Prospective outcome measurement.** Freeze conditions and horizons before outcomes. Measure any incremental decision value separately from readability; do not tune policies against the same evaluation cohort.

Required behavioural cases:

- Reopening an older report preserves its cutoff and never relabels its signal “today.”
- Missing consensus blocks a beat claim but still permits verified actuals to be explained.
- Current guidance without a comparable prior forecast produces no raise claim.
- Positive results with a negative price response expose disagreement without asserting “priced in.”
- A rising benchmark with weak covered-universe participation produces qualified divergence, not unanimous bullish evidence.
- Mixed technical windows produce an appropriately scoped assessment, not five copies of one daily trend.
- Contradictory provider/issuer facts cannot yield an authoritative conclusion until reconciled.
- Missing/stale options data cannot generate dealer-hedging intent or a squeeze prediction.
- LLM errors leave the saved evidence and deterministic summary usable, and cannot change trade eligibility.
- Browsers show the conclusion before long tables; every material claim can be traced to its source.

## 8. How to measure improvement

Evaluate four separate dimensions:

- **Evidence integrity:** citation coverage, unsupported claims, contradictions, invalid comparison attempts, timestamp/identity violations and correct abstentions.
- **Reader usefulness:** can the reader identify the assessment, strongest drivers, counterargument and next observation without inspecting raw fields? Measure task success and time against the current table-only report.
- **Operational quality:** availability, latency, narration cost, fallback rate, and data freshness by input—not merely report generation time.
- **Prospective decision value:** if scenarios or forecasts are evaluated, predeclare event/session grain, horizon, target, opportunity population, costs and missingness; report independent counts and uncertainty. Any later trading trial needs the existing risk/execution and experiment controls.

A larger number of resolved fields, a persuasive summary or a higher user rating does not establish forecast accuracy or profitable returns. The first product milestone is a reader who can explain the evidence-supported assessment and what would change it.

## 9. Market drivers: earnings, policy, inflation and shocks

Added following the user's market-trend framework. These dimensions belong in Market Outlook as evidence-backed, time-sensitive drivers, not generic economic commentary repeated in every report. Their relative importance depends on the market, horizon, expectations and prevailing conditions. This is a proposed analytical framework, not a diagnosis of the current market.

| Driver | Evidence to assemble | Interpretation and conditional transmission |
|---|---|---|
| Earnings and expectations | Comparable results, same-period guidance revisions, earnings-estimate revisions with snapshot history, index/sector weights and valuation context | Distinguish stronger cash-flow expectations from expanding valuation multiples; a beat may coexist with weaker guidance or a negative reaction. Index contribution needs actual weights; watchlist means do not establish it. |
| AI investment and adoption | Issuer disclosures on capital expenditure, demand, monetization, margins and cash flow; sector exposure and concentration | Treat AI as a potentially material theme, not a universal determinant of market direction. Separate spending by customers from revenue and profits earned by suppliers. Do not count the same theme repeatedly as independent confirmation. |
| Monetary policy and yields | Policy decision and communication, expectations frozen beforehand, yield-curve changes, nominal/real yields where available, credit conditions | Separate expected decisions from surprises. Lower rates may ease discounting and financing pressures, but a deteriorating growth outlook can offset that channel. Distinguish policy rates from long-term yields. |
| Growth, employment and inflation | Published data, original vintage, prior value/revisions, available pre-release consensus, wages, consumption and activity | Compare changes against expectations and prior trends. Cooling inflation with resilient growth differs from disinflation caused by collapsing demand. A missing consensus prevents a surprise claim, not reporting the actual release. |
| Energy and input costs | Oil/gas prices, inventories and supply evidence, issuer cost exposure, hedges, pricing power and margins | Determine whether the move is associated with demand, supply or uncertainty. Effects differ for producers, transport, industrial users and consumers; cost increases need not translate one-for-one into lower margins. |
| Geopolitics, trade and regulation | Authoritative announcement, publication/effective dates, proposed versus enacted status, affected jurisdictions/products and issuer exposures | Explain the plausible channel—supply disruption, tariffs, sanctions, revenue access or capital spending—and its timing. A headline or political label alone is not a directional signal. Avoid assuming a threatened policy is implemented. |
| Market confirmation and vulnerability | Aligned benchmark/sector returns, breadth, volatility, credit spreads, positioning evidence and source quality | Show whether market behavior corroborates or contradicts the thesis. Correlation is not causal proof; unavailable positioning is not neutral positioning. Valuation and concentration can describe vulnerability without predicting a crash date. |

### Required driver card

Each selected driver states:

1. **What changed:** observed value/event, units, source, publication and availability times.
2. **Compared with what:** frozen expectations or comparable previous observation; unknown where absent.
3. **Why it may matter:** explicit transmission mechanism and affected sectors/companies.
4. **Evidence for and against:** observable market response over aligned windows and plausible alternatives.
5. **Horizon and next trigger:** measurable observation that would strengthen or weaken the interpretation, with expiry/review timing.

Use supportive/adverse/mixed/unknown only as scoped interpretations of a specified channel. Do not mechanically add bullish/bearish votes into a market forecast or count correlated manifestations of one event as multiple independent drivers. Any aggregate directional model requires its own prospective evaluation and declared weighting.

### Example conditional analysis—not a current forecast

“If inflation is below the pre-release expectation, earnings expectations remain stable, and participation broadens, the evidence would be consistent with easing valuation pressure alongside resilient growth. If yields fall while earnings estimates deteriorate and credit spreads widen, the same yield decline would support a different, more cautious interpretation.”

This is a scenario with testable dependencies, not a rule that a rate cut or lower yield makes stocks rise. Near-term reports emphasize new surprises and response; multiweek reports emphasize follow-through and revisions; longer-horizon reports emphasize cash flows, financing and valuation assumptions. None acquires a forecast probability from prose.

### Data integration and geographic scope

Reuse existing macro-event, earnings, news and market-data components; inventory what is actually available before adding feeds. Economic releases need release/vintage timestamps and frozen consensus when available. Issuer financials need fiscal identity and accounting basis. Authoritative policy documents need status and effective dates. Archive source evidence and revisions; repeated retrieval is not new information.

For US reports, use relevant Fed and US macro inputs. For HK reports, add applicable HK/China policy, funding, currency and company exposure rather than copying a US-only explanation. Distinguish index-level weights from an ingested watchlist. Missing data should limit the affected driver, not force unsupported completeness.

Monitor earnings revisions, policy surprises, inflation/growth divergence, energy disruptions and sector participation through existing task contracts. No notification subscriptions or trading responses are activated by this design.

### Sources and interpretation limits

The financial mechanisms motivate what to measure; they are not evidence that a particular mechanism explains today's price move. Federal Reserve research explicitly notes the difficulty of identifying policy effects when policy and asset prices respond to other variables: [The Impact of Monetary Policy on Asset Prices](https://www.federalreserve.gov/econres/feds/the-impact-of-monetary-policy-on-asset-prices.htm). EIA describes the interacting supply, demand, inventory and spare-capacity influences on oil prices: [Oil prices and outlook](https://www.eia.gov/energyexplained/oil-and-petroleum-products/prices-and-outlook.php). These support conditional analysis rather than fixed directional rules. The user's supplied secondary articles are topic suggestions, not independently verified evidence for the current market state.

Acceptance includes expected versus unexpected policy changes, an energy shock with different issuer exposures, proposed versus effective tariffs, rate declines accompanied by deteriorating earnings, duplicated AI headlines, and missing pre-release consensus. All must produce scoped explanations rather than unconditional direction claims.
