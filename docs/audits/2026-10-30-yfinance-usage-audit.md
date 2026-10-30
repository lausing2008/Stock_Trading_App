# yfinance usage audit (2026-10-30)

A read-only audit of every production call into yfinance. It asks which calls are the main
source and which are fallbacks, how many requests each makes per trading day, and what could
move to Unusual Whales (UW) or be cached. It follows the 2026-10-28 Yahoo rate-limit episode
(`docs/incidents/yfinance-rate-limit-amplification.md`, AUD-UW-COOLDOWN-YFSPILL). Nothing was
changed.

**Labels.** *V* means verified from code or production; *E* means estimated. The symbol counts
(142 US, 42 HK, about 184 in total) come from `docs/features/data-provider-routing-and-schedule.md`.

## The one fact that changes the totals

**`yf.download([...])` is not one request.** yfinance 1.7.0 (the version in the market-data
container) fetches the chart for each ticker separately. A probe on production made **6** Yahoo
requests for a 3-ticker `download` (V, 2026-10-30): two identical `/v8/finance/chart/{T}` calls
per ticker. The second may be an internal retry; either way it is at least one per ticker. The
comments that say "one bulk call" are wrong (`api/routes.py:188`,
`services/paper_trading_engine.py:2112`, `services/scheduler.py:63/3008/15770`), and so is every
load estimate built on them.

## Estimated Yahoo requests per trading day

| Area | Site | Role | Trigger | Cache | Requests/day |
|---|---|---|---|---|---|
| Live prices | `refresh_live_price_cache` → `_fetch_live_bulk` | main source | every minute while **either** the US (9–17 ET) or HK (9–17 HKT) window is open, about 960 runs; fetches **every active stock in both markets** (V) | Redis 90 s | **≈176,600+** (184 × 960, at least one request each) |
| Price alerts | `check_price_alerts`, alerts plus paper drawdown | main source | every minute, **no market-hours gate** (agent; first lines checked); one `fast_info` per symbol (V) | none | (alert + open-position symbols) × 1,440; ≈43k at 30 symbols (E) |
| Paper trading | `_fetch_live_prices`, index `fast_info`, `_batch_compute_atr`, `_batch_sector_rs_lag` | main source | about 150 paper steps a day | regime only (30 min) | ≈10–17k (E) |
| Bars, HK | `yfinance_adapter.fetch_ohlcv` | **only source** | 5m: 42 × 72; 1d: 43 × 65 | — | ≈5,800 (V) |
| Fundamentals | `routes.get_fundamentals` (`.info` plus about 11 sub-requests) | main source | signal-engine, alerts, research | Redis 24 h, **no negative cache** | ≈2,200 steady (E); far more while throttled |
| Avg volume | `refresh_avg_volume_cache` | main source | every 4 h, **including weekends** | Redis 6 h | ≈1,100 (E) |
| Earnings | `earnings._fetch_earnings_for_symbol` | main source | 06:30 UTC across all Stock rows, plus a 15-min sync for reporters | none | ≈700 plus reporters (E) |
| Gamma alert | `check_gamma_unwind_alerts` uses `t.options` / `option_chain` (V, `scheduler.py:5511`) | main source | every 4 h | none | ≈360 (E); see the bug list |
| Corporate actions | `corporate_actions.fetch` | main source | 19:50 ET | — | 314 (V, doc) |
| Bars, US | adapter | **fallback**: UW cooldown (regular-session spill limited by `ad55a635`), first and forced ingests, the weekly Sunday `force=True` refresh (all 184) | — | — | 0 to thousands |
| Regime, futures, equity curve | `_fetch_market_regime`, HMM, `_fetch_hk_market_regime`, overnight futures | main source | paper steps, premarket | 30 min / 1 h | ≈170 (E) |

**Driven by page views (per view, not estimated):**
- Unfiltered `latest_prices` re-downloads all 184 on a 90 s cache miss, so every open tab does it outside the job window.
- `latest_prices?symbols=` (the board's 60 s poll) makes one `fast_info` call per symbol, **uncached**.
- Market overview: 15 `fast_info` calls per 60 s cache miss.
- Fed Watch, Fear & Greed, sector rotation, relative strength, quarterly financials, dividends, institutional holders, news, the 15m/1h/4h price endpoints, quick scan, and portfolio risk/stress (uncached).

**User actions:** add stock, valuation refresh (Yahoo first, UW fallback already built), manual
exit, liquidate, financial-statement backfill, earnings discovery/backfill.

**Fallback only and rare:** decision-engine `_yf_last_price`, research-engine
`_yf_fundamentals` and `_yf_sync_fetch`, ranking `_etf_20d_return` (DB first).

**Removed from the list:** `uw_option_chain.get_expiries` and `routes._uw_expiries` already use UW;
only their docstrings mention yfinance.

## What UW already offers (from our client code)

UW has the following:
- `/stock/{t}/stock-state` (a quote).
- `/screener/stocks`: up to 50 tickers per request, but `open` comes back as None; fine as a price sweep, not as a bar source.
- `/stock/{t}/info`, `/financials`, `/ohlc`, `/earnings/{t}` and `/screener/analysts`.

It has **no HK coverage**, and nothing in our client covers indexes or futures. Consensus
estimates (`eps_trend`, `earnings_estimate`) have no confirmed UW equivalent.

## Candidates, by expected reduction

1. **Live-price cache (≈176k/day, the dominant load).**
   - Fetch each market's symbols only during that market's window. This roughly halves the load with no provider change.
   - Then serve US quotes from UW `/screener/stocks` (about 3 requests a minute, ≈1,400 UW/day).
   - HK stays on Yahoo at 42 × 480 ≈ 20k.
   - Effort: 1–2 days.
2. **`check_price_alerts` (≈43k/day).** Read the shared `stockai:live_prices` cache, as the other per-minute scanners do, and gate on market hours. Effort: a few hours.
3. **Paper-trading prices (≈10–17k/day).** Read the minute-fresh Redis cache instead of downloading; cache index `fast_info` per cycle and ATR/sector-RS per day. Effort: about half a day.
4. **Fundamentals and earnings consensus (what failed on Oct 28).** Add a 15–30 min negative cache, serve stale data instead of blank, and fall back to UW `/info`, `/financials` and `/earnings` following the valuation pattern. Effort: 1–2 days.
5. **Gamma-unwind alert.** Move it to `uw_option_chain`, as T404 did for the other five chain consumers. Effort: about half a day.

## Likely defects

- The "one bulk call" comments understate load by a factor of N (above).
- `check_price_alerts` runs 24/7, including a second per-symbol drawdown loop.
- **Amplification under throttling:** fundamentals has no negative cache. While Yahoo refuses, signal-engine's 4 GETs per symbol each re-fire about 12 Yahoo requests, which is up to ≈6,800 per signal cycle (E).
- `latest_prices?symbols=` is uncached, despite its docstring promising per-symbol Redis keys.
- **The gamma-unwind alert is probably dead.** Yahoo's options endpoint returned empty for every symbol from 2026-09-15 (T404), yet the alert still spends requests on it.
- The average-volume refresh runs on weekends.
- `sync_all_earnings` iterates all Stock rows, including inactive and delisted ones.

## Method and limits

A subagent read all 21 files. The headline claims were then re-checked on production or in code:
- the per-ticker download behaviour;
- the live-price window and its both-markets symbol scope;
- the per-symbol `fast_info` loop in price alerts;
- the gamma alert's Yahoo options calls.

The counts of alert symbols, open positions, portfolios and reporters are not knowable from code, so the per-day figures for those rows rest on stated assumptions.
