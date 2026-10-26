# Four-strategy preset option shortlists — October 26, 2026

## Why only Covered Call showed alternatives

The default `build_strategy_matrix()` response populated `candidate_review` for **Covered Call only**. The selector opened on Covered Call, and the one comparison table showed only the currently selected strategy. An explicit **Find expirations for holding period** request already produced reviews for all four strategies, but that was a separate user action. The three returned Covered Call alternatives were the cap for its one preset expiry; they did not mean it was the best strategy or that the other three had no contracts.

The four strategy cards independently used **nearest-strike preset references**. These were never an expected-return optimum. The newer research-lead cards used a disclosed quote-quality order within whatever bounded candidate reviews were available. When only Covered Call had a default review, the other lead cards could not assess their default alternatives. This is an availability and labeling gap, not evidence that Covered Call wins.

## Change

- Build the initial `candidate_review` for Long Call, Protective Put, Covered Call and Cash-Secured Put from the **same call and put chains already fetched** for the plan. Calls use the selected call expiry; puts use the independently selected put expiry. Each screen keeps its existing basic quote/payoff rules, original reference, rejection counts, stable rank and **at most three** alternatives. No extra provider request, full-chain optimization or automatic replacement is introduced.
- The default shortlists identify their scope as the selected strategy expiry. The wider explicit search still reads the earliest four eligible expiries with separate failure/coverage evidence.
- The selector shows each strategy's returned count. The table summary names its active strategy. Cards say **preset nearest-strike reference** or **explicit comparison choice**; the research-lead highlight remains separate. The source, account, assignment and WAIT checks remain in place.

## What “best” means here

The preset contract is **not** the best option. The research lead is the narrowest displayed spread among qualifying returned candidates, followed by OI, anchor distance, expiry and identity. It is a starting point for comparing quote quality, not the contract with highest expected net return or a personalized trade recommendation. OIC notes that a narrow bid/ask spread and displayed size are liquidity evidence, while OI alone does not guarantee a fill ([OIC liquidity FAQ](https://www.optionseducation.org/referencelibrary/faq/general-information)). Strike choice also changes covered-call premium, upside cap and assignment tradeoffs ([OIC covered-call guide](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write)). This UI does not observe quote time/size or the user's exact broker and position data.

## Validation and release boundary

Full market-data: **5,932 passed / 27 skipped**. Full frontend: **988 passed / 83 files**. Full TypeScript and whitespace checks passed. Focused backend checks and current-snapshot tests: **85 passed**. The new backend test verifies four initial review keys, correct call/put and buy/sell sides, three-candidate caps, original preset preservation and research-only eligibility. The route test verifies all four reviews without extra reads. The UI regression switches between separate shortlists and checks count labels.

This is **validated local code pending deployment**. The separately documented [RZY ingest-observation release](../2026-10-25/rzy-ingest-observations-and-session-evidence.md) was verified deployed while this implementation was in progress. Its **October 26 US-session evidence capture** is waiting for the post-close job. Restarting market-data during this session would cut the capture's source continuity. The option change is therefore held until that capture completes and verifies its artifacts; no production behavior is claimed here.
