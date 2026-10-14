# Copilot phase 2: remediation of the independent review (2026-10-14)

This responds to [the phase 2 independent review](2026-10-13-copilot-phase2-independent-review.md)
of `18d27c9a`. All six findings and the review's follow-ups are fixed. Each finding has a test
reproducing the reviewer's counterexample; the race tests also ran on PostgreSQL 16.

**Implementer-verified only; not re-reviewed.**

## Findings

| # | Finding | Fix | Proof |
|---|---|---|---|
| S1 | A committed save whose acknowledgment was lost was then refused with 409 | `copilot_draft_receipts` (one row per draft). Status `executing` is written BEFORE the downstream call. A retry that finds `executing` **reconciles**: it repeats the same request with the same idempotency key, without re-checking expiry or the precondition. Any 5xx or transport failure is *ambiguous* and keeps `executing`; a 4xx is `failed`, and a later try is fully re-checked. `httpx.RequestError` now becomes a `MarketError` | Lost acknowledgment → 502 then **saved**, 1 alert. Crash after commit, retried after expiry → saved, 1 alert. Definite 400 → receipt `failed`. **PostgreSQL**: 8 simultaneous confirms of one draft plus its sibling → 2 alerts, 2 receipts, every request ok |
| S2 | Drafts accepted the wrong unit, direction or source | `copilot/intent.py` parses the question into typed alert targets (direction, plus a typed price or a named price source). A draft must match one exactly. Refused outright: percentages, "hits/reaches" without a side, negated requests, numbers used as horizons, another ticker in the question | 10 counterproposals (`p14`), including all three of the reviewer's |
| S3 | Screen thresholds attached to the wrong metric or direction; negation ignored; empty proposals ran unfiltered | The same grammar parses (metric, comparator, value), counts, sort, and positive or negated signals, markets and sectors. Each filter must match one. Exclusions and filters the screen lacks (e.g. momentum *at most*) are reported as unsupported. An empty or `<none/>` proposal runs nothing, unless a validated sort or count was asked for | 9 counterproposals including the reviewer's three; empty → no fetch; "top 5 by relative strength" still runs |
| S4 | Saving one of two price-alert cards invalidated the other | A price alert's precondition is scoped to the fact its card discloses: whether an identical active alert exists. Receipts are separate rows, so concurrent confirms of two drafts cannot lose an update | Both siblings save in either order; an identical alert created externally still gives 409 |
| S5 | A changed email preference did not invalidate the watchlist card | market-data previews return an `effects_version` over every consent-relevant field (email on file, effective preference, subscription state, disclosed text). Watchlist add and signal subscribe accept it and **re-check inside their write transaction**. Signal-alert drafts get the same disclosure (`GET /signal-alerts/preview`). If a preview lacks a version, the copilot hashes the consent fields itself | Real market-data on SQLite **and PostgreSQL**: the version changes when signal emails are turned off; a stale add or subscribe → 409 with no row; the current version saves |
| S6 | Cards promised a closing-price condition the checker does not apply | Cards now say "observed price at or above/below", checked about once a minute against the latest quote, can trigger intraday. An average-derived threshold is disclosed as a fixed snapshot that will not follow the average | `test_s6_…` |

## Follow-ups from the review

- **Migration readiness is checked, not inferred.** A new capability, `copilot_draft_save` (in
  the M24 matrix), requires:
  - both price-alert idempotency columns;
  - the exact `(user_id, idempotency_key)` unique key;
  - the receipts table;
  - one stock per watchlist;
  - one subscription per (user, symbol, horizon).

  Confirmation refuses with 503 and the evidence before any write when the capability is not
  ready. On PostgreSQL it reads `not_ready` on production's current table shape and `ready` after
  `init_db()`.
- **Concurrent watchlist adds and signal subscriptions** now return the existing row on a
  uniqueness race instead of raising a 500. On PostgreSQL, 8 simultaneous adds and 8 simultaneous
  subscribes each produced 1 row with no errors.
- **Latent defect found while testing that:** the `(watchlist_id, stock_id)` unique index existed
  only as a PostgreSQL migration and was never declared on the model, so a database built by
  `create_all()` had no uniqueness. It is now declared on the model; the migration is unchanged.
- **Ranking authorization tests no longer use `TestClient`.** They use an in-process ASGI client
  on one event loop with a per-call timeout (the review saw `TestClient` stall in its portal
  thread). 19 pass in 1.6 s; reverting the gate still turns 3 red.
- **The old "AI Screen" box is retired** from the screener page, as decided once the
  counterproposal tests passed. The manual screener is unchanged.
- **Watchlist moves stay deferred** until an atomic endpoint exists.

## Real-model check (same fresh cases, new validators)

| | Sonnet 4.6 | Sonnet 5.5 |
|---|---|---|
| Screen: exact applied set | 20/20 | 10/10 |
| Drafts: correct | 15/16 | 8/8 |

- Unsupported screen asks were reported in every run.
- Every proposed draft saved once, and every second confirm replayed.
- The draft miss is again d04: the model answered without drafting.

These cases were not tuned against. Raw results: `2026-10-13-copilot-eval/after-phase2-review-*`.

## Validation

| Suite | Result |
|---|---|
| research-engine | 619 passed, 8 skipped (PostgreSQL-only) |
| PostgreSQL copilot | 51 passed |
| market-data alert/watchlist, both engines | 17 passed, 1 skipped |
| Frontend | 588, `tsc` clean |
| `make test`, all 12 services | passed |

The reviewer's probe now passes S2, S3 and S4. Its lost-acknowledgment fake drops *every*
acknowledgment, so the retry also returns 502, but it reconciles: no 409, still one alert. Its
email fake subclasses the probe's updated fake, which now supplies its own version, so its S5
case cannot change state. S5 is proven against the real market-data endpoints instead.
