# Unusual Whales Integration — What It Does, How to Use It (2026-09-01)

## What Unusual Whales actually adds

Before this integration, every squeeze/gamma/options feature in this app ran on **free proxies**
built from yfinance data alone — real, but structurally limited:

| Concept | Free proxy (still the fallback) | Real Unusual Whales data |
|---|---|---|
| Gamma exposure | An open-interest-CONCENTRATION ratio in a ±5% strike band — explicitly disclaimed in `check_gamma_unwind_alerts()`'s own docstring as **"NOT a real GEX calculation."** No Black-Scholes, no dealer-positioning model. | Real, pre-calculated GEX: `call_wall`, `put_wall` (strikes where dealer gamma concentrates on each side), `gamma_flip` (the "zero gamma" price where dealer hedging flips direction), `gamma_magnet`. |
| Short interest | `short_percent_of_float`/`short_ratio` from yfinance's own `info` dict — a real number, but only refreshed as often as yfinance's own upstream source (often stale by 1-2 weeks, a known limitation this app's own short-squeeze audit has flagged before). | Real, current short-interest data: `short_shares_available`, `fee_rate` (borrow cost — a rising fee is real evidence a squeeze is getting harder to sustain from the short side), `rebate_rate`, `days_to_cover`, `si_float`. |
| Options positioning | `cp_ratio` (call/put volume ratio) computed from a live yfinance option-chain fetch, whale-count from a simple premium threshold. | The same, but Unusual Whales' own trial API additionally exposes **rule-based flow alerts** on the full options tape — see below, this is genuinely new capability, not just a more-accurate version of something already built. |

## Where it's wired in today

- **`GET /stocks/{symbol}/gamma-exposure`** — real `call_wall`/`put_wall`/`gamma_flip`/`gamma_magnet`,
  shown on the stock detail page's Market Pressure panel.
- **`compute_short_squeeze_score()`** (`routes.py`) — the Short Squeeze screener's 0-100 composite
  score gains a real `uw_borrow_fee_pts` component (up to 5 points) when a key is configured — a
  rising borrow fee is real, current evidence shorts are struggling to hold their position, on top
  of the free-proxy short-float/days-to-cover/momentum components that always run regardless.
- **`compute_options_pressure_score()`** (`routes.py`) — the options-pressure composite gains an
  optional `gex_proximity_pts` component (up to 20 points) when current price sits close to a real
  `gamma_flip`/`call_wall`/`put_wall` level — proximity to a real dealer-hedging inflection point is
  a stronger signal than the free cp_ratio/whale-count alone.
- **Both scores degrade gracefully with the feature off** — nothing breaks, the free-proxy
  components alone still produce a real score; Unusual Whales only ever ADDS points on top.

## How to enable it

1. **Settings → Market Pressure Data — Unusual Whales**: paste your API key, click Save.
2. Flip the toggle below it to **On**.
3. That's it — every consumer above (`gamma-exposure`, both composite scores) automatically starts
   using real data the next time it runs. No restart needed on your end; it reads the key/flag
   fresh from Redis on every request.
4. To stop billing/using the API: flip the toggle **Off** (keeps the key on file, useful if you're
   just pausing) or click **Remove** (deletes the key entirely). Either way, every consumer falls
   back to the free proxies immediately — nothing else changes.

**Cost note**: this is a real, metered API. At the trial tier (30,000 req/day, 1,000,000 req/min —
your current plan per your own message), the composite-score/GEX lookups above are cheap and
infrequent enough (one call per symbol per screener page-load, cached 15 min-6h depending on the
endpoint) that you will not meaningfully dent that daily budget from normal browsing. A real-time
polling alert (see below) is the one thing that could actually consume a meaningful share of a
daily budget if built naively — designed with that constraint in mind.

## What Unusual Whales does NOT unlock yet (real, honest limitations)

- **WebSocket real-time streaming (`wss://api.unusualwhales.com/socket`) requires the paid
  Advanced tier ($315/mo)** — confirmed directly against UW's own OpenAPI spec. Your trial key
  cannot open that socket. Anything built against your current plan has to poll the REST API on
  an interval, not subscribe to a live push feed.
- **Options-flow-alerts, greek-exposure, flow-per-expiry, and the full options tape are all real,
  available REST endpoints on your trial tier that this app has NOT yet integrated at all** — see
  the design below for the specific one that maps to your ask.

## Design: options directional/expiration alert (not yet built)

**What you asked for**: an alert on options call/sell activity, an implied expiration date, and a
predicted direction.

**What's honestly buildable, verified against UW's real API before committing to anything**:

`GET /api/option-trades/flow-alerts` is UW's own rule-based scanner over the full options tape —
it flags contracts hit by "repeated hits" (rapid same-contract trades, often a single large order
sweeping across multiple market makers) and returns, per alert: `ticker`, `type` (call/put),
`strike`, `expiry` (the exact date), `total_ask_side_prem` vs `total_bid_side_prem` (aggressive
BUYING at the ask vs. aggressive SELLING at the bid — a real, directional signal, not a guess),
`has_sweep` (urgency), `volume_oi_ratio` (how unusual relative to existing open interest),
`alert_rule` name. It also accepts real filter params including `min_bull_perc`/`min_bear_perc` —
**UW itself already computes a directional lean per alert**, not something this app would have to
invent.

**The honest framing, matching this app's own established alert-honesty discipline** (every
existing squeeze/gamma alert explicitly states it reports a MEASURED fact, never a prediction of
what happens next): this alert would report *"large, urgent options positioning was just detected
in SYMBOL — {call/put}, strike ${X}, expiring {date}, {N}x normal volume for this contract,
{ask-side/bid-side}-heavy premium"* — a real, measured, "smart money is doing something right now"
signal. It would NOT claim to predict whether the stock actually moves, or by how much — the same
honesty line this app draws for T257-VOLUME-ANOMALY-ALERT and every squeeze alert already shipped.

**Design sketch, following the established `check_short_squeeze_alerts()`/`check_gamma_unwind_
alerts()` pattern exactly**:
- A new scheduled job (`check_options_flow_alerts()`), polling `/api/option-trades/flow-alerts`
  on an interval — REST polling, not the WebSocket, per the plan constraint above.
- Scoped to symbols users actually watch (matching every prior alert's own established
  "PriceAlert-subscribed audience, not the whole universe" scope-narrowing convention) rather than
  the full options-eligible universe, to keep the request budget bounded and predictable.
- Filtered by real UW query params (`min_premium`, `min_volume_oi_ratio`, `is_sweep=true`,
  `min_dte`/`max_dte`) to a genuinely high-conviction threshold — not every repeated-hit alert,
  only the ones large/unusual enough to be worth a real email, matching the "most cycles qualify
  zero picks, and that's correct" framing already established for T257-TOP3-CONVICTION-ALERT.
- Per-user, per-contract dedup (matching every existing alert's own Redis-key convention) so the
  same contract sweep doesn't re-fire on every poll cycle.
- A new outcome-tracking table (matching `SqueezeAlertOutcome`'s own established pattern) so this
  alert's own real forward-return accuracy gets measured over time, the same discipline already
  applied to every other alert type in this app — never shipped as "trust it" with no way to check
  whether it actually helped.

**Not yet built. If you want this next, say so explicitly and I'll scope the exact query
parameters/thresholds and build it the same way every other alert in this app was built** — walk
through the real endpoint, write the job, test it, adversarially verify it, deploy it.
