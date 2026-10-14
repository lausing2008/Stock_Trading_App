# Independent copilot code review

Reviewed October 13, 2026, against `9a48aca8` and `ab3c0dd7`. Phase 2 remains an untracked design document; there is no phase 2 implementation to approve. This review changes only documentation and offline review evidence. No model calls, credentials, production access, push or deployment were used.

**Conclusion: fix the findings below before deployment or adding copilot writes.** The implementation has useful foundations: authenticated ownership checks, public-report filtering, bounded repair attempts, database sessions closed during model calls, shared-budget reservations, and a conditional retry claim. Passing the existing tests does not cover the counterexamples below.

## Findings

### R1 — P1: The numeric grounding guarantee is not enforced for ordinary number words

Location: `services/research-engine/src/copilot/grounding.py:152–169`.

The checker rejects magnitude words such as “hundred” and “percent,” but accepts “The target is twenty dollars.” It returns `accepted=True` with no evidence keys. “The price will double” also passes. These answers can consequently receive the UI's “Figures from stored data” badge without a sourced target or multiplier. The existing spelled-number test only exercises “five hundred dollars” and “twelve percent.”

Fix: recognize quantitative phrases, including number-word amounts, ranges, fractions and multipliers, while preserving ordinary nonfinancial counts. Add adversarial cases that were not used to tune the prompt. If unrestricted prose remains, describe the actual validation scope rather than guaranteeing every figure is cited. The numeric checks still cannot establish that a correctly substituted value is used in a semantically valid claim.

### R2 — P1: Per-user daily limits can be exceeded through concurrency or conversation deletion

Locations: `services/research-engine/src/copilot/service.py:136–138`; `store.py:48–55`, `store.py:110–119`.

Admission counts completed assistant messages. With a limit of one, three concurrent requests with distinct IDs all reached the scripted model and stored answers. Separately, completing one answer, deleting its conversation, and asking again also succeeded under a limit of one. The usage record disappears with the conversation. Shared token budgeting is a separate control and does not make this per-user promise true.

Fix: use an atomic per-user/day reservation ledger, count in-flight admissions, and settle/refund according to a documented policy. Keep minimal usage counters independent of deletable conversation content. Add PostgreSQL tests for distinct request IDs near the limit and deletion followed by another request; existing concurrency tests cover same-ID requests and the shared token budget, not these cases.

### R3 — P2: “New” reloads the conversation the user just left

Locations: `frontend/src/components/CopilotPanel.tsx:115–131`, `:186–188`.

`startNew()` clears `convId` and messages. That satisfies the history-loading effect, which immediately retrieves the latest conversation. The next question ordinarily continues that conversation instead of starting a new one. This is established by the state/effect flow; the review did not run a browser interaction test. Current frontend tests render only `CopilotAnswer`, so they never exercise the panel lifecycle.

Fix: separate initial history hydration from an explicit new-conversation mode. Test open/resume → New → send, and symbol navigation while a request is pending. Guard asynchronous results with a request/symbol generation identifier so responses from an older panel state cannot update a newer one.

### R4 — P2: Request IDs are not bound to their original payload

Locations: `services/research-engine/src/copilot/service.py:132–135`, `:144–165`.

A completed request replayed successfully after changing its question, symbol to an unknown ticker, and conversation ID to a nonexistent value. More seriously, a failed request retried with a different question sent that new question to the model but retained the original user message in history. The stored question and paid answer can therefore disagree. Ownership remains enforced; this is an integrity issue, not evidence of cross-user disclosure.

Fix: persist a normalized request fingerprint and validate it before replay or claim acquisition. Include symbol and question, and validate conversation association while retaining the legitimate first-turn retry that has no conversation ID yet. Decide explicitly whether model/provider changes are allowed on retries. Reject conflicting payloads with 409. Test both completed and failed paths.

### R5 — P2: Copilot labels stored-row returns as exchange-session returns

Locations: `services/research-engine/src/copilot/packet.py:317–328`; underlying `intel_reports/adapters.py:85–101`.

The return adapter selects the Nth stored daily bar; it does not verify a complete exchange calendar. Copilot adds the explicit meaning “over the last N trading sessions.” Removing an intermediate bar leaves the quantity marked OK with that same meaning, despite a longer observation window. The adapter predates these commits, but the new packet exposes the stronger unsupported description. The synthetic fixtures themselves include calendar-day bars across weekends, so passing them does not validate session counting.

Fix: label these as N-bar returns with both endpoint dates, or validate continuity against the appropriate exchange calendar before claiming N sessions. Also preserve the unadjusted-price basis in return/trend descriptions and handle corporate actions before interpreting discontinuities as investment returns.

### R6 — P2: Moving-average citations omit most of the inputs

Locations: `services/research-engine/src/intel_reports/adapters.py:153`; `copilot/service.py`, `_evidence_refs`, `evs[:3]`.

The trend adapter computes the 50-bar average but records only the first 20 bars as evidence. The copilot then returns only three of those evidence records for the cited quantity. The review probe confirms 50 inputs, 20 recorded references, and three returned references. A user cannot reconstruct the displayed average from the supplied citation set. The adapter issue is pre-existing; truncating the references without a retrieval path is new in the copilot.

Fix: preserve the complete input set and derivation metadata, with a server-side calculation/evidence ID and an authorized detail endpoint. The UI may collapse a long list, but the underlying evidence must remain retrievable. Add a test that recomputes each cited average from its evidence, rather than merely checking that citation IDs resolve.

## Additional improvements

- **Freshness semantics:** a 29-day-old report and its changes were both marked OK. This may be acceptable for explicitly historical comparisons, but it must not imply current analytical relevance. Expose report cutoff and age independently. `data.as_of` is the latest daily price timestamp, not necessarily the newest observation across news, signals and reports; rename it accordingly or return per-source times.
- **Stop acknowledgment:** `stop()` swallows cancel errors and ignores `{cancelled: false}`, then promises no further call will be made. Distinguish confirmed server cancellation from merely stopping the browser wait. Reopening the collapsed panel does not refresh an existing conversation, so the saved result of a stopped request may remain invisible until remount/reload. Test these paths with deferred promises and failed cancel requests.
- **Historical context:** prior answers are sent back as rendered numeric prose wrapped in draft tags, despite the current placeholder-only protocol. Preserve a structured, versioned representation or explicitly separate historical observations from current evidence. Evaluate follow-ups after prices and reports change, not only repeated questions over one fixed packet.
- **Deletion during generation:** another tab can delete a conversation while a paid request is running. The final insert then encounters a missing parent on PostgreSQL. Define a deletion/cancellation policy and exercise it with FK enforcement; do not resurrect deleted content.
- **Permissions:** the pre-existing refresh and two ranking-tune endpoints still depend only on `get_current_username`. The separately approved admin-gating fix is not implemented in this checkout. Verify scheduler/service callers before adding the gate.
- **Phase 2 planning:** the untracked design still lists the four decisions as unresolved. Record the accepted decisions: disclose existing email enrollment, database-backed price-alert idempotency, retain non-admin watchlist scope, and implement screening before drafts before chart commands. Enforce screening scope on the server, not only in the browser.

## Validation and reproducibility

- Existing backend copilot suite rerun: **14 passed**.
- Existing frontend copilot suite rerun: **3 passed**.
- New offline counterexamples: [probe](evidence/2026-10-13-copilot-review/probe.py), [results](evidence/2026-10-13-copilot-review/results.json).
- Run from repository root: `python docs/audits/evidence/2026-10-13-copilot-review/probe.py`. It clears `STOCKAI_INTEL_DB` within its process and uses the existing harness's disposable SQLite database and scripted models. It does not contact a provider. The concurrent-quota reproduction pauses calls after admission, so it demonstrates the missing in-flight reservation without relying on simultaneous SQLite writes.

The full 12-service suite, production deployment, PostgreSQL tests and real-model evaluation were not rerun by this review. PostgreSQL-specific remediation needs its own follow-up validation. UI findings are source inspection; add actual interaction tests before claiming them fixed.
