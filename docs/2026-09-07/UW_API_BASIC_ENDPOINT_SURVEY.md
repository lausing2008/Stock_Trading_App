# Unusual Whales — What the API BASIC Tier Actually Gives Us (surveyed 2026-09-07)

**Method:** parsed UW's own live OpenAPI spec (`GET /api/openapi`, 1.1 MB of YAML, **226
documented paths**), then **probed candidates live against our real key**. Documented ≠
entitled — the tier gates access, and only a real call reveals which side of the line an
endpoint falls on. Every status below is measured, not inferred from docs.

**Tier confirmed from response headers:** `x-uw-token-req-limit: 120000`,
`x-uw-daily-req-count: 12653` at time of survey (~10% of budget used).

---

## Direct answers to what was asked

| Asked about | Available? | Detail |
|---|---|---|
| **Earnings call transcripts** | ❌ **403** | `advanced_tier_required`. **Already wired in our code** — see the "already-built, silently dead" section below. |
| **Futures** | ❌ **403** | `futures_access_required` — needs Advanced tier. 7 endpoints (contracts, flow, candles, stats, trades). |
| **Predictions** (prediction markets) | ✅ **200** | 4 of 5 work: whales, smart-money, unusual, insiders. Categories span Crypto/Culture/Finance/Games/Politics/Sports/Weather. |
| **Private markets** | ❌ **422** | `Missing access for private markets` — separate entitlement, not just a tier step. 9 endpoints. |
| **Stock screener** | ✅ **200** | `/screener/stocks` (rich: implied_move_7/30, call/put premium + bid/side splits, rv_1d_last_12q, avg30_volume), `/screener/analysts` (500 rows), `/screener/option-contracts` (already wired). |

---

## Fully accessible and NOT yet wired — the actual opportunity

| Endpoint | Rows returned | Why it may matter here |
|---|---|---|
| `/api/screener/stocks` | 50 | Server-side screening on options-derived fields we currently compute ourselves or lack entirely (implied move, premium splits, realized vol over 12 quarters). |
| `/api/screener/analysts` | 500 | Analyst actions w/ target, sector, recommendation, analyst_name. |
| `/api/institution/{t}/ownership` | 50 | Per-ticker institutional holders w/ avg_price + filing_date. |
| `/api/institutions/latest_filings` | 50 | Fresh 13F filings, hedge-fund flagged. |
| `/api/institution/{name}/holdings` | — | Follow a specific institution's book. |
| `/api/etfs/{t}/holdings` | 250 | Real ETF constituents. |
| `/api/etfs/{t}/in-outflow` | 750 | **ETF creation/redemption flow** — genuine money-in/out, distinct from anything we track. |
| `/api/etfs/{t}/exposure` | 22 | Which ETFs hold a given name, and at what weight. |
| `/api/market/insider-buy-sells` | 500 | Market-wide insider aggregate (purchases/sells + notional). |
| `/api/market/fda-calendar` | 100 | FDA catalysts — event-driven, nothing equivalent today. |
| `/api/market/total-options-volume` | 1 | Market-wide call/put volume + premium. |
| `/api/market/oi-change` | ✅ | Open-interest change, market-wide. |
| `/api/market/sector-etfs` | ✅ | Sector ETF snapshot. |
| `/api/insider/transactions` | 500 | Per-transaction insider detail. |
| `/api/lit-flow/{t}` | 500 | Lit (exchange) prints — complements the dark-pool data already wired. |
| `/api/volatility/character/top` | 50 | Vol regime characterisation: half_life_days, hurst_rv, entropy. |
| `/api/volatility/anomaly/top` | ✅ | Requires `direction=short_vol\|long_vol` (422 without it). |

## Gated — do NOT build against these without a tier upgrade

| Endpoint(s) | Error code |
|---|---|
| `/api/companies/{t}/transcripts/{q}` | `advanced_tier_required` |
| `/api/companies/{t}/earnings-estimates`, `/profile`, `/dividends`, `/listings` | `advanced_tier_required` |
| `/api/futures/*` (7) | `futures_access_required` |
| `/api/private-markets/*` (9) | `Missing access for private markets` (422, separate entitlement) |
| `/api/volatility/vix-term-structure` | `volatility_scope_required` |

**Three distinct entitlement axes**, not one ladder: tier (`advanced_tier_required`), product
add-ons (`futures_access_required`, private markets), and scopes (`volatility_scope_required`).
An "Advanced" upgrade would unlock transcripts + company fundamentals, but **futures, private
markets, and VIX term structure look like separate purchases** — worth confirming with UW
before assuming one upgrade buys all of it.

## Documented but 404 on live call (spec is ahead of deployment)

`/api/market/spike`, `/api/analytics/sectors`, `/api/options-pulse/{t}`, `/api/group-flow/{t}`,
`/api/politics/recent-trades`, `/api/politician-portfolios`, `/api/crypto/tickers`,
`/api/forex/tickers`.

Consistent with `docs/incidents/external-data-source-liveness.md`: presence in a spec is not
evidence an endpoint is live. **Always probe before designing against one.**

---

## Already-built but silently dead: earnings-call transcripts

`get_earnings_transcript()` is wired and feeds the post-earnings "management tone" LLM email
(tier 338). It returns **403 on our tier**, and the fetcher fails open to `[]` — deliberately,
its docstring already names a tier-403 as an anticipated failure. So the feature degrades
silently and correctly, **but the management-tone content it was built for has never rendered**.

This is not a bug. It IS a reason to be skeptical of "we integrated X" claims where X sits
behind an entitlement: fail-open plumbing means the absence produces no error, no alert, and no
visible difference — exactly the shape that survives review indefinitely. If transcripts matter,
the Advanced tier is what unlocks an already-written feature; if not, that code is dead weight
worth flagging.

---

## Recommendation

**Highest value, zero new spend** (all confirmed 200):

1. **`/api/etfs/{t}/in-outflow`** — real ETF fund flows. No current equivalent anywhere on the
   platform, and it measures actual money movement rather than a derived proxy.
2. **`/api/screener/stocks`** — server-side screening on options fields, including implied move
   and premium bid/ask splits we don't have.
3. **`/api/market/fda-calendar`** — a catalyst class the platform is entirely blind to today.
4. **`/api/institution/{t}/ownership` + `/institutions/latest_filings`** — 13F-grade
   institutional positioning w/ avg cost basis.

**Deliberately NOT recommending prediction markets** despite full access: the categories are
Sports/Politics/Culture/Weather-heavy, which is a different domain from equities/options
trading. Interesting, but no obvious path to improving signal quality here — it would be
building because we can, not because it helps.

**Before any build:** these are new daily-request draws. Budget is 120k/day with ~12.6k used, so
there is ample headroom — but see `project_uw_budget_and_redundancy` for how quickly two
per-minute jobs consumed 95% of the old 30k quota. Per-symbol-per-minute polling is what breaks
the budget; daily batch snapshots do not.
