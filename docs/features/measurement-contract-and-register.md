# Measurement contract and pending-work register (`shared/metrics/`)

Milestone A of [the measurement framework](2026-09-30-measurement-framework-and-improvement-backlog.md):
"metric registry, status register, versioned extract, known missingness."

**Built 2026-10-01. Additive and inert in production** — no production module imports it yet, no
schema change, no migration. `shared/` normally forces a 12-backend rebuild; this does not,
because nothing in a container loads it. It becomes live when the first consumer is written.

## Why a contract rather than a dashboard

Three headline findings from the 2026-09-30 checkpoint were withdrawn within a day, and all
three failed identically: **the reported quantity was not the quantity production consumes.**

| Withdrawn claim | What was actually measured |
|---|---|
| Confidence "flat 36.5–43.9%" | Pooled across slices production splits. Sliced as the consumer slices: 59 supported slices, wide dispersion (LONG/US 0–40 **53.0%** vs 85+ **35.9%**) |
| "Bearish flow is anti-predictive" | Counted option-chain rows the consumer dedupes. Equal symbol/date weighting **reverses** the gap (bullish 42.3%, bearish 47.1%) |
| GEX corroboration comparison | Pooled raw returns across a bullish and a bearish thesis, so the **sign was backwards** (thesis-signed: −1.87% vs +2.28%) |

None was a coding error. Each was a number published without declaring its population,
weighting, or outcome definition — which is why none could be caught by reading it. A dashboard
built on top of that would have rendered all three confidently.

So this module enforces the declaration instead of displaying the result. `MetricDefinition`
cannot be constructed without every dimension §4 requires, so an under-specified metric cannot
be published at all.

## The rules that have teeth

Each is a refusal, and each maps to a number this project actually got wrong:

- **Operational metrics may not declare an outcome.** A counter proving a job ran cannot certify
  strategy quality. Non-operational metrics must declare one, and must name their decision
  authority — M04's withdrawn rate summed shadow and authoritative rejections.
- **A zero denominator is UNDEFINED, never 0.0.** "No candidate reached the gate" and "the gate
  rejected nothing" are different facts; only one is evidence about the gate. Same rule as
  §6's profit factor: display undefined, not an impressive infinity.
- **Unknown renders as `unknown`.** Never as `0`, and an unknown without a reason is rejected.
- **Window bounds must be timezone-aware**, end exclusive —
  [utc-vs-et-date-boundary](../incidents/utc-vs-et-date-boundary.md) is this repo's documented
  one-session-drift bug.
- **Exclusions require reasons.** A silent filter is indistinguishable from the bug it replaced.
- **Changing a formula needs a new version.** Same-version redefinition is refused, so a tile
  cannot keep its name while its meaning changes.
- **Comparability ignores version/owner/notes but not weighting, cohort or authority.**

`reconciliation.py` carries §9's acceptance identities as executable checks — gate funnel,
authority split, cohort totals, delivery attempts, order lifecycle, equity. Each returns a
reason, not a bool, because "these disagree" is only useful with *by how much* and *which one*.

## M04, the first metric on it

`metrics.anti_chase` computes the funnel from `paper_entry_scan_logs.skip_tally`. It
**reconciles before it publishes**: if `authoritative + shadow ≠ rejected`, or rejections exceed
reach, every derived rate is UNKNOWN carrying the failing identity. A rate computed from counts
known to disagree is worse than nothing because it looks fine.

Two limits are encoded rather than discovered later:

- **The denominator is the population that reached the gate** — not all BUY signals. Anti-chase
  runs after the watchlist and conviction gates, which is why the old "30.1% of BUY signals carry
  `roc_10 ≥ 10`" was never comparable to a ~17% incremental block rate.
- **The unique-event rate is permanently UNKNOWN.** `skip_tally` has no symbol identity, so the
  same symbol rejected on twenty consecutive cycles contributes twenty. Repeated checks are not
  unique bets, so the attempt rate must never be served in the unique-event slot.

It is OPERATIONAL. It measures what share of reached entries the gate stopped, and nothing about
whether stopping them helped — that needs vetoed candidates' counterfactual outcomes.

## The register

`metrics.register` carries M01–M25 with §2's three independent status axes (engineering /
research / decision), so `reported_deployed` + `collecting` + `no_action` is expressible — the
most common state here, and the one that gets misread as "working".

§8's rule is enforced: a `Trigger` must name a **condition**, not a date. `"check next month"`
raises. Every item partitions into exactly one of data-gated (10), awaiting-approval (2),
blocked (2), actionable-now (11) — an item in two buckets means its real blocker was never
identified.

**The dependency this surfaced:** M22 (Jev) and M17 are blocked on **M20** (outbox / immutable
event IDs). Neither design doc states it outright, but M22 requires reusing the experiment
registry and JEV-05's paired trial cannot be interpreted without durable delivery. Encoded as
`blocked_by` so it is checkable rather than remembered.

## Verification

53 behavioural tests (`services/market-data/tests/test_metrics_contract.py`), exercising the real
classes — the one source-text assertion checks the authoritative-source tuple against
`paper_trading_engine.py` so the contract cannot drift from the engine.

**Eight sabotage runs, all caught:** empty population reporting 0.0; the unique-event slot
silently serving the attempt rate; dropped authority-split reconciliation; operational metric
allowed an outcome; registry accepting a changed formula under one version; vague-trigger guard
removed; ACTIVATED-without-evidence guard removed; Jev's blocker deleted.

Suites: market-data **4,714 passed**, signal-engine 549, event-intelligence 581,
news-intelligence 129. T401 ratchet unchanged at 180.
