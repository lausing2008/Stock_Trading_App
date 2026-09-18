# Fed Watch — market-implied odds of an FOMC move

## T405-FEDWATCH (built 2026-09-18)

**The gap.** FOMC existed in this app only as a **blackout gate** — `paper_trading_engine` and
`decision-engine/hard_rejects` both refuse entries near a meeting, because "FOMC, CPI, NFP, PCE
cause unpredictable 1-3% moves". That says a meeting is *risky* without saying what the market
thinks will *happen* at it. This fills in the second half.

## Where the numbers come from

**CBOT 30-Day Fed Funds futures (ZQ)** — the same instrument CME's own FedWatch uses. A ZQ
contract settles to `100 − (average daily effective fed funds rate over the contract month)`, so
its price *is* a market-implied forecast of that month's average rate.

Both inputs already existed in this app:

- **FOMC dates** — `economic_events` where `event_type = 'fomc_meeting'` (source `fed_calendar`),
  24 rows through 2027. Not a hardcoded list that silently goes stale every January.
- **Futures quotes** — yfinance `history()`, which kept working throughout the options outage;
  only Yahoo's *options* endpoint broke.

## The arithmetic, which is the whole tool

An FOMC decision takes effect the day after the meeting, so a month containing a meeting on day
`D` of `N` averages the old rate for `D` days and the new rate for `N − D`:

```
implied_average = (D/N)·rate_before + ((N−D)/N)·rate_after
    ⟹  rate_after = (N·implied_average − D·rate_before) / (N − D)
```

**The interpretation that matters.** The Fed moves in 25bp steps, so an expected change of
−12.5bp is *not* a forecast of a 12.5bp cut — no such move exists. It is the market pricing
roughly **50% of a 25bp cut and 50% of no change**. Every probability on the page expresses that
blend, and changes beyond one increment blend the two nearest steps (−30bp → 80% of −25, 20% of
−50).

**Meetings are chained, not independent.** Each meeting's `rate_before` is the previous meeting's
derived `rate_after`. Treating every meeting as starting from today's rate would re-price
earlier cuts and double count them.

## What it refuses to do

- **A meeting on the final day of its month returns `None`, not an estimate.** Zero days of the
  new rate fall inside that contract, so the month's average carries no information about it.
  That is a genuine absence, and approximating it would be inventing a number.
- **A missing contract is reported, never skipped** (`available: false`, `no_futures_quote`), so
  a data gap cannot masquerade as a shorter Fed calendar.
- **Meetings with fewer than 5 remaining days are flagged `low_precision`** — one or two days of
  new rate has to carry the whole inference, so a small quote error is amplified enormously. The
  number is still shown, flagged rather than hidden.

## Stated limits, carried in the payload

These travel in the API response itself, not only in the docs, so they follow the numbers
wherever they are rendered or copied:

1. Market **expectations** priced into futures — not a forecast by this app, not a Fed communication.
2. ZQ settles on the **effective** rate, which trades a few bp inside the target band. The levels
   here are effective-rate expectations, **not the target range**.
3. Futures carry a small risk/term premium, so implied probabilities lean slightly toward the
   direction of carry. CME's published FedWatch makes the same simplification.
4. A 70% chance of a cut means cuts do **not** happen 30% of the time — and the market is often
   wrong by considerably more.

## Surface

`GET /fed-watch` (market-data, registered in the gateway's `_ROUTES` **in the same change** —
`docs/incidents/gateway-proxy-route-gaps.md` records three separate occasions where a complete
backend 404'd purely because its prefix never reached that dict). Cached 15 min in Redis: futures
move all day but the derived probabilities move slowly, and one yfinance call per viewer is
pointless. One quote per distinct contract *month*, never one per meeting, since two meetings can
share a month.

Page at `/fed-watch` under Tools.

18 behavioural tests against the pure module — the arithmetic *is* the product, and a wrong
probability is not a cosmetic bug but a confidently-stated wrong claim about what the market
expects. Controls included: a flat month must decompose to exactly no move, a fully-priced 25bp
cut must recover exactly 3.75 from 4.00, and probabilities must sum to 100% across seven
different expected changes.

## Not built

**A hike/cut forecast of our own.** This reports what the market prices. Building a model that
disagrees with the fed funds futures curve is a research project, not a feature, and one where
being wrong is expensive and easy.
