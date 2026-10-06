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
- **Value-trap risk** — `UNKNOWN` for every company, with the two computable figures reported
  as labelled observations rather than a verdict. See the retraction below.

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


## Retraction: the first value-trap thresholds were measuring capital intensity

**Shipped, then retracted the same day, by running it.** The first version of
`value_trap_gate()` returned `BLOCKED` on net debt above 2× equity or two consecutive negative
free-cash-flow years. Against production it flagged **48 of 200 companies** as a thesis at risk
— 24% of the universe — and reading the list is what refuted it:

| Company | Figures | Why the verdict was wrong |
|---|---|---|
| LMT | net debt 2.62× equity, FCF $6.9bn, up from $5.3bn | Buybacks shrink equity, so the ratio rises as the company returns money it is plainly earning |
| CM | net debt 2.68× equity | A bank. Net debt against equity is not a solvency reading for a bank at all |
| VST, CWEN, NATL | 3.78×, 4.77×, 6.06×, all FCF-positive | Power and utilities. High structural leverage against positive cash flow is the capital model |
| ORCL | FCF −$23.7bn then −$0.4bn | Operating cash flow spent on capacity. The statements cannot separate that from distress |

The thresholds were not measuring a value trap. They were measuring capital intensity, buybacks
and business model. `BLOCKED` is reserved for **evidence of a disqualifying condition**, and a
ratio crossing a number chosen without validation is not that — the plan says so directly:
*"Do not ship an arbitrary 20%/30% threshold as empirically proven. Select provisional
thresholds in shadow mode and freeze them before prospective evaluation."*

What changed: the figures are still computed and still shown, as observations carrying their own
caveat (the ratio rises with buybacks; the statements cannot separate investment from distress).
They no longer render a verdict. Banks, insurers, REITs and capital-markets businesses have the
leverage figure marked not applicable rather than merely high. The one remaining route to
`BLOCKED` is a disqualifying finding supplied by something that actually established one —
nothing in the platform supplies these yet, and the path stays live and tested so that when a
source does, it is not a new mechanism.

Six tests pin the retraction, each sabotage-verified, using the real figures above.

## What running it against production also caught

A `TypeError` that would have made every call a 500: `FinancialStatement.fetched_at` is a naive
`DateTime` and this endpoint took its age against an aware `datetime.now(timezone.utc)`. The
report generators had already settled the convention (`_naive_utc_now()`); this endpoint was
written against the other one. The shipped business-performance adapter was checked and is not
affected. The helper moved to the ORM-free module to be testable at all — the service conftest
stubs `db`, so anything importing it cannot be imported from a test.

Neither defect was reachable by the unit tests. Both were found in the first real run.
