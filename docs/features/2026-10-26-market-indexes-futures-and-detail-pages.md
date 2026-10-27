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
- **1D = the latest session**, defined as the bars after the last gap of 45+ minutes and within
  24 h of the last bar. A UTC-date cut would split a futures session (opens 18:00 ET the
  previous evening); the 24 h cap handles BTC, which never pauses.
- fast_info returns **0** for fields it lacks (VIX day high/low); those render as "—".

## Code

- `services/market-data/src/api/routes.py` — `_INDICES` (name, ticker, market, group),
  `_fetch_index`, `market_overview`, `_INDEX_INFO` (delay + description), `_INDEX_RANGES`,
  `_last_session_start`, `market_index_detail`.
- `frontend/src/pages/index.tsx` — grouped US rows, yield-in-bp tile, tiles link to detail page.
- `frontend/src/pages/market/[ticker].tsx` — detail page (lightweight-charts area series,
  loaded client-side only).
- `frontend/src/lib/api.ts` — `MarketIndex.group/change`, `MarketIndexDetail`, `marketIndexDetail`.
- Tests: `services/market-data/tests/test_market_overview_indices.py` (11).

## Load

`market_overview` now makes 13 fast_info reads per 60 s cache miss (was 5), only while someone
has the dashboard open. A detail page adds one history call per range per cache window.
