# Options dominance and dark-pool identity continuation

Date: October 23, 2026, America/Los_Angeles. **Implementation is local, unstaged and uncommitted. No service was restarted or deployed.** Production activity in this continuation was limited to a bounded read-only aggregate query and an isolated PostgreSQL temporary-table probe with full rollback.

## Per-stock activity summary

Phase C is implemented as `options_dominance_v1`, attached to the existing options-flow response as `dominance`. `GET /stocks/{symbol}/options-dominance` reuses the same function and cache. The stock detail Options tab renders the summary above its strategy cards, for any requested symbol. The frontend uses its existing flow request; no extra polling, provider request or independently timed chain read is added.

| Measurement | Implementation | Limits |
|---|---|---|
| Contract volume | Call/put counts, shares and exact existing cut points: C/P ≥ 1.3 call-heavy, ≤ 0.8 put-heavy, otherwise mixed | Descriptive activity, not trade direction or opening activity |
| Open interest | Counts and shares from the exact same archived rows and included expirations; higher-call/higher-put/equal comparison | Volume thresholds are not applied to OI. Provider OI reporting date is unavailable and remains null; the snapshot session is not presented as proof of the OI report date |
| Paid premium | Explicitly unavailable | A chain mark or volume × mark is not a measured paid-premium total; selected unusual-contract rows are not substituted for full-scope tape |
| Buy/sell estimate | Explicitly unavailable | No comparable event window or classified-share evidence is captured for this scope. No trade intent is fabricated |

The response carries symbol, source session, computation time, method version, requested/included/missing expirations and per-expiration archive coverage. It reads the source session's nearest four expirations, not the whole listed chain and not a current trade-candidate universe. Historical expirations remain historical evidence; the new block does not override existing current-candidate restrictions.

Different sessions, unrequested expirations and duplicate expiration slices are refused by the accumulator. Unknown, negative, nonfinite and fractional contract counts are not accepted as measured zeros. Partial fields retain known subtotals and known/total coverage, but shares are withheld. A measured zero total has no percentage split and remains distinct from missing measurements. When volume is zero or entirely unmeasured, the legacy flow response can remain unavailable while its dominance block still exposes measured OI.

Missing expiration reads and unverified/incomplete archive coverage remain visible. When fields are measured in the included expirations, displayed shares describe only those expirations; they do not establish complete market coverage. When volume and OI favor opposite sides, the UI displays the disagreement without combining them into a directional score. `directional_intent` remains `not_established`; `actionability` remains `research_only`.

Loading, request failure, unavailable summary and failed-refresh-with-cached-data states are visible. A cached result retains its original computation time and ages in an open tab. An older backend response without the new block produces an explicit unavailable summary rather than guessed OI/premium figures.

## Cache invalidation repair

The snapshot job was still deleting `options_flow:v5:*` while the deployed route had already moved to v6. The new summary requires v7 to avoid reusing responses without it. The route and post-capture invalidator now call one `flow_cache_key()` function. Regression coverage checks both the real route's populated key and the job's deleted keys. This fixes a discovered pre-existing cache mismatch; no trading score or legacy sentiment threshold is changed.

## Dark-pool identity investigation

The new `scripts/dark_pool_identity_report.py` runs a repeatable-read, read-only transaction with a 10-second statement timeout. Defaults: 21-day current-ambiguity window, at most 500 selected IDs; days are bounded to 1–30 and the ID limit to 1–2,000. The final SQL result contains counts only, never IDs or payloads. Failures roll back and expose only the exception class, not connection details. The query does not fetch additional provider data or alter the ledger/quarantine.

The production aggregate at **October 23, 17:35 PDT** (`2026-10-24T00:35:24.964921+00:00` in the raw evidence) found:

| Count | Observed |
|---|---:|
| Currently ambiguous IDs in the window / reviewed | 47 / 47 |
| Retained payload versions reviewed | 127 |
| IDs whose retained versions vary only NBBO quote fields | 0 |
| IDs whose retained versions vary trade fields | 47 |
| IDs with size variants | 43 |
| IDs with price variants | 7 |
| IDs with execution-time or cancellation variants | 0 / 0 |

Field-variant counts can overlap. The query compares **all retained versions of currently ambiguous IDs**. It cannot reconstruct the exact conflicting response that originally caused quarantine, because the version ledger stores distinct payloads with their first observation time rather than every repeated observation. These results therefore do not establish whether a provider ID represents a correction, aggregation, reuse or multiple executions. They do rule out treating this reviewed set as exclusively quote-only variation. Quarantine and baseline exclusions remain unchanged.

The [official developer example](https://unusualwhales.com/developers) includes `tracking_id` in dark-pool responses. That example alone does not establish uniqueness scope or correction ordering. The [official documentation index](https://api.unusualwhales.com/docs/llms.txt) lists a ticker dark-pool endpoint; its detailed endpoint page was inaccessible during this check. No stronger provider identity guarantee was inferred from the example.

Next identity work: establish documented ID scope and correction/cancellation ordering, retain sufficient per-observation provenance where needed, and verify those semantics against controlled captures before changing keys or relaxing quarantine. Legacy tuple collisions remain unrecoverable from the old table.

## Validation and evidence

The [evidence directory](evidence/2026-10-23-options-dominance/) contains the production aggregate, synthetic PostgreSQL probe result and its witness. The PostgreSQL probe asserts temporary-table resolution, tests quote-only versus trade-field variants, limit and empty-symbol behavior, rolls back all DDL/data and verifies that the temporary table disappeared. It never reads or modifies production event rows.

Focused route/aggregation checks passed (29 tests), including actual route execution, a shared cached chain read, zero/unknown volume with usable OI, cross-session rejection, exact composition boundaries, partial fields and unavailable event evidence. UI checks passed (16 tests across new and existing flow cards), including call-heavy/put-heavy cases with opposite legacy sentiment, missing data, cached failures and zero versus unknown values. These focused totals are subsets of the full-suite validation recorded after completion.

No interactive browser verification, production build or deployment was performed. Deployment must include the new helper and cache-key invalidator together. Existing unrelated working-tree changes remain outside this feature's scope.


Final full-suite results on the current working tree: **5,784 market-data tests passed, 26 skipped** (361.39 seconds); **828 frontend tests passed**, 70 files; **44 script tests passed**. Full frontend typecheck and diff whitespace checks passed. The signal-engine source was unchanged in this phase and was not retested. Temporary-copy guard-removal checks detect treating partial fields as complete and accepting mixed-session slices; see `guard-removal-results.json`. Skips are not counted as passes. Independent review, interactive browser acceptance and production rollout remain pending.
