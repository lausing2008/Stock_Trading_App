# Quality & Value: shadow evaluation and dashboard (built)

Built 2026-10-06. Implements steps 1–3 of
[the Quality & Value dashboard and alerts plan](2026-10-06-quality-value-opportunities-dashboard-and-alerts.md)
— inventory and contracts, the offline evaluator, and the shadow dashboard. **Steps 4–6
(notification dry run, opt-in release, expansion) are deliberately not built.** No alert type is
registered, no subscription exists, no outbox row is written and nothing can send email.

## What shipped

- `services/research-engine/src/intel_reports/quality_value.py` — the gate contract and
  composition, ORM-free so it can be tested directly.
- `services/research-engine/src/api/quality_value_routes.py` — `GET /quality-value/evaluations`,
  read-only, persisting nothing.
- `frontend/src/pages/quality-value.tsx` — the shadow dashboard, led by evidence coverage.
- `services/api-gateway/src/api/proxy.py` — the `quality-value` prefix, added in the same commit
  as the router because a prefix absent from that map 404s however complete the backend is.
- 33 tests, every composition rule sabotage-verified.

## The composition rule, and why there is no score

A required gate cannot be compensated for. Gates compose by AND over a declared required set —
business quality, competitive durability, valuation, entry condition and value-trap risk — and
there is no weighting anywhere, because any weighting lets a strong technical reading outvote
absent fundamental evidence, which is the failure a quality-and-value screen exists to avoid.
`catalysts` and `portfolio_fit` are deliberately NOT required: a catalyst is optional context,
and inventing a deadline for price convergence is worse than having none.

Three distinctions the status enum exists to keep:

| | Meaning | Closed by |
|---|---|---|
| `UNKNOWN` | No evidence either way | Acquiring data |
| `FAIL` | Evidence that the gate's condition is not met | The company or its price changing |
| `BLOCKED` | Evidence of a disqualifying condition | Nothing — it is a finding, not a gap |

`UNKNOWN` is not `PASS`, which is the direction this codebase has got wrong repeatedly: a check
written as "stop if it looks wrong" never fires when the number is missing, so it becomes
"allow" at exactly the inputs it existed to catch. `compose()` requires an explicit `PASS`, and
a gate never evaluated at all blocks rather than defaulting.

One ordering is deliberate: a company with both a data gap and a failed gate reports as
insufficiently evidenced, not as failed — the failure was judged on incomplete evidence and may
not survive the missing input.

## What today's evidence can decide — and what it cannot

Measured against production in
[the readiness inventory](../audits/2026-10-06-quality-value-readiness-inventory.md):

- **Business quality** — computable. 156 symbols have ≥2 annual statements. The gate refuses on
  too few periods and on either kind of staleness, which are two different facts: retrieval age
  (uniformly ~1 month; closed by refetching) and reported-year age (3 of 156 symbols; closed
  only by the issuer filing).
- **Entry condition** — computable. Two completed closes above a 20-session average that
  excludes the close it is compared against. A testable heuristic, labelled one; it is the only
  timing input because combining correlated indicators manufactures agreement rather than
  evidence.
- **Competitive durability** — `UNKNOWN` for every company. Nothing about switching costs, cost
  position, network effects, intangibles or distribution is stored. Margin persistence in the
  statement series is **not** evidence of durability; for a cyclical business it is equally
  consistent with the cycle.
- **Valuation** — `UNKNOWN` for every company. See below.
- **Value-trap risk** — `UNKNOWN` even when leverage and cash burn both look fine, because those
  are the only two classes observable. Returning `PASS` there would read as "no value trap" when
  what was established is "no value trap of the two kinds we can see". Structural demand
  decline, customer concentration, restatements and refinancing schedules are stored nowhere.

**So no company is eligible, and the dashboard says so as a result rather than an error.** Two of
five required gates have no evidence for any company in the universe.

## Valuation values the whole equity and never a share

The plan specified per-share arithmetic. There is no share count to make a per-share value with:
`financial_statements` has no share or dilution column at all, and the one `shares_outstanding`
column that exists (on `institutional_ownership`) covers 5 tickers and is the issuer total
copied onto each institution's 13F row so an ownership percentage can be divided out.

Both routes to inventing one are worse than the gap. From net income and EPS the division
silently adopts whichever accounting basis the EPS used — and no basis is stored. From market
cap ÷ price the implied count absorbs every intervening buyback, issuance and split, because the
stored caps are up to 25 days old against a current price.

So the pilot compares a whole-equity value against market capitalisation:
`discount = (V − cap) / V`. This removes a quantity there is no evidence for from the
arithmetic. The cost is real and stated on the dashboard: an aggregate discount cannot be
converted to a per-share target, and is not comparable across a share issuance.

`discount()` and `upside()` are separate functions with **different denominators**, and both are
shown with their denominator named: the same gap is 20% off the value and 25% of upside, and
presenting one as the other overstates by exactly the amount the reader is trying to judge.
Neither divides by zero or a negative value, and a missing input returns `None` rather than
`0`, because a zero discount sorts as "fairly valued" while unknown must not.

## Consent is inverted for this feature, deliberately

`alert_preferences` has **zero rows** in production, and the existing semantics treat an absent
row as subscribed. Registering `quality_value_opportunity` under that default would subscribe
every user at the moment of registration, without anyone opting in. This feature therefore
requires an explicit enabled row to send, with absence meaning NOT subscribed — the opposite of
the platform default. Recorded here so the inconsistency is a decision rather than a surprise.

Nothing registers the type yet. That is step 4.

## Not built, and why

Persistence of evaluations, episode/transition history, the notification outbox, hysteresis and
cooldown, and any email path. All of them depend on an evaluation worth persisting, and today
every evaluation is `insufficient_evidence` for the same two reasons. Building a transition
ledger over a constant would be building machinery for a signal that does not yet exist.

The dependency the plan already names holds: *"the companion fundamentals/moat work must supply
evidence before entry-ready eligibility. The dashboard shell can ship with honest blocked
states; inventing a moat or fair value to populate it cannot."*
