# Two Game Plan perspectives — 2026-10-16

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

Validation passed: research-engine 696 / 8 skipped; frontend 608 and TypeScript; PostgreSQL plan probe 75. After the final context-persistence refinement, all 120 copilot tests and the 75 PostgreSQL plan tests passed again; the two transport tests and final TypeScript check also passed. Production verification follows deployment. No paid provider evaluation is run as part of this change, so real-model scenario quality remains unmeasured; this release establishes the comparison flow and grounding contract.
