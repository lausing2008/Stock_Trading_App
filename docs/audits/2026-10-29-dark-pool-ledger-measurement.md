# Dark-pool identity ledger: first measurement — October 29, 2026

The R10 release (October 23) left three dark-pool questions open: provider correction and cancellation semantics, legacy collisions, and feed-gap completeness. This is the first measurement of the identity ledger since it started recording. It used read-only production queries, each in a read-only transaction with a 120-second statement timeout, returning aggregates only. Queries and outputs are in [the evidence folder](evidence/2026-10-29-dark-pool-ledger-measurement/).

## Results

The ledger has recorded 95,847 identified prints across 63 symbols since 2026-10-23 22:24 UTC, about 20,000–27,000 per full trading day.

| Question | Measured | Meaning |
|---|---|---|
| Legacy collisions | On each full day (Oct 26–29), the ledger holds **769–1,034 more prints** than the legacy table: 20,047 against 19,278 on Oct 26, rising to 26,285 against 25,284 on Oct 29 | The legacy key (symbol, executed time, price, size) merges about **4%** of distinct provider prints. Any baseline or count built on the legacy table undercounts by about that much |
| Cancellations | **34** (0.035%) | Rare |
| Later corrections | **61** identities with more than one version, observed at different times and not quarantined | Rare (0.06%) |
| "Ambiguous" quarantine | **1,965** identities (2.05%), steady at about 360–530 per full day | See below: this is not occasional conflicting corrections |
| Baseline readiness (uncanceled, unambiguous, priced, last 14 days, at least 20 prints) | **55 of 63** symbols ready; 8 short; median 672 prints | Usable for most symbols after six days; the remaining 8 have thin tape, and none has zero |

## The ambiguous quarantine is systematic

For **1,964 of the 1,965** ambiguous identities, every conflicting version arrived in the **same poll**. One response carried the same `tracking_id` several times (up to 13) with different values:

| Field that differs between versions | Identities |
|---|---:|
| premium | 1,962 |
| size | 1,676 |
| price | 864 |
| venue | 7 |
| NBBO bid / ask | 3 / 2 |
| executed time | 1 |

Execution time almost never differs, while size, price and premium do. That pattern fits several executions reported under one shared provider ID, for example child fills of one parent order, better than conflicting corrections. **That is a hypothesis, not established provider semantics.** Under the current rule these prints are excluded from the baseline and from activity, and they do not resolve, because later polls repeat the same multi-row response. About 2% of identified tape is therefore held out indefinitely.

**Suggested next step (implemented October 30, not deployed; see below):** treat "same `tracking_id`, several rows in one response, same execution time" as a distinct state, `multi_row_same_id`, rather than a conflict. Do not merge or sum those rows until the provider's documentation or support confirms what they represent. Keep the existing quarantine for genuine cross-poll conflicts.

## Follow-up: same-response groups classified (October 30, not deployed)

`dark_pool_ledger.persist` now classifies every tracking ID that appears more than once, with differing payloads, in one response. Results go in a new table, `dark_pool_id_groups_v2`, keyed by the set of payload hashes:
- `multi_row_same_id`: every row is uncanceled and shares one execution time.
- `conflicting_rows`: anything else.

`first_observed_at` and `last_observed_at` bound a group across polls without adding a row per poll. **Nothing else changes:** the events row stays `ambiguous`, so these prints remain outside the baseline and activity, and they are neither merged nor summed. The classification only records why an identity is held out, so the provider's meaning can be confirmed later without remeasuring.

A new table only, so no existing table is altered. The ledger probe (`tests/_dark_pool_ledger_probe.py`) covers both states, a repeat poll and a single-payload duplicate (not a group). It passed on SQLite and on a local PostgreSQL using temporary tables, rolled back.

## Not measured

**Feed gaps (poll journal).** The Redis poll journal holds attempt status, saturated windows and unidentified counts. A read-only aggregation was prepared ([script](evidence/2026-10-29-dark-pool-ledger-measurement/poll-journal-aggregate-NOT-RUN.py), SCAN/ZRANGE only), but this session's permission policy refused it as a production read. Polling completeness and window saturation therefore remain unmeasured.

## Boundary

Read-only measurement. No application, data or configuration change. Provider tracking IDs remain unproven as exchange-wide identities, and these counts describe this application's captured sample, not the consolidated tape.
