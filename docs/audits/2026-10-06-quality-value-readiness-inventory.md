# Quality & Value pilot: data readiness inventory

Step 1 of [the Quality & Value dashboard and alerts plan](../features/2026-10-06-quality-value-opportunities-dashboard-and-alerts.md)
— *"audit data readiness for the pilot, existing alert-consent behaviour, statement vintages and
valuation inputs"* — run against the production database on 2026-10-06 before any evaluator was
written. Read-only. Nothing was ingested, enabled or sent.

The purpose is to decide what the pilot may compute at all. A dashboard shell can ship with
honest blocked states; a fair value invented to populate it cannot.

## What is actually there

| Input | Measured | Usable for the pilot? |
|---|---|---|
| Universe | 189 active, non-delisted stocks | Yes |
| Annual statements | 1,424 rows, 156 symbols with ≥2 annuals | Yes |
| Statement **retrieval** age | `max(fetched_at)` = 2026-09-07, uniform — every row | One month old, uniformly |
| Statement **reported-year** gap | 3 of 156 symbols lack an annual within 400 days: 9992.HK, ZS, MU | Narrow, and all three are non-calendar fiscal years |
| Market capitalisation | 167 of 189 symbols, fetched between 2026-09-11 and 2026-10-06 | Yes, with a freshness gate |
| Share count | **Nowhere.** `financial_statements` has no share or dilution column at all | No |
| `institutional_ownership.shares_outstanding` | 5 tickers only, and it is the issuer total repeated on every 13F row (AAPL: 14,594,180,000 on all five rows, report_date 2026-06-30) | No — a denominator for ownership %, not a maintained series |
| Accounting basis | Not stored on any statement row | No |
| Filing date | Not stored; only retrieval time | No |
| `alert_preferences` | **Zero rows** | See consent, below |

## Two corrections to my own earlier audit

**1. "The table is stale" was too broad.** The
[fundamentals audit](2026-10-06-fundamentals-data-audit.md) generalised MU's missing fiscal year
into a statement about the table. Measured, these are two different facts with different
remedies: retrieval is uniformly one month old (a refresh fixes it), while only 3 of 156 symbols
are missing a reported year, all of them non-calendar filers whose year was reported after the
last fetch (a coverage check fixes it). For the other 153 the stored series is current. The
business-performance section's docstring now carries both figures rather than the blended claim.

**2. "No share count anywhere" was right about the thing that matters, for a reason I had not
measured.** `shares_outstanding` does exist as a column — but on `institutional_ownership`,
covering 5 tickers, and the five AAPL rows above show what it is: one issuer-level figure copied
onto each institution's holding so an ownership percentage can be divided out. It is not a
per-issuer series, it is not maintained for the universe, and it carries no basis or date of its
own. The conclusion stands; the evidence for it is this, not an absence.

## The consequence for valuation: value the whole equity, never a share

The plan specifies per-share arithmetic — *"for price P and positive per-share base valuation V,
display discount as `(V - P) / V`"*. **There is no share count to make V per-share with**, and
the two routes to inventing one are both worse than the gap:

- **From net income and EPS** — the plan already forbids this (*"do not infer missing share
  counts from incompatible EPS"*), and the stored rows carry no accounting basis, so the
  division would silently adopt whichever basis the EPS used.
- **From market cap ÷ price** — arithmetically consistent only if both are the same instant and
  the same share class. The caps here are up to 25 days old against a current price, so the
  implied count would absorb every intervening buyback, issuance and split as if it were
  valuation. A stale cap divided by a fresh price is not a share count; it is an error term
  wearing one.

**So the pilot values the equity in aggregate and never computes a per-share figure.** Market
capitalisation is the price of the whole equity, and the stored statements are whole-company
figures, so the comparison needs no share count at either end:

    discount = (V_equity - market_cap) / V_equity

This is not a workaround — it removes a quantity the platform has no evidence for from the
calculation entirely. What it costs is real and must be stated on the dashboard: an aggregate
discount **cannot** be converted to a per-share target price, and it is **not comparable across
a share issuance**, because the market cap moves with the count while the statements do not.

## Consent: `alert_preferences` is empty, and that means subscribed

The table has zero rows. Per the existing semantics
(`docs/features/admin-and-settings.md` — "Absence = subscribed, filter fails OPEN"), an absent
row means subscribed, so every registered alert type currently resolves to "send" for every
user. That is a deliberate earlier decision for alert types people already expected.

**This feature must not inherit it.** The plan requires *"its own positive subscription
requirement rather than inheriting opt-in from an unrelated price alert"*, and the empty table
is why that matters concretely: if `quality_value_opportunity` were registered under the
existing default, registering it would subscribe every user at the moment of registration,
without anyone opting in to anything. The pilot therefore requires an explicit enabled row to
send, with the absence of a row meaning NOT subscribed — the opposite of the platform default,
stated here so the inconsistency is a recorded decision rather than a surprise.

## What this permits the pilot to compute today

| Gate | Status | Why |
|---|---|---|
| Business quality | **Can compute** | ≥2 annual statements for 156 symbols; already built and shipped in Stock Outlook |
| Competitive durability | **BLOCKED — insufficient evidence** | Needs sourced switching costs, cost advantage, networks, IP. None is stored. A statement series cannot evidence it |
| Valuation | **BLOCKED — inputs incomplete** | Aggregate route is open (above), but a defensible V still needs normalized earnings or cash-flow assumptions that are not yet specified or frozen |
| Entry condition | **Can compute** | Completed-session prices exist and are already gated for session completeness |
| Value-trap risk | **PARTIAL** | Leverage, cash burn and dilution-by-proxy are computable from the statements; demand decline, customer concentration and accounting issues are not stored |
| Catalysts | **Can compute** | Event-intelligence holds scheduled releases |
| Portfolio fit | **Out of pilot scope** | Requires authorized holdings read |

Two of seven gates can be evaluated today, two partially, and **the two that decide eligibility
— durability and valuation — cannot**. That is the honest shape of the shadow dashboard: it will
report `insufficient evidence` for every symbol until the companion fundamentals/moat work
supplies the evidence, and that empty eligible list is the correct result, not a defect.

## What this inventory does not establish

It does not establish that the stored statements are comparable across years (no basis), that
the provider's period labels match the issuers' fiscal ends (not verified against any filing),
or that a refresh would close the three reported-year gaps (the provider may not yet carry
them). Each of those is its own piece of work, already queued.
