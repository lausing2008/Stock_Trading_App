# Options decision desk and screened contract leads — October 25, 2026

## Request and observed problem

The user supplied an Options tab screenshot with lengthy evidence blocks preceding the contract table and asked for a more professional decision view and the best option for each strategy by default. The current server shortlist is bounded and ranks primarily by strike proximity; it has no measured expected-return, fill, or personal account model. Per-contract quote observation time, displayed size and broker availability remain unavailable. A specific highest-return or executable "best option now" therefore cannot be established from these records.

## Delivered design

The **Options decision desk** places four **contract research leads** near the top, one for each common strategy. A lead is a **default highlight for review**, not an automatic strategy change or order. Its card shows strike, expiry, archive debit/credit, displayed spread, OI and delta. One explicit **Compare this contract** action loads that source-indexed contract into the existing comparison, marks its table row, and clears assignment acceptance. Existing budget, holdings, event, preference, snapshot and WAIT checks still decide goal fit. The source is labelled provider capture or settled archive; expired captures show no lead.

For each strategy, the client examines only the original and the API's returned alternatives. It refuses an unpriced original and any candidate with the wrong leg/side, unreadable identity, missing or non-positive OI, missing or unusable two-sided quote, spread outside the existing 20% research screen, inconsistent premium, invalid/expired expiry, invalid payoff, or a DTE/absolute-delta preference conflict. The planned holding period plus two calendar days must fit. Failed expiration reads suppress the lead. Edited preferences suppress it until the server acknowledges a matching search. This is a **subset of supplied contracts**, never a claim that the entire market was searched.

Among qualifying rows, the deterministic order is **narrowest displayed spread → highest measured OI → strike distance to the strategy anchor → earlier expiry → contract identity**. This is a transparent quote-quality policy. It does not treat delta as probability, gamma as a universal optimum, maximum profit as expected profit, or a narrow spread/OI as proof of a good fill. A user's declared objective can produce the existing conditional strategy fit; the new per-strategy leads do not override it. Detailed dated signal and event tables are collapsed behind accessible disclosures; their critical status, dates, filters and early-assignment warning stay visible.

The [Options & Dark Pool Tutorial](/options-dark-pool-tutorial) and Improvements tracker explain the flow. This report and the [session index](../2026-10-24/session-index.md) record the boundary.

## Why "best for me" remains conditional

OIC describes a covered call as stock downside exposure with capped upside, and says strike choice trades off premium against the sale price ([covered-call strategy](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write)). OIC also explains that a tight quoted spread and displayed size are liquidity evidence and that OI alone can mislead ([liquidity FAQ](https://www.optionseducation.org/referencelibrary/faq/general-information)). These support displaying contract quality and risk separately. They do not validate a return-ranking formula.

To make a personal selection or order, the app would need current broker quotes and size, exact deliverables, the user's stock cost basis and account permissions, available capital, assignment willingness, verified event timing, portfolio concentration and a strategy-specific objective. To rank expected net returns it would additionally need validated forward outcome probabilities, realistic spread/slippage/fees and held-out replay across the **complete** candidate set. The screen states WAIT until those checks. A provider capture may be newer than the archive but is still not a simultaneous executable quote.

## Verification and release

Frontend: **985 tests in 83 files**, full TypeScript check and `git diff --check` passed. Unit regressions cover deterministic ties, quote/OI/expiry/preferences screens and incomplete reads. A component regression confirms a visible lead does not select a contract or override WAIT; its explicit Compare action preserves the source index. Native offline Chrome checks at **1449, 1024 and 390 pixels** found four visible lead cards, collapsed evidence tables and no page/card overflow. The mobile check caught and resolved an overflowing contract dropdown. Visual fixtures use fictional quotes, so they do not prove a production contract recommendation.

See the separate evidence directory `docs/audits/evidence/2026-10-25-options-research-leads/` for tests, visuals and rollout records. Deployment status is recorded after the pinned production check, rather than inferred from the tracker status.

## Production result

Application commit **`ad287b04c823eb325f4946cf4e704bed82dbc287`** was pushed to public `prod` and deployed as a fresh EC2 production frontend build, build ID **`O7Rjp_UeR9xzd5NDLNmr3`**. The pinned deploy kept a rollback image and passed its health gate. A separate read-only verification found **15/15 healthy containers**, the **14 non-frontend start times unchanged**, all **12 backend source/shared hashes matching**, and HTTP 200 for home, MU stock, Improvements and tutorial. Served JS/CSS markers confirm the decision desk, lead buttons, tracker/tutorial wording and responsive rules are in this build. Host free space was **29 GB** after deployment cleanup.

This was a frontend-only release. No backend restart or data-feed change occurred. Verification checked served assets and an offline rendered interaction fixture; it did **not** include an authenticated production click-through or prove that a highlighted contract is the user's best net-return trade.
