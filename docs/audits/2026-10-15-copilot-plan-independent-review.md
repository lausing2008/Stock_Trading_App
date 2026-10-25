# Independent review: Copilot Plan P1, commit 410a238c

> **Completion review — 2026-10-24:** C1–C5 are resolved under the user-stated/candidate-stop contract; current plan acceptance passes 95 tests. The card and validated LLM proposal workflow now exist. See [current review and remaining work](2026-10-24-alerts-plan-workspace-completion.md). The dated record below is preserved.

**Resolution (2026-10-15):** implemented and deployed in `89756b83`; see
[final remediation and verification](2026-10-15-copilot-plan-final-remediation.md).
The findings below are the historical review record.

Reviewed 2026-10-15. Scope: forecast/playbook backend, its request parsing, grounding,
deterministic rules, endpoint and evidence storage. This review did not deploy, push, run paid
model evaluations, or change application code. The Game Plan frontend remains outside this
slice. Existing work on price-risk files was left alone.

## Decision

Request changes before deploying P1 or building the Game Plan card on its current contract.
The architecture is implemented: code-generated rules, horizon-specific calls and historical
records, a deterministic endpoint, evidence deduplication and a deterministic answer fallback.
However, five independently reproduced behaviors contradict the reported acceptance claims.

## Findings

### C1 — High: trailing stops can fall between requests

`services/research-engine/src/copilot/plan.py:744` resets `active = tight` on each calculation.
It compares today's trailing candidate only with today's entry/structure/breakeven candidates.
There is no previously established stop or position-entry time in the request or persisted
plan state. The “never moves down” guarantee therefore holds only inside one calculation.

Reproduction using the shipped plan probe's rising-price fixture and the same typed position
(`I own 10 shares, avg cost 200`):

- Initial latest close 230: H6 active stop **226.00**.
- Change the latest bar to close 225, low 224, high 226 and recalculate: H6 active stop
  **220.50**, while still saying “A stop is not moved down to avoid it.”

This uses a revised snapshot to demonstrate the stateless contract; it does not assert a
particular production price path. A subsequent request likewise has no prior stop input.
The existing test covers a rising candidate within one snapshot, not two successive plans.

Required: accept and validate a user-confirmed prior stop (with provenance), or establish
position-scoped stop state and update it monotonically. If P1 remains stateless, label this a
candidate and explicitly say the current stop is unknown; remove claims of maintaining it.
Test rising and falling prices, changing ATR and repeated requests with an established stop.

### C2 — High: unsupported options positions receive stock rules

`intent.py:287` permits only one intervening word before calls/puts/contracts, so
`I bought 2 AAPL January calls at 50. What should I do?` is accepted as a long stock holding
with entry **50.00**, no refusal, and H1–H8 stock rules (apart from unavailable earnings).

Even a recognized unsupported request is not actually blocked at the plan builder:
`I bought calls at 5, what now?` produces a refusal but also E1–E4/E6 long-stock entry rules.
`plan.py:396` unconditionally builds entry rules for entry/holding kinds when levels exist.
The current test asserts that unsupported requests have no H rules; it does not assert that
they have no stock rules at all.

Required: carry an explicit unsupported-position state into `build_plan()` and suppress stock
position/entry/sizing rules. Make derivative detection handle ordinary symbol/expiry/strike
wording, with narrow tests that still allow references to the platform's BUY/SELL calls.
Check both copilot packets/fallbacks and the eventual Game Plan request contract.

### C3 — High: abbreviated risk amounts are silently truncated

`intent.py:397` reads a numeric prefix for dollar risk without checking a trailing magnitude
suffix. `I want to buy, risk $10k` becomes **risk_amount=10.0**, with no refusal. On the probe's
100 close / 98 stop fixture, E6 prints **5 shares ($500)** from a **$10** risk budget.

Required: tokenize the whole amount and either explicitly support suffixes or refuse them.
Apply the same rule to every risk grammar branch, including “can lose”, and reject conflicting
or malformed amounts. Test `$10k`, `$1.5k`, malformed thousands and ordinary complete numbers
through the real packet and sizing path, not only the parser.

### C4 — Medium: an unrelated conditional phrase bypasses forecast grounding

`grounding.py:162` skips the entire sentence if any condition word is present, regardless of
its relationship to the predicted move. Both of these passed the actual `G.check()` with a
valid `{{call.swing}}` citation appended:

- “If you are wondering about next week, the stock will rise.”
- “When considering the evidence, it will rally.”

These are unconditional price predictions with introductory clauses. The reported guarantee
that unconditional predictions are refused is broader than what the checker establishes.

Required: do not exempt a whole sentence based on an arbitrary condition word. Limit accepted
conditional language to supported price conditions or render conditional rules deterministically;
refuse ambiguous model-authored forecast assertions. Add positive/negative neighboring cases
through `G.check()` and service retry/fallback behavior.

### C5 — Medium: a loss-making exit is called taking profit

`plan.py:764` chooses a profit-taking target by comparing the current close with stored highs,
without checking the typed cost basis. With the standard fixture, close **100**, typed entry
**104**, and prior high **101**, H5 says to take partial profit at **101**. That is a loss before
fees. The existing losing-position fixture checks H1–H3 but omits H5.

Required: compare target levels with the typed entry. Label exits below cost as loss reduction
or resistance review, and distinguish them from profit-taking targets. Test loss, breakeven
and profitable positions through both the packet and deterministic endpoint.

## Validation and limits

- Existing P1 suite: **58 passed** locally on SQLite.
- Additional reproductions: the five findings above used the shipped fixture setup, actual
  `parse_position()`, `build_packet()` and `G.check()`. No alternative rule implementation or
  model output was substituted for the behavior under review.
- The local reproduction script is `/tmp/copilot-410-review.py`; durable inputs and outputs are
  recorded above. It creates only a temporary SQLite database.
- The implementer's PostgreSQL, sabotage and paid-model results are useful supplied evidence;
  they were not rerun as part of this review. Their coverage does not establish the missing
  behaviors above. Tuned evaluation results remain tuned, rather than new held-out evidence.
- Full research-engine suite: **679 passed, 8 skipped** in 23.83 seconds. The skips are
  PostgreSQL-only checks; no new PostgreSQL run was performed. `git diff --check` passed.

## Next steps

1. Resolve C1–C5 with tests at the actual request/plan/grounding boundaries. Keep deployment on
   hold for this slice until they pass.
2. Settle the stop-state contract before P2: the card must distinguish a calculated candidate
   from an established protective stop. It must render refusals, stale inputs, historical
   record windows and provisional evidence clearly.
3. Rebuild the Game Plan card on the corrected deterministic endpoint, then add chart levels
   and user-confirmed alert drafts. Conditional daily-close exits need appropriate alert
   semantics; an intraday crossing alert is not proof that a daily-close condition occurred.
4. Run targeted regressions plus the required suite once after the fixes. Use new held-out
   model questions only if grounding/routing behavior changes; no paid rerun is needed merely
   to reproduce deterministic arithmetic/parser defects.
