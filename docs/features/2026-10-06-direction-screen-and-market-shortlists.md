# Direction screen and US/HK shortlists

## Purpose and delivered local scope

Requested: quick direction, main factors, US/HK filters, top 20 per market, breakout/breakdown
watchlists and a strategy summary without reading the entire research record.

The Quality & Value page now leads with a separate read-only `/quality-value/setups` screen.
Filters: market (US/HK/both), setup, sector, symbol search, 10/20/50/100 per market. All active,
non-delisted platform listings are scanned BEFORE ranking; the old 200-name alphabetical
evaluation cap does not constrain this screen. Coverage is the platform universe, not every
listing on either exchange. Existing quality assessments and their eligibility are unchanged.
The quality coverage and company list are collapsed under a separate evidence section. Each
long company research summary opens with its evidence rather than filling the default list.

Cards show direction, three price/volume factors, support/resistance triggers, an approach and
collapsed evidence. No LLM, provider call, email, trade, persistence or new subscription is used
by the new endpoint. The existing quality evaluation endpoint still persists evaluations.

## Explicit v1 rules (hypotheses, not validated investment recommendations)

- Read 21 consecutive daily session labels from the existing US/HK calendar, excluding today's
  local session even after close. This conservative convention avoids treating a forming or
  not-finalised daily bar as complete; it intentionally delays same-day signals until tomorrow.
- Support/resistance: minimum low / maximum high of the 20 bars BEFORE the tested close.
- Range break up/down: close strictly above/below that range. This describes an observation;
  it does not establish a successful future breakout. Equality is not a break.
- Breakout watch: inside range, within 2% of resistance and above the preceding 20-close mean.
  Breakdown watch mirrors this at support and below that mean.
- Otherwise: inside range, no directional setup. This does not predict the price will stay still.
- Missing/duplicate/stale sessions, invalid OHLC/volume or changing adjustment factors yield
  Unknown. Never manufacture sideways from unavailable data.
- Relative volume: tested daily volume divided by the prior 20-session mean. No comparison of
  a partially completed day's volume with a full day's volume.
- Ranking separately in each market: observed breaks, watches, inside range, unknown; then
  relative volume descending, boundary distance ascending, symbol tie-break. Both directions
  are research priorities; filter direction to obtain a directional shortlist. Up to 20 means
  at most 20 matching listings per market, not a requirement to invent qualifying candidates.
- The 2%, 20-session windows and 5–20-session research horizon are design choices requiring
  prospective evaluation, not optimised or calibrated parameters. No success probabilities.

## Strategy summary for the reader

Use this as a two-stage workflow: price setups identify what to examine; company quality,
valuation, liquidity, event risk and personal risk constraints decide whether an entry deserves
consideration. A price setup does not override missing moat or valuation evidence.

| Setup | Research approach | What changes the reading |
|---|---|---|
| Breakout watch | Wait for completed close through resistance; check volume and event risk | Failure to break, or loss of support |
| Range break up | Examine hold/retest, avoid chasing an extended move | Close back below broken resistance |
| Breakdown watch / range break down | Review downside exposure; avoid automatic dip buying | Reclaim of support; reassess the business thesis |
| Inside range | Wait for a directional trigger | Boundary breaks with supporting evidence |
| Unknown | Resolve missing/stale evidence | Usable completed daily history |

Before any trade, define entry condition, invalidation, maximum loss, time horizon and exit
review. A stop price cannot guarantee realised loss in a gap. Downside screening is not an
automatic short strategy. A modelled valuation discount and a technical breakout are different
research approaches, with different horizons; do not combine them into a single probability.

## Remaining integrations, in order

1. Add authoritative instrument type and canonical listing IDs. Until then, disclose possible
   funds in the scan; do not pretend a name heuristic reliably excludes ETFs. Route banks and
   insurers to appropriate fundamental models. Resolve alias duplicates before counting issuers.
2. Add watchlist scope and liquidity floors by market (turnover in named local currency, spread
   and trading availability). Add event proximity, sector/benchmark relative strength, earnings
   and news risk. All unavailable inputs remain explicit. Never call low volume a liquid setup.
3. Complete corporate-action-adjusted OHLC provenance. Current raw bars check available
   adjusted-close factors; missing factors mean corporate-action verification is incomplete.
4. Use exchange close/auction schedules and ingestion finality to admit same-day completed bars.
   Current calendars include conservative half-day treatment and are not a full venue schedule.
5. Persist technical input values, rules, ranking population and observation time before outcome
   measurement. Track 5/10/20-session returns, false breaks, adverse excursion and coverage;
   include delistings, fees, spreads and slippage. Use chronological holdouts separately for US
   and HK. Define the success event and denominator before reporting any calibrated probability.
6. Opt-in daily digest only after that storage exists: US/HK sections, up to 20 each, short
   summaries and links. Existing absence-means-subscribed alert defaults must not apply. Later
   transition alerts require idempotency, expiration and explicit consent. No email enabled here.

## Acceptance and limits

Tests exercise range breaks/watches, invalid/stale input, corporate-action changes, per-market
ranking after filtering, and rendered card content/error states. An actual endpoint query runs
against SQLite with 206 listings, including an inactive listing and forming bars; it verifies
full-population ranking, exclusion of those bars and separate per-market limits. Local tests do not verify
production price conventions, authenticated browser interactions or database performance.
No production deployment is implied by this document.

Local verification: research-engine 461 tests, frontend 527 tests and TypeScript checking
passed. All 12 backend service suites passed across the runs. The combined `make test` run
was interrupted at a stalled gateway async test; gateway (54), decision-engine (390) and
event-intelligence (626) completed successfully when rerun outside the sandbox. This is not
a claim that the interrupted `make test` command itself returned success.

Support/resistance and volume are conventional technical tools, with false breaks possible:
[Fidelity support and resistance](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/support-and-resistance),
[Fidelity volume oscillator](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/volume-oscillator).
Those sources do not validate this implementation's thresholds or establish a trading edge.
