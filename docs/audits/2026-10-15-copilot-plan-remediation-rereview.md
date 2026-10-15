# Independent re-review: Copilot Plan C1–C5 remediation

Reviewed October 15, 2026, against the uncommitted research-engine changes on top of `ebed80d3`. The previous plan implementation is `410a238c`. This review leaves application code, the original independent review, and unrelated price-risk changes untouched. Reviewed source hashes are saved with the evidence because the fixes do not yet have a commit ID.

**Decision: request changes on C2, C3 and C4 before deploying this plan slice.** The original counterexamples now pass the regression tests, and C1/C5 behave as specified for the reviewed cases. Nearby cases still reproduce the same classes of failure in options exclusion, amount handling and forecast grounding. No paid model calls or production access were used.

## Status of the five original findings

| Finding | Re-review status |
| --- | --- |
| C1: a calculated stop was presented as a maintained stop | Addressed for the reviewed scenarios. Repeated snapshots show candidates and an unknown current stop; a supplied held stop is kept or raised and the code reports the daily-close condition. `stop=` is passed through the deterministic endpoint. The accepted stateless stop contract is preserved. See C3 below for malformed/percentage stop input. |
| C2: options positions received stock rules | Original examples fixed; still incomplete for “I have … calls” and “long calls,” below. |
| C3: `$10k` became `$10` | k/m examples fixed; units, malformed numeric tails and conflicting budgets still need handling, below. |
| C4: unrelated condition words permitted predictions | Original condition examples fixed; unrelated negation words still permit predictions, below. |
| C5: an exit below entry was called taking profit | Addressed for the reviewed loss, breakeven and gain scenarios through packet and deterministic endpoint. Below/equal-cost levels are distinguished from profit-taking. |

## R-C2 — High: ordinary options wording still receives stock rules

Locations: `services/research-engine/src/copilot/intent.py:297–309`.

Two independently reproduced cases using the actual parser, packet, rule generator and fallback key selection:

- **“I have 2 AAPL January calls at 50. What should I do?”** returns `unsupported=False` and E1–E4/E6 stock entry/sizing rules. `_POSITION_VERB` omits `have`, so the derivative words do not activate the guard.
- **“I bought long calls at 50. What should I do?”** returns `unsupported=False`, entry 50 and both entry and holding stock rules. The case-insensitive `_PLATFORM_CALLS` exemption removes “long calls” before derivative detection, treating ordinary options wording as a platform horizon reference.

The rule keys also remain available in the deterministic fallback. By contrast, the original “I bought 2 AAPL January calls …” is correctly refused, and an explicit stock holding followed by “SWING calls” still receives stock rules.

Required: recognize natural possession statements and narrow the platform-call exception to demonstrable signal context. Ambiguous “long calls” in a position statement should not silently become stock. Add the two failing cases at packet/fallback boundaries alongside the existing SWING-call acceptance case.

## R-C3 — High: the shared money reader still accepts incomplete values and ignores units

Locations: `services/research-engine/src/copilot/intent.py:315–333`, risk parsing at `:439` onward, and typed-stop parsing.

`money_amount()` matches an initial amount but does not validate the remainder as part of the amount/unit. The percentage-risk parser only recognizes the word “risk,” while the “can lose” path accepts the number as currency:

| Question | Actual result on the hand-computed fixture |
| --- | --- |
| I want to buy. I can lose 10% | Risk **$10**, no refusal, sizes **5 shares** |
| I want to buy. I can lose 10%, account 50000 | Again risk **$10**, no refusal, **5 shares** |
| I want to buy, risk $1.2.3k | Risk **$1.20**, no refusal; the remaining `.3k` is ignored |
| I bought 10 shares at 95, my stop is 5% | Treats the user's held stop as **$5**, with no refusal |
| I want to buy, risk $500, actually risk $1000 | Silently takes the first budget, **$500**, without resolving or refusing the conflict |

The original `$10k` case correctly produces a $10,000 budget and 5,000 shares; the fix works for that example but does not yet establish whole-amount parsing.

Required: parse a complete typed quantity with explicit units, reject malformed suffixes/tails, and apply the same unit policy to every grammar branch. A percentage without a defined account-size interpretation must be refused, not converted into dollars. A percentage stop must be rejected or explicitly supported as a different input, never labeled as a held absolute price. Collect multiple budget statements and refuse conflicts or support an explicit, tested correction syntax. Test the final sizing and held-stop output, not only numeric parser values.

## R-C4 — Medium: an unrelated negation still exempts an asserted forecast

Locations: `services/research-engine/src/copilot/grounding.py:163–173`.

The checker now correctly rejects the original “If you are wondering … the stock will rise” and accepts “I cannot say whether it will rise.” However, it exempts any nearby `no`, `not` or `cannot` in the same punctuation-delimited clause without establishing what is negated. Both of these pass `G.check()` with an otherwise valid `{{call.swing}}` citation:

- **“There is no doubt the stock will rise.”** This strengthens the prediction rather than negating it.
- **“I do not own shares and the stock will rise.”** The negation applies to ownership, while the price prediction remains asserted.

Required: tie allowed negation to an explicit denial of the prediction/evidential claim, rather than any preceding negative word. A conservative set of supported denial forms is sufficient for this bounded product. Preserve the valid “cannot say whether …” case; add the two counterexamples through the checker and repair/fallback path.

Also update the rejection hint at `grounding.py:322`: it still suggests that the model may write “a condition ('if a close is above …')”, contradicting the new policy that conditions come only from code-written rule quantities. The hint should direct repair toward those keys and the allowed call/record description.

## Additional observations

- The revised H6 says the platform does not retain a stop and uses an explicitly supplied stop as evidence. Keep that wording in the Game Plan UI; do not reintroduce an unlabeled “active stop” from the candidate alone.
- The design's new stop contract should also be reflected in the older H4/H6 descriptions in §2.4, which still describe a current/active stop without the candidate distinction.
- The newly requested held-out and regression model-run results were not available as new artifacts in the repository during this review. They were not rerun here. Good model output does not eliminate validator counterexamples, but rejection/repair/fallback rates remain relevant after these fixes.

## Validation and reproduction

- Existing focused plan suite: **68 passed** on disposable SQLite, including the C1–C5 regressions.
- Full research-engine suite: **689 passed, 8 PostgreSQL-only checks skipped**, in 27.86 seconds. An initial repository-root invocation could not import `src`; the passing run uses the same service-directory convention as `make test`.
- New counterexamples: [probe](evidence/2026-10-15-copilot-plan-rereview/probe.py), [results](evidence/2026-10-15-copilot-plan-rereview/results.json), [reviewed source hashes](evidence/2026-10-15-copilot-plan-rereview/source-sha256.json).
- Run from repository root: `python docs/audits/evidence/2026-10-15-copilot-plan-rereview/probe.py`. It clears `STOCKAI_INTEL_DB` within its process, uses the shipped fixture harness on a disposable SQLite database, and invokes the actual parser, packet, plan and grounding code. No provider request is made.
- PostgreSQL, sabotage tests, frontend and the full 12-service suite were not rerun by this review. No changes were committed, pushed or deployed.
