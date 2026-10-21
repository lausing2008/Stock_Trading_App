# Two Game Plan perspectives — 2026-10-16

**Status: deployed and verified on 2026-10-16.** Release `0a003197` is running in production: research-engine serves packet v7 and the frontend serves both Game Plan perspectives with entry, stop and take-profit details. The user-approved reboot recovered the host after the on-host build stall; the verified off-host frontend image was installed under the deployment lock. Acceptance passed: 15/15 containers healthy, 0/12 backend source drift, tracker 446–450 served, guarded routes return 401, and the public HTTPS stock page returns 200. Risk email delivery remains off. GitHub publication remains blocked by HTTP 403. See the recovery record and deployment handoff below.

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


## Authorized SSH-key production release

The user explicitly requested production deployment with their existing EC2 key. SSH authentication succeeds, while a fresh GitHub push still returns HTTP 403. The production checkout is clean at `f8c21a79`. This release transfers the committed Git history as a Git bundle over that authenticated SSH connection, verifies the required base and target, and fast-forwards `prod` under the host deployment lock. It rebuilds research-engine and frontend from that checkout using the normal deploy script. GitHub is not modified by this path; upstream publication remains pending until its write access is restored.

The unrelated untracked `services/market-data/src/services/fear_greed.py` is excluded from the release. Verification compares all backends with an isolated checkout of the committed release, rather than misreporting that parallel untracked file as deployed source drift. No alert enrollment, delivery flag or trading setting changes are included. Final production results follow execution.


### Production build stall and recovery

The clean isolated release passed 615 frontend tests and TypeScript at deployment. Research-engine rebuilt and returned packet v7, typed stops, two holding targets, unsupported-position refusal and forecast refusal in 1.232 seconds. During the following on-host frontend build, new SSH connections timed out at banner exchange and an HTTP request received no response within ten seconds. Cancelling only that build's local SSH connection did not restore access. The build wrapper exited; its lock-control SSH connection did not acknowledge completion within 30 seconds. Do not assume a stale status file proves the host lock is free: inspect the actual lock on recovery.

AWS reports the instance and system checks healthy, with a 100-GB gp3 volume (3,000 IOPS / 125 MiB/s); no SSM-managed instance is registered. Available console output has no matching OOM/hung-task evidence. Recent CPU averages were approximately 53–65%, and volume queue length approximately 1.4–1.7. These observations do not establish the root cause, and this is not evidence of gp2 credit exhaustion. No volume, instance size, network or trading configuration was changed.

A controlled reboot request was initially **rejected before execution by automatic approval review**, because deployment authorization does not explicitly authorize broad production interruption. The user subsequently explicitly answered “Yes, reboot production and finish deployment”; AWS accepted the reboot request, and the console screenshot shows the guest booting. Application recovery and frontend activation still require verification. See [the deployment handoff](../incidents/2026-10-16-deployment-handoff.md).

The off-host image `stockai-frontend:dual-game-plan-0a003197` built successfully. A loopback-only preview verified `/stock/AAPL` serves both views, entry references, stops and take-profit details, and `/improvements` includes tracker 450. The compressed image archive is ready at `/tmp/stockai-frontend-0a003197.tar.gz`, with a SHA-256 sidecar. Once access is recovered, inspect and cancel any remaining release build under the host lock, transfer/load this artifact, preserve the existing frontend image for rollback, and recreate **only frontend** with `--no-deps --no-build`. Do not run another production frontend compile. Then verify all 15 services, all 12 backend source hashes against this committed release, served bundles, guarded routes, database blocking and the still-disabled risk-delivery flag. Deployment is not complete until these pass.

Recovery completed on 2026-10-16. CloudTrail records the approved reboot request at 17:29:11 PDT; SSH recovered with a fresh guest uptime. No previous release builder remained. Under the host deployment lock, the transferred archive passed SHA-256 verification, the former frontend image was preserved as `stockai-frontend:rollback-before-dual-game-plan`, and only frontend was recreated from the prepared image, without a production compile. The first acceptance run encountered frontend's initial health-check interval; the subsequent full run passed.

Final evidence: 15/15 healthy; 0/12 backend source drift against the isolated committed release; packet-v7 smoke 0.456 seconds with typed stops and two holding targets; served two-perspective plan and tracker 446–450; all six guarded-route probes returned 401; `/risk-alerts`, `/news`, and `/signal-accuracy` returned 200; public `https://lausing.com/stock/AAPL` returned 200; risk-delivery flag absent/off. Database checks found no blocked sessions or idle transactions over 60 seconds. Post-reboot PostgreSQL reads briefly reached the gp3 volume's 125 MiB/s throughput limit; the next three samples settled to 0–2.2% disk utilization and 0–0.9% iowait. That transient observation does not establish the cause of the original stall.

Concurrent uncommitted CNN Fear & Greed changes remain outside this release and intact for their owning session. Their focused 23 tests and frontend TypeScript check passed; review and integration acceptance remain separate. See [the handoff](../incidents/2026-10-16-deployment-handoff.md).

## Scenario playbook (packet v8, 2026-10-21)

User question: "why are both plans exactly the same?" By design the LLM may only **select** one of the code-calculated setups (it cannot write prices), so when it picks the rules plan's scenario every number matches. What differed was hidden: its reasoning sat in a collapsed section. The LLM view now shows the reasoning openly and states why the prices can match.

Each valid setup now carries a code-written `playbook` — conditional branches, never a forecast:

- **Pullback:** *falls* (the 20-bar low is the entry zone; a close below the stop cancels; next stored support), *rises* (do not chase above `(T1 + 2 × stop) / 3`, where T1 drops under 2R; a confirmed close above the 20-bar high switches to the breakout), *sideways* (no trade between the edges; levels roll daily; alerts at both edges), *after entry* (breakeven at entry + 1 ATR, partial at TP1, trail at close − 2 ATR toward TP2).
- **Breakout:** *rises* (confirmed close above the high on 1.5× volume triggers; light volume is unconfirmed; same no-chase limit), *falls* (pullback setup applies; a close back below the stop is a failed breakout), *sideways*, *after entry*.
- **Held position:** *rises* (trail, never lower a stop; partial at TP1), *falls* (close below the assessed stop exits; gap risk), *sideways* (hold above the stop).

Each branch is a citable `playbook.<setup>.<case>` quantity, so the LLM is asked to write the same three headings citing them and to add its own reading of which case matters, in words only. The existing no-forecast check still applies. Tests: hand-computed branch values on the fixture (ATR 2, low/high 99/101 → pullback no-chase $99.00, breakout $101.00), a grounded playbook answer, and UI rendering. research-engine 713 passed / 8 skipped (SQLite); frontend 696 + TypeScript.
