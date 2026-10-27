# Market Indexes, Futures and Macro Gauges + Detail Pages (Tier 474, 2026-10-26)

## What was added

The dashboard's **US Markets** card now has three labeled rows (it previously showed S&P 500,
NASDAQ, Dow and VIX only):

| Row | Symbols (Yahoo ticker) |
|---|---|
| Indexes | S&P 500 `^GSPC`, NASDAQ `^IXIC`, Dow Jones `^DJI`, **Russell 2000 `^RUT`**, VIX `^VIX` |
| Futures | **S&P `ES=F`, Dow `YM=F`, Nasdaq `NQ=F`, Russell 2000 `RTY=F`** |
| Rates & Macro | **10-Yr Yield `^TNX`, Gold `GC=F`, Bitcoin `BTC-USD`** |

Bold = new. HK still shows Hang Seng `^HSI`.

Every tile on that card (US and HK) is a link to **`/market/[ticker]`** (e.g. `/market/ES%3DF`): the
quote and change vs the previous close, a delay badge, a 1D / 5D / 1M / 6M / 1Y / 5Y chart, open,
day range, 52-week range, and a one-line description of the instrument.

## How to access it

Dashboard (`/`) → US Markets card → click any tile. There is no separate nav entry; the
dashboard is the index of these pages.

## How fresh the numbers are

Quotes come from Yahoo via yfinance. Yahoo's own `exchangeDataDelayedBy`, measured 2026-10-26:

| Delay | Symbols |
|---|---|
| 0 min | S&P 500, NASDAQ, Dow, Bitcoin, Hang Seng |
| 10 min | ES, YM, NQ, RTY futures, Gold |
| 15 min | Russell 2000, VIX, 10-Yr |

On top of that: the server caches quotes for 60 s (`stockai:market_overview`) and the page polls
every 60 s, so add up to ~2 min. True real-time CME/CBOE data would need a paid exchange feed —
Unusual Whales does not supply index or futures quotes.

## Design decisions

- **Change baseline is `regular_market_previous_close`, not `previous_close`.** fast_info's
  `previous_close` is derived from daily history and for futures points one session too far
  back: on 2026-10-26 ES=F read **+0.67%** against it vs the real **+0.13%** (Yahoo's own
  figure). `previous_close` is only the fallback when the regular value is NaN (BTC-USD). This
  also nudges S&P/NASDAQ/Dow by a few hundredths of a percent versus before.
- **10-Yr yield change is shown in basis points.** A percent change of a yield ("−0.04%" of
  4.99%) is not how anyone reads it.
- **VIX colours are inverted** (up = red) on the tile and the detail page.
- **Nothing is stored.** History is fetched from Yahoo on demand by
  `GET /stocks/market_index/{ticker}?range=` and cached in Redis per range (60 s for 1D up to
  1 h for 6M+). These symbols are deliberately not `Stock` rows, so they never enter signals,
  rankings, alerts or backtests. If that is wanted later, it is a separate, larger change.
- **The endpoint is an allowlist** of the dashboard tickers (404 otherwise) — not a general
  Yahoo proxy. An empty Yahoo response is never cached.
- **1D = the latest session, anchored on the market's calendar**, never on gaps in the data:
  futures/gold from the 18:00 ET Globex open, BTC the last 24 h, cash indexes the
  exchange-local date of the last bar. The first deploy (`1e65e340`) used "bars after the last
  45-min gap"; live, Yahoo's ES=F 5m data had a hole from 02:25 to 04:05 UTC mid-session, so 1D
  showed only the last ~70 minutes. Fixed the same night (tests include that exact shape).
- fast_info returns **0** for fields it lacks (VIX day high/low); those render as "—".

## SMA / EMA overlays (added 2026-10-27)

The detail-page chart has toggles for **SMA 5 / 10 / 20 / 50 / 200 and EMA 20 / 50 / 200** — the
same periods and colours as the stock chart. Default on: SMA 20, 50, 200 (also the stock
chart's default). The choice is remembered per browser (localStorage) across indexes and
ranges. A legend under the chart shows each line's latest value.

**Warm-up:** each range fetches a longer window in the same single Yahoo call (1D→7d of 5m,
5D→1mo of 15m, 1M→6mo of 1h, 6M/1Y→2y daily, 5Y→10y weekly) and returns up to 400 bars before
the range, marked by `visible_from`. Averages are computed over all bars and only the visible
part is drawn, so SMA 200 starts at the left edge of a 6M chart instead of being absent. An
average that still has no value in the range (e.g. too little history) is listed as "not
enough bars in this range" rather than drawn partially without comment. Checked live: all
13 tickers × 6 ranges return strictly increasing timestamps; repeats are dropped anyway,
because the chart library throws on one.

5D is the last five **sessions** (futures by trade date), not five calendar days.

## Code

- `services/market-data/src/api/routes.py` — `_INDICES` (name, ticker, market, group),
  `_fetch_index`, `market_overview`, `_INDEX_INFO` (delay + description), `_INDEX_RANGES`,
  `_last_session_start`, `market_index_detail`.
- `frontend/src/pages/index.tsx` — grouped US rows, yield-in-bp tile, tiles link to detail page.
- `frontend/src/pages/market/[ticker].tsx` — detail page (lightweight-charts area series,
  loaded client-side only).
- `frontend/src/lib/api.ts` — `MarketIndex.group/change`, `MarketIndexDetail`, `marketIndexDetail`.
- `frontend/src/lib/marketIndexOverlays.ts` — overlay periods/colours and `overlayLines()` (+ vitest).
- Tests: `services/market-data/tests/test_market_overview_indices.py` (15), `frontend/src/lib/marketIndexOverlays.test.ts` (3).

## Load

`market_overview` now makes 13 fast_info reads per 60 s cache miss (was 5), only while someone
has the dashboard open. A detail page adds one history call per range per cache window.
