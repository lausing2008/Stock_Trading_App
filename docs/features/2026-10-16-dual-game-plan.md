# Two Game Plan perspectives — 2026-10-16

**Status: implemented and validated locally, not pushed or deployed.** Implementation commit: `3a85df69`. GitHub returned HTTP 403 on two attempts to push `prod`; a read-only remote check confirmed it remained at `f8c21a79`. The deployment was not started. Tracker 450 remains in progress until publication and production verification. Repository write access must be restored, then push `prod` and deploy research-engine plus frontend under the host lock.

Tracker 450 adds two labeled panels to the stock-page Game Plan card:

- **Rules-based plan:** the existing deterministic rules, numeric levels, sources, daily-chart overlays, Board snapshot and explicitly confirmed alert drafts. No AI key, model call or copilot quota is required to build it.
- **LLM alternative plan:** the user's configured Claude or DeepSeek model can choose a preferred conditional scenario, explain waiting, identify invalidation and discuss the strongest alternative. Generation is explicit, uses the existing metered copilot and displays the actual responding model. It may agree with the first plan. Shared evidence means agreement is not independent corroboration.

“From you” is interpreted as the platform rules implemented with the assistant's help, not a separate live Codex analysis. Codex is also an LLM. The runtime identities are labeled by how they are produced.

## Evidence and behavior

The client sends structured position inputs and the displayed packet fingerprint through the existing authenticated `/copilot/chat` endpoint. One pure validation contract supplies both plan views. For a new generation, the backend rebuilds the packet and refuses a changed snapshot **before** creating a conversation, reserving an answer or calling a provider. Stale, refused, unsupported and derivative-position comparisons are also refused before admission. The model receives those typed inputs, not a lossy re-parsing of a generated sentence.

The LLM can prioritize a different existing scenario; it cannot create new price levels, change rule conditions, invent account facts or provide an unchecked price forecast. The existing evidence checker, at most one repair, fallback and daily/shared-budget controls remain in force. A rejected output is labeled as a failed model response and a stored-data fallback, not sold as a second plan. It can be reopened with its citations in normal Copilot history.

This is an alternative interpretation, not an independently calculated numerical strategy or evidence that either approach predicts returns. There is no forced disagreement or claim of improved accuracy. The rules-based actions retain their existing explicit confirmations. Comparison responses cannot propose alert/watchlist drafts, even if a model emits draft tags. No orders, enrollments or delivery switches are changed.

The request identity binds the full typed context and fingerprint as well as question/symbol. The typed comparison context is stored with the owned answer for auditability. Replays return the original answer and snapshot rather than refreshing its data. A retry after an ambiguous network failure reuses the same ID; a deliberately new generation gets a new ID. The browser also checks the response fingerprint. Changing inputs or leaving the card unmounts the comparison, aborts the browser request and requests cancellation of further repair calls. An already-started call may still be billed and stored; this is disclosed next to cancellation.

## Validation

Behavioral checks cover the actual packet, service, quota, stored messages and grounding on SQLite and PostgreSQL with a scripted provider boundary. They exercise typed held-stop inputs, fingerprint matching, replay without another call, conflicts on changed inputs, snapshot changes without admission, invalid inputs, stale and derivative requests, rejected predictions and ignored action tags. UI checks cover explicit generation, provider/stale gating, actual model labels, fallback labels, mismatch refusal, retry identity and late results after cancellation/unmount. Transport tests verify structured context reaches chat while ordinary requests remain unchanged.

Validation passed: research-engine 696 / 8 skipped; frontend 608 and TypeScript; PostgreSQL plan probe 75. After the final context-persistence refinement, all 120 copilot tests and the 75 PostgreSQL plan tests passed again; the two transport tests and final TypeScript check also passed. Production verification is blocked on publishing; see the status above. No paid provider evaluation is run as part of this change, so real-model scenario quality remains unmeasured; this release establishes the comparison flow and grounding contract.

## Detailed entry, stop and take-profit setups (packet v7)

Both perspectives now expose structured trade details, not just prose:

- Pullback and breakout reference entry prices and their existing code-calculated stops.
- Two take-profit levels, each labeled as structural resistance or a calculated objective, with its nominal reward:risk when measurable.
- Risk per share, optional risk budget, whole-share size, nominal capital before fees, and nominal P/L at the assessed stop. Budgets are in the stock's quote currency; no FX conversion or buying-power check is inferred.
- The complete daily-close entry/exit condition, invalidation, trend exclusions, earnings caution or missing date, and blocked/stale/unavailable status.
- Held-position management uses the original typed cost, stated shares and the higher of the stated stop and calculated candidate. It does not maintain a broker stop. When that stop is at or above cost, original risk and R-multiples remain unknown rather than being labeled zero-risk. Resistance below cost is labeled recovery/loss reduction, never take profit.

New-entry sizing is `floor(risk_budget / (entry - stop))`. The first target is the nearest structural resistance above the entry; absent one, it is an explicitly labeled 2R objective. The second is the next higher structural resistance, or `max(entry + 3R, target1 + R)`. Targets are conditional objectives, not price forecasts. A first target below the existing 2R minimum blocks a new entry, as do the existing falling-trend rule, broken pullback structure, unavailable inputs or a budget too small for one whole share. A tiny numeric tolerance prevents floating-point roundoff from incorrectly rejecting an exact 2R objective.

Holding targets must exceed both cost and the latest close. With no such resistance, positive nominal cost-to-stop risk supplies the same objective calculation; if that objective is already below the latest close, or original risk cannot be measured, the fallback is above the greater of cost and close by two ATRs. The second objective is the next resistance or a higher three-ATR/stretch objective. The summary explicitly flags an exit condition already met. An earnings gap, slippage and fees can exceed nominal stop loss; actual fills require recalculation.

The LLM must emit exactly one validated setup ID (or `wait`) and cite that setup's complete code-written summary. Missing, conflicting, unknown or blocked choices trigger the existing repair/fallback path. The backend returns its selected ID and the code-calculated setup objects; the browser renders those objects, never numbers parsed from model prose. On `wait`, conditional alternatives remain visible without claiming an actionable entry. Rejected outputs remain clearly labeled fallback.

The rules-based Board control chooses which setup to save, preserving its exact entry, stop and first profit objective. Blocked or stale setups cannot be saved through that control. An unusable holding-stop candidate is reported as unavailable rather than causing a server error. Both profit objectives are also named, citable chart levels and can be drafted as fixed-price alerts through the existing confirmation flow. No alerts or orders are automatically created.

Publishing remains blocked by the previously recorded GitHub HTTP 403; these additions are local until repository write access is restored. Updated final validation is recorded below.

Final detailed-setup validation: **709 research-engine tests passed / 8 skipped; 615 frontend tests passed; TypeScript passed; 79 plan tests passed on PostgreSQL**. After the final unavailable-stop guard, the 20 affected UI tests and TypeScript passed again. Tests verify hand-calculated entry/stop/targets, nominal sizing and capital, exact target-alert arguments, Board selection consistency, unknown holding risk, loss reduction below cost, zero-share budgets, low first-target R:R, decimal rounding and unusable stops. Selection tests reject ambiguous, unknown, blocked, stale and malformed model choices. All provider calls in validation were scripted; no paid model evaluation or production write was performed.
