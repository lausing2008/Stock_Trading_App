# Four common option strategies and conditional playbooks

The stock Options tab compares long call, protective put, covered call and
cash-secured put. All four remain visible when a contract is missing, with an
unavailable explanation and a management checklist. Existing combinations remain
available in a disclosure. Existing Advanced/admin access rules are unchanged.

Each priced strategy has its contract strike/expiry, archive premium, expiry
breakeven, capital, maximum expiry profit/loss, entry condition, profit objective,
exit/invalidation rule and before-entry/while-open/expiration playbook. Expiry payoff
diagrams and leg details expand on demand. No additional provider or model call is
introduced. The request now runs without a stock stop or analyst target; those
missing references previously hid the entire comparison unnecessarily. Late
responses from another symbol are ignored, and inputs reset on symbol changes.

## Conditional preference

The browser deterministically compares the four server-priced structures using the
declared objective, unencumbered shares, reserved assignment cash, premium-loss
budget and assignment acceptance. These inputs are temporary, not broker-verified.
The preference is not the existing broader combination ranking: it deliberately
compares only these four common strategies and never treats a missing objective as
a bullish view. The displayed AI call is context only and does not set the objective.

- Upside: long call first; with known IV rank >=70, cash-secured put first.
  Both still need the relevant premium budget or cash and assignment acceptance.
- Income: covered call first with at least 100 unencumbered shares and assignment
  acceptance; otherwise cash-secured put if the full strike × 100 is reserved.
- Protection or bearish: protective put only with at least 100 shares and a premium
  budget. A bearish view without shares returns no fit among these four.
- Compare-only: no preference until an objective is chosen.

Last-trade-only prices, missing/two-sided spread evidence, spreads over 20%, expired
contracts, inconsistent prices and nonpositive maximum payoffs cannot win a
preference. The response chain date must be known, not future and at most seven
calendar days old. These are conservative research admission policies, not measured
profitability or execution thresholds. IV rank is used only when supplied and valid;
the current interactive backend normally supplies no IV rank, which is stated.

Every preference is labelled **Preferred strategy to research**, with its reason.
**Entry status remains WAIT** for fresh quotes and broker checks. The current backend
returns settled Unusual Whales prices and a response-level chain date, not independently
verified live per-leg timestamps. Underlying quote freshness, standard deliverables,
earnings/dividend coverage, existing encumbrances, permissions, fees and portfolio
risk are not verified. No probability, live executable price or trade quantity is
claimed. The UI makes no enrollment, order or delivery change.

## Exit illustrations

The long-call example shows premium TP1 +50%, TP2 +100%, and an exit review at -50%,
all recalculated from the actual fill. The full debit can still be lost. Short
options illustrate a buyback review after 50% of the original credit is retained;
loss can exceed the credit. These are labelled illustrative policies, not backtested
parameters, forecasts, guaranteed fills or maximum-loss stops. The protective put
uses a stock-protection objective and hedge reassessment, not a premium profit
target or premium stop that would remove insurance. Stock invalidation and analyst
targets are separately labelled; their horizon may exceed option expiry.

Mechanics references: [OIC long call](https://www.optionseducation.org/strategies/all-strategies/long-call),
[protective put](https://www.optionseducation.org/strategies/all-strategies/protective-put),
[covered call](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write),
[cash-secured put](https://www.optionseducation.org/strategies/all-strategies/cash-secured-put).
The percentage exit policies above are this application's examples, not a claim
that OIC recommends those thresholds.

## Validation and release

Full frontend suite: 660 tests passed across 49 files. Focused coverage checks the
four strategy objectives, the 99/100-share boundary, assignment/cash/premium gates,
bearish compatibility, absent IV, old/future dates, last-only/invalid/wide quotes,
expiry, exact exit arithmetic, missing contracts, fetching without stock levels,
unavailable-feed cards and late symbol responses.

TypeScript and mocked desktop/390px phone browser acceptance passed: all four
strategies, declared-budget preference, 99/100-share boundary, bearish hedge,
missing-holdings wait, no horizontal overflow or runtime errors. The runnable
fixture is `scripts/smoke_options_strategy_plans.py`, with a frontend server on
port 3031 using `NEXT_PUBLIC_API_URL=/api`. It intercepts all APIs; no model calls,
emails or broker operations occur.

[Desktop preview](assets/common-options-playbooks/options-strategy-desktop.png) ·
[Phone preview](assets/common-options-playbooks/options-strategy-mobile.png).
These screenshots use sample data.

Release build and production verification are recorded after execution.
Deployment is frontend-only, built off the production host.
The release also contains the preceding full-width chart/Game Plan workspace commit
`5ab91daa`, which had not yet been deployed. Alert delivery stays off.
