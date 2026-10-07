# Prospective capture, persisted evaluations, and precise gate labels

Built 2026-10-06, in response to a review that found four things wrong with the first Quality &
Value slice. Each section below is one of them.

## 1. Whole-equity valuation, with alignment that can refuse

`equity_discount()` replaces the earlier `discount()`/`upside()` pair. Three rules, each able to
return nothing rather than a number:

- **Equity against equity, never enterprise value.** An EV is a claim about the whole capital
  structure, and becoming a claim about the equity needs a sourced bridge — debt, cash, leases,
  pensions, minorities, preferred. None is stored with a source, so `basis` must be `"equity"`
  and anything else raises `ValuationMisaligned` rather than silently converting.
- **Both sides must describe the same moment.** Production market caps range over 25 days
  against a current price. A value and a cap more than `MAX_VALUATION_ALIGNMENT_DAYS` (3) apart
  refuse, because the gap absorbs every intervening buyback, issuance and price move as if it
  were valuation. Both timestamps travel with every result; an untimestamped side refuses too,
  rather than assuming "now".
- **Never per share.** There is no reliable share count, so the result is aggregate and offers
  no route to a per-share entry target. A test asserts no numeric per-share value can appear.

Both denominators are returned and named: the same gap is 20% off the value and 25% of upside.
A missing input returns unavailable, not zero — a zero discount sorts as "fairly valued".

## 2. Gate labels say what a PASS establishes, and what it does not

The review's point: "business quality passes" and "151 companies pass evidence-readiness checks"
are different claims, and the gate name asserted the stronger one.

`GATE_CLAIM` now declares, per gate, a `label`, what a PASS `establishes`, and what it
`does_not_establish`. Every gate carries all three into `as_dict()`, so no surface can render a
verdict without the sentence that bounds it, and the dashboard shows **passing** gates with
their bounds too — a reader who sees only the failures reads the passes as endorsements.

| Gate | Label | A PASS establishes | It does not establish |
|---|---|---|---|
| `business_quality` | Fundamental checks | the stored fundamentals cleared the configured completeness and freshness checks | that this is a high-quality business — no accounting basis is stored, so the periods are not shown to be comparable, and nothing measures returns on capital |
| `entry_condition` | Price-stabilization rule | a versioned price rule was satisfied on completed sessions | that the stock is a suitable entry |

Reaching `entry_review_ready` now says so in its own explanation: *"which establishes that the
configured checks were met, NOT that this is a suitable investment"*.

## 3. Persisted evaluations — a shadow trial, not a preview

The review was right that nothing persisted makes this a preview. A screen that only recomputes
cannot be measured: the inputs move underneath it, so months later there is no way to say what
it concluded on a given day.

`quality_value_evaluations` stores cutoff, policy version, **policy fingerprint**, state, full
gate results, evidence references and blocking gates.

**The fingerprint is the part that makes the row evidence.** A hand-maintained version string
drifts the first time someone adjusts a threshold without bumping it — and then two rows both
labelled `qv-1` came from different screens, which is worse than no version because it looks
trustworthy. So the identity is computed from the rules themselves: required set, quality
subset, every claim sentence, every threshold. The unique key is
`(symbol, cutoff, policy_fingerprint)`:

- a re-run under identical rules writes nothing (page views do not accumulate rows);
- a re-run after **any** rule change lands as a new row **beside** the old one;
- there is no update path anywhere in the store module.

`/quality-value/history/{symbol}` is deliberately unscoped by fingerprint — it is the view that
answers whether the answer changed because the company changed or because we changed the rules,
and that needs both kinds of row with the policy stamped on each. `latest_evaluations()` is
scoped, because listing verdicts from different rules together presents them as comparable.

A storage failure is reported in `persist_errors` and the run says it is not a complete record;
it does not take the view down.

## 4. Prospective capture: estimates and macro expectations

> "Do not reconstruct past expectations from today's values."

`earnings_events.eps_estimate` is a current value overwritten by every refresh, so the consensus
as it stood a week before a release is not recoverable by any query. It is only recoverable by
having copied it out on the day.

**`estimate_snapshots`** — append-only. Issuer, provider's target-period label, metric, units,
accounting basis, provider, value, the provider's own `source_as_of` (distinct from our
`captured_at`), and the raw payload. A revision is a new row; the unique key includes
`captured_at`, so there is no update path. `accounting_basis` is written as NULL rather than
omitted: NULL records that the provider did not say, which is why these cannot later be compared
with an issuer-reported actual without crossing an unverified basis boundary.

**`macro_expectations`** — one row per release and reference period, with the expectation, its
source, when it was captured, and the first published actual kept **separate** from later
revisions. Two refusals, both raising rather than skipping silently:

- an expectation captured at or after `published_at` is rejected — a consensus read once the
  number is out is contaminated by it, and storing it would make every surprise look smaller
  than it was, in the direction that flatters the platform;
- an actual with no captured expectation is rejected rather than creating the row — an
  expectation that was never captured cannot be reconstructed from the result.

The first print is never overwritten. A market reacted to the number published on the day; a
later revision is a different fact, appended to `revisions` with what it supersedes.

### The earnings arm captures nothing, and that is a finding, not a bug

Running the job against production is what established this. Of 845 stored earnings events,
676 carry an `eps_estimate` — and **every one of them has already reported**. All 129 scheduled
events have none, and `revenue_estimate` is populated in 0 of 845. The column is therefore
written at or after the release: there is no forward consensus in it to capture.
`fundamentals_snapshot.eps_estimate` is populated in **0 of 2,476 rows**.

So the platform holds no forward EPS or revenue consensus anywhere. The earnings arm is kept —
it costs nothing and begins capturing the moment an upstream job writes one — but it currently
returns `captured: 0`, and the endpoint says so rather than letting a zero look like success.

### What the platform does hold forward, and is losing daily

`fundamentals.target_price` (164 of 189 symbols) and `fundamentals.forward_pe` (161 of 189) are
genuinely forward-looking, populated, and **overwritten by every refresh** — the same
unrecoverability, on data that actually exists. `capture_analyst_forwards` snapshots both with
the same discipline:

- `target_period` is `"no_stated_period"`. A target price describes no reporting period, and
  labelling one would repeat the fiscal-quarter mistake.
- The target price carries its missing horizon as a caveat: twelve months is the convention, the
  provider does not say so, and assuming it invents the field that makes the number comparable
  over time.
- The forward P/E records that **its denominator cannot be recovered** — the earnings estimate
  behind it is not stored, so neither its period nor its basis is knowable.
- A provider zero is captured, not dropped as missing.

**Scheduling.** `capture_prospective_estimates` runs daily at 11:00 UTC, before the US open and
ahead of the estimate-refresh jobs. `misfire_grace_time` is 2 hours, deliberately generous:
unlike most jobs this one has no second chance, because the value it would have recorded is
overwritten upstream. A 60-second grace is exactly what made a day permanently uncapturable in
`AUD-T398-MISFIREGAP`. Registered for liveness with that unrecoverability stated in the entry.

The selection rule is a pure function (`estimates_to_capture`) separated from the write, because
the service conftest stubs `db` and a function importing ORM classes cannot be imported from a
test — the fourth time that trap has needed this treatment.

## 5. Verification

- **Real PostgreSQL, relevance query** (`test_llm_usage_relevance_integration.py`, 7 tests) —
  creates `context` as a real `json` column and executes the production SQL. One test asserts
  the **uncast** query still fails on that column, so a dropped cast cannot go unnoticed.
- **Real PostgreSQL, persistence** (`test_quality_value_persistence_integration.py`, 15 tests) —
  immutability across a policy change, database-enforced uniqueness, revision handling, the
  backdated-capture refusals. 5 sabotages verified.
- **Pure selection rules** (13 tests) and **gate composition** (51 tests), 11 sabotages verified.
- **Scheduler wiring** (5 tests, 4 sabotages) — the service token, the cron trigger, the misfire
  grace and the failure path.
- **A red frontend suite now blocks a frontend deploy.** `scripts/deploy.sh` runs vitest and
  `tsc --noEmit` before building and refuses on failure (`SKIP_FRONTEND_TESTS=1` to override
  deliberately). `AUD-FRONTEND-SHIPPED-RED` happened because a documented rule did not hold; a
  gate at the moment of deployment does.

## 6. Two defects this round found in my own tests

- `test_latest_is_scoped_to_the_current_policy` **passed under sabotage**: it stored both rows
  under `MU`, and `latest_evaluations` dedupes by symbol, so it returned 1 whether or not the
  fingerprint filter existed. Rewritten with a symbol present only under the old policy.
- `test_the_relevance_block_cannot_blank_the_whole_panel` matched **its own explanatory
  comment** — the comment quotes the old `calls_with_relevance_data: 0`. Fourth occurrence of
  this trap here. The check now strips comment lines and matches numeric literals by regex.

## Still not built

Email remains impossible from this feature: no alert type is registered, no subscription exists,
no outbox row is written. The consent inversion (absence means NOT subscribed, against the
platform default) is recorded as a decision and not yet implemented, because there is nothing to
send — every company is still `insufficient_evidence`, durability and valuation having no stored
evidence for any of them.
