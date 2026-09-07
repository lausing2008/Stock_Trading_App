"""MPE-06/MPE-07/MPE-OPTIONS-FLOW-ALERT: a real Unusual Whales API client — genuine dealer gamma
exposure (GEX), real short-interest data (borrow fee, shares available, days-to-cover), and
real-time unusual-options-activity flow alerts (rule-based, with a genuine ask-side/bid-side
directional split UW itself computes), replacing this app's own free-data proxies where a
subscription is active.

Not a `DataAdapter` (services/market-data/src/adapters/base.py) — that ABC's whole contract is
strictly OHLCV bars (`fetch_ohlcv(symbol, timeframe, start, end) -> OHLCV`); Unusual Whales'
response shapes (options Greeks, short-interest fields, GEX levels) have nothing in common with
a price bar, so forcing this into the adapter registry would mean either lying about the return
type or bolting on unrelated methods no other adapter has. This module is deliberately its own,
separate thing, matching the same "a genuinely new data shape needs a genuinely new module, not
a plugin into an ABC built for something else" judgment already made for T259-NEWS-INTELLIGENCE.

Every real call is gated behind BOTH a configured API key AND the admin enabled flag (see
shared/common/ai_keys.py's get_unusual_whales_key()/is_unusual_whales_enabled() — a key existing
does not by itself mean the feature is turned on) — this is a real, metered, per-request-cost
API, so every function here fails open to `None` rather than raising, and callers must always
have a free-data fallback path ready (never assume Unusual Whales data will be present).

Real endpoint shapes below were verified directly against the live
https://api.unusualwhales.com/api/openapi spec (not guessed/assumed) before being coded against:
  - /api/stock/{ticker}/gex-levels     — call_wall, put_wall, gamma_flip, gamma_magnet
  - /api/stock/{ticker}/greek-exposure — call_gamma, put_gamma, call_delta, put_delta, ...
  - /api/stock/{ticker}/greeks (per-EXPIRY, per-strike) — call_delta/gamma/theta/vega/vanna/
    charm and the put-side equivalents, one row per strike — a genuinely different granularity
    from greek-exposure above (which is already-aggregated across the whole chain). AUD-GREEKS:
    see get_greeks() below.
  - /api/stock/{ticker}/iv-rank — volatility, iv_rank_1y, close, date. See get_iv_rank() below.
  - /api/stock/{ticker}/max-pain — per-expiry {expiry, max_pain} array. AUD-MAXPAIN: see
    get_max_pain() below.
  - /api/stock/{ticker}/oi-per-strike — per-strike {strike, call_oi, put_oi}, across all
    expiries. AUD-MAXPAIN: see get_oi_per_strike() below.
  - /api/stock/{ticker}/nope — call_delta, call_fill_delta, call_vol, nope, nope_fill,
    put_delta, put_fill_delta, put_vol, stock_vol, timestamp. Published per-MINUTE (the only
    field this app consumes at that cadence) — AUD-NOPE: see get_nope() below.
  - /api/earnings/{ticker} — per-historical-report expected_move/expected_move_perc (the
    pre-report options-implied move) paired with post_earnings_move_1d/1w (what actually
    happened). AUD-EARNINGSMOVE: see get_historical_earnings_moves() below.
  - /api/companies/{ticker}/transcripts/{quarter} — real earnings-call transcript statements
    (speaker, title, content, sentiment). Requires UW's own Advanced+ tier (higher than every
    other endpoint here assumes) — a 403 fails open identically to "no transcript yet."
    AUD-TRANSCRIPT: see get_earnings_transcript() below.
  - /api/seasonality/market — real, multi-year monthly seasonal return stats for UW's own fixed
    13-ticker sector/index ETF set (with real example data confirmed in UW's own spec).
    AUD-SEASONALITY: see get_sector_seasonality() below.
  - /api/shorts/{ticker}/interest-float/v2 — days_to_cover, fee_rate, rebate_rate,
    short_interest, short_shares_available, si_float, total_float
  - /api/shorts/{ticker}/data           — fee_rate, rebate_rate, short_shares_available
  - /api/option-trades/flow-alerts     — ticker, option_chain, type, strike, expiry,
    total_ask_side_prem/total_bid_side_prem, has_sweep, volume_oi_ratio, alert_rule (the
    non-deprecated replacement for /api/stock/{ticker}/flow-alerts). Real-time WebSocket
    streaming of this same feed (wss://api.unusualwhales.com/socket, channel "flow-alerts")
    requires UW's paid Advanced tier — this app polls the REST endpoint instead, which the
    trial tier's 30,000 req/day budget comfortably supports for a bounded symbol set.
  - /api/congress/recent-trades — T323-DARKPOOL: a live, dedicated Congressional-disclosure
    feed, replacing services/event-intelligence's own EI-CONGRESS1 fallback (an unofficial,
    rolling ~5000-row GitHub mirror) when a paid subscription is configured — see
    get_congress_trades() below. Response field names for this specific endpoint are NOT
    documented in UW's own published skill.md/API reference (confirmed by direct inspection);
    get_congress_trades() therefore probes several plausible key names per field (e.g.
    politician_name/reporter/name) rather than assuming one, and every row-level failure is
    swallowed per-row so a genuinely different real shape degrades gracefully to fewer parsed
    fields rather than dropping the whole response.
  - /api/darkpool/{ticker} — genuinely new capability, not previously built anywhere in this
    app: real off-exchange block trades (FINRA-reported, not OTC/pink-sheet). See
    get_dark_pool_prints() below and DarkPoolPrint's own model docstring (shared/db/models.py)
    for what this actually is.
  - /api/screener/option-contracts — T324-OPTIONSFLOW-TAB: a real, universe-wide options
    screener (not per-symbol) — see get_options_screener() below.
  - /api/option-trades — the raw options tape, filterable by is_multi_leg/max_dte/min_premium
    (all confirmed-real UW params) — the single shared data source behind this app's 0DTE Flow,
    Multi-leg Flow, and Interval Flow UI views (UW itself has no separate endpoint for any of
    the three). See get_option_trades() below.
  - /api/market/market-tide — real market-WIDE net call/put options premium over time (UW's own
    aggregate sentiment measure), not a per-symbol rollup. See get_market_tide() below.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

import structlog

from common.ai_keys import get_unusual_whales_key, is_unusual_whales_enabled

log = structlog.get_logger()

_BASE_URL = "https://api.unusualwhales.com"

# Real Redis cache TTLs — GEX/greek-exposure are recomputed by UW throughout the trading day
# (short, intraday-relevant TTL); short-interest settles ~2x/month with a real reporting lag,
# so it can be cached far longer without ever serving genuinely stale data relative to its own
# real update cadence.
_GEX_TTL = 900          # 15 min — matches this app's own options-flow cache cadence exactly
_SHORT_INTEREST_TTL = 21600  # 6h — short interest itself only updates ~2x/month
_IV_RANK_TTL = 900      # 15 min, matching _GEX_TTL — this app's only consumer (the daily
# Options Game Plan batch snapshot, AUD-DECIDE4-EXPECTEDMOVE) reads it at most once/day per
# symbol anyway; a short TTL just avoids a stale read if the same symbol is looked up twice in
# one batch run for any reason, not a claim that IV itself is stable at this cadence intraday.
_GREEKS_TTL = 900       # 15 min, same rationale as _IV_RANK_TTL — this app's only consumer
# (the daily Options Game Plan batch snapshot, AUD-GREEKS) reads a specific (expiry, strike)
# pair once/day per symbol.
_MAX_PAIN_TTL = 900     # 15 min, matching _GEX_TTL — AUD-MAXPAIN's consumer is the live
# /gamma-exposure route (a per-page-view fetch, same cadence as get_gex_levels() itself).
_OI_PER_STRIKE_TTL = 900  # 15 min, same rationale as _MAX_PAIN_TTL.
_NOPE_TTL = 60          # 1 min ONLY — NOPE is UW's own per-minute reading; the 15-min TTL used
# for every other UW field here would serve a stale intraday snapshot for 15x longer than the
# data itself is even valid for, defeating the entire point of a live pressure gauge. Still a
# real cache (not zero) so a symbol looked up twice within the same minute — e.g. this route's
# own gex/max-pain/oi-per-strike/nope calls all hitting the same page load — doesn't spend two
# API calls for data guaranteed to be identical.
# AUD-UWRATELIMIT-FLOWALERTS: get_flow_alerts() was documented as "deliberately NOT cached" —
# fine in isolation, but check_options_flow_alerts() (scheduler.py) calls it once per symbol,
# every 1-minute tick, over a symbol set that's uncapped on one side (top-20 K-Score UNIONED
# with every distinct PriceAlert symbol, no LIMIT). Confirmed live: 22,031 real UW 429 responses
# in 48h, and the math shows this ONE function alone reaches N x 1,440 requests/day — already
# ~28,800/day (96% of the platform's own assumed 30,000/day trial-tier budget) at N=20 with ZERO
# PriceAlert symbols, before any other UW-calling code path adds anything. Originally set to 45s
# on the assumption of a reliable ~60s job cadence.
#
# AUD-UWUSAGE-FLOWALERTCAP (2026-09-06): that assumption broke on two fronts at once. (1) the
# PriceAlert side grew uncapped to 38 real symbols (78 alerts from one user) before being capped
# at scheduler.py's own _OPTIONS_FLOW_ALERT_SYMBOLS_CAP; (2) AUD-MISFIREGRACE-OPTIONSFLOW
# (2026-09-04) fixed this job from silently NOT firing to firing reliably, but at an irregular
# ~60-90s real cadence (misfire_grace_time=60 on a 1-minute interval means a genuinely late tick
# can land up to ~120s after the prior one) — live-confirmed at 47.3 calls/min from THIS single
# endpoint alone (~68,100/day projected, 2.27x the entire daily budget) because most symbols
# were expiring between ticks, turning nearly every run into a full live sweep rather than a
# cache hit. Raised to 150s — comfortably past the worst-case ~120s tick gap plus buffer, so a
# symbol checked on any two consecutive real ticks (even a late one) is now reliably a cache hit.
# Still short enough that "alert on fresh activity" holds: a real sweep/block that just posted
# is still meaningfully "fresh" 150s (2.5 minutes) later for a scanner meant to catch same-day
# unusual activity, not sub-minute latency.
_FLOW_ALERT_TTL = 150
_EARNINGS_MOVE_TTL = 21600  # 6h, matching _SHORT_INTEREST_TTL's own rationale — this is
# HISTORICAL per-report data that only grows (a new row) once per quarter per symbol; no reason
# to re-fetch it as often as GEX/NOPE.
_TRANSCRIPT_TTL = 86400  # 24h — once a specific (ticker, quarter) transcript is published it
# never changes again (a real historical record, not a live reading); a full trading day is a
# safe, generous cache given this endpoint is only ever read after a report has already landed.
_SEASONALITY_TTL = 86400  # 24h — genuinely multi-year historical statistics; recomputes on
# UW's own side at most daily, no reason to re-fetch more often than once per day.


class UnusualWhalesRateLimitError(Exception):
    """A 429 from Unusual Whales — caller should back off, never retry immediately."""


class UnusualWhalesAuthError(Exception):
    """A 401/403 — the configured key is invalid/expired. Never retried (retrying a bad key
    wastes the request budget on an error that can't self-resolve)."""


@dataclass
class IVRankData:
    """AUD-DECIDE4-EXPECTEDMOVE: real, per-symbol implied volatility + historical-percentile
    context from UW's own /api/stock/{ticker}/iv-rank — confirmed field shape (close, date,
    iv_rank_1y, updated_at, volatility) via UW's own published API operation spec, fetched
    2026-09-03. `volatility` is the real IV reading itself (used for the expected-move formula);
    `iv_rank_1y` is where that reading sits relative to its own trailing 1-year range (0-100) —
    a genuinely different, complementary signal from raw IV (e.g. 30% IV could be historically
    high for a normally-sleepy utility, or low for a name that's always volatile)."""
    volatility: float | None
    iv_rank_1y: float | None
    close: float | None
    as_of_date: str | None


@dataclass
class StrikeGreeks:
    """AUD-GREEKS: real per-strike option Greeks from Unusual Whales'
    /api/stock/{ticker}/greeks — confirmed field shape via UW's own published API operation
    spec, fetched 2026-09-03. This app's own guide explicitly documents "no real per-contract
    Greeks (delta/theta/vega) beyond implied volatility are shown" as a known limitation of the
    Options Game Plan card — this closes that gap for the SPECIFIC put/call strike the game
    plan already selected, not a full chain. vanna/charm are real second-order Greeks (delta's
    sensitivity to IV, and delta's sensitivity to time, respectively) UW exposes that this app
    has never surfaced anywhere before."""
    strike: float | None
    call_delta: float | None
    call_gamma: float | None
    call_theta: float | None
    call_vega: float | None
    call_vanna: float | None
    call_charm: float | None
    put_delta: float | None
    put_gamma: float | None
    put_theta: float | None
    put_vega: float | None
    put_vanna: float | None
    put_charm: float | None


@dataclass
class GexLevels:
    call_wall: float | None
    put_wall: float | None
    gamma_flip: float | None
    gamma_magnet: float | None
    as_of_date: str | None


@dataclass
class MaxPainRow:
    """AUD-MAXPAIN: one expiry's real max-pain strike from Unusual Whales'
    /api/stock/{ticker}/max-pain — confirmed field shape via UW's own published API operation
    doc, fetched 2026-09-03 (data: [{expiry, max_pain}], date). Max pain is the strike where,
    in aggregate, option WRITERS (not holders) lose the least money at expiry — a real, distinct
    concept from GEX's call_wall/put_wall/gamma_flip (which describe dealer HEDGING pressure,
    not option-writer P&L), and a genuinely different magnet-effect theory some traders watch
    for expiry-week price action."""
    expiry: str | None
    max_pain: float | None


@dataclass
class OIPerStrikeRow:
    """AUD-MAXPAIN: one strike's aggregate call/put open interest from Unusual Whales'
    /api/stock/{ticker}/oi-per-strike — confirmed field shape via UW's own published API
    operation doc, fetched 2026-09-03. The raw OI distribution GEX's own call_wall/put_wall
    only imply indirectly (those are gamma-weighted, not a plain OI count) — this is the actual
    number of open contracts at each strike, the same data an "OI wall" reading is built from."""
    strike: float | None
    call_oi: float | None
    put_oi: float | None


@dataclass
class NopeReading:
    """AUD-NOPE: real, delta-weighted directional options pressure ("Net Options Pricing
    Effect") from Unusual Whales' /api/stock/{ticker}/nope — confirmed field shape via UW's own
    published API operation doc, fetched 2026-09-03. Genuinely different construction from this
    app's own homegrown compute_options_pressure_score() (routes.py) — that score is built from
    raw call/put PREMIUM ratio and volume/OI ratio; NOPE weights by each option's actual DELTA,
    a more theoretically-grounded read of real directional exposure. Deliberately fetched LIVE
    per page-view (not a daily batch): NOPE is published per-MINUTE by UW, the only field this
    app consumes at that cadence — a daily batch would only ever show yesterday's stale close,
    defeating the entire point of a live intraday pressure gauge. Same live-per-request pattern
    already used for GEX/max-pain/OI-per-strike on this exact route (GET /gamma-exposure) — no
    new batch infrastructure, no new operational polling pattern for this codebase.
    `nope`/`nope_fill` are UW's own two variants (volume-weighted vs. fill-weighted delta) — both
    surfaced since neither is documented as strictly superior."""
    nope: float | None
    nope_fill: float | None
    call_delta: float | None
    put_delta: float | None
    call_vol: float | None
    put_vol: float | None
    stock_vol: float | None
    timestamp: str | None


@dataclass
class HistoricalEarningsMoveRow:
    """AUD-EARNINGSMOVE: one historical earnings report's real, options-market-implied expected
    move alongside what the stock ACTUALLY did afterward, from Unusual Whales'
    /api/earnings/{ticker} — confirmed field shape via WebFetch against UW's own published API
    operation doc, fetched 2026-09-04 (every value a string in the real response; converted to
    float here). Genuinely different from AUD-DECIDE4-EXPECTEDMOVE's own expected_move_pct: that
    one is a GENERIC 30-day reference-window figure derived from get_iv_rank() for the paper-
    trading engine's stop/target math, computed for ANY symbol on ANY day. This is a real,
    EARNINGS-SPECIFIC expected move for one specific historical report, paired with the actual
    outcome — "was the options market's fear justified THIS time" — a ready-made per-symbol
    track record the Earnings Calendar previously had no equivalent for (it only had backward-
    looking EPS beat-rate/surprise stats, never anything about the stock's own PRICE reaction).
    report_time distinguishes before-market/after-market/unknown, needed to correctly interpret
    which post_earnings_move window actually captures the market's first real reaction.
    """
    report_date: str | None
    report_time: str | None
    expected_move: float | None
    expected_move_perc: float | None
    post_earnings_move_1d: float | None
    post_earnings_move_1w: float | None
    source: str | None


@dataclass
class TranscriptStatement:
    """AUD-TRANSCRIPT: one statement from a real earnings-call transcript, from Unusual Whales'
    /api/companies/{ticker}/transcripts/{quarter}. Confirmed field shape by pulling UW's own
    full published OpenAPI YAML spec directly (fetched 2026-09-04) after the rendered docs page
    itself showed the schema as an undocumented placeholder ([null]) — the real "Transcript
    Statement" schema has exactly 4 fields: content (the statement text), sentiment (UW's own
    per-statement sentiment score), speaker (name), title (role/position, e.g. "CEO"/"Analyst").
    NOTE: this endpoint's own docs state it "Requires Advanced+ tier (Advanced, Enterprise, or
    Enterprise + Kafka)" — a HIGHER UW subscription level than this app otherwise assumes for
    every other endpoint. Same fail-open contract as everything else here: a 403 from an
    insufficient tier is caught by _get()'s own UnusualWhalesAuthError handling and degrades to
    an empty list, exactly like "no transcript exists yet" — this app cannot and does not try to
    distinguish "wrong tier" from "not available" at the call site."""
    speaker: str | None
    title: str | None
    content: str | None
    sentiment: float | None


@dataclass
class SeasonalityRow:
    """AUD-SEASONALITY: one (ticker, month) row of real, multi-year seasonal return statistics
    from Unusual Whales' /api/seasonality/market — confirmed field shape (with real example
    data) via UW's own full published OpenAPI YAML spec, fetched 2026-09-04. Covers the same 13
    major sector/index ETFs UW itself computes this for: SPY, QQQ, IWM, XLE, XLC, XLK, XLV, XLP,
    XLY, XLRE, XLF, XLI, XLB. A genuinely different lens from this app's own existing
    sector_trajectory.py (K-Score-momentum-based, "who's outperforming right now") — this is
    calendar-effects-based, "who has historically tended to do well in THIS MONTH, independent
    of current momentum." avg_change/median_change/min_change/max_change are fractional returns
    (0.0092 = +0.92%) over `years` years of history for this specific calendar month."""
    ticker: str | None
    month: int | None
    avg_change: float | None
    median_change: float | None
    min_change: float | None
    max_change: float | None
    positive_closes: int | None
    positive_months_perc: float | None
    years: int | None


@dataclass
class ShortInterestData:
    short_interest: float | None
    short_shares_available: float | None
    days_to_cover: float | None
    fee_rate: float | None
    rebate_rate: float | None
    si_float: float | None
    total_float: float | None
    market_date: str | None


@dataclass
class FlowAlert:
    """One row from UW's real /api/option-trades/flow-alerts — a rule-based aggregation over
    the full options tape (repeated same-contract trades within milliseconds, often a single
    large order sweeping across multiple market makers). Field shapes verified directly against
    the live OpenAPI spec's own real example payload, not guessed."""
    ticker: str
    option_chain: str  # UW's own per-contract symbol, e.g. "MSFT231222C00375000" — the real
                       # per-CONTRACT identity this module's own dedup keys off of, not `ticker`
    option_type: str  # "call" | "put"
    strike: float | None
    expiry: str | None  # ISO date string, e.g. "2023-12-22"
    price: float | None  # fill price of the contract
    underlying_price: float | None
    total_premium: float | None
    total_ask_side_prem: float | None  # aggressive BUYING at the ask
    total_bid_side_prem: float | None  # aggressive SELLING at the bid
    total_size: int | None
    volume: int | None
    open_interest: int | None
    volume_oi_ratio: float | None  # how unusual this volume is relative to existing OI
    has_sweep: bool
    alert_rule: str | None
    created_at: str | None


# AUD-DQCHECKS-VISIBILITY: rolling 48h counter of real 429 rate-limit responses from UW's own
# API — surfaced as a "gauge" _DQ_CHECK entry (scheduler.py) for admin visibility, matching the
# existing squeeze_fund_cache_miss-style counters' own convention exactly. Deliberately a plain
# INCR+expire-once-on-first-write (not imported from scheduler.py's own _incr_rolling_counter,
# to avoid a circular import — scheduler.py imports things that transitively reach this module)
# rather than raising/blocking anything: a rate-limit event already fails open everywhere it's
# called from (every real caller already handles UnusualWhalesRateLimitError or an unavailable
# result), this counter exists purely so a sustained rate-limit PATTERN — as opposed to one
# isolated 429 — is visible somewhere other than grepping logs.
#
# AUD-UW429SAWTOOTH (2026-09-06 deep audit): this used to be a SINGLE key with a flat 48h TTL
# set on first increment — not a rolling 48h window despite the key name and the dashboard's
# "429s (48h)" label. It accumulated from whenever it was first incremented, then vanished
# entirely and restarted from zero at that key's own TTL expiry (confirmed live: value 109184,
# TTL 3021s — within ~50 minutes it would read 0 even with 429s continuing at full rate). An
# admin checking the dashboard during an ACTIVE rate-limit incident, right after that flip,
# would see "429s (48h): 0". Rebuilt as hourly buckets (matching check_llm_usage_spike()'s own
# established hourly-bucket idiom in scheduler.py) summed over the trailing 48 hours at read
# time — a genuine rolling window, not a sawtooth.
_RATE_LIMIT_COUNTER_PREFIX = "stockai:metric:uw_rate_limit_count_hourly"
_RATE_LIMIT_COUNTER_BUCKET_TTL_S = 50 * 3600  # a little over 48h so a bucket outlives its own window
_RATE_LIMIT_COUNTER_WINDOW_HOURS = 48

# Retained ONLY so any external reader of the old key during the rollout doesn't hard-fail —
# no code in this module writes to it anymore.
_RATE_LIMIT_COUNTER_KEY = "stockai:metric:uw_rate_limit_count_48h"


def _incr_rate_limit_counter() -> None:
    try:
        r = _get_redis()
        hour_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        key = f"{_RATE_LIMIT_COUNTER_PREFIX}:{hour_bucket}"
        r.incr(key)
        if r.ttl(key) == -1:
            r.expire(key, _RATE_LIMIT_COUNTER_BUCKET_TTL_S)
    except Exception:
        pass


def _read_rate_limit_count_48h() -> int:
    """Sum the trailing _RATE_LIMIT_COUNTER_WINDOW_HOURS hourly buckets — a genuine rolling
    window read at query time, so it can never sawtooth back to 0 while an incident is
    ongoing. Missing buckets (no 429s that hour) are simply absent keys, contributing 0."""
    try:
        r = _get_redis()
        now = datetime.now(timezone.utc)
        total = 0
        for i in range(_RATE_LIMIT_COUNTER_WINDOW_HOURS):
            hour_bucket = (now - timedelta(hours=i)).strftime("%Y%m%d%H")
            raw = r.get(f"{_RATE_LIMIT_COUNTER_PREFIX}:{hour_bucket}")
            if raw:
                total += int(raw)
        return total
    except Exception:
        return 0


# AUD-UWUSAGE: unlike the 429-only counter above, this tracks EVERY real UW request (any
# status) — the leading indicator ("how close to today's ~30,000 req/day trial budget are we
# right now") that a 429-only counter can never show, since it only lights up after headroom
# is already gone. Bucketed per-endpoint (a fixed template like "/api/stock/{symbol}/gex-levels"
# — every real call site interpolates a ticker and/or quarter string into its path, so a naive
# per-path counter would grow one key per traded symbol instead of staying bounded to this
# module's ~14 real endpoints) AND per calendar day (UTC), since UW's own budget is itself a
# daily ceiling, not a rolling window. TTL is a little over 24h so "today's" bucket naturally
# expires rather than accumulating forever, while still being readable for a few hours into the
# next day for any dashboard poll that catches the day boundary.
#
# Deliberately NOT a regex-based path-segment collapse (tried first, rejected): a generic
# "does this segment look like a ticker" pattern also matches plenty of the FIXED path words
# themselves (e.g. "api", "v2", short category names), silently mis-collapsing them. Since
# every real call site already knows its own path template at the point it calls _get(), each
# one passes its template explicitly — no guessing required.
#
# AUD-UWCACHE-YESTERDAYTTL (2026-09-06 deep audit): this was 25h, set on the FIRST write of
# each day's bucket — so a bucket first written at 00:00:30 UTC expired at 01:00:30 the NEXT
# day, meaning the dashboard's "Yesterday (this app's count)" field read 0 for roughly 23 of
# every 24 hours (verified live: scanning for yesterday's keys at 18:19 UTC returned zero,
# despite genuinely heavy usage the day before). A displayed "0" reads as "no usage yesterday,"
# not "the data expired." Extended to 49h so a day's bucket reliably survives the ENTIRE
# following day before expiring, comfortably covering any dashboard poll time.
_CALL_COUNTER_PREFIX = "stockai:metric:uw_calls"
_CALL_COUNTER_TTL_S = 49 * 3600


def _incr_call_counter(endpoint: str) -> None:
    try:
        r = _get_redis()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        key = f"{_CALL_COUNTER_PREFIX}:{endpoint}:{day}"
        r.incr(key)
        if r.ttl(key) == -1:
            r.expire(key, _CALL_COUNTER_TTL_S)
    except Exception:
        pass


# AUD-UWUSAGE-REALHEADERS: UW's own real, authoritative usage snapshot — see _get()'s own
# comment for the header names and source. A short TTL (2 min, comfortably longer than any
# single job's own inter-call gap) means the dashboard always reflects a genuinely RECENT real
# call rather than serving an arbitrarily stale snapshot from hours ago if UW-backed features
# ever go quiet for a while.
_USAGE_HEADERS_KEY = "stockai:metric:uw_usage_headers"
_USAGE_HEADERS_TTL_S = 120


def _record_usage_headers(headers) -> None:
    try:
        daily_count = headers.get("x-uw-daily-req-count")
        daily_limit = headers.get("x-uw-token-req-limit")
        minute_count = headers.get("x-uw-minute-req-counter")
        minute_remaining = headers.get("x-uw-req-per-minute-remaining")
        minute_reset_ms = headers.get("x-uw-req-per-minute-reset")
        if daily_count is None and daily_limit is None:
            # Real UW responses always send these per the published guide — a response with
            # neither present is either a non-UW response (shouldn't happen, _BASE_URL is
            # fixed) or a UW API change; either way there's nothing real to record.
            return
        snapshot = {
            "daily_count": int(daily_count) if daily_count is not None else None,
            "daily_limit": int(daily_limit) if daily_limit is not None else None,
            "minute_count": int(minute_count) if minute_count is not None else None,
            "minute_remaining": int(minute_remaining) if minute_remaining is not None else None,
            "minute_reset_ms": int(minute_reset_ms) if minute_reset_ms is not None else None,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        r = _get_redis()
        import json as _json
        r.setex(_USAGE_HEADERS_KEY, _USAGE_HEADERS_TTL_S, _json.dumps(snapshot))
    except Exception:
        pass


def _get_redis():
    from common.redis_client import get_redis as _get_pool_redis
    return _get_pool_redis()


def is_available() -> bool:
    """True only when BOTH a real key is configured AND the admin has turned the feature on —
    the single check every caller should make before attempting any real fetch, so a caller
    never has to separately reason about the key-vs-flag distinction itself."""
    return is_unusual_whales_enabled() and bool(get_unusual_whales_key())


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    reraise=True,
    retry=retry_if_not_exception_type((UnusualWhalesRateLimitError, UnusualWhalesAuthError)),
)
def _get(path: str, params: dict | None = None, *, endpoint: str | None = None) -> dict | None:
    """A single authenticated GET against the real Unusual Whales API. Returns the parsed
    `data` field of the response (UW's own real response envelope, confirmed live), or `None`
    on any real absence of data. Raises UnusualWhalesRateLimitError/UnusualWhalesAuthError for
    the two error classes that must never be blindly retried — every other transient failure
    (timeout, 5xx, connection error) retries up to 3x with exponential backoff via tenacity,
    matching polygon_adapter.py's own established retry convention exactly.

    `params` is passed straight through to httpx's own query-string encoding (real percent-
    encoding, not manual string interpolation) — every caller with real query parameters
    (get_flow_alerts) uses this rather than building a query string by hand.

    `endpoint` (AUD-UWUSAGE) is the fixed per-endpoint template used for the call-volume
    counter (e.g. "/api/stock/{symbol}/gex-levels") — every real caller passes its own static
    template explicitly rather than this function trying to derive one from the interpolated
    `path` (see _incr_call_counter's own docstring for why deriving it generically was
    rejected). Defaults to `path` itself for the few callers with no variable segments at all
    (already a stable template as-is, e.g. "/api/seasonality/market").
    """
    key = get_unusual_whales_key()
    if not key:
        return None
    with httpx.Client(timeout=15) as client:
        r = client.get(
            f"{_BASE_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        # AUD-UWUSAGE: count every real request against the daily budget regardless of outcome
        # (a 429/401/403/404 still consumed one of today's ~30,000 allotted requests) — placed
        # right after the real network call completes, before any status-code branch below.
        _incr_call_counter(endpoint or path)
        # AUD-UWUSAGE-REALHEADERS (2026-09-06): UW's own response headers carry the REAL,
        # authoritative usage/limit numbers — confirmed via UW's own published guide
        # (unusualwhales.substack.com/i/188524666/how-to-check-your-api-usage): every response
        # (any status code) includes x-uw-daily-req-count / x-uw-token-req-limit /
        # x-uw-minute-req-counter / x-uw-req-per-minute-remaining / x-uw-req-per-minute-reset.
        # This is strictly better than this module's own Redis call-volume counter (which is
        # this app's OWN estimate of usage, inferred from the requests it happens to remember
        # making) and than _UW_ASSUMED_DAILY_BUDGET (a guess of 30,000/day from an incident
        # writeup, not a confirmed real limit) — UW itself reports both the real daily count
        # AND the real limit on every single call, no extra request needed. Snapshot the
        # latest values into Redis so the dashboard can show real headroom, not an estimate.
        # getattr, not r.headers directly: a real httpx.Response always has .headers, but this
        # keeps the call defensive against anything else _get() might ever be handed.
        _record_usage_headers(getattr(r, "headers", None) or {})
        if r.status_code == 429:
            log.warning("unusual_whales.rate_limit", path=path)
            _incr_rate_limit_counter()
            raise UnusualWhalesRateLimitError(f"Unusual Whales rate limit on {path}")
        if r.status_code in (401, 403):
            log.warning("unusual_whales.auth_error", path=path, status=r.status_code)
            raise UnusualWhalesAuthError(f"Unusual Whales auth failed on {path} ({r.status_code})")
        if r.status_code == 404:
            # A real, expected "no data for this symbol/expiry" case (e.g. no listed options,
            # not delisted-in-UW's-own-sense) — never an error worth logging as one.
            return None
        r.raise_for_status()
        body = r.json()
        return body.get("data") if isinstance(body, dict) else None


def get_gex_levels(symbol: str) -> GexLevels | None:
    """Real, calculated gamma exposure levels for `symbol` — call_wall/put_wall (the strikes
    where dealer gamma concentrates on each side) and gamma_flip (the "zero gamma" price level
    dealers' own hedging flips direction at). Redis-cached 15 min. Returns None if Unusual
    Whales is disabled/unconfigured, the fetch fails for any reason, or the symbol has no real
    GEX data (e.g. no listed options) — every failure mode fails open, never raises, so a
    caller can always fall back to the existing free OI-concentration proxy
    (check_gamma_unwind_alerts()) without special-casing which failure occurred.
    """
    if not is_available():
        return None
    sym = symbol.upper()
    cache_key = f"stockai:uw:gex:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            d = json.loads(cached)
            return GexLevels(**d) if d else None
    except Exception:
        pass

    try:
        data = _get(f"/api/stock/{sym}/gex-levels", endpoint="/api/stock/{symbol}/gex-levels")
    except Exception as exc:
        log.warning("unusual_whales.gex_levels_failed", symbol=sym, error=str(exc))
        return None

    result: GexLevels | None
    if not data:
        result = None
    else:
        # UW's real response is a list of per-expiry rows (confirmed via the live spec) —
        # the nearest/most-relevant row is the first one; a symbol with zero listed options
        # returns an empty list, not a dict, so this must handle both shapes defensively.
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not row:
            result = None
        else:
            result = GexLevels(
                call_wall=_to_float(row.get("call_wall")),
                put_wall=_to_float(row.get("put_wall")),
                gamma_flip=_to_float(row.get("gamma_flip")),
                gamma_magnet=_to_float(row.get("gamma_magnet")),
                as_of_date=row.get("date"),
            )

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _GEX_TTL, json.dumps(asdict(result) if result else None))
    except Exception:
        pass
    return result


def get_max_pain(symbol: str) -> list[MaxPainRow]:
    """AUD-MAXPAIN: real, per-expiry max-pain strikes for `symbol` from Unusual Whales'
    /api/stock/{ticker}/max-pain — one row per listed expiry (confirmed real response shape via
    UW's own published API operation doc, fetched 2026-09-03). Returns an empty list (never
    None) on any failure/unavailability, matching get_flow_alerts()'s own list-returning
    contract. Redis-cached 15 min.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    cache_key = f"stockai:uw:max_pain:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [MaxPainRow(**r) for r in rows]
    except Exception:
        pass

    try:
        data = _get(f"/api/stock/{sym}/max-pain", endpoint="/api/stock/{symbol}/max-pain")
    except Exception as exc:
        log.warning("unusual_whales.max_pain_failed", symbol=sym, error=str(exc))
        return []

    result: list[MaxPainRow] = []
    if isinstance(data, list):
        for row in data:
            result.append(MaxPainRow(
                expiry=row.get("expiry"),
                max_pain=_to_float(row.get("max_pain")),
            ))

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _MAX_PAIN_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def get_oi_per_strike(symbol: str) -> list[OIPerStrikeRow]:
    """AUD-MAXPAIN: real aggregate call/put open interest per strike for `symbol` (across all
    expiries) from Unusual Whales' /api/stock/{ticker}/oi-per-strike — confirmed real response
    shape via UW's own published API operation doc, fetched 2026-09-03. Returns an empty list
    (never None) on any failure/unavailability. Redis-cached 15 min.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    cache_key = f"stockai:uw:oi_per_strike:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [OIPerStrikeRow(**r) for r in rows]
    except Exception:
        pass

    try:
        data = _get(f"/api/stock/{sym}/oi-per-strike", endpoint="/api/stock/{symbol}/oi-per-strike")
    except Exception as exc:
        log.warning("unusual_whales.oi_per_strike_failed", symbol=sym, error=str(exc))
        return []

    result: list[OIPerStrikeRow] = []
    if isinstance(data, list):
        for row in data:
            result.append(OIPerStrikeRow(
                strike=_to_float(row.get("strike")),
                call_oi=_to_float(row.get("call_oi")),
                put_oi=_to_float(row.get("put_oi")),
            ))

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _OI_PER_STRIKE_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def get_nope(symbol: str) -> NopeReading | None:
    """AUD-NOPE: real, delta-weighted directional options pressure for `symbol` from Unusual
    Whales' /api/stock/{ticker}/nope — confirmed real response shape via UW's own published API
    operation doc, fetched 2026-09-03. Unlike get_max_pain()/get_oi_per_strike() above, this
    endpoint returns a SINGLE object (the most recent minute's reading), not a list — no `date`
    param is passed, so UW returns its own current/latest snapshot. Cached only 60s
    (_NOPE_TTL) — see that constant's own comment for why every other field's 15-min TTL would
    be wrong here. Returns None (never raises) on any failure/unavailability, matching
    get_gex_levels()'s own single-object fail-open contract.
    """
    if not is_available():
        return None
    sym = symbol.upper()
    cache_key = f"stockai:uw:nope:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            d = json.loads(cached)
            return NopeReading(**d) if d else None
    except Exception:
        pass

    try:
        data = _get(f"/api/stock/{sym}/nope", endpoint="/api/stock/{symbol}/nope")
    except Exception as exc:
        log.warning("unusual_whales.nope_failed", symbol=sym, error=str(exc))
        return None

    result: NopeReading | None
    row = data if isinstance(data, dict) else (data[0] if isinstance(data, list) and data else None)
    if not row:
        result = None
    else:
        result = NopeReading(
            nope=_to_float(row.get("nope")),
            nope_fill=_to_float(row.get("nope_fill")),
            call_delta=_to_float(row.get("call_delta")),
            put_delta=_to_float(row.get("put_delta")),
            call_vol=_to_float(row.get("call_vol")),
            put_vol=_to_float(row.get("put_vol")),
            stock_vol=_to_float(row.get("stock_vol")),
            timestamp=row.get("timestamp"),
        )

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _NOPE_TTL, json.dumps(asdict(result) if result else None))
    except Exception:
        pass
    return result


def get_historical_earnings_moves(symbol: str, *, limit: int = 8) -> list[HistoricalEarningsMoveRow]:
    """AUD-EARNINGSMOVE: real historical earnings-move track record for `symbol` from Unusual
    Whales' /api/earnings/{ticker} — confirmed real response shape via WebFetch against UW's own
    published API operation doc, fetched 2026-09-04 (every value a real string in the response;
    converted to float/int here). Returns most-recent-first, capped at `limit` (default 8 —
    matching this app's own existing eps_beat_rate convention of an 8-quarter lookback). Empty
    list (never None) on any failure/unavailability, matching get_max_pain()'s own list-returning
    contract. Redis-cached 6h (_EARNINGS_MOVE_TTL) — this is historical per-report data that only
    grows once per quarter per symbol, not a fast-moving live reading like NOPE/GEX.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    cache_key = f"stockai:uw:earnings_moves:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [HistoricalEarningsMoveRow(**r) for r in rows]
    except Exception:
        pass

    try:
        data = _get(f"/api/earnings/{sym}", endpoint="/api/earnings/{symbol}")
    except Exception as exc:
        log.warning("unusual_whales.earnings_moves_failed", symbol=sym, error=str(exc))
        return []

    result: list[HistoricalEarningsMoveRow] = []
    if isinstance(data, list):
        rows_sorted = sorted(
            (r for r in data if r.get("report_date")),
            key=lambda r: r["report_date"], reverse=True,
        )
        for row in rows_sorted[:limit]:
            result.append(HistoricalEarningsMoveRow(
                report_date=row.get("report_date"),
                report_time=row.get("report_time"),
                expected_move=_to_float(row.get("expected_move")),
                expected_move_perc=_to_float(row.get("expected_move_perc")),
                post_earnings_move_1d=_to_float(row.get("post_earnings_move_1d")),
                post_earnings_move_1w=_to_float(row.get("post_earnings_move_1w")),
                source=row.get("source"),
            ))

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _EARNINGS_MOVE_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def earnings_quarter_from_report_date(report_date) -> str:
    """AUD-TRANSCRIPT: derives UW's required "YYYYQ[1-4]" quarter string (e.g. "2024Q1") from a
    real report_date (a date or ISO date string) — deliberately NOT from EarningsEvent's own
    fiscal_year/fiscal_quarter fields, which this codebase's own AUD264-EARNINGS-FISCAL-QUARTER-
    FROM-ANNOUNCEMENT-MONTH fix already documents as "still only a best-effort calendar-month
    label," not a reliably correct fiscal quarter (a report's ANNOUNCEMENT date is 1-6 weeks
    after the fiscal period it actually covers, and can cross a calendar-quarter boundary).
    report_date is this codebase's own established reliable, unambiguous identity for a specific
    earnings event (the same reasoning that made it the real uniqueness key for EarningsEvent
    itself) — using simple calendar-quarter math on it is a plain, deterministic guess, still not
    guaranteed to match UW's own internal fiscal-quarter labeling for every company (some
    companies' fiscal quarters don't align with calendar quarters at all), but no worse a guess
    than the alternative, and a wrong guess fails open exactly the same way (UW returns no
    transcript for a quarter it doesn't recognize) as this endpoint's every other failure mode.
    """
    from datetime import date as _date
    if isinstance(report_date, str):
        report_date = _date.fromisoformat(report_date[:10])
    q = (report_date.month - 1) // 3 + 1
    return f"{report_date.year}Q{q}"


def get_earnings_transcript(symbol: str, quarter: str) -> list[TranscriptStatement]:
    """AUD-TRANSCRIPT: real earnings-call transcript statements for `symbol`/`quarter` (format
    "YYYYQ[1-4]", e.g. "2024Q1" — see earnings_quarter_from_report_date() above for how this app
    derives it) from Unusual Whales' /api/companies/{ticker}/transcripts/{quarter}. Confirmed
    real field shape (speaker, title, content, sentiment) via UW's own full OpenAPI YAML spec.
    List-returning, fail-open to an empty list — matching get_max_pain()'s own contract — on
    ANY failure, including (deliberately indistinguishable at this layer) a 403 from an account
    not on UW's own required Advanced+ tier for this specific endpoint. Redis-cached 24h
    (_TRANSCRIPT_TTL) — once published, a transcript is a fixed historical record.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    cache_key = f"stockai:uw:transcript:{sym}:{quarter}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [TranscriptStatement(**r) for r in rows]
    except Exception:
        pass

    try:
        data = _get(f"/api/companies/{sym}/transcripts/{quarter}", endpoint="/api/companies/{symbol}/transcripts/{quarter}")
    except Exception as exc:
        log.warning("unusual_whales.earnings_transcript_failed", symbol=sym, quarter=quarter, error=str(exc))
        return []

    result: list[TranscriptStatement] = []
    rows = data.get("statements") if isinstance(data, dict) else None
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            result.append(TranscriptStatement(
                speaker=row.get("speaker"),
                title=row.get("title"),
                content=row.get("content"),
                sentiment=_to_float(row.get("sentiment")),
            ))

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _TRANSCRIPT_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def get_sector_seasonality() -> list[SeasonalityRow]:
    """AUD-SEASONALITY: real, multi-year monthly seasonal return statistics for UW's own fixed
    13-ticker sector/index ETF set (SPY, QQQ, IWM, XLE, XLC, XLK, XLV, XLP, XLY, XLRE, XLF, XLI,
    XLB) from Unusual Whales' /api/seasonality/market — confirmed real field shape (with real
    example data) via UW's own full published OpenAPI YAML spec, fetched 2026-09-04. Takes no
    parameters — UW returns the full (ticker, month) matrix in one call (13 tickers x 12 months
    = up to 156 rows); callers filter to the ticker/month they need. List-returning, fail-open
    to an empty list (never None), matching get_max_pain()'s own contract. Redis-cached 24h
    (_SEASONALITY_TTL) — genuinely multi-year historical statistics, no reason to re-fetch more
    than once per day.
    """
    if not is_available():
        return []
    cache_key = "stockai:uw:seasonality:market"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [SeasonalityRow(**r) for r in rows]
    except Exception:
        pass

    try:
        data = _get("/api/seasonality/market", endpoint="/api/seasonality/market")
    except Exception as exc:
        log.warning("unusual_whales.sector_seasonality_failed", error=str(exc))
        return []

    result: list[SeasonalityRow] = []
    if isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            result.append(SeasonalityRow(
                ticker=row.get("ticker"),
                month=int(row["month"]) if row.get("month") is not None else None,
                avg_change=_to_float(row.get("avg_change")),
                median_change=_to_float(row.get("median_change")),
                min_change=_to_float(row.get("min_change")),
                max_change=_to_float(row.get("max_change")),
                positive_closes=int(row["positive_closes"]) if row.get("positive_closes") is not None else None,
                positive_months_perc=_to_float(row.get("positive_months_perc")),
                years=int(row["years"]) if row.get("years") is not None else None,
            ))

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _SEASONALITY_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def get_iv_rank(symbol: str) -> IVRankData | None:
    """AUD-DECIDE4-EXPECTEDMOVE: real, per-symbol implied volatility + 1-year percentile from
    Unusual Whales' /api/stock/{ticker}/iv-rank — confirmed field shape via UW's own published
    API spec (fetched 2026-09-03), same fail-open contract as get_gex_levels()/
    get_short_interest(). Built specifically to replace paper_trading_engine.py's
    _build_game_plan_for_style() fixed-percentage take-profit/no-ATR-stop fallback with a real,
    market-implied expected move — see OptionsGamePlanSnapshot's own model docstring
    (shared/db/models.py) for the full rationale and why this is computed via the daily batch
    snapshot rather than a live per-candidate call.

    Response is a list of daily rows (most recent first, per UW's own `timespan`/`date` params
    — this call takes neither, so UW's own default window applies); the most recent row is what
    a caller wants. Redis-cached 15 min.
    """
    if not is_available():
        return None
    sym = symbol.upper()
    cache_key = f"stockai:uw:iv_rank:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            d = json.loads(cached)
            return IVRankData(**d) if d else None
    except Exception:
        pass

    try:
        data = _get(f"/api/stock/{sym}/iv-rank", endpoint="/api/stock/{symbol}/iv-rank")
    except Exception as exc:
        log.warning("unusual_whales.iv_rank_failed", symbol=sym, error=str(exc))
        return None

    result: IVRankData | None
    # _get() already unwraps UW's real {"data": [...]} envelope once (see its own docstring) —
    # `data` here is already the row list, matching get_gex_levels()'s own handling exactly.
    rows = data
    if not rows or not isinstance(rows, list):
        result = None
    else:
        row = rows[0]
        result = IVRankData(
            volatility=_to_float(row.get("volatility")),
            iv_rank_1y=_to_float(row.get("iv_rank_1y")),
            close=_to_float(row.get("close")),
            as_of_date=row.get("date"),
        )

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _IV_RANK_TTL, json.dumps(asdict(result) if result else None))
    except Exception:
        pass
    return result


def get_greeks(symbol: str, expiry: str) -> list[StrikeGreeks]:
    """AUD-GREEKS: real per-strike call/put Greeks for `symbol` at a single `expiry`
    (YYYY-MM-DD) from Unusual Whales' /api/stock/{ticker}/greeks — one row per strike, matching
    UW's own real response shape (confirmed via its published API spec, fetched 2026-09-03).
    Returns an empty list (never None) on any failure/unavailability, matching
    get_flow_alerts()'s own list-returning contract — callers select the specific strike(s)
    they need from the returned list. Redis-cached 15 min per (symbol, expiry) pair.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    cache_key = f"stockai:uw:greeks:{sym}:{expiry}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [StrikeGreeks(**r) for r in rows]
    except Exception:
        pass

    try:
        data = _get(f"/api/stock/{sym}/greeks", params={"expiry": expiry}, endpoint="/api/stock/{symbol}/greeks")
    except Exception as exc:
        log.warning("unusual_whales.greeks_failed", symbol=sym, expiry=expiry, error=str(exc))
        return []

    result: list[StrikeGreeks] = []
    if isinstance(data, list):
        for row in data:
            result.append(StrikeGreeks(
                strike=_to_float(row.get("strike")),
                call_delta=_to_float(row.get("call_delta")),
                call_gamma=_to_float(row.get("call_gamma")),
                call_theta=_to_float(row.get("call_theta")),
                call_vega=_to_float(row.get("call_vega")),
                call_vanna=_to_float(row.get("call_vanna")),
                call_charm=_to_float(row.get("call_charm")),
                put_delta=_to_float(row.get("put_delta")),
                put_gamma=_to_float(row.get("put_gamma")),
                put_theta=_to_float(row.get("put_theta")),
                put_vega=_to_float(row.get("put_vega")),
                put_vanna=_to_float(row.get("put_vanna")),
                put_charm=_to_float(row.get("put_charm")),
            ))

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _GREEKS_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def get_short_interest(symbol: str) -> ShortInterestData | None:
    """Real, exchange-reported short-interest data for `symbol` (borrow fee, shares available,
    days-to-cover) from Unusual Whales' short-interest-float endpoint — a materially richer,
    faster-updating source than yfinance's own `shortPercentOfFloat`/`shortRatio` fields, which
    this app's squeeze-alert family currently relies on exclusively. Redis-cached 6h, matching
    the real ~2x/month settlement cadence of the underlying exchange data — no point re-fetching
    more often than the source itself updates. Fail-open, same contract as get_gex_levels().
    """
    if not is_available():
        return None
    sym = symbol.upper()
    cache_key = f"stockai:uw:short_interest:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            d = json.loads(cached)
            return ShortInterestData(**d) if d else None
    except Exception:
        pass

    try:
        data = _get(f"/api/shorts/{sym}/interest-float/v2", endpoint="/api/shorts/{symbol}/interest-float/v2")
    except Exception as exc:
        log.warning("unusual_whales.short_interest_failed", symbol=sym, error=str(exc))
        return None

    result: ShortInterestData | None
    if not data:
        result = None
    else:
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not row:
            result = None
        else:
            result = ShortInterestData(
                short_interest=_to_float(row.get("short_interest")),
                short_shares_available=_to_float(row.get("short_shares_available")),
                days_to_cover=_to_float(row.get("days_to_cover")),
                fee_rate=_to_float(row.get("fee_rate")),
                rebate_rate=_to_float(row.get("rebate_rate")),
                si_float=_to_float(row.get("si_float")),
                total_float=_to_float(row.get("total_float")),
                market_date=row.get("market_date"),
            )

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _SHORT_INTEREST_TTL, json.dumps(asdict(result) if result else None))
    except Exception:
        pass
    return result


_FLOW_ALERT_MAX_AGE_HOURS = 48  # AUD-OPTIONSFLOW-STALEALERTS — see get_flow_alerts()'s own
# docstring for the real production incident (live confirmed) this closes: UW's own endpoint,
# with no newer_than sent, silently returns EVERY historically-qualifying row still inside
# its retention window — confirmed live to include rows over 1,300 HOURS (54 days) old, not
# just "recent activity." 48h is a real, evidence-based choice, not a guess: live-checked
# real created_at ages across 5 liquid symbols and found a consistent, sharp gap between a
# fresh cluster (5-30h old) and the next-oldest batch (100h+) — 48h sits cleanly inside that
# gap, generous enough that a genuinely fresh sweep is never missed even across a brief job
# outage, while excluding the multi-week backlog entirely.


def get_flow_alerts(
    symbol: str,
    *,
    min_premium: float = 50_000,
    min_volume_oi_ratio: float = 1.0,
    is_sweep: bool = True,
    max_dte: int = 45,
) -> list[FlowAlert]:
    """Real, rule-based unusual-options-activity alerts for `symbol` from UW's own full-tape
    scanner (`/api/option-trades/flow-alerts` — the non-deprecated replacement for the older
    per-ticker `/api/stock/{ticker}/flow-alerts` endpoint). Each row is a contract UW's own
    scanner already flagged as having repeated, rapid same-contract hits — often a single large
    order sweeping across market makers, a real "urgency" signal UW computes for us, not
    something this app derives itself.

    AUD-UWRATELIMIT-FLOWALERTS: previously documented as "deliberately NOT cached" on the
    reasoning that flow alerts are a fast-moving, minute-to-minute feed and caching would defeat
    the point of an "alert on fresh activity" check — true in isolation, but this function is
    called once per symbol on every ~1-minute scheduler tick (check_options_flow_alerts(),
    scheduler.py), over a symbol set that's now capped on both sides (see that function's own
    docstring and scheduler.py's _OPTIONS_FLOW_ALERT_SYMBOLS_CAP). Confirmed live: 22,031 real UW
    429 responses in 48h (original incident), then AGAIN at 109,184/48h (AUD-UWUSAGE-FLOWALERTCAP,
    2026-09-06) after the symbol cap grew organically and a separate misfire-grace fix restored
    this job's true firing cadence. Cached at _FLOW_ALERT_TTL=150s — comfortably past the job's
    real observed ~60-120s inter-tick gap (not just its nominal 60s interval), so a symbol checked
    on any two consecutive real ticks is reliably a cache hit, while still meaningfully "fresh"
    for a same-day activity scanner. Safe at the trial tier's 30,000 req/day budget as long as the
    caller stays scoped to a bounded, CAPPED symbol set (see check_options_flow_alerts()'s own
    docstring in scheduler.py) — never called for the whole tracked universe.

    Filter thresholds are real UW query params (confirmed against the live OpenAPI spec, not
    guessed) — min_premium/min_volume_oi_ratio/is_sweep/max_dte are all genuine server-side
    filters, so UW itself does the filtering rather than this app fetching everything and
    discarding most of it. Defaults are deliberately a high-conviction floor (a real sweep, real
    size, genuinely unusual relative to existing open interest, expiring soon enough to matter)
    — every caller can override them, but the defaults alone already exclude routine, low-
    signal trades.

    AUD-OPTIONSFLOW-STALEALERTS (found 2026-09-02, a real user report): a user received an
    email for an HPE contract whose OWN expiry (2026-07-31) had already passed by the time the
    alert arrived. Confirmed live against production: this endpoint sends max_dte (bounding the
    CONTRACT's own expiration window) but never sent newer_than (bounding how recently the
    ALERT ITSELF fired) — UW happily keeps re-serving a real, still-qualifying-by-the-filter row
    from 7 weeks ago as if it were fresh activity, because nothing ever asked it not to. Now
    sends a real newer_than = now - _FLOW_ALERT_MAX_AGE_HOURS, so a genuinely stale alert can
    never reach a caller at all — this is a SERVER-side filter (UW itself excludes the old rows
    before they're ever returned), not a client-side post-filter, closing both the staleness bug
    and a real cost/flood contributor at the same time (see AUD-OPTIONSFLOW-FLOODED).

    Returns an empty list (never raises) on any failure — the disabled/unconfigured/rate-limited/
    no-alerts-for-this-symbol cases are all indistinguishable to a caller by design, matching
    get_gex_levels()/get_short_interest()'s own fail-open contract.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    # AUD-UWRATELIMIT-FLOWALERTS: cache key includes every caller-overridable filter param, not
    # just the symbol — two callers requesting the SAME symbol with DIFFERENT filters (e.g. a
    # future caller wanting a looser min_premium) must never share a cached result computed under
    # the other caller's filters. Only one real caller exists today (check_options_flow_alerts(),
    # scheduler.py), but that caller already passes its own module-level constants rather than
    # this function's own defaults, so keying on symbol alone would already be fragile.
    cache_key = f"stockai:uw:flow_alerts:{sym}:{min_premium}:{min_volume_oi_ratio}:{is_sweep}:{max_dte}"
    try:
        cached = _get_redis().get(cache_key)
        if cached is not None:
            import json
            return [FlowAlert(**row) for row in json.loads(cached)]
    except Exception:
        pass
    # UW's own newer_than param silently ignores ANY value with a time component (a full ISO
    # datetime, with or without a "Z"/offset suffix) — confirmed live against production: only
    # a bare unix-epoch integer or a bare YYYY-MM-DD date actually filters server-side; every
    # datetime-with-time form returns the exact same unfiltered result as omitting the param
    # entirely. Epoch seconds gives real sub-day precision (a bare date would only round to
    # "since midnight N days ago"), so that's the format sent here, not the ISO string this
    # module's docstrings otherwise use for get_historical_flow_alerts()'s own date-only params.
    newer_than = str(int((datetime.now(timezone.utc) - timedelta(hours=_FLOW_ALERT_MAX_AGE_HOURS)).timestamp()))
    try:
        data = _get(
            "/api/option-trades/flow-alerts",
            params={
                "ticker_symbol": sym,
                "min_premium": min_premium,
                "min_volume_oi_ratio": min_volume_oi_ratio,
                "is_sweep": "true" if is_sweep else "false",
                "max_dte": max_dte,
                "newer_than": newer_than,
                "limit": 50,
            },
            endpoint="/api/option-trades/flow-alerts",
        )
    except Exception as exc:
        log.warning("unusual_whales.flow_alerts_failed", symbol=sym, error=str(exc))
        return []
    result = _parse_flow_alert_rows(data, sym)
    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _FLOW_ALERT_TTL, json.dumps([asdict(a) for a in result]))
    except Exception:
        pass
    return result


def _parse_flow_alert_rows(data, sym: str) -> list["FlowAlert"]:
    """Shared row-parsing between get_flow_alerts() (live) and get_historical_flow_alerts()
    (backtest) — both hit the identical /api/option-trades/flow-alerts response shape, so this
    is the ONE place that shape gets translated into a FlowAlert, never a second, independently-
    drifting copy."""
    if not data or not isinstance(data, list):
        return []
    alerts: list[FlowAlert] = []
    for row in data:
        try:
            alerts.append(FlowAlert(
                ticker=row.get("ticker", sym),
                option_chain=row.get("option_chain", ""),
                option_type=row.get("type", ""),
                strike=_to_float(row.get("strike")),
                expiry=row.get("expiry"),
                price=_to_float(row.get("price")),
                underlying_price=_to_float(row.get("underlying_price")),
                total_premium=_to_float(row.get("total_premium")),
                total_ask_side_prem=_to_float(row.get("total_ask_side_prem")),
                total_bid_side_prem=_to_float(row.get("total_bid_side_prem")),
                total_size=int(row["total_size"]) if row.get("total_size") is not None else None,
                volume=int(row["volume"]) if row.get("volume") is not None else None,
                open_interest=int(row["open_interest"]) if row.get("open_interest") is not None else None,
                volume_oi_ratio=_to_float(row.get("volume_oi_ratio")),
                has_sweep=bool(row.get("has_sweep")),
                alert_rule=row.get("alert_rule"),
                created_at=row.get("created_at"),
            ))
        except Exception:
            continue  # one malformed row must never drop the rest of a real response
    return alerts


_HISTORICAL_FLOW_ALERTS_MAX_PAGES = 5  # UW's own limit=200 max per call — 5 pages = up to 1,000
# rows per symbol per backtest window, a real bound on both wall-clock time and the trial tier's
# request budget when scanning many symbols over a multi-month window.


def get_historical_flow_alerts(
    symbol: str,
    *,
    newer_than: str,
    older_than: str,
    min_premium: float = 250_000,
    min_volume_oi_ratio: float = 3.0,
    is_sweep: bool | None = True,
    max_dte: int = 45,
) -> list[FlowAlert]:
    """MPE-OPTIONS-FLOW-ALERT backtest: the SAME real /api/option-trades/flow-alerts endpoint
    get_flow_alerts() uses, but scoped to a real historical date range via newer_than/older_than
    — confirmed live against production (not assumed from the spec alone) that UW genuinely
    retains at least 2 months of real historical flow-alert rows with real created_at
    timestamps, real premiums, and real underlying_price snapshots at the moment each alert
    fired. This is a GENUINE historical replay, not a proxy — unlike squeeze_alert_backtest()'s
    own gamma-unwind gap (no historical options open-interest data exists anywhere for that),
    UW's own flow-alerts feed IS retained historically, so a real backtest is possible here.

    Defaults match check_options_flow_alerts()'s own live thresholds exactly (see
    AUD-OPTIONSFLOW-FLOODED) — a caller can override to test alternate thresholds, but the
    default backtest run tests the SAME filter that's actually live today, not a different one.

    is_sweep=None omits the query param entirely, returning a genuine MIX of sweep and non-
    sweep rows — confirmed live against production that UW's own is_sweep param is a hard
    binary filter both ways (True returns ONLY sweeps, False returns ONLY non-sweeps, neither
    is "unfiltered"), so a caller wanting to actually COMPARE sweep-vs-non-sweep outcomes must
    omit the key, not pass False (which would silently exclude every sweep, the opposite of a
    fair comparison).

    Paginates via older_than (UW's own 200-row-per-call max) up to
    _HISTORICAL_FLOW_ALERTS_MAX_PAGES pages — bounded, not exhaustive, matching this app's own
    "no silent truncation, log what's dropped" discipline: a symbol/window combination that
    would need more pages than this cap simply returns whatever the cap covers (the newest rows
    in the window, since UW returns newest-first), not a crash or an infinite loop.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    all_rows: list[dict] = []
    cursor_older_than = older_than
    params: dict = {
        "ticker_symbol": sym,
        "min_premium": min_premium,
        "min_volume_oi_ratio": min_volume_oi_ratio,
        "max_dte": max_dte,
        "newer_than": newer_than,
        "limit": 200,
    }
    if is_sweep is not None:
        params["is_sweep"] = "true" if is_sweep else "false"
    for _page in range(_HISTORICAL_FLOW_ALERTS_MAX_PAGES):
        try:
            data = _get(
                "/api/option-trades/flow-alerts",
                params={**params, "older_than": cursor_older_than},
                endpoint="/api/option-trades/flow-alerts",
            )
        except Exception as exc:
            log.warning("unusual_whales.historical_flow_alerts_failed", symbol=sym, error=str(exc))
            break
        if not data or not isinstance(data, list):
            break
        all_rows.extend(data)
        if len(data) < 200:
            break  # fewer than a full page — no more history left in this window
        oldest_created_at = data[-1].get("created_at")
        if not oldest_created_at:
            break
        cursor_older_than = oldest_created_at  # page backward in time
    return _parse_flow_alert_rows(all_rows, sym)


# T323-DARKPOOL: get_congress_trades()/CongressTradeRow live in shared/common/uw_congress.py,
# not here — event-intelligence (the only real consumer, via congress.py) runs in a separate
# container that never mounts market-data's own src/ tree. Re-exported here so any market-data
# code that wants congress data alongside GEX/short-interest/flow-alerts can still `from
# services.unusual_whales import get_congress_trades` without knowing about the split.
from common.uw_congress import get_congress_trades, CongressTradeRow  # noqa: E402,F401

_DARK_POOL_TTL = 900  # 15 min — matches GEX's own cadence; a real, current-day trading signal.


@dataclass
class DarkPoolPrintRow:
    """One row from UW's real `/api/darkpool/{ticker}` — a genuine off-exchange block trade,
    reported under FINRA's own trade-reporting rules. See DarkPoolPrint's own model docstring
    (shared/db/models.py) for what a dark pool actually is and why `venue` is stored verbatim."""
    symbol: str
    price: float | None
    size: int | None
    premium: float | None
    venue: str | None
    executed_at: str | None  # ISO datetime


def get_dark_pool_prints(symbol: str, *, limit: int = 50) -> list[DarkPoolPrintRow]:
    """Real off-exchange block trades for `symbol` from UW's real `/api/darkpool/{ticker}`.
    Redis-cached 15 min, matching GEX's own cadence — a real, current-trading-day signal that
    should never be more than briefly stale. Fails open (empty list), same contract as every
    other function in this module — a caller (MarketPressurePanel's dark-pool card,
    check_dark_pool_alerts()) must always be able to render/scan nothing without special-casing
    which failure mode occurred.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    cache_key = f"stockai:uw:darkpool:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [DarkPoolPrintRow(**r) for r in rows]
    except Exception:
        pass

    try:
        data = _get(f"/api/darkpool/{sym}", params={"limit": limit}, endpoint="/api/darkpool/{symbol}")
    except Exception as exc:
        log.warning("unusual_whales.dark_pool_failed", symbol=sym, error=str(exc))
        return []

    result: list[DarkPoolPrintRow] = []
    if isinstance(data, list):
        for row in data:
            try:
                price = _to_float(row.get("price"))
                size = int(row["size"]) if row.get("size") is not None else None
                premium = _to_float(row.get("premium"))
                if premium is None and price is not None and size is not None:
                    premium = price * size
                result.append(DarkPoolPrintRow(
                    symbol=sym,
                    price=price,
                    size=size,
                    premium=premium,
                    venue=row.get("market_center") or row.get("venue"),
                    executed_at=row.get("executed_at") or row.get("timestamp"),
                ))
            except Exception:
                continue

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _DARK_POOL_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


_OPTIONS_SCREENER_TTL = 300  # 5 min — a live scanning tool, but not fast enough-moving to need
# flow-alerts' own deliberately-uncached freshness; short enough that a user re-opening the page
# a few minutes later still sees genuinely current results.
_OPTION_TRADES_TTL = 120  # 2 min — the fastest-moving of the new endpoints (individual prints,
# not an aggregated screener), short TTL so 0DTE/interval views stay close to real-time without
# re-fetching on every keystroke of a filter change.
_MARKET_TIDE_TTL = 300  # 5 min — market-wide aggregate, doesn't need per-symbol freshness.


@dataclass
class OptionsScreenerRow:
    """One row from UW's real `/api/screener/option-contracts` — a live scan across the full
    options-eligible universe by unusual-activity criteria (volume > OI, min premium, DTE
    window, OTM-only, etc — all real UW server-side filters, confirmed against its own
    published example params), not a per-symbol lookup. Field names are UW's own best-known
    shape for this endpoint (option_symbol/underlying_symbol/type/strike/expiry/volume/
    open_interest/premium/iv) — this endpoint's exact response shape is NOT independently
    documented with a full field list in UW's own skill.md (only the request params are), so
    parsing below probes plausible key names defensively, matching get_congress_trades()'s own
    established discipline for an under-documented endpoint rather than assuming one true shape."""
    ticker: str
    option_symbol: str | None
    option_type: str | None  # "call" | "put"
    strike: float | None
    expiry: str | None
    volume: int | None
    open_interest: int | None
    premium: float | None
    implied_volatility: float | None


@dataclass
class OptionTradeRow:
    """One row from UW's real `/api/option-trades` — the raw options tape (individual prints),
    filterable by is_multi_leg/max_dte/min_premium/min_volume/is_otm/etc (all real, confirmed
    UW server-side params). This is the SAME endpoint that powers 3 conceptually-different UI
    views in this app (0DTE Flow = max_dte filter of 0, Multi-leg Flow = is_multi_leg=True,
    Interval Flow = a time-window filter) — deliberately ONE client method + ONE frontend page
    with filter toggles, not three independently-drifting copies of the same fetch/parse logic,
    since UW itself does not distinguish these as separate endpoints (confirmed directly against
    its own published API reference — no dedicated 0DTE or multi-leg endpoint exists)."""
    ticker: str
    option_symbol: str | None
    option_type: str | None
    strike: float | None
    expiry: str | None
    price: float | None
    size: int | None
    premium: float | None
    is_multi_leg: bool | None
    volume: int | None
    open_interest: int | None
    executed_at: str | None


@dataclass
class MarketTideRow:
    """One row from UW's real `/api/market/market-tide` — market-WIDE (not per-symbol) net
    call/put options premium over time, UW's own real aggregate sentiment measure. This is what
    this app's new Net Flow page is built from; UW's own per-symbol `/api/stock/{ticker}/
    net-prem-ticks` endpoint is NOT used here because its response shape is undocumented in
    UW's own skill.md (confirmed by direct inspection — the endpoint is merely listed, with zero
    example params or fields), and this app does not code against an unverified shape (matching
    the standing discipline applied to every other endpoint in this module)."""
    timestamp: str | None
    net_call_premium: float | None
    net_put_premium: float | None


def get_options_screener(
    *,
    option_type: str | None = None,
    min_premium: float = 100_000,
    max_dte: int = 45,
    is_otm: bool | None = None,
    min_volume: int | None = None,
    limit: int = 100,
) -> list[OptionsScreenerRow]:
    """Real, universe-wide options screener from UW's own `/api/screener/option-contracts` —
    scans ALL options-eligible symbols by real, server-side unusual-activity criteria, not a
    per-symbol lookup (the one genuinely new "search the whole market" capability among the
    endpoints added in this batch). `option_type` is passed through as UW's own `type` param
    ("Calls"/"Puts") when given; every other param matches UW's own confirmed-real example
    request shape (min_premium, max_dte, is_otm, min_volume, vol_greater_oi always sent as True
    — this is deliberately a SCREENER for unusual activity, not a raw unfiltered contract list).

    Redis-cached 5 min. Fails open (empty list) on any error, matching every other function here.
    """
    if not is_available():
        return []
    cache_key = f"stockai:uw:screener:{option_type}:{min_premium}:{max_dte}:{is_otm}:{min_volume}:{limit}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [OptionsScreenerRow(**r) for r in rows]
    except Exception:
        pass

    params: dict = {
        "limit": limit,
        "min_premium": min_premium,
        "max_dte": max_dte,
        "vol_greater_oi": "true",
    }
    if option_type:
        params["type"] = option_type
    if is_otm is not None:
        params["is_otm"] = "true" if is_otm else "false"
    if min_volume is not None:
        params["min_volume"] = min_volume

    try:
        data = _get("/api/screener/option-contracts", params=params, endpoint="/api/screener/option-contracts")
    except Exception as exc:
        log.warning("unusual_whales.options_screener_failed", error=str(exc))
        return []

    result: list[OptionsScreenerRow] = []
    if isinstance(data, list):
        for row in data:
            try:
                result.append(OptionsScreenerRow(
                    ticker=(row.get("ticker") or row.get("underlying_symbol") or "").upper(),
                    option_symbol=row.get("option_symbol") or row.get("option_chain"),
                    option_type=(row.get("type") or row.get("option_type") or "").lower() or None,
                    strike=_to_float(row.get("strike")),
                    expiry=row.get("expiry"),
                    volume=int(row["volume"]) if row.get("volume") is not None else None,
                    open_interest=int(row["open_interest"]) if row.get("open_interest") is not None else None,
                    premium=_to_float(row.get("premium") or row.get("total_premium")),
                    implied_volatility=_to_float(row.get("implied_volatility") or row.get("iv")),
                ))
            except Exception:
                continue
    result = [r for r in result if r.ticker]

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _OPTIONS_SCREENER_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def get_option_trades(
    *,
    max_dte: int | None = None,
    is_multi_leg: bool | None = None,
    min_premium: float = 50_000,
    min_volume: int | None = None,
    limit: int = 100,
) -> list[OptionTradeRow]:
    """Real raw options-tape prints from UW's own `/api/option-trades` — the single shared data
    source behind this app's 0DTE Flow (`max_dte=0`), Multi-leg Flow (`is_multi_leg=True`), and
    Interval Flow (no extra filter, just a recent window) UI views. Every param matches UW's own
    confirmed-real example request shape.

    Redis-cached 2 min per distinct filter combination — the shortest TTL among the new
    endpoints, since this is the closest of the three to a live tape rather than an aggregate.
    Fails open (empty list) on any error.
    """
    if not is_available():
        return []
    cache_key = f"stockai:uw:option_trades:{max_dte}:{is_multi_leg}:{min_premium}:{min_volume}:{limit}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [OptionTradeRow(**r) for r in rows]
    except Exception:
        pass

    params: dict = {"limit": limit, "min_premium": min_premium}
    if max_dte is not None:
        params["max_dte"] = max_dte
    if is_multi_leg is not None:
        params["is_multi_leg"] = "true" if is_multi_leg else "false"
    if min_volume is not None:
        params["min_volume"] = min_volume

    try:
        data = _get("/api/option-trades", params=params, endpoint="/api/option-trades")
    except Exception as exc:
        log.warning("unusual_whales.option_trades_failed", error=str(exc))
        return []

    result: list[OptionTradeRow] = []
    if isinstance(data, list):
        for row in data:
            try:
                result.append(OptionTradeRow(
                    ticker=(row.get("ticker") or row.get("underlying_symbol") or "").upper(),
                    option_symbol=row.get("option_symbol") or row.get("option_chain"),
                    option_type=(row.get("type") or row.get("option_type") or "").lower() or None,
                    strike=_to_float(row.get("strike")),
                    expiry=row.get("expiry"),
                    price=_to_float(row.get("price")),
                    size=int(row["size"]) if row.get("size") is not None else None,
                    premium=_to_float(row.get("premium") or row.get("total_premium")),
                    is_multi_leg=row.get("is_multi_leg"),
                    volume=int(row["volume"]) if row.get("volume") is not None else None,
                    open_interest=int(row["open_interest"]) if row.get("open_interest") is not None else None,
                    executed_at=row.get("executed_at") or row.get("timestamp"),
                ))
            except Exception:
                continue
    result = [r for r in result if r.ticker]

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _OPTION_TRADES_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def get_market_tide(*, interval_5m: bool = False) -> list[MarketTideRow]:
    """Real market-WIDE net call/put options premium over time from UW's own `/api/market/
    market-tide` — a genuine aggregate sentiment measure UW itself computes, not a per-symbol
    rollup. `interval_5m` matches UW's own real request param name verbatim.

    Redis-cached 5 min. Fails open (empty list) on any error.
    """
    if not is_available():
        return []
    cache_key = f"stockai:uw:market_tide:{interval_5m}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [MarketTideRow(**r) for r in rows]
    except Exception:
        pass

    try:
        data = _get("/api/market/market-tide", params={"interval_5m": "true" if interval_5m else "false"}, endpoint="/api/market/market-tide")
    except Exception as exc:
        log.warning("unusual_whales.market_tide_failed", error=str(exc))
        return []

    result: list[MarketTideRow] = []
    if isinstance(data, list):
        for row in data:
            try:
                result.append(MarketTideRow(
                    timestamp=row.get("timestamp"),
                    net_call_premium=_to_float(row.get("net_call_premium")),
                    net_put_premium=_to_float(row.get("net_put_premium")),
                ))
            except Exception:
                continue

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _MARKET_TIDE_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


def _to_float(v) -> float | None:
    """UW returns several numeric fields as strings (confirmed live) — a plain float() cast
    with a None/empty/non-numeric guard, matching this app's own established `_consensus_num()`/
    `_growth_num()` NaN-safety convention elsewhere (never let a malformed numeric field either
    crash the caller or silently become a fabricated 0.0)."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN self-inequality guard, same as _consensus_num()
    except (TypeError, ValueError):
        return None


# ── OPTHIST-1: historical option chains ──────────────────────────────────────────────────

def get_historical_option_chain(symbol: str, as_of: str) -> list[dict]:
    """OPTHIST-1: the full option chain for `symbol` as it existed on `as_of` (YYYY-MM-DD).

    This is the endpoint that makes a historical options backtest possible at all — before it,
    no historical chain existed anywhere in this platform (see OptionChainHistory's docstring).

    Returns UW's raw rows (dicts) so the persistence layer owns all parsing/coercion in one
    place; returns an EMPTY LIST on any failure or unavailability, never None and never raising,
    matching get_greeks()'s own list-returning contract.

    Deliberately NOT Redis-cached, unlike the live-data helpers above: a historical chain for a
    settled past date is immutable, so a short TTL buys nothing, and each response is thousands
    of rows — caching them would evict genuinely hot live keys for no benefit. The DB table IS
    the cache here.

    `greeks=true` asks UW to enrich each row with delta/gamma/theta/vega/rho + implied
    volatility. Measured on a real response: those arrive for exactly the contracts that traded
    that day (47.6% of rows, correlating 1:1 with volume>0), so expect them to be sparse — that
    is UW's behaviour, not a fetch failure.

    Lookback is a ROLLING window and tier-dependent — a date beyond it returns HTTP 403, which
    surfaces as UnusualWhalesAuthError and is caught here like any other failure. Verified
    2026-09-07 on API BASIC: real chains back to at least 2024-09-09.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    try:
        raw = _get(
            f"/api/stock/{sym}/option-chains",
            params={"date": as_of, "greeks": "true"},
            endpoint="/api/stock/{ticker}/option-chains",
        )
    except Exception as exc:  # incl. UnusualWhalesAuthError for out-of-window dates
        log.warning("unusual_whales.hist_chain_failed", symbol=sym, as_of=as_of, error=str(exc))
        return []
    if raw is None:
        return []
    rows = raw if isinstance(raw, list) else raw.get("data", [])
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]
