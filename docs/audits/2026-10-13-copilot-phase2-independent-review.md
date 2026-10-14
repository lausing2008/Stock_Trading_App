# Copilot remediation and phase 2: independent review

Reviewed October 13, 2026, at `18d27c9a`, covering `4844b8bf`, `fb1bc52b`, `41fab0b5`, and `18d27c9a`. Working tree was clean at the start. This review adds only documentation and an offline reproduction probe; no application changes, real-model calls, deployment or push.

**Decision: hold chart commands until the findings below are fixed.** The previous R1–R6 counterexamples are covered by the remediation and its passing tests, but phase 2 introduces new interpretation and save-recovery gaps. Successful real-model examples do not establish that the validators reject an incorrect proposal.

## Findings

### S1 — P1: A committed save cannot recover after its acknowledgment is lost

Location: `services/research-engine/src/copilot/service.py:380–403`.

Confirmation checks the old state hash before executing the downstream save and only then records `saved` in the message. If market-data commits an alert but its response is lost, the draft remains proposed. The retry now sees a changed alert list and returns 409 **before reaching the endpoint's idempotency replay**. The user sees a failed save even though an alert exists, and is told to ask for another draft, which has a new idempotency key and can create another alert.

Reproduced with the real confirmation service and a scripted market client that records the write before raising a 502: one alert exists; first confirmation returns 502; second confirmation returns 409; the downstream replay is never attempted. This also represents a research-engine crash after the downstream commit but before the local receipt transaction.

Fix: introduce a durable per-draft execution record and reconcile ambiguous outcomes by downstream idempotency key **before** applying pre-execution state checks. Do not solve this by blindly bypassing state checks. Make watchlist/signal operations provide equivalent reconciliation semantics. Test commit-then-lost-response, process restart, and retry after expiry. Actual transport exceptions also need explicit handling: `MarketClient._req` currently does not translate `httpx.RequestError` into its declared error type.

### S2 — P2: Draft validation accepts a different condition or measurement than requested

Location: `services/research-engine/src/copilot/drafts.py:74–123`.

The validator establishes that a number appeared somewhere in the question, or that a proposed key is some stored price. It does not establish its units, role, comparator or requested source. Independently supplied incorrect proposals were accepted for all of these:

| Question | Accepted draft |
| --- | --- |
| Alert me if it drops 10% | Absolute price below $10 |
| Alert me below 100 | Price **above** $100 |
| Alert me below its 50-bar average | Price below the K-Score fair-value estimate |

The final confirmation card is a useful safeguard but does not satisfy the claimed validation contract. The 10%-drop case was in the real-model evaluation; a model choosing correctly on that run does not test refusal of the wrong draft.

Fix: bind a proposal to a supported intent with typed amount, unit, comparison and evidence reference. Refuse percentage changes until they have an explicit supported interpretation; do not reinterpret them as absolute prices. Reject an unsupported source rather than choosing another available price. Add counterproposal tests, including numbers used as horizons/counts and negated actions.

### S3 — P2: Screening validation does not bind thresholds to metrics or comparison direction

Location: `services/research-engine/src/copilot/screen.py:113–133`.

The same numeric-membership problem exists in screening. Reproduced accepted plans:

- “K-Score below 70” → `min_score=70`.
- “Show 10 stocks with K-Score above 70” → `min_confidence=10`.
- “Technology stocks, excluding BUY signals” → `signal=BUY`.

The third case matches a keyword while ignoring its negation. Market/sector matching has the same structural limitation. These are interpretation errors, not a bypass of the correctly server-resolved watchlist scope.

Fix: validate metric/comparator/value relationships and polarity, not a bag of mentioned numbers and words. A bounded grammar for supported requests is sufficient; ask for clarification or return unsupported for ambiguous cases. Test wrong proposals against correct questions, alongside good-model evaluations. Also handle empty `<screen>`/`<none>` proposals explicitly: they currently count as parsed and can reach an unfiltered screen without a validated filter.

### S4 — P2: Saving one of two price-alert drafts invalidates its sibling

Locations: `services/research-engine/src/copilot/drafts.py:204–215`; `service.py:385–388`.

Two alerts for the same symbol are allowed in one answer, but both are bound to the hash of the entire active alert list. Saving the first changes that list. Saving the second immediately returns 409, even when nothing outside this answer has changed. Reproduced by asking for below-100 and above-150 alerts: two drafts are presented, but only one can be saved.

Fix: scope preconditions to facts relevant to each action, or explicitly account for acknowledged sibling operations. Do not remove concurrency protection wholesale. Add a test confirming both cards in either order and tests for genuinely conflicting external changes. Store independent draft receipts atomically; rewriting the entire message JSON also risks lost updates when different drafts are confirmed concurrently.

### S5 — P2: Watchlist confirmation does not bind the email effect the user reviewed

Location: `services/research-engine/src/copilot/drafts.py:223–225`; confirmation discards refreshed `_effects` at `service.py:385`.

The preview correctly returns effective email settings, but the state hash includes only list identity, membership and subscription fields. It excludes `email_on_file` and `signal_emails_enabled`. Changing the preference from off to on changes the disclosed effect without changing the hash; confirmation proceeds using the old card. The probe reproduces identical hashes for “Emails OFF” and “Emails ON.”

Fix: include all consent-relevant effect fields in the bound snapshot and require a refreshed confirmation when they change. Carry expected state into the write transaction if the contract promises refusal of stale drafts; the current read-state-then-POST sequence alone is not an atomic precondition. Apply consistent email-address/preference disclosure to direct signal-alert drafts too.

### S6 — P2: Price-alert cards promise a closing-price condition that the saved alert does not enforce

Location: `services/research-engine/src/copilot/drafts.py:121–123`; actual evaluator: `services/market-data/src/services/scheduler.py:9198`, `:9260–9262`.

The card says “when TESTCO closes below …”. The existing alert checker uses `fast_info.last_price` and inclusive comparisons (`>=`/`<=`), rather than waiting for a closing bar. Users can therefore receive an intraday notification for a condition they understood as close-confirmed.

Fix: describe the actual saved rule, such as “observed price at or below …”, and disclose that an average-derived threshold is a fixed snapshot, not a moving-average alert. If close confirmation is desired, add a distinct supported rule and evaluator rather than implying it in the card. This finding is source inspection; no live market data was fetched.

## Previous remediation and ranking permissions

The reviewed remediation implements independent atomic admission counters, request fingerprints, explicit new-chat hydration state, N-bar labels, full calculation evidence, source-specific times, clearer Stop acknowledgment and deletion handling during answer generation. Existing regression tests for these passed in this review. This is not a guarantee against all natural-language grounding errors; the reported remaining semantic problems still apply.

All three ranking mutation endpoints use `require_model_admin`. Its implementation checks the live account for human admins and accepts the model-scoped service credential. No new authorization defect was found in the inspected changes. The ranking HTTP authorization suite stalled in this review environment on its first unauthenticated TestClient request and a bounded rerun timed out; consequently its reported HTTP results were **not independently reproduced here**. The stack was waiting in the AnyIO/TestClient portal; the cause was not diagnosed as a product defect.

## Migration and follow-up recommendations

- The price-alert migration adds the columns and partial unique index to an existing table, which is appropriate. However, `_apply_isolated_ddl` only logs non-contention failures. Before deployment, verify both columns and the unique index definition and fail readiness for draft saves if they are missing; do not infer readiness solely from successful startup. Retain the existing serialization/lock-timeout behavior when tightening this path.
- Watchlist and signal creates use select-then-insert and do not handle uniqueness races like the new price-alert endpoint does. Sequential replay tests do not establish concurrent confirmation reliability. Exercise these downstream paths under simultaneous saves on PostgreSQL.
- Keep the old and new screening paths from becoming permanent competitors. Retire the old “AI Screen” box in a separate small change once S3's negative-proposal tests pass; preserve the ordinary manual screener.
- Keep watchlist removal and moves outside this slice. In particular, do not add move drafts before an atomic move endpoint exists.
- Resume chart work after this slice is corrected and re-reviewed. Begin with the chart state/command boundary and manual-control regression tests; add one command family at a time with acknowledgment and undo.

## Independent validation

| Check | Result |
| --- | --- |
| Research-engine copilot suite | 32 passed |
| Market-data alert idempotency suite | 6 passed, 1 PostgreSQL test skipped |
| Ranking screen scope suite | 3 passed |
| Copilot frontend rendering/interaction/screen tests | 14 passed |
| Ranking HTTP authorization tests | Bounded rerun timed out; not counted as passing |
| Targeted phase 2 counterexamples | Reproduced S1–S5 offline; S6 inspected in source |

[Reproduction probe](evidence/2026-10-13-copilot-phase2-review/probe.py) and [captured results](evidence/2026-10-13-copilot-phase2-review/results.json). Run the probe from repository root. It clears `STOCKAI_INTEL_DB` in its process and uses disposable SQLite, scripted model output and a scripted market client. It deliberately exercises incorrect proposals and downstream partial failure, not model quality. No PostgreSQL database, real provider or production system was used in this review. The full 12-service suite was not rerun.
