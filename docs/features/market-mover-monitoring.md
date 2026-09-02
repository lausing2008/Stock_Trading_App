## Feature Reference: Tier 249 — Market-Mover Monitoring (P0/P1/P2)

**Built 2026-07-14/15, P3 added 2026-07-17.** User's original ask: "monitor the news or any
information that would make the market go up or down. Get current earning reports or CPI/FOMC
before market starts, analyze the impact. Or get the results from CPI/FOMC after they announce
it ASAP and predict the trend. Same for earnings and news." A Fable 5 consult broke this into 5
slices (P0–P4); P0–P3 are built and live as of this writing. P4 (news pulse card) is still
`todo` in the tracker.

### The foundational bug this whole tier fixes: reference-period vs. release-date

`economic.py`'s original `sync_fred()` stores `event_date` as the observation's **reference
period** — e.g. `event_date="2026-06-01"` for June's CPI data — not the date BLS actually
**published** that number (July 14). These are two different axes wearing the same column
name. Any "alert me when CPI is released" feature needs the release-date axis; the reference-
period axis is for asking "what was June's CPI," which nothing in this tier needed. This gap
existed silently because `FRED_API_KEY` wasn't even set in production until this tier's work
started — `sync_fred()` had been no-op'ing (`fred_skip`) the whole time.

### P0 — Real release-date calendar (done)

- **FRED_API_KEY** set in production `.env` (get one free at
  fred.stlouisfed.org/docs/api/api_key.html). Rotated once already — see the log-leak section
  below for why.
- `economic.py`'s new `sync_fred_release_dates()` calls FRED's `fred/release/dates` endpoint
  (NOT `fred/series/observations`, which is what `sync_fred()` uses) per release_id in
  `_FRED_RELEASES`, with `include_release_dates_with_no_data=true` (required to see FUTURE
  scheduled dates — without it FRED only returns dates that already have data). Writes
  `{event_type}_release` rows (e.g. `cpi_release`) — a distinct event_type family from
  `sync_fred()`'s plain `cpi`/`nfp`/etc., so the two paths' rows never collide under
  `uq_economic_event(event_type, country, event_date)`.
- Scheduled daily at 06:15 UTC (`job_sync_fred_release_dates`) plus once at startup
  (`asyncio.create_task`, so a fresh deploy isn't empty until tomorrow's cron).
- market-data's `events_calendar()` now calls new `_macro_events_from_db()` first (reads the
  real `*_release` rows), and only falls back to the hand-maintained `_MACRO_2026` list for
  `(type, date-range)` combos the DB has no row for yet — `_MACRO_2026` is a safety net during
  rollout, not deleted.
- **Why BLS's own API was rejected as a data source** (relevant background for P2 below too):
  live research found BLS's own documentation states data is available via their v2 API
  ~1 day after the real release — disqualifying for same-day detection. FRED itself was
  confirmed live to have same-day availability (June 2026 CPI's `realtime_start` exactly
  equals its real July 14 release date).
- **Not built**: `EconomicEvent.expected_value` nowcast (Cleveland Fed proxy) — investigated
  and explicitly rejected. Cleveland Fed's inflation nowcast has no FRED series and no public
  API; the only live data is an internal FusionCharts JSON
  (`clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_{month,quarter,year}.json`)
  meant for their own chart widget. Fetched it directly and found real numbers, but the
  date-axis semantics were genuinely ambiguous (MM/DD labels with no year, all three files
  starting at the identical `08/20` regardless of month/quarter/year window) — could not
  confirm what a label actually means without rendering the real chart. Decided not to ship a
  data field whose correctness can't be verified. If revisited, the next step would be
  rendering the actual chart in a headless browser or comparing against an archived snapshot
  to pin down the axis, not re-guessing from the raw JSON.

### P1 — Earnings day-of alerts (done)

Two halves, both in `market-data/src/services/scheduler.py`, both scoped to `PriceAlert`-
subscribed users (not full watchlist/portfolio membership — a deliberate v1 scope-narrowing,
matching the existing `T230-ALERTING-EARNINGS-PROXIMITY` reminder's own audience rather than
introducing a wider join).

1. **Pre-market**: enriched the *existing* `T230-ALERTING-EARNINGS-PROXIMITY` day-of reminder
   (previously a generic "review your position" line) via new `_earnings_reminder_body()`,
   using `forward_eps`/`eps_beat_rate`/`eps_avg_surprise_pct` — all three already computed by
   `GET /stocks/{symbol}/fundamentals`, so this was pure wiring, not a new data source.
2. **Post-release**: genuinely new `check_earnings_reactions()`, a 1-minute-interval job (same
   cadence/lock pattern as `check_price_alerts`) reading event-intelligence's shared
   `earnings_events` table directly (same cross-service shared-table-read convention already
   used for `Ranking`/`Signal` elsewhere in this file) for symbols with `eps_actual` populated
   in the last 2 days. Fires one alert per `(user, symbol, report_date)` via a 7-day Redis
   dedup key, using the already-computed `surprise_pct`/`earnings_strength_score` — no LLM.

### P2 — Macro post-announcement fast reaction (done)

The literal "get the results ASAP and predict the trend" ask. The honest, buildable version:
fast detection of the real released number + an LLM reaction read — not an actual direction
prediction, which nobody can honestly deliver for an unreleased number.

**Detection — two independent, release-day-armed polls, both cheap no-ops on non-release days:**

- `services/event-intelligence/src/services/macro_reaction.py`'s
  `check_release_day_fast_poll()` — armed only 8:30–9:59am ET on weekdays
  (`CronTrigger(minute="*/2", hour="8-9", day_of_week="mon-fri", timezone="America/New_York")`
  — `America/New_York` handles DST correctly without manual UTC-offset math). Polls FRED's
  `series/observations` for CPI/PPI/GDP/NFP against `economic_events` rows still missing
  `actual_value` for today.
- `check_fomc_statement_poll()` — armed only 2:00–2:59pm ET, and only on real FOMC dates from
  `economic.py`'s `_FOMC_DATES`. Polls the Fed's own `press_monetary.xml` RSS feed directly
  (confirmed live: `federalreserve.gov/feeds/press_monetary.xml` — real entries, real dates)
  via `feedparser`, the same library already used in market-data's `news.py`. FRED's own rate
  series lag a day and have no "statement just posted" signal — hence the direct RSS poll.

**LLM reaction**: `generate_reaction()` calls Claude Haiku via raw `httpx` (same pattern as
decision-engine's `llm_scorer.py` — API key from Redis `stockai:admin:claude_api_key`),
fail-open (returns `None` on any error, never raises) — a missing reaction just means no email
fires that cycle, not a broken page.

**Delivery split** (same pattern as P1): event-intelligence detects + generates, writing
`reaction_text`/`reaction_generated_at` into `economic_events`; market-data's new
`check_macro_reaction_alerts()` (1-minute interval) polls for generated-but-unsent rows
(`reaction_sent_at IS NULL`) and emails the same `PriceAlert`-subscribed audience. `reaction_sent_at`
only advances inside an `if any_sent:` gate — a failed send cycle must retry next minute, not
get silently marked done (adversarially verified: removing this gate was caught by a dedicated test).

**New DB columns** (manual `ALTER TABLE` required — `create_all()` doesn't add columns to an
existing table): `economic_events.reaction_text` (TEXT), `.reaction_generated_at` (TIMESTAMP),
`.reaction_sent_at` (TIMESTAMP).

**New UI**: `GET /events/overview` gained a `latest_macro_reaction` field; a "Latest Macro
Reaction" card was added to `intelligence.tsx`'s Overview tab.

**Not built (deferred, not silently dropped)**: `sectors_helped`/`sectors_hurt` watchlist-join
personalization ("you hold/watch 4 rate-sensitive names") from the original design — the
current reaction is a general market-impact paragraph, not yet joined against the user's
specific holdings/sectors. Also not built: the per-user "macro alerts on/off" preference from
the original design (v1 reuses the `PriceAlert`-subscriber audience instead, per explicit
user choice to keep scope bounded).

### Recurring Issue: httpx Logs Full Request URLs (Including API Keys) at INFO Level

**Found 2026-07-15, while reviewing P2's deploy logs.** `httpx`'s own internal logger prints
`HTTP Request: GET https://api.stlouisfed.org/...?api_key=<real key>...` at INFO level on every
outbound call. Since `shared/common/logging.py`'s `configure_logging()` sets the stdlib root
logger to INFO (and `httpx`'s logger propagates to it), **every service that calls an external
API with a key as a query parameter had that key appear in plaintext in Docker logs** — this
had been happening since P0's `sync_fred_release_dates()` first shipped, invisible until
someone actually read the logs closely (42+ occurrences by the time it was caught).

**Fix applied**: added `logging.getLogger("httpx").setLevel(logging.WARNING)` to
`configure_logging()` in `shared/common/logging.py` — one shared fix covers every service.
WARNING still surfaces real connection/timeout errors, just not routine request lines.
Deployed by syncing `shared/common/logging.py` to all 11 backend containers and restarting
all of them (confirmed via `docker ps` diff that recreation was intentional and total, and via
a post-restart log grep that zero new `HTTP Request:` lines appeared).

**The exposed FRED key was rotated** as a precaution (get a new one free, instant, at
fred.stlouisfed.org/docs/api/api_key.html) — same "never embed real credential values in SSH
command strings" discipline applied throughout: the rotation was done by piping the key line
over SSH stdin to a remote Python script that rewrote `.env` in place, never as a `sed -i
's/.../<key>/'`-style command-line argument (which the permission system correctly blocked on
the first attempt) and never written to an intermediate file on either host (a `scp`-based
attempt was also correctly blocked for leaving a persistent plaintext artifact).

**A stray terminal escape sequence corrupted EC2's `.env` during this same edit** — line 2
became `61;7600;1cPOSTGRES_USER=stockai` instead of `POSTGRES_USER=stockai` (a leftover
cursor-position response terminal escape code, likely from an interactive editing session on
that file), which broke `docker compose` entirely (`unexpected character ";" in variable
name`). Fixed by stripping the garbage prefix (confirmed via `cat -A` before AND after the
fix, and confirmed no other line in the file had the same corruption) — **always run `docker
compose ... config` after any manual `.env` edit** to catch this class of corruption before it
blocks a real deploy.

**What to check if a future API key needs adding**: confirm `configure_logging()` still sets
`httpx`'s logger to WARNING (`docker exec <container> python3 -c "import logging;
print(logging.getLogger('httpx').level)"` should print `30`) before assuming a new key-bearing
API call is safe to add.

### P3 — Pre-market brief (done 2026-07-17)

The "before market starts" half of the original ask, generalized once P0–P2 existed. New
`send_premarket_brief()` job in `services/market-data/src/services/scheduler.py`, registered
as `premarket_brief_us`/`premarket_brief_hk` at 8:00 local (50 min ahead of the existing
`morning_digest_us`/`_hk` at 8:50, so catalyst context arrives before the opportunities digest).
`send_premarket_brief_email()` builder in `email_service.py` matches
`send_morning_digest_email()`'s section-composition HTML style.

**Deliberate scope narrowing from the original design doc**: no new LLM call. The original P3
fix note proposed generating a fresh conditional-scenario paragraph per brief ("if CPI prints
above X: historically pressures rate-sensitive names...") for an event that hasn't happened
yet. Built instead as pure composition of three already-computed sources, zero new LLM cost/
latency/hallucination risk per send:
1. Today's high/critical-importance macro releases — reuses P0's own `_macro_events_from_db()`
   (imported directly from `routes.py`, not re-queried).
2. Which of the recipient's own watched symbols report earnings today — `EarningsEvent.report_date
   == today` (the day-of window, vs. `check_earnings_reactions()`'s post-release `>=today-2d,
   eps_actual IS NOT NULL` window), same `user_symbols` construction pattern as P1.
3. Macro reactions generated in the last 18h — reuses P2's own already-LLM-generated
   `reaction_text` on real releases that already happened. This is the section that actually
   satisfies the "historically reacted" framing goal, and is more honest than a hypothetical
   pre-release scenario paragraph would have been — it reports what really happened, not what
   might. This required a genuinely new query (`reaction_generated_at >= now - 18h`); no
   existing helper covered this shape (`check_macro_reaction_alerts()` only tracks
   sent-vs-unsent, a queue, not a time window).

Audience: same `PriceAlert`-subscribed recipients as P1/P2 (`check_earnings_reactions()`/
`check_macro_reaction_alerts()`), deliberately narrower than `send_morning_digest()`'s all-`User`
audience, for consistency within the T249 alert family rather than introducing a third audience
model.

**Testing constraint hit again**: `send_premarket_brief()` itself can't be imported under the
local pytest harness — `scheduler.py`'s import chain pulls in `apscheduler` (and
`ingestion.py`/`paper_trading_engine.py`/`api/routes.py`), none of which `conftest.py` stubs,
matching the same constraint already documented in `test_price_alert_price_check.py` and
`test_earnings_alert_bodies.py`. `send_premarket_brief_email()` has no such problematic imports
(only `smtplib`/`common.config`/`common.logging`, all stubbed or stdlib) so it's tested directly
with real inputs — 9 tests covering empty-state notes in every section, impact-color
distinctness between critical/high, None-safe EPS-estimate formatting (adversarially verified:
temporarily removed the `is not None` guard and confirmed the resulting `TypeError` was caught
before restoring it), a 5-item cap on rendered reactions, and disclaimer presence. The job
function itself gets 5 source-text regression checks (matching `test_scheduler_static_names.py`'s
established pattern for the exact "MagicMock masks a real NameError" risk this repo has hit
before) plus a genuine live-verification call against the real deployed container:
```python
# Run inside stockai-market-data-1 with send_email monkeypatched to a no-op logger —
# calling the real function unpatched would email every real PriceAlert-subscribed user.
import sys; sys.path.insert(0, '/app')
import src.services.email_service as es
es.send_email = lambda *a, **kw: (print('WOULD SEND to', a[0], '| subject:', a[1]) or True)
from src.services.scheduler import send_premarket_brief
send_premarket_brief(['US'])
```
Ran clean on the real deployed container immediately after the `docker cp` + restart deploy:
no exceptions, real DB queries executed (P0/P1/P2 tables), logged
`premarket_brief.nothing_to_report` (a legitimate state — no high/critical macro releases
scheduled and no matching earnings/reactions at verification time), and
`scheduler:job:premarket_brief_us` recorded `{"status": "ok", "error": null}` in Redis —
confirming `_record_job_status()` wiring is correct too, not just the absence of a crash.

**Design invariant reinforced by this feature**: when a new scheduler.py function would send
real emails/pushes to real users, verify it live by monkeypatching the SEND function to a no-op
logger, never by calling the real function unpatched against production data — this is a
stricter version of the "verify against live state, not just tests" discipline already
documented elsewhere in this file, adapted for the case where the live verification itself has
a real-world side effect that must be neutralized first.

---


## Research: Reports Tab — Per-Market (US/HK) Report Aggregation (2026-07-16)

**Ask:** a Reports tab covering, per market: market trend, key asset performance, top-performing
stocks, money-flow-by-sector + recommended best stocks in that sector, news-sentiment
monitoring, and self-tuning/backtesting reports. Research-only pass — documents what already
exists (to maximize reuse) vs. what needs new backend work.

**User clarification (important, changes report #4's scope):** "best stocks in the sector"
means discovery across the WHOLE MARKET, not just symbols already in this app's ~150-stock
universe — with a one-click "add to my system" action once a good candidate is found. This is
a genuinely new capability (market-wide screening), not just aggregating existing per-symbol
data, and is the one part of this feature that can't be pure reuse.

### Per-report-type inventory (build-vs-reuse verdict)

| # | Report type | Verdict | Key existing endpoints/tables |
|---|---|---|---|
| 1 | Market trend | **Reuse** (near-complete) | `GET /stocks/regime?market=`, `/stocks/market_overview`, `/stocks/fear_greed` (includes `sp500_regime`/`sp500_vs_ma200_pct`), `/stocks/market_breadth` (US only — gap), `/stocks/regime-state` (HMM), `/events/valuation/cape` |
| 2 | Key asset performance | **Reuse** | `market_overview`'s `_INDICES` (^GSPC/^IXIC/^DJI/VIX/HSI), `GET /stocks/sector_rotation` (US sector ETFs vs SPY, 1w/1m/3m, leading/lagging) — gap: no HK sector-ETF equivalent |
| 3 | Top performing stocks | **Reuse** | `GET /rankings?market=` (K-Score), `/stocks/sector_performance` (per-sector day-change), `/rankings/screen`, `/admin/watchlist-performance` |
| 4 | Money-flow-by-sector + best stocks | **Reuse + 1 new endpoint + NEW market-wide screener** | `GET /stocks/sector-rotation` (Redis-cached K-Score momentum per sector, written weekly by `_compute_sector_rotation()`), `/stocks/hk-connect-flow/{symbol}` (per-symbol only — gap: no market-level top-N aggregation), `/{symbol}/options-flow`, `/{symbol}/institutional`, event-intelligence's insider/congress/institutional leaderboards, `/catalyst/leaderboard`. **NEW (per user clarification): a whole-market screener + "add to my system" action — see below.** |
| 5 | News sentiment (market-level) | **Mostly build** | Today's `news.py` (`_google_news`/`_claude_sentiment`) is per-symbol only. `T249-MARKETMOVER-P4-MARKET-PULSE-NEWS-CARD` (tracker, `todo`, effort S) is exactly this design — market-level queries through the existing pipeline, 30-min cache. `GET /events/overview`'s `latest_macro_reaction` field is already live and reusable now. |
| 6 | Self-tuning/backtest reports | **Reuse** (rich, already built) | `GET /signals/tune_status` (already rendered by `signal-tuning.tsx`), `/signals/outcomes/summary`, `/signals/accuracy`, `/signals/rolling_accuracy`, `/signals/gate_backtest`, `/admin/promotion-history`, `/admin/watchlist-rotation-history`, `/admin/scheduler-status`, `/paper-portfolio/entry_factors`, `/paper-portfolio/min_rr_calibration` |

### New capability needed for report #4 (per user's "whole market" clarification)

A market-wide stock screener is needed — NOT limited to this app's existing ~150-symbol
universe. yfinance itself has a screening capability (`yf.screen()` / predefined + custom
screener queries against Yahoo's own screener backend, still free-tier) that could surface
candidates by sector + performance without needing a paid screener API. Design: once
sector-rotation identifies a leading sector, run a market-wide screen scoped to that sector,
rank candidates by a simple momentum/volume heuristic (full K-Score requires data this app
doesn't have for a symbol not yet in the universe), and surface each with an **"Add to my
system"** button — reusing this app's EXISTING add-stock/ingest pipeline (the same one driving
manual symbol additions today) to seed the new symbol, trigger initial ingestion, and optionally
add it to a chosen watchlist in one action.

### Page structure precedent

`frontend/src/pages/intelligence.tsx` is the model to follow: a `type Tab` union + a `TABS`
array + `useState<Tab>` + one component per tab, backed by a single aggregate fetch
(`eventsOverview()`). Nav: add a `Reports` entry to the `Markets` group in `_app.tsx`'s
`NAV_GROUPS`.

### Phased plan (not yet built)

**Phase 1 — frontend-only, composing existing endpoints** (covers report types 1, 2, 3,
4-partial, 5-partial via the macro-reaction field, and 6 in full): new
`frontend/src/pages/reports.tsx` with a US/HK market toggle + tabs (Trend / Assets / Top Stocks
/ Money Flow / News & Macro / Self-Tuning), composing `regime`, `marketOverview`, `fearGreed`,
`marketBreadth`, CAPE, `sectorRotationEtf`, `sectorRotation` (K-Score momentum), `rankings`,
`sectorPerformance`, `eventsOverview`, `signalTuneStatus`, `outcomesSummary`,
`promotionHistory`, `schedulerStatus`, `minRrCalibration`, `entryFactors`. Touches:
`frontend/src/pages/reports.tsx` (new), `frontend/src/pages/_app.tsx` (nav entry),
`frontend/src/lib/api.ts` (a few missing wrappers — `hkConnectFlow`, `gateBacktest`,
insider/congress/institutional leaderboards if not already present).

**Phase 2 — new backend, ranked by effort:**
1. HK southbound money-flow top-N endpoint (simple SQL over the already-existing
   `hk_connect_flows` table) — S.
2. HK market breadth (extend `market_breadth` with a `market` param) — S.
3. T249-P4 market-level news-pulse endpoint (design already written in the tracker) — S/M.
4. Whole-market sector screener + "Add to my system" action (per the user's clarification
   above — the one genuinely new discovery capability, not just aggregation) — M.
5. HK sector-ETF rotation equivalent to the existing US one — M.
6. `/stocks/top_movers?market=` N-day gainers/losers convenience endpoint (optional — largely
   already composable from rankings + sector_performance client-side) — S/M.

---


## Research: Tier 257 — Four Feature Designs (2026-07-17, design-only, no code yet)

User ask, verbatim intent: (1a) a per-minute abnormal-volume alert with a breakout-or-breakdown
read; (1b) a per-minute "top 3 stocks about to move, very very high confidence" buy/sell email;
(3) overnight options-flow + futures-flow analysis to read whether the market opens high/low
and lay out the day; (4) prod E*Trade "using client secret but why still login — make it more
systematic." All four researched against the actual codebase (3 parallel mapping agents,
file:line-verified) before designing. Tracker: T257-* entries.

### 1a. Abnormal-Volume Alert (T257-VOLUME-ANOMALY-ALERT)

**The data path already exists and is the ONLY viable one at 1-minute cadence:**
`stockai:live_prices` (refreshed every 1 min by `_live_price_refresh_job`, scheduler.py:4657 —
one bulk yf.download for the whole universe; carries current-day cumulative `volume` +
`change_pct`) and `stockai:avg_volume` (`_AVG_VOLUME_KEY`, 20-day mean, refreshed 4-hourly).
A new 1-min job MUST read only these two Redis keys — per-symbol yfinance or `/rvol` DB calls
at 150-symbols/minute would rate-limit or hammer the DB (yfinance was observed rate-limited
this very day). Precedent: the post-open digest's vol_surge scan (scheduler.py:3838) already
does exactly this Redis-only universe sweep, just at 5-6×/day instead of every minute.

**Abnormality math — reuse T241's session-elapsed scaling, don't invent new math:** raw
`volume/avg_volume` compares partial-day cumulative volume against a FULL-day average — at
10:00 ET even a normal day looks "low" and a slightly-busy open looks normal. The already-fixed
form (T241-AUDIT-RVOL-INTRADAY-BIAS, scheduler.py:3881-3887) scales the threshold by session
elapsed fraction: `surge_threshold = max(1.05, BASE × elapsed_frac)`. New job uses the same,
with a higher BASE (e.g. 2.5-3.0× for "abnormal/huge" vs. the digest's 1.5×) — exact value to
tune after observing a week of candidate counts.

**Direction + breakout/breakdown read (honest version):** direction from `change_pct` sign.
For the handful of symbols that actually trigger (not universe-wide): compare live price
against the stored game-plan `breakout` level (already computed into signal reasons,
scheduler.py:843) and stop level → label "pressing its breakout level ($X) on Nx volume" /
"breaking below stop/support ($Y) on Nx volume." One technical-analysis `/levels` HTTP call
per TRIGGERED symbol is acceptable (few/day); never in the universe loop. Framing per repo
discipline: the email reports the measured fact (volume ratio + which level price is testing)
and historical context — it does NOT claim "this WILL break out"; nobody can honestly deliver
that, and the repo's T249-P3 precedent explicitly rejects prediction claims.

**Job shape:** every-minute `add_job(..., "interval", minutes=1, max_instances=1,
coalesce=True)`, 55s Redis lock (`stockai:lock:...` — same pattern as check_price_alerts),
market-hours-gated (the live-price cache is only fresh during market hours anyway). Recipients:
the established PriceAlert-subscriber audience (consistent with the whole T249/T230 alert
family). Dedup: `stockai:vol_anomaly:{uid}:{sym}:{date}` with a same-day TTL, PLUS an
escalation re-fire if RVOL later doubles again from the alerted level (a 3× alert shouldn't
suppress a later 6× climax). Daily cap per user (e.g. 10) to bound spam on broad-market
high-volume days when many symbols trigger simultaneously.

**Extend, don't duplicate:** `AlertCondition.VOLUME_SPIKE` (models.py:325) exists but is
daily-bar, per-subscribed-symbol, ~5-min cadence via check_technical_alerts — a different
product (subscribed-symbol technical alert) from this universe-wide anomaly scan. Keep both;
name the new job distinctly (`check_volume_anomalies`).

### 1b. Top-3 High-Confidence Movers Alert (T257-TOP3-CONVICTION-ALERT)

**The honest version of "very very high confidence" already exists as data:** signal-engine's
confidence calibration (`_build_confidence_calibration`, signal-engine routes.py:260) buckets
REAL measured win rates from signal_outcomes by (horizon, direction, market, confidence-band),
min n=30, cached in Redis (`signal:confidence_calibration`, 1h TTL) — this is the number shown
as "Historical win rate (n=85)" on stock pages. **The design gates on MEASURED bucket win rate,
not raw model confidence**: a pick qualifies only if its bucket's tracked win rate ≥ threshold
(propose 70% to start) AND n ≥ 30 AND `conviction_tier == "full"` (the 7-layer/4-layer
`_is_conviction_buy` gate, scheduler.py:585) AND K-Score ≥ 55 AND regime not bear/risk_off for
BUYs. Rank all qualifying candidates by bucket win rate (tiebreak: confidence), hard-cap 3.

**What's genuinely new vs. today's check_signal_alerts:** (a) cross-symbol ranking + cap — the
existing alert fires per-symbol independently with no selection step; (b) wiring
calibrated_win_rate into the FIRE decision — today it's display-only; (c) cadence honesty:
signals regenerate on the 5-minute refresh bursts, so a 1-minute loop would mostly re-scan
unchanged data — run the scan every minute (cheap Redis/DB reads) but fire only when the
qualifying set CHANGES (new symbol qualifies, or direction flips), dedup per
(user, symbol, direction, day), max one email per composition change.

**Expectation to set explicitly with the user (put it in the email footer too):** on most days
ZERO picks will clear a 70%-measured-win-rate bar — an empty day means the bar is working, not
that the feature is broken. The email includes each pick's measured win rate + sample size
("this setup class won 72% over the last 41 tracked outcomes") — never an unbacked confidence
claim. If the user later wants more alerts, the threshold is one Redis-tunable knob; lowering
it trades accuracy for frequency, and the email's own printed win-rate keeps that trade-off
visible.

### 3. Overnight Options/Futures Flow → Morning Day-Plan (T257-OVERNIGHT-FLOW-BRIEF)

**Current state (mapped, verified):** ZERO futures data exists anywhere (no ES=F/NQ=F/YM=F/
RTY=F references; market_overview._INDICES is spot-only ^GSPC/^IXIC/^DJI/^VIX/^HSI).
Options-flow exists per-symbol (`GET /stocks/{symbol}/options-flow` — call/put volume,
cp_ratio, whale premiums >$500K) but is live-only via yfinance option chains, rate-limit
fragile, with NO historical persistence — nothing can currently answer "what did flow look
like yesterday/overnight." Premarket bars ARE ingested and labeled (T230-CHARTING-PREMARKET's
`_classify_session`) but only for charting. The 8:00 local `send_premarket_brief` (T249-P3) is
the natural delivery vehicle — an overnight-flow section slots in as section 4.

**Phase 1 (cheap, buildable immediately): overnight futures + premarket read.** New ~7:15 ET
job fetching ES=F, NQ=F, YM=F, RTY=F + VIX via one bulk yfinance call → overnight change vs.
prior settle; top premarket gappers in the universe from already-ingested PRE-session bars;
both added to the pre-market brief. Framing: futures ARE the market's own live expectation of
the open — "ES +0.8% overnight" is a measurement, and reporting it as "futures point to a
higher open" is honest because that's literally what futures prices mean; predicting whether
that holds through the open is not claimed. Optionally later: compute and print the tracked
historical stat "on days futures were up >0.5% overnight, SPY's open was green X% of the time
(n=...)" from our own stored data — only once actually measured, never asserted from intuition.

**Phase 2: options-flow snapshots (the "where investors put money" half).** New
`options_flow_snapshots` table + an end-of-day job persisting per-symbol cp_ratio, call/put
premium, whale_count for a bounded set (PriceAlert-subscribed + top-K by K-Score, NOT the whole
universe — yfinance option chains are the most rate-limited endpoint we touch), spread over
minutes with backoff. The morning brief can then report "yesterday's late-day flow was
call-heavy on X/Y/Z (cp_ratio 3.2, $1.4M whale premium)" — real observed positioning, which is
what "see where the investors putting money" actually asks for. True OVERNIGHT options flow
(index options trading in Globex hours) is not available from yfinance at all — that would
need a paid data source (documented as a known limitation, not silently faked with stale data).

**Phase 3: the "day layout" — an attention list, not a plan-of-trades.** Brief section listing
symbols scoring on ≥2 of: premarket gap beyond threshold, unusual prior-day options flow
(Phase 2), earnings today (P1 data), macro release today (P0 data) — "pay attention to these
today, here's why," each reason a measured fact. Explicitly NOT auto-generated buy/sell
instructions — that's what the signal pipeline + T257-TOP3 alert are for, with their tracked
accuracy; duplicating direction calls here with no outcome tracking would be the dishonest
version.

### 4. Systematic Prod E*Trade Auth (T257-ETRADE-PROD-SYSTEMATIC)

**Direct answer to "using client secret but why still login":** E*Trade uses OAuth 1.0a. The
consumer key + client secret only identify THE APP — they cannot produce an access token by
themselves (there is no client-credentials or refresh-token grant in OAuth 1.0a, by design).
The browser login + PIN (verifier) step is E*Trade's mandated way for the ACCOUNT HOLDER to
authorize the app. This step cannot be legitimately automated (scripting their login page
violates their API agreement), and E*Trade access tokens **hard-expire at midnight US Eastern
every day** — plus go inactive after ~2h of no API activity intraday (reactivatable via
renew). So some periodic re-auth is an E*Trade platform constraint, not an app bug.

**What IS ours to fix (mapped against the real code):**
1. **`renew_access_token()` exists (etrade_broker.py:115) but is NEVER scheduled** — only a
   manual "Reconnect" button calls it. Fix: an intraday keepalive cron (e.g. every 90 min,
   market hours) renewing all authorized E*Trade connections so tokens never go 2h-idle-dead
   mid-session. This is the single highest-value change.
2. **Silent intraday failure:** on a dead token, paper trading's broker calls silently no-op
   (`_get_portfolio_broker` returns None / exceptions logged as warnings) until the ONCE-DAILY
   08:30 ET `_check_broker_auth` health check notices. Fix: in-loop 401/token_rejected
   detection in `_place_broker_entry`/`_place_broker_exit`/`poll_broker_order_fills` →
   immediately mark `is_authorized=False` + fire the (already-existing)
   `send_broker_reauth_email` with a fresh authorize URL, instead of waiting for tomorrow's
   cron.
3. **One-tap morning re-auth UX:** the daily re-auth email already exists and already embeds a
   fresh authorize URL; streamline the landing so Settings auto-focuses the PIN input (and
   auto-completes on paste) — the human step shrinks to: click email link, log in, copy PIN,
   paste. That's the floor OAuth 1.0a allows.
4. **Prod switch itself is config-only:** broker_type `etrade` (vs `etrade_sandbox`) with prod
   consumer key/secret entered in Settings — OAuth endpoints already always hit the prod base
   (etrade_broker.py:73,101,118); data/order calls swap base by flag. Prerequisite is E*Trade's
   own portal approval for a prod API key.
5. **If daily-login is unacceptable for full automation:** the structural answer is
   TIER84-BROKER-ALPACA (already in the tracker) — Alpaca auths with a plain API key/secret,
   no PIN, no daily expiry. E*Trade's daily midnight expiry is a hard platform limit for
   unattended trading; document the trade-off rather than fighting it.

**Also flagged during research:** T205-ETRADE-SANDBOX's tracker text is stale (describes
"OAuth 2.0" and claims no live calls exist — the full 1.0a flow shipped in Tier 18); fold its
correction into the T257 work when built.

### T257-ETRADE-PROD-SYSTEMATIC — Built 2026-07-17

Items (1)-(3) above shipped same-day; item (4) (Alpaca) remains documented only.

**Shared helpers** (`scheduler.py`) factored out of `_check_broker_auth`'s previously-inline
logic so the new keepalive cron and in-loop detection don't duplicate (and risk drifting
from) the same checks: `_is_token_rejected_error(err)` — pure string-matching on
`token_rejected`/`401`/`unauthorized`, case-insensitive; `_mark_broker_unauthorized_and_notify
(session, conn)` — flips `is_authorized=False`, mints a fresh `start_oauth()` URL, emails via
the existing `send_broker_reauth_email`.

**New keepalive cron** `_renew_broker_tokens()`, registered at 5 fixed ET clock times spanning
the trading session — `(9,45), (11,15), (12,45), (14,15), (15,45)` — **not** a raw cron
`minute="*/90"` interval. Caught this exact mistake while implementing: APScheduler's
`CronTrigger` minute field only spans 0-59, so `*/90` would silently register a job that never
fires — a genuinely dangerous silent failure for something meant to prevent silent failures.
Calls the existing (previously never-scheduled) `renew_access_token()` on every
active+authorized `etrade`/`etrade_sandbox` connection; skips other broker types (Alpaca, when
it exists, doesn't have this OAuth 1.0a concept). On a genuine rejection (not just idle),
immediately hands off to `_mark_broker_unauthorized_and_notify` rather than waiting for the
08:30 ET check.

**In-loop detection** — new `_handle_broker_error_if_token_rejected(session, portfolio, exc)`
in `paper_trading_engine.py`, wired into all three previously-silent broker call sites
(`_place_broker_entry`, `_place_broker_exit`, `poll_broker_order_fills`). Each now distinguishes
a token rejection (immediate mark-unauthorized + reauth email) from a transient/unrelated
error (still just logged — a network timeout must NOT flip a healthy connection to
unauthorized, which would be its own new bug). **Lazily imports** `scheduler.py`'s two helpers
inside the function body, not at module top — `scheduler.py` already imports several names
from `paper_trading_engine.py` at its own module level (`get_last_regime`,
`paper_trading_step`, etc.), so a top-level import in the reverse direction would create a
circular import. A dedicated test asserts the import stays lazy; adversarially verified by
temporarily moving it to module-top and confirming the test caught it.

**Settings UX**: the PIN/verifier input auto-focuses via a callback ref (`ref={el =>
el?.focus()}`) the instant the authorize URL appears — correct here specifically because this
input only mounts once `oauthUrl[b.id]` is set, so ref-callback-on-mount fires exactly when the
field first exists, no separate ref map needed for the per-broker-row case. Enter now submits
too. Net flow: click the emailed/on-screen link → log in on E*Trade → copy PIN → switch back
(already focused) → paste → Enter. That's the floor OAuth 1.0a's mandated human-authorization
step allows — there is no way to remove it entirely without abandoning E*Trade for a
key-only broker like Alpaca (see design section above, item 5).

**Tests**: `services/market-data/tests/test_etrade_token_renewal.py`, 12 cases.
`_is_token_rejected_error` is pure/dependency-free (no DB/HTTP), loaded directly via the
exec()-from-source technique (matching `test_earnings_alert_bodies.py`) and tested with real
inputs — including the important negative case that a timeout or 500 must NOT match, which
would silently misfire the whole feature (flipping healthy connections unauthorized on any
transient error). The scheduling/wiring is source-text-checked (`scheduler.py` can't be
imported in this test environment — its import chain pulls in `apscheduler` — matching
`test_scheduler_static_names.py`'s established pattern for exactly this constraint).

---


## Feature Reference: T257-VOLUME-ANOMALY-ALERT — Abnormal Volume Detection (Built 2026-07-17)

**User ask:** "I want a volume alert, check every min on the volume, if you see some abnormal
vol or huge vol going up or down, send me the stock details and will it breakout or breakdown."

**Design constraint carried over from this repo's established rate-limit discipline:** a
1-minute universe-wide scan must NEVER call yfinance or hit per-symbol DB rows in the main
loop — this repo has hit yfinance rate-limiting before from exactly this class of tight loop.
`check_volume_anomalies()` (`services/market-data/src/services/scheduler.py`) reads only the
pre-existing Redis caches `stockai:live_prices` and `stockai:avg_volume`, both already
maintained by other jobs for other purposes. Only for the small subset of symbols that
actually trigger does it make a per-symbol HTTP call — to technical-analysis's
`GET /ta/{symbol}/levels` — to find the nearest support/resistance level in the move's
direction, for the "will it breakout or breakdown" part of the ask.

**Threshold — session-elapsed-scaled, not a flat multiple**, reusing the same principle
already documented for T241-AUDIT-RVOL-INTRADAY-BIAS elsewhere in this file: comparing a
partial trading day's cumulative volume against a full day's average volume would produce
false triggers in the first hour of trading even on a perfectly normal day. Computes separate
US/HK session-elapsed fractions via `ZoneInfo`, then `threshold = max(1.5, 2.5 * elapsed_fraction)`
— early in the session the bar is lower (in raw multiple terms) but the absolute volume
required to clear it is still proportionally reasonable for how much of the day has passed.

**Gating and delivery**: Redis lock (`_VOL_ANOMALY_LOCK_KEY`, 55s TTL) prevents overlapping runs
if one cycle runs long. Triggered symbols sort by RVOL descending. Delivery is scoped to the
`PriceAlert`-subscriber audience (same narrower v1 scope already established for P1/P2 of
Tier 249's Market-Mover Monitoring — not the full watchlist/portfolio membership). Per-recipient
dedup + a daily cap prevent spam: `stockai:vol_anomaly_cap:{uid}:{today}` caps total emails per
user per day; `stockai:vol_anomaly:{uid}:{symbol}:{today}:{int(rvol//1)}` dedups the same
symbol at materially the same RVOL magnitude within the same day (a stock climbing from RVOL 3
to RVOL 8 over the day fires again — a stock oscillating between RVOL 3.1 and 3.3 does not).

**Honesty note on "will it breakout or breakdown"**: the email includes the nearest S/R level
and which side of it price sits on, framed as context, not a prediction — matching this
repo's standing disclaimer convention (see the Top-3 Conviction Alert below for the same
principle applied more strongly). No model claims to know the outcome; it surfaces the
structural level a trader would want to know about before deciding for themselves.

**Files**: `services/market-data/src/services/scheduler.py` (`check_volume_anomalies()`,
registered `id="volume_anomaly_check"`, `"interval"`, `minutes=1`, right after
`price_alert_check`), `services/market-data/src/services/email_service.py`
(`send_volume_anomaly_email`).

**Tests**: `services/market-data/tests/test_volume_anomaly_alert.py`, 11 cases.
`send_volume_anomaly_email` is tested directly; the scan logic (Redis-only reads, no
yfinance/DB calls in the loop, threshold math, dedup/cap keys) is source-text-checked, matching
the established pattern for functions with heavy Docker-only dependencies. One false-positive
caught while writing these: an early assertion checked `"yfinance" not in body`, which failed
because the function's own docstring legitimately explains why yfinance is avoided — fixed to
check `"import yfinance" not in body` (actual usage, not word presence).

**What to check if this looks wrong**: `docker logs stockai-market-data-1 --since 1h | grep
'volume_anomaly'` for scan activity; confirm the Redis caches it reads are actually fresh
(`docker exec stockai-redis-1 redis-cli get stockai:live_prices` — if stale, the alert is
comparing against old prices, not a bug in this feature itself but in whatever job populates
that cache).

---


## Feature Reference: T257-TOP3-CONVICTION-ALERT — High-Conviction Pick Alert (Built 2026-07-17)

**User ask:** "I want to get email when you think 3 top stocks will be going up or down with
very very high confidence, I will buy or sell the stock as you recommended, I need it to be
very accurate and confident." Because the user explicitly said they'd act on these picks
directly, the gating design deliberately optimizes for honesty over pick frequency — most
1-minute cycles are expected to qualify zero picks, by design, not as a bug.

**Why measured win rate, not raw model confidence**: raw signal confidence
(`abs(fused_probability - 0.5) * 200`, see the "Why a BUY Signal Can Show Low Confidence"
design reference elsewhere in this file) measures distance from a coin-flip, not real-world
accuracy. Given the user's explicit intent to act on these directly, `check_top3_conviction()`
instead gates on signal-engine's existing confidence-calibration cache — real historical
bucket win rates keyed `"{horizon}|{direction}|{market}|{band}"`, built from actual
`signal_outcomes` rows, requiring a minimum sample count before a bucket counts at all
(`_TOP3_MIN_COUNT = 30`). **If the calibration cache is empty for any reason, the function
returns zero picks rather than silently falling back to raw confidence** — this fallback-to-
zero is deliberate and adversarially verified (temporarily replaced the guard with `pass` and
confirmed the dedicated test caught it before reverting). A default minimum win rate of 0.70
(`_TOP3_MIN_WIN_RATE`, Redis-tunable via `stockai:top3_min_win_rate` without a redeploy) is the
"very very high confidence" bar; BUY additionally requires regime not bear/risk-off and
K-Score ≥ 55 (`_TOP3_MIN_KSCORE`).

**Deliberately NOT the full 7-layer Conviction Gate**: `_is_conviction_buy()` (K-Score/Uptrend/
RSI/MACD/OBV/ADX/ML) would require per-symbol signal-detail fetches for the whole universe
every minute — reintroducing exactly the rate-limit cost problem this feature has to avoid.
Instead built a lighter gate directly from data already fetchable in bulk: `GET /signals?
style=X` for all 4 horizons, `GET /signals/confidence-calibration`, `GET /rankings` for
K-Scores — 3 bulk calls total per cycle, not N per-symbol calls.

**Regime lookup is a direct function call, not HTTP** — an earlier draft had this reaching back
into market-data's own `/stocks/regime` endpoint via a hacky URL string substitution
(`_settings.signal_engine_url.replace('signal-engine', 'market-data')`); caught and fixed to
call `get_last_regime()` / a locally-imported `get_last_hk_regime()` directly, since
`scheduler.py` already runs inside market-data itself — no HTTP round-trip needed for a
same-process call.

**Delivery**: sorts qualifying candidates by `(win_rate, confidence)` descending, caps to the
top 3. Tracks the last-sent composition (`stockai:top3_last_composition`) so an unchanged set
of 3 picks doesn't re-email every single minute — only fires again when the actual composition
changes.

**Files**: `services/market-data/src/services/scheduler.py` (`check_top3_conviction()`,
registered `id="top3_conviction_check"`, `"interval"`, `minutes=1`),
`services/market-data/src/services/email_service.py` (`send_top3_conviction_email`, subject
line explicitly says "measured win rate ≥70%" rather than implying a company-endorsed
prediction, and the body disclaimer explicitly states "not a prediction... Most cycles qualify
zero picks" so a user seeing an empty inbox for days understands that's expected, not broken).

**Tests**: `services/market-data/tests/test_top3_conviction_alert.py`, 15 cases, including
dedicated checks for the no-fallback-to-raw-confidence guard, the regime-lookup-is-a-direct-
call-not-HTTP property, and the ranked-by-win-rate-not-confidence ordering. One false positive
fixed during writing: a 300-character slice window used to isolate the calibration-empty-guard
source text cut off before the word "return" appeared — widened to 400 characters.

**What to check if this looks wrong**: `docker logs stockai-market-data-1 --since 1h | grep
'top3_conviction'`; if zero emails have fired in a long time, check
`docker exec stockai-redis-1 redis-cli get stockai:top3_min_win_rate` (confirm no stale
override) and whether `GET /signals/confidence-calibration` is actually returning populated
buckets — an empty calibration cache means this feature will correctly, silently produce zero
picks forever until enough `signal_outcomes` accumulate.

---


## Feature Reference: T249-MARKETMOVER-P4 — Market Pulse Card (Built 2026-07-18)

**The last unbuilt slice of Tier 249's original ask** ("monitor the news or any information
that would make the market go up or down") — P0-P3 covered the structured, high-signal half
(CPI/FOMC/NFP/earnings releases and reactions); this is the deliberately lower-signal,
free-headline half, framed from the start as an honest MVP rather than a real-time
breaking-news engine (that would need a paid data source — Benzinga/Polygon news tier — and
remains an explicit non-goal here).

**New endpoint**: `GET /stocks/market/pulse` (`services/market-data/src/api/news.py`), reusing
the existing per-symbol news pipeline's exact building blocks rather than a new one: three
market-level `_google_news()` queries (`"stock market"`, `"S&P 500"`, `"Federal Reserve"`),
merged/deduped via the existing `_merge()`, top ~10 headlines piped through a new
`_claude_market_themes()` — same Haiku-call shape as `_claude_sentiment()` (same model, same
fail-open contract) but additionally asks for up to 3 recurring themes, since a market-level
digest needs more than a bare score to be useful. Falls back to a plain VADER average with no
themes if Claude is unavailable or fails. Cached 30 min in Redis
(`stockai:market_pulse`), matching the per-stock news cache's own TTL.

**Deliberately NOT wired into any alert/notification path** — 30-minute cadence and unranked
headlines are too noisy to page someone about; this is a passive dashboard card only, rendered
as `MarketPulseCard` on `intelligence.tsx`'s Overview tab (above the existing Latest Macro
Reaction card).

**Test environment gap found and fixed**: `feedparser` and `vaderSentiment` are both real,
pinned `services/market-data/requirements.txt` dependencies that `news.py` imports at module
level, but neither was installed in this local dev environment nor stubbed by `conftest.py` —
attempting to import `news.py` for testing raised `ModuleNotFoundError` on both in turn. Fixed
by a local `pip install feedparser==6.0.11 vaderSentiment==3.3.2` (matching the exact pinned
versions) rather than adding them to conftest's stub list — same class of gap already
documented for `jose`/`requests_oauthlib`/`redis` elsewhere in this file, and the same
resolution: prefer running tests against the real library over stubbing it, so `_google_news()`
RSS parsing and the VADER fallback path are exercised for real, not mocked.

**A real bug found live, right after first deploy**: production returned `source: "vader"` with
empty `themes` even though the user had already set a Claude API key on the admin Settings
page. Root cause: `news.py`'s `_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")` reads a
plain environment variable set once at import time — but nothing in this app ever writes
`ANTHROPIC_API_KEY` into a container's env. The Settings page instead writes to
`stockai:admin:claude_api_key` in Redis, the SAME key `llm_scorer.py`/`risk_agent.py` already
read via their own `_get_api_key()`/`_get_claude_key()` helpers — `news.py`'s per-symbol
`_claude_sentiment()` had this identical gap the whole time, just never noticed because the
per-symbol sentiment endpoint's VADER fallback is unremarkable-looking either way. Fixed by
adding `news._get_claude_key()` (Redis-first via `_get_redis().get(_REDIS_CLAUDE_KEY)`, falling
back to the env var only if Redis has nothing or errors), matching `llm_scorer.py`'s exact
established pattern, and switching every `_ANTHROPIC_KEY` read site (`_claude_sentiment()`,
`_claude_market_themes()`, `get_news_sentiment()`) to call it instead of reading the module-level
constant directly.

**A real test-writing gotcha hit while wiring this up**: the existing tests patched
`news._ANTHROPIC_KEY` directly to simulate "no key configured" — but once the code called
`_get_claude_key()`, which itself calls `_get_redis()` first, the conftest-stubbed `MagicMock`
Redis client returned a truthy `MagicMock` from `.get(...).strip()`, silently defeating the "no
key" test case (it kept passing, but for the wrong reason — the code proceeded past the
guard into a stubbed `httpx.Client()` call that itself degraded to `None` via the non-200 path,
not via the intended early-return). Caught by adversarially disabling the real guard
(`if not api_key or not titles` → `if False or not titles`) and finding the test still passed —
the same "still passes after sabotage" red flag already documented for the correlation
self-exclusion finding elsewhere in this file. Fixed by replacing all `patch.object(news,
"_ANTHROPIC_KEY", ...)` call sites with `patch.object(news, "_get_claude_key", return_value=...)`
and adding an explicit `mock_client.assert_not_called()` to the no-key test so a regression here
fails on the right assertion instead of coincidentally landing on the same return value via a
different code path. Re-verified: the same sabotage now correctly fails this test.

**A second real bug found live, right after the first fix deployed**: with the Redis key now
correctly found, the endpoint STILL returned `source: "vader"` — live-calling
`_claude_market_themes()` directly against the real Anthropic API in the production container
showed the HTTP call itself succeeded (`200 OK`) but `json.loads(text)` raised `Expecting value:
line 1 column 1 (char 0)`, silently swallowed by the function's own `except Exception` fail-open
contract. Root cause: Claude sometimes wraps its JSON response in `` ```json ... ``` `` markdown
fences despite the system prompt explicitly saying not to — `risk_agent.py` already strips this
via `re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL)` before its own `json.loads()`,
but `news.py`'s two Claude call sites (`_claude_sentiment()`, the pre-existing per-symbol
endpoint, and the new `_claude_market_themes()`) never had this stripping — the per-symbol
endpoint's silent VADER fallback made this identical, pre-existing gap invisible until Market
Pulse's live verification actually inspected the real failure reason instead of just checking
`source: "vader"` and assuming "no key configured" was still the cause. Fixed by adding a shared
`_strip_markdown_fence()` helper (matching `risk_agent.py`'s regex exactly) and applying it at
both `json.loads(text)` call sites in `news.py`. Adversarially verified by reverting one call
site to its unstripped form and confirming the new
`test_claude_market_themes_strips_markdown_fence_before_parsing` test correctly failed
(`result is None` instead of a populated dict) before restoring it.

**Lesson reinforced**: after a fix ships, "check that it returns 200 / doesn't error" is not the
same verification bar as "check that it returns the CORRECT thing for the CORRECT reason" — the
first live check here only confirmed `source: "vader"` was still showing, which could have
several different causes, and assuming it was still the already-diagnosed Redis-key issue would
have been wrong. Calling the actual failing function directly and reading its real exception
(rather than its swallowed, logged-only failure) found the true, different root cause in under a
minute.

**Tests**: `services/market-data/tests/test_market_pulse.py`, 19 cases — Claude-available vs.
VADER-fallback scoring paths, neutral-with-no-headlines, confirming all three market-level
queries are actually issued, Redis cache write + warm-cache read (no re-fetch when cache is
warm), themes capped at 3, `_claude_market_themes()`'s own fail-open cases (missing API key —
now asserting the HTTP client is never constructed, not just that the result is `None` — non-200
response, malformed JSON), and 4 new cases for `_get_claude_key()` itself (Redis value preferred
over the env var, env-var fallback when Redis is empty, env-var fallback on a Redis connection
error, whitespace-only Redis value treated as absent). Adversarially verified four guards by
sabotage, confirmed each caught the induced failure, then reverted: removing the `[:3]` themes
cap (test caught 5 themes surviving instead of 3); disabling the warm-cache early-return in
`get_market_pulse()` (test caught 3 live re-fetch calls instead of the expected 0); appending a
4th entry to `_PULSE_QUERIES` (test caught the extra query appearing, confirming the test reads
the real module-level constant rather than a hardcoded duplicate that could silently drift from
it — the exact failure mode documented in the T258-TRADE-POSTMORTEM entry above); and the
Redis-priority order in `_get_claude_key()` (test caught the env-var value winning instead of
the Redis value); and the markdown-fence stripping (test caught a `None` result instead of a
populated dict when a call site's stripping was reverted). Full 313-test market-data suite and
frontend typecheck green.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 python3 -c 'from jose import jwt' 2>/dev/null  # sanity: jose still present
docker exec stockai-redis-1 redis-cli get stockai:market_pulse
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'lauwing2','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/stocks/market/pulse', headers={'Authorization': f'Bearer {tok}'}, timeout=20)
print(r.status_code, r.json())
"
```

---


## Feature Reference: AUD250-MACRO-CALENDAR-FALLBACK-GRANULARITY — Per-Month Fallback Tracking (Built 2026-07-20)

**The gap**: `_macro_events_from_db()` (`services/market-data/src/api/routes.py`) reads real
FRED release-date rows and tells `events_calendar()`'s fallback loop which hardcoded
`_MACRO_2026` entries are now redundant. This tracking was a flat `types_with_db_rows:
set[str]` — if the DB had even ONE row for a type (e.g. `"cpi"`) anywhere in the requested
window, EVERY hardcoded `_MACRO_2026` entry for that type was skipped across the ENTIRE
window, including months `sync_fred_release_dates()`'s 180-day sync horizon never actually
reached. `GET /stocks/events/calendar?days_ahead=365` is a valid, allowed request (the route's
own `Query(90, ..., le=365)` permits it) — a caller requesting the far end of that range could
have a real near-term DB row silently suppress fallback coverage for months 181-365 that the
DB genuinely has no data for, dropping a real CPI/NFP/PCE/GDP release from the calendar with no
error anywhere. Flagged but deliberately deferred during the original AUD250 audit pass
(2026-07-16) given the frontend only ever requests the 90-day default in practice — this was
the follow-up.

**Fix**: `_macro_events_from_db()` now returns `covered_type_months: set[tuple[str, int,
int]]` — `(macro_type, year, month)` — built from each DB row's own `event_date`, instead of a
bare type-level set. `events_calendar()`'s fallback loop checks `(ev["type"], ev_date.year,
ev_date.month) in covered_type_months` instead of `ev["type"] in types_with_db_rows`. A real
July CPI row now only suppresses the July `_MACRO_2026` entry — August, September, etc. still
correctly fall back to the hardcoded calendar if the DB has no row for them yet.

**Other caller unaffected**: `scheduler.py`'s `send_premarket_brief()` also calls
`_macro_events_from_db()` (`macro_events, _ = _macro_events_from_db(session, today, today)`)
but discards the second return value entirely — confirmed via its own 16-test suite
(`test_premarket_brief.py`) staying green with no changes needed.

**Tests**: 2 new cases added to `services/market-data/tests/test_macro_events_from_db.py`
(now 6 total) — one directly reproducing the bug scenario (a DB row for one month must leave
other months of the same type uncovered), one confirming multiple distinct covered months
accumulate correctly in the set. Adversarially verified by collapsing the `(year, month)`
components to a constant `(0, 0)` in the fix — reproducing the exact original per-type bug —
and confirming the 2 new tests plus the pre-existing type-mapping test all failed correctly
before reverting. Full 318-test market-data suite (up from 316) and frontend typecheck green.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "covered_type_months" /app/src/api/routes.py
```
Should show the tuple-based tracking in both `_macro_events_from_db()` and
`events_calendar()`'s fallback loop. If a macro event still looks like it's silently missing
from the calendar, check `sync_fred_release_dates()`'s actual sync coverage directly against
production Postgres (`SELECT event_type, MIN(event_date), MAX(event_date) FROM
economic_events WHERE event_type LIKE '%_release' GROUP BY event_type;`) — a gap could now be
a genuine sync-coverage gap rather than this fallback-suppression bug, which this fix closes.

---


## Feature Reference: AUD256 — "Top Buys" Leaderboards No Longer Show Net Sellers (Built 2026-07-20)

**The gap**: `services/event-intelligence/src/services/insider.py`'s `get_insider_leaderboard()`
and `congress.py`'s `get_congress_leaderboard()` sorted every stock with any activity in the
window by `net_value`/`net_amount` descending, with no floor at zero. Both are named and
consumed everywhere as "Top Buys" leaderboards — `GET /events/insider/leaderboard` /
`GET /events/congress/leaderboard`, `reports.tsx`'s "Insider Top Buys"/"Congress Top Buys"
cards, `intelligence.tsx`'s Overview tab — but a stock with heavy net SELLING (a negative
`net_value`/`net_amount`) could still appear under a "Top Buys" heading whenever fewer than
`limit` stocks had genuinely positive net buying in the requested window.

**Fix**: extracted the aggregation logic into two new pure functions,
`_build_insider_leaderboard()` / `_build_congress_leaderboard()` — taking already-fetched row
dicts, no DB dependency — each now filtering to `net_value > 0` / `net_amount > 0` **before**
truncating to `limit`. A window with fewer than `limit` genuine buyers now correctly returns
fewer rows instead of padding out the list with net sellers. The original DB-querying functions
are now thin wrappers: fetch rows via the same query/joins as before, convert to plain dicts,
delegate to the pure function. Nothing else about the query changed.

**Why extract to pure functions instead of just adding an inline filter**: this service's
`conftest.py` stubs `sqlalchemy` itself as a bare `MagicMock` — heavier than ranking-engine's
stubbing (which allows a real in-memory SQLite session in tests, see
`test_rank_symbol_market_scoping.py`). Here, only pure logic with zero DB dependency can be
exercised directly in this test environment, so the fix needed the aggregation logic separated
from the DB I/O to be testable at all.

**Tests**: `services/event-intelligence/tests/test_insider_leaderboard.py` (8 cases) and
`test_congress_leaderboard.py` (8 cases) — a net-negative stock is excluded even when it would
otherwise fill out the list, a window with fewer genuine buyers than `limit` returns fewer
rows (not padded), exactly-zero net value/amount is also excluded (strict `> 0`, not `>= 0`),
genuine buyers are still sorted correctly, `limit` still applies after filtering,
purchases/sales/`unique_politicians` counts on surviving rows are unaffected by the new filter,
and `None` amounts are treated as zero without crashing.

**Adversarial verification**: sabotaged both filters (replacing each list comprehension with
an unfiltered `list(result.values())`) and confirmed exactly 4 of 8 tests in each file failed
correctly before reverting.

Full 159-test event-intelligence suite (up from 143) green; frontend typecheck clean — no
frontend files needed changes, since `reports.tsx`/`intelligence.tsx` already just render
whatever the backend returns.

**What to check if this looks wrong**:
```bash
docker exec stockai-event-intelligence-1 grep -n "net_buyers = \[v for v" /app/src/services/insider.py /app/src/services/congress.py
```
Both should show the `net_value > 0` / `net_amount > 0` filter. If a "Top Buys" card still
shows what looks like a net seller, check the actual returned `net_value`/`net_amount` directly
against a live call to `GET /events/insider/leaderboard` or `GET /events/congress/leaderboard`.

---


## Feature Reference: AUD256 — Pre-Market Brief Send-Loop Dedup + Per-Recipient Error Isolation (Built 2026-07-20)

**The gap**: `send_premarket_brief()`'s (`services/market-data/src/services/scheduler.py`)
per-recipient send loop had two related reliability gaps, both flagged but deliberately
deferred during the 2026-07-17 AUD256 deep audit. (1) No dedup — the job is registered with
`misfire_grace_time=60`; a restart within that window could re-fire the same day's brief and
re-email every recipient a second time. (2) No per-recipient error isolation — a single
recipient's `send_premarket_brief_email()` raising (a malformed address, a transient SMTP
error) would propagate to the function's one shared outer `except Exception`, aborting the
whole batch and silently skipping every recipient still left in the loop.

**Fix**:
1. **Dedup**: a Redis key `stockai:premarket_brief:{uid}:{market_key}:{date}` (20h TTL — one
   brief per user per market per day), checked before the send and set only inside the `if ok:`
   branch after a genuinely successful send. Mirrors the existing per-(user, symbol, date)
   dedup shape `check_earnings_reactions()` already uses elsewhere in the same file.
2. **Isolation**: the `send_premarket_brief_email()` call is now wrapped in its own try/except
   (not the whole loop iteration) — a failure logs `premarket_brief.recipient_send_error` and
   increments a new `errors` counter instead of re-raising. The `premarket_brief.done` log line
   now reports both `sent` and `errors`, so a partial-failure batch is visible in logs instead
   of looking identical to a fully clean run.

**Also noted, not fixed this pass**: `send_morning_digest()` (same file) has the **identical**
unguarded send-loop pattern (no per-user try/except, no dedup) — out of scope since this task
was specifically the pre-market brief, but flagged as a real, same-class follow-up candidate.

**Tests**: 4 new cases added to `services/market-data/tests/test_premarket_brief.py` (now 20
total), using that file's own established source-text-extraction technique for
`send_premarket_brief()` itself (`scheduler.py` can't be imported directly in this test
environment — its import chain pulls in `apscheduler`) — the dedup check happens before the
send call, the dedup key is set only after a successful send (not unconditionally, not
before the send), the send call is wrapped in its own try/except distinct from the outer
function-level except, and the per-recipient error is logged/counted without re-raising.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. Removing the dedup check entirely.
2. Setting the dedup key unconditionally instead of gated on a successful send.
3. Removing the per-recipient try/except so a send exception would propagate unguarded.

Full 327-test market-data suite (up from 323) green; frontend typecheck clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n 'stockai:premarket_brief:' /app/src/services/scheduler.py
docker exec stockai-redis-1 redis-cli keys 'stockai:premarket_brief:*'
```
If a user reports getting the brief twice on the same day, check whether the job actually
fired twice (`docker logs stockai-market-data-1 --since 24h | grep premarket_brief`) — the
dedup key should have prevented a second send within its 20h TTL, so a duplicate despite this
fix would point to a genuinely new failure mode, not a regression of this one.

---


## Feature Reference: AUD-EARNINGS-DIGEST — Consolidated Earnings Reminder Email (Built 2026-07-22)

**User report**: a real inbox screenshot showed 8+ separate "⏰ Earnings in Xd: SYMBOL" emails
in a row — one per watched stock reporting that week, each its own send. Asked to consolidate
into one email with a table including current prices and other context.

**Root cause**: `check_signal_alerts()`'s `T230-ALERTING-EARNINGS-PROXIMITY` block looped over
every `(user, symbol)` pair with an upcoming print and called `send_email()` once per symbol —
correct dedup (one reminder per symbol per `days_to_earnings` milestone, 20h TTL) but no
batching across symbols for the same user in the same cycle.

**Fix**: new `send_earnings_reminder_digest_email(to, rows)` in `email_service.py` — one HTML
`<table>` per user (not stacked cards, per the user's explicit request), columns: Symbol,
Reports (Xd), Price (with `change_pct`), Est. EPS, Beat Rate (X/8 + avg surprise), K-Score.
Rows sorted soonest-first. `check_signal_alerts()` now collects every qualifying row for a
user into a `digest_rows` list (same per-`(user, symbol, days_to_earnings)` dedup key,
unchanged 20h TTL — the dedup granularity is untouched, only delivery is batched) and sends
ONE `send_earnings_reminder_digest_email()` call at the end of that user's loop, instead of a
`send_email()` inside it.

**Data sources**: `fundamentals_cache[sym]` (`forward_eps`, `eps_beat_rate`,
`eps_avg_surprise_pct`) and `kscores[sym]` were already fetched earlier in the same function
for the signal-alert scoring pass — reused directly, no new fetch. Current price + `change_pct`
are new to this code path: read from `stockai:live_prices` (the same Redis cache
`check_volume_anomalies()`/`check_value_area_breakdown()` already read this session), computed
the same way those functions do (`(price - prev_close) / prev_close * 100`).

**Old `_earnings_reminder_body()` deleted** — its only caller was the per-symbol send this fix
removed, and its sentence-formatting logic doesn't fit a table cell. Its 4 unit tests in
`test_earnings_alert_bodies.py` were removed along with it; the wiring test was rewritten to
confirm `check_signal_alerts()` now calls `send_earnings_reminder_digest_email()` with a
`digest_rows` list instead of the old per-symbol call, and a new test confirms the dedup-key
granularity is unchanged.

**Tests**: `services/market-data/tests/test_earnings_reminder_digest.py` (14 cases) — multiple
symbols land in one send (the core consolidation guarantee), subject reflects count,
soonest-earnings-first sort, price/change_pct/beat-rate/K-Score rendering including
missing-field placeholders, negative change_pct sign handling. Adversarially verified by
disabling the sort and confirming the ordering test failed correctly before restoring.

**Verification**: full market-data suite (387 tests) green.

**What to check if this looks wrong**:
```bash
docker logs stockai-market-data-1 --since 24h | grep 'earnings_reminder_digest_sent'
# Should show one log line per user per cycle with a `symbols=[...]` list, not one line per
# symbol. If a user reports still getting multiple separate earnings emails, confirm this log
# line's symbols list actually contains all of them together — if it doesn't, check whether
# stockai:earnings_remind:{uid}:{sym}:{dte_int} dedup keys are firing at different cycles for
# different symbols (expected — a symbol due tomorrow and one due in 5 days will land in
# different weekly cycles unless both cross a reminder threshold on the same run).
```

---


## Feature Reference: T257-OVERNIGHT-FLOW-BRIEF Phase 1 — Overnight Futures Section on the Pre-Market Brief (Built 2026-07-22)

**The gap this closes**: per the tracker's own Tier 257 design ("overnight options-flow +
futures-flow analysis to read whether the market opens high/low and lay out the day"), this
session scoped down to just Phase 1's futures half — confirmed via grep that **zero** futures
data (`ES=F`/`NQ=F`/`YM=F`/`RTY=F`) existed anywhere in the codebase before this. Phase 1's
OTHER half (premarket gappers "from already-ingested PRE-session bars") was investigated and
found **not buildable as scoped**: no scheduled job currently ingests intraday bars during the
4:00–9:30 ET premarket window (`us_5m_intraday`/`hk_5m_intraday` are both cron-gated to regular
market hours only, scheduler.py ~5344-5363) — `Price.session == "PRE"` rows for the whole
universe are effectively empty in production today. Rather than build a feature reading from
data that doesn't exist, this session shipped ONLY the futures half (fully self-contained, no
new ingest dependency) and documented the gappers gap honestly as a real prerequisite for a
future session, instead of silently building on top of an empty data source.

**New function**: `_fetch_overnight_futures()` (`services/market-data/src/services/
scheduler.py`) — one bulk `yf.download(["ES=F","NQ=F","YM=F","RTY=F"], period="5d",
interval="1d", ...)` call, matching this file's own house rule ("All ingests use
yf.download(symbols_list) — one batch call") and `_fetch_live_bulk()`'s exact multi-ticker
column-shape handling (`raw[symbol]["Close"]` vs `raw["Close"]`, branching on
`len(symbols) > 1`). Uses `period="5d"` (not `_fetch_live_bulk`'s `"2d"`) specifically so a
thin/holiday-adjacent session doesn't leave fewer than 2 valid daily closes to diff. Redis-
cached 60s (`stockai:overnight_futures`, matching `market_overview()`'s own short-TTL
convention) since the US and HK brief jobs could both call this in the same minute window.

**Framing, matching this repo's established alert-honesty discipline** (T249-P3, T257-VOLUME-
ANOMALY-ALERT, T257-TOP3-CONVICTION-ALERT): reports a MEASURED overnight change — "ES +0.8%
overnight" is literally what futures prices mean (the market's own current expectation for the
open) — never a prediction of whether that holds through the cash session. Both the HTML
footer and the plain-text footer of the brief email were updated to state this distinction
explicitly, separate from the brief's pre-existing "historical-scenario context, not a
prediction" disclaimer for its other 3 sections.

**Wiring**: `send_premarket_brief()` gates the fetch to `if "US" in markets:` (same reasoning
as sections 1/3 — no HK futures data source exists), passes `overnight_futures` into
`send_premarket_brief_email()`, includes `futures=len(overnight_futures)` in the `.done` log
line, and includes `not overnight_futures` in the early-return "nothing to report" guard so a
morning with real futures movement but no macro/earnings/reaction content still sends.
`send_premarket_brief_email()` gained an `overnight_futures: list[dict] | None = None`
parameter (defaults to `None`→treated as `[]`, so no existing caller needed to change) and a
4th section ("Overnight Futures") rendered via the SAME `_section()` helper every other section
already uses — green/red color-coded change_pct, `None`-safe em-dash fallback for price/change,
explicit empty-state note ("Overnight futures data unavailable this morning.") matching the
file's own established "every section needs an explicit empty state" convention.

**Also fixed in passing**: a duplicated 5-line comment block (an accidental copy-paste,
unrelated to this feature) in `send_premarket_brief()`'s per-recipient try/except, found while
editing this exact function.

**Tests**: `services/market-data/tests/test_overnight_futures_brief.py` (13 cases) —
`_fetch_overnight_futures()`'s real source is extracted via `exec()` (matching
`test_backfill_realized_ev.py`'s/`test_tune_strategy.py`'s established technique, since
`scheduler.py` can't be imported directly in this test environment) with a fake `yfinance`
module injected via `sys.modules` patching and a fake `_get_redis` injected directly into the
exec()'d function's own `__globals__` — covers change_pct computation, warm-cache short-
circuit (no `yf.download()` call at all on a cache hit), cache-write after a real fetch, a
ticker with fewer than 2 valid closes being silently skipped (not crashing or fabricating a
value), and a download failure degrading to `[]`. `send_premarket_brief_email()`'s new section
is tested directly (pure composition) — rendering, explicit empty state, `None`-parameter
backward compatibility, red/green color coding, and `None`-safe em-dash fallback. 4 source-text
regression checks confirm the scheduler wiring (US-only gate, `overnight_futures` reaching both
the email call and the `.done` log line, and inclusion in the nothing-to-report guard).

**Two real test-writing bugs caught and fixed before shipping** (both self-caught, not shipped
with false confidence):
1. `test_missing_change_pct_renders_em_dash_not_none_or_crash`'s first version asserted
   `"None" not in html` against the WHOLE rendered page — failed not because of a real bug, but
   because the pre-existing "Your Symbols Reporting Today" section's own empty-state note
   ("None of your watched symbols report earnings today.") legitimately contains the substring
   "None". Fixed by scoping the assertion to just the futures row's own HTML slice.
2. `test_premarket_brief_gates_futures_fetch_to_us_only`'s first version anchored on
   `body.index("_fetch_overnight_futures()")` — but the function's own docstring mentions
   `_fetch_overnight_futures()` in prose BEFORE the real call site, so `.index()` (first match)
   found the docstring text, not the actual gated call, making the test's `rindex` search
   accidentally correct for the wrong reason on the first pass. Fixed by anchoring on the more
   specific assignment form `"overnight_futures = _fetch_overnight_futures()"`, which only
   appears once, at the real call site.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: removing the
`if "US" in markets:` gate around the fetch call (caught — the test correctly found 12 lines of
unrelated code between the PRECEDING section's gate and the now-unconditional fetch call,
failing the `<= 2` proximity check); removing `overnight_futures` from the nothing-to-report
guard (caught directly). Full 432-test market-data suite (up from 419) green.

**Not built (Phase 1's other half, deliberately deferred, not silently dropped)**: premarket
gappers from PRE-session bars — needs a new premarket-hours intraday ingest job first (extending
`us_5m_intraday`'s cron window to start before 9:30 ET, or a dedicated new job), since the
underlying data genuinely doesn't exist yet. Phase 2 (options-flow snapshot persistence + EOD
job) and Phase 3 (the "day attention-list" combining premarket gap + options flow + earnings +
macro) remain unbuilt, per the original design's own phasing.

**What to check if this looks wrong**:
```bash
docker exec stockai-redis-1 redis-cli get stockai:overnight_futures
# Manually trigger the brief to see the new section live (safe — respects the existing
# per-recipient dedup key, so re-running within 20h for the same user/market/day is a no-op):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from src.services.scheduler import _fetch_overnight_futures
print(_fetch_overnight_futures())
"
```
If `_fetch_overnight_futures()` returns `[]` outside a Redis-cache-hit scenario, check
`docker logs stockai-market-data-1 --since 1h | grep overnight_futures.download_failed` for the
underlying yfinance error.

---


## Feature Reference: T257-OVERNIGHT-FLOW-BRIEF — Premarket Gappers (Built 2026-07-23)

**Closes the other half of Phase 1** — the futures reading shipped earlier this session; the
"top premarket gappers" half was blocked because no scheduled job ingested intraday bars
during the 4:00–9:30 ET premarket window, so `Price.session == "PRE"` rows were effectively
empty for the whole universe. This session added the missing ingest job plus the gappers
query and email section.

**New ingest jobs**: `_refresh_premarket_5m()` (`services/market-data/src/services/
scheduler.py`), registered as two cron jobs (`us_premarket_5m_early`: hours 4-8, every 5 min;
`us_premarket_5m_9am`: hour 9, minutes 0-25 only — a SEPARATE registration specifically so it
stops at 9:25, handing off cleanly to `us_5m_intraday`'s own 9:30 start rather than double-
firing at 9:30). US-only — HK has no premarket session concept (`_classify_session()` in
`ingestion.py` returns `"REGULAR"` unconditionally for any non-US market).

**Deliberately does NOT reuse `_refresh_5m()` as-is**, even though the underlying
`ingest_universe(symbols, "5m")` call is identical: `_refresh_5m()` also unconditionally runs
`_run_paper_trading_step()` and `_check_short_intraday_triggers()` after every ingest — both
designed and tuned around regular-hours trading logic. Firing them on a new, untested
premarket cadence would be new, unreviewed behavior outside this feature's actual scope
(surfacing gappers in an email). `_refresh_premarket_5m()` does the ingest only.

**New query**: `_fetch_premarket_gappers(session)` — gap % = (today's latest PRE-session 5m
close) vs. (the prior trading day's REGULAR-session daily close), the same "gap from
yesterday's close" definition a trader means by "premarket gapper." Uses the same
`row_number()`-per-`stock_id` window-function pattern already established in `routes.py`'s
`_latest_prices_from_db()` — one query, no per-symbol Python loop. Ranked by `|change_pct|`
descending, capped at 10, Redis-cached 5 min (matching the premarket ingest job's own
cadence). Reads only already-persisted `Price` rows — no live yfinance call in this path,
matching this file's own established discipline (see `check_volume_anomalies()`'s docstring
for the same reasoning applied to a different feature).

**Wired into `send_premarket_brief()`** as a 5th section (`premarket_movers`), US-only-gated
like sections 1/3/4, folded into the existing nothing-to-report guard and `.done` log line.
`send_premarket_brief_email()` gained a `premarket_movers: list[dict] | None = None`
parameter (defaults to `None` → treated as `[]`, so no existing caller needed to change),
rendered via the same `_section()` helper and green/red change_pct color convention already
used for `overnight_futures`.

**Tests**: `services/market-data/tests/test_premarket_gappers.py` (19 cases) — `scheduler.py`
can't be imported directly in this test environment, so `_fetch_premarket_gappers()` is
extracted via `exec()` and run against a REAL in-memory SQLite session (established
`test_correlation_preentry.py`/`test_broker_position_sync.py` technique), covering gap-%
computation, ranking, the 10-item cap, US-only filtering, correct exclusion of stocks with no
PRE bar (not a fabricated 0% gap), and — the one genuinely tricky case — that a REGULAR-
session 5m bar is never mistaken for a PRE one just because it's the same timeframe. Plus
source-text regression checks confirming the cron registration's exact hour/minute windows,
that `_refresh_premarket_5m()`'s real CODE (not its own docstring, which legitimately mentions
both names in prose while explaining why they're skipped) never calls
`_run_paper_trading_step`/`_check_short_intraday_triggers`, and the `send_premarket_brief()`
wiring (US-only gate, `.done` log, nothing-to-report guard). Plus 4 email-composition tests
for the new section (render, empty-state, `None`-default backward compatibility, red/green
color coding) — extended `test_overnight_futures_brief.py`'s own extraction boundary (which
previously ran all the way to `send_premarket_brief`) to stop right after
`_fetch_overnight_futures` instead, since it would otherwise also pull in this new function's
`Session`-typed signature with no `Session` in that test's own exec namespace.

**A real test-writing mistake caught and fixed before shipping**: the first version of
`test_refresh_premarket_5m_does_not_call_paper_trading_step` checked the function's FULL
source text (including its own docstring) for the two forbidden call names — but the
docstring itself legitimately names both functions in prose while explaining why they're NOT
called, making the test fail against correct code. Fixed by slicing past the closing
docstring delimiter before checking for the two names, so only the real executable code is
scanned.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: removing the
`Stock.market == Market.US` filter (a real HK stock leaked into a US-only gappers list);
removing the `Price.session == "PRE"` filter (a REGULAR-session 5m bar was mistaken for a
premarket one). Full 486-test market-data suite (up from 467) green.

**What to check if this looks wrong**:
```bash
# Confirm the new ingest jobs actually ran and populated PRE rows:
docker logs stockai-market-data-1 --since 6h | grep premarket_5m_ingest
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*) FROM prices WHERE session='PRE' AND ts > now() - interval '1 day';"

# Manually trigger the gappers query directly against real data:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from db import SessionLocal
from src.services.scheduler import _fetch_premarket_gappers
with SessionLocal() as s:
    print(_fetch_premarket_gappers(s))
"
```
If the gappers list is always empty despite real premarket volatility, first confirm the
ingest job itself actually ran (`docker logs ... | grep premarket_5m_ingest_done`) before
assuming the query is broken — an empty PRE-session `Price` table (ingest job silently
failing, or running outside its own cron window) looks identical to "no gappers today" from
the query's own perspective.

---


## Feature Reference: Market Pulse Card Now Shows Its Own Underlying Headlines (Built 2026-07-29)

**User report**: on the Market Pulse card (`intelligence.tsx`'s Overview tab), "I don't see
much details about the market too" — the card showed only a sentiment score and 3 short theme
chips.

**Investigation finding (not a bug, not a Claude-fallback-degraded state)**: confirmed via
live production logs that every real Market Pulse generation this session was a genuine
`source: "claude"` success (no VADER fallback) — the 3-theme cap and bare score are exactly
what the backend has always returned for those two specific fields, working as designed. The
REAL gap: `GET /stocks/market/pulse`'s response (`MarketPulse` type, `frontend/src/lib/
api.ts:844`) has ALWAYS also included a full `headlines: NewsItem[]` array — up to ~10 real,
specific, per-source-attributed headlines (each with its own `title`/`url`/`source`/
`sentiment_label`) that the Claude call's own sentiment score and themes are actually derived
from. This field was fetched, typed, and delivered over the wire the entire time — the
frontend component simply never rendered it. The "not much detail" the user was seeing wasn't
a data gap at all — it was a rendering omission of data that already existed on every single
response.

**Fix**: `MarketPulseCard` now renders `pulse.headlines` using the EXISTING `NewsCard`
component (`frontend/src/components/NewsCard.tsx`) already used for per-symbol news
elsewhere in this app — no new component, no new API call, zero new Claude cost, since this
data was already being fetched every time. Capped to 4 visible headlines by default with a
"Show N more headlines" toggle (`showAllHeadlines` state) to keep the card's original compact-
dashboard framing intact rather than turning it into a long scrolling feed.

**Also answered directly, since it came up in the same investigation**: Market Pulse's real
trigger cadence is NOT a fixed schedule — `useSWR`'s `refreshInterval: 300_000` (5 min) only
controls how often the FRONTEND re-fetches while a page is open; the backend Redis-caches the
result for `_PULSE_TTL = 30 * 60` (30 min, `services/market-data/src/api/news.py:331`), so a
real Claude call only happens on a cache-miss — i.e., whenever someone actually visits a page
that reads this endpoint AND the prior cache entry has expired. Confirmed live: over a real
24h production window, exactly 4 real Claude calls fired (`news.market_pulse_claude` log
lines), each ~1-5 hours apart depending on page-visit timing, not a background cron running on
its own schedule.

**Verification**: `npx tsc --noEmit` clean, full 89-test frontend vitest suite unaffected
(pure presentational addition, no logic under test), a full `next build` clean, and confirmed
the compiled `intelligence-*.js` bundle contains the new "Show N more headlines" string —
proving the change reached what would actually ship, not just correct-looking in source.

**What to check if this looks wrong**:
```bash
# Confirm the backend is actually returning headlines (it always has):
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/market/pulse' | python3 -c "import sys, json; print(len(json.load(sys.stdin).get('headlines', [])))"
```

---


## Feature Reference: T257-OVERNIGHT-FLOW-BRIEF Phase 2 — Late-Day Options-Flow Snapshot + Brief Section (Built 2026-07-30)

**Closes the other half of Tier 257's original "see where the investors putting money now"
ask** — Phase 1 (2026-07-22/23) covered overnight futures + premarket gappers, but options
flow (`GET /{symbol}/options-flow`) was still live-only with a 15-minute Redis cache and zero
persistence, so nothing could ever answer "what did flow look like yesterday" for the brief.

**New module**: `services/market-data/src/services/options_flow_snapshot.py` —
`compute_options_flow(symbol)` independently re-derives the SAME aggregate the live endpoint
computes (`routes.py`'s `get_options_flow()`) directly from a fresh yfinance option-chain
fetch: cp_ratio (capped at 10.0), call/put volume, call/put premium (a field the live endpoint
does NOT already aggregate — it only tracks per-contract premium inside its own top-10
"unusual activity" list, never a running chain-wide total), whale detection (>$500K threshold,
matching the live endpoint exactly), and the same 5-tier sentiment ladder
(strongly_bullish/bullish/neutral/slightly_bearish/bearish, gated by `sufficient_put_vol >=
100` so a near-zero put volume never falsely reads as extreme bullish). **Deliberately NOT a
shared implementation with `get_options_flow()`** — a second, independent port, matching
`volume_area.py`'s own established "two independently-ported-not-shared math functions" caveat
for the same reason (the live endpoint is FastAPI-route-shaped and Redis-cached; this needs a
pure, DB-facing function). If either's math changes, check whether the other needs the same
change too.

**New table**: `OptionsFlowSnapshot` (`shared/db/models.py`) — `(stock_id, as_of)` unique, one
row per symbol per day. A brand-new table, `create_all()`-friendly — no manual `ALTER TABLE`
needed.

**Bounded symbol set, not the whole universe** — `_bounded_options_flow_symbols()`
(`scheduler.py`) unions PriceAlert-subscribed US symbols with the top-20-by-K-Score US stocks,
matching Phase 2's own original design note ("PriceAlert-subscribed + top-K by K-Score, NOT
the whole universe — yfinance option chains are the most rate-limited endpoint we touch").
Both queries correctly exclude delisted/inactive stocks
(`Stock.delisted.is_(False)`/`Stock.active.is_(True)`) — a fresh generation-path query added
in the same session `BUG-DELISTED-GENERATION-BLIND`'s own 10-instance sweep already covered,
so this one was built delisted-safe from the start rather than needing an 11th retrofit.

**EOD compute job**: `compute_options_flow_snapshots_eod()`, scheduled at 17:00 ET (after
US close at 16:00, well before the next day's 08:00 ET pre-market brief that reads these rows)
— per-symbol try/except isolation (one symbol's fetch failure doesn't abort the batch) plus a
fixed 2-second inter-symbol sleep (rate-limit discipline for yfinance's most fragile endpoint,
matching this file's own established convention for options-chain calls). Upserts via `ON
CONFLICT DO UPDATE` on `(stock_id, as_of)`, matching `volume_area.py`/`sector_trajectory.py`'s
established idempotent-upsert pattern for this exact class of dated-snapshot table.

**Read side**: `_fetch_recent_options_flow()` reads ONLY already-persisted `OptionsFlowSnapshot`
rows for the most recent `as_of` — no live yfinance call in this path at all, matching
`check_volume_anomalies()`'s/`_fetch_premarket_gappers()`'s own established discipline. Ranks
by `|cp_ratio - 1.0|` descending (most-extreme-sentiment-first) and caps at 10.

**Wired into `send_premarket_brief()`** as a 6th section, US-only-gated (no HK options-flow
coverage anywhere in this app — matches sections 1/3/4/5's own gating), folded into the
existing nothing-to-report guard and `.done` log line.
`send_premarket_brief_email()` gained an `options_flow: list[dict] | None = None` parameter
(defaults to `None` → treated as `[]`, so no existing caller needed to change), rendered via
the same `_section()` helper every prior section already uses — symbol/cp_ratio/sentiment,
plus an optional whale-trade note when `whale_count > 0`, `None`-safe em-dash fallbacks for a
missing cp_ratio, and `sentiment=None` degrading to `"neutral"` rather than crashing.

**Tests**: `services/market-data/tests/test_options_flow_snapshot.py` (11 cases) —
`compute_options_flow()` tested directly against a mocked `yfinance.Ticker`/`option_chain()`
returning real pandas DataFrames (no source-text extraction needed — this module has zero
Docker-only import at its top level besides `db`/`sqlalchemy`, both already stubbed by
`conftest.py`), covering no-options/zero-volume returning `None`, all 5 sentiment tiers,
cp_ratio capping at 10.0, the near-zero-put-volume-doesn't-falsely-declare-bullish guard,
call/put premium aggregation across the FULL chain (not just the top-10 "unusual" list),
whale detection at the exact $500K threshold, and per-expiry fetch-failure isolation.
`services/market-data/tests/test_options_flow_brief_wiring.py` (15 cases, source-text
regression checks matching `test_premarket_gappers.py`'s established pattern for functions in
`scheduler.py` that can't be imported directly in this test environment) — the bounded-symbol
query's delisted/inactive/US-only filters, the EOD job's registration/schedule/per-symbol
sleep/error-isolation, `send_premarket_brief()`'s US-only gate on the fetch call + inclusion in
both the email-call kwarg/`.done` log and the nothing-to-report guard, and
`_fetch_recent_options_flow()`'s no-live-yfinance-call discipline. Plus 5 direct
`send_premarket_brief_email()` composition tests for the new section (render, explicit empty
state, `None`-default backward compatibility, missing-whale-count/missing-cp_ratio/
missing-sentiment graceful degradation).

**Adversarial verification** — 5 sabotage cycles, all caught and reverted: removing
`Stock.delisted.is_(False)` from the alert-symbol lookup (caught by the delisted/inactive
exclusion test, `1 == 2` count mismatch); removing the US-only market filter from the top-K
K-Score query (caught by the US-only test); removing the `if "US" in markets:` gate around the
fetch call in `send_premarket_brief()` (caught by the gate-proximity test — the fetch found
the PRECEDING section's gate 7 lines away instead of its own, 2 lines); removing
`recent_options_flow` from the nothing-to-report guard (caught directly); and — the one real
runtime-crash catch — removing the `None`-safety guards on `cp_str`/`sentiment` in
`email_service.py`'s row builder (caught with a genuine `TypeError: unsupported format string
passed to NoneType.__format__`, confirming the test exercises a real crash path, not just a
value mismatch).

Full 656-test market-data suite green after every revert (up from 630); `pyflakes` clean on
all 3 touched/new files (confirmed via `git stash` that all pre-existing warnings in
`scheduler.py`/`email_service.py` predate this change — only line numbers shifted, plus one
new harmless `f-string is missing placeholders` warning matching 4 pre-existing sibling
occurrences of the exact same style in the same function).

**Not built this pass, matching Phase 1's own explicit phasing**: Phase 3 (the "day
attention-list" combining premarket gap + options flow + earnings + macro into one
scored-attention section) remains unbuilt — this Phase 2 delivers the raw options-flow data
Phase 3 would need as one of its inputs, but the cross-referencing/scoring logic itself is a
separate, larger piece of work.

**What to check if this looks wrong**:
```bash
# Confirm the new table exists and has real rows after the next 17:00 ET run:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT st.symbol, ofs.cp_ratio, ofs.sentiment, ofs.as_of FROM options_flow_snapshots ofs JOIN stocks st ON ofs.stock_id = st.id ORDER BY ofs.as_of DESC LIMIT 10;"

# Check job status/logs directly:
docker logs stockai-market-data-1 --since 24h | grep 'options_flow_eod'

# Manually trigger the EOD compute job (safe — idempotent upsert, real yfinance calls with a
# 2s inter-symbol sleep, so this can take a few minutes for a real bounded set):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from src.services.scheduler import compute_options_flow_snapshots_eod
compute_options_flow_snapshots_eod()
"
```
If the brief's new section always shows the empty state despite real recent flow existing,
first confirm the EOD job actually ran and populated a row for TODAY's `as_of` —
`_fetch_recent_options_flow()` never computes live, it only reads whatever the daily job
already persisted for the most recent date.

---


## Feature Reference: T257-OVERNIGHT-FLOW-BRIEF Phase 3 — Per-Recipient "Day Layout" Attention List (Built 2026-07-30)

**Closes the final phase of Tier 257's original ask** — Phase 1 (futures + premarket gappers)
and Phase 2 (late-day options-flow persistence) both shipped earlier; Phase 3 was always
scoped as "combine the 4 already-computed signals into one scored attention list, never a new
buy/sell direction call of its own" (`.claude/CLAUDE.md`, Research: Tier 257, section 3, Phase
3 — "an attention list, not a plan-of-trades").

**New pure function**: `_build_attention_list()` (`services/market-data/src/services/
scheduler.py`) — for each of a recipient's own watched symbols, checks up to 4 independent,
already-computed signals and requires **≥2 hits** before the symbol qualifies:
1. **Premarket gap ≥2.0%** (`_ATTENTION_GAP_THRESHOLD_PCT`) vs. yesterday's close, from the
   SAME `premarket_movers` list `send_premarket_brief()` already computes for its own Section 5
   — no new query. 2.0% was chosen as a real, non-noise premarket move; the underlying
   `_fetch_premarket_gappers()` itself has no floor (just top-10-by-magnitude), so this is the
   first place a genuine significance threshold applies to that data.
2. **Notable options-flow sentiment** — `strongly_bullish`/`bullish`/`bearish`
   (`_ATTENTION_NOTABLE_SENTIMENTS`, deliberately excluding `neutral`/`slightly_bearish` as too
   weak to flag on their own) OR a real whale trade (`whale_count > 0`) regardless of
   sentiment tier — from the SAME `recent_options_flow` list Section 6 (Phase 2) already reads.
3. **Reports earnings today** — from the SAME `earnings_by_symbol` dict Section 2 already
   builds.
4. **A high/critical macro release is scheduled today** — a single MARKET-WIDE bool
   (`macro_has_high_impact = bool(macro_today)`, computed ONCE before the per-recipient send
   loop, not per-symbol) from the SAME `macro_today` list Section 1 already computes — matching
   the design's own framing of "macro release today" as a shared, market-wide fact rather than
   something scoped to one stock.

Each qualifying symbol carries its own list of human-readable reason strings (the specific
gap %, the specific sentiment tier, etc.) — deliberately NEVER a synthesized buy/sell verdict
of its own, exactly matching the design's explicit rejection of "duplicating direction calls
here with no outcome tracking" — that's what the signal pipeline and the T257-TOP3 conviction
alert are for, both of which have real tracked accuracy this ad-hoc list does not.

**Computed per-recipient, inside the send loop** — unlike every other section in this
function (all market-wide, computed once before the loop), the attention list is genuinely
per-USER since it only scores each recipient's OWN watched symbols
(`user_symbols.get(uid, set())`), matching how `my_earnings` (Section 2) is already
per-recipient-filtered the same way, just one level further (an actual scoring pass, not only
a filter).

**Wiring**: `send_premarket_brief_email()` gained an `attention_list: list[dict] | None = None`
parameter (defaults to `None` → treated as `[]`, so no existing caller needed to change),
rendered as a new "Today's Attention List" section (symbol + a bulleted `<ul>` of its own
reasons) via the same `_section()` helper every prior section uses, placed right after "Late-
Day Options Flow" (its natural position matching the code's own numbered-section ordering).
The `.done` log gained a running `attention_symbols_total` counter (summed across every
recipient in the loop) — the ONE section in this function whose count can't be reported as a
single market-wide number the way the others are, since it's inherently per-recipient.

**Deliberately NOT added to the "nothing to report" early-return guard** — a symbol can only
ever qualify when at least 2 of the 4 ALREADY-GUARDED inputs (macro/earnings/reactions/
futures/movers/options_flow) are non-empty, so the existing guard already transitively covers
the attention list; adding it there too would be a redundant, always-true clause, not a real
protection — a dedicated test documents this choice explicitly so a future "fix" doesn't
mistakenly add it back in.

**Tests**: `services/market-data/tests/test_attention_list.py` (23 cases) —
`_build_attention_list()`'s real source is extracted via `exec()` (matching this file's
established source-text-extraction technique for pure functions in `scheduler.py`, which can't
be imported directly in this test environment) and exercised with real dict/set/list inputs,
no mocking needed since the function has zero DB/network dependency: the ≥2-signal threshold
(a single signal never qualifies, exactly 2 does), the gap-threshold boundary (0.5% doesn't
count, a negative gap beyond threshold correctly counts via `abs()`), the sentiment-tier
boundary (`neutral`/`slightly_bearish` don't count without a whale, `strongly_bullish` does,
and a whale trade counts even at `neutral` sentiment), the market-wide macro flag applying
identically to every scored symbol but never qualifying one alone, alphabetical result
ordering, and symbols outside every input never appearing. Plus 5 source-text regression
checks for the `scheduler.py` wiring (the per-recipient call site, the once-before-the-loop
macro flag, the email-call kwarg, the `.done` log counter, and the deliberate absence from the
nothing-to-report guard) and 4 direct `send_premarket_brief_email()` composition tests (render,
explicit empty state, `None`-default backward compatibility, multi-reason rendering).

**A real test-writing mistake caught and fixed before shipping** (the same class already
documented for `test_overnight_futures_brief.py`/`test_premarket_gappers.py` earlier in this
tracker item's own history): the first version of the per-recipient-call-site test anchored on
`body.index("_build_attention_list(")`, which matched the function's own DOCSTRING mention
(where it's named in prose, describing Section 7) before the real call site — making the test
pass or fail for the wrong reason depending on relative ordering. Fixed by anchoring on the
more specific assignment form `"attention_list = _build_attention_list("`, which only appears
once, at the real call site — this is now the third time this exact `.index()`-finds-the-
docstring-first trap has been hit and fixed in this same tracker item's history, worth
remembering as a standing gotcha whenever a new function's own docstring happens to name it.

**Adversarial verification** — 4 sabotage cycles, all caught and reverted: loosening the `>=2`
threshold to `>=1` (5 of 23 tests failed correctly, each with a real assertion diff showing an
under-qualified symbol slipping through); removing the gap-threshold check entirely (caught
directly, a real 0.5% gap wrongly qualifying); widening `_ATTENTION_NOTABLE_SENTIMENTS` to
include `neutral`/`slightly_bearish` (both dedicated boundary tests caught it); severing the
email-call wiring (`attention_list=[]` hardcoded instead of the real computed value) — caught
by the dedicated wiring test. Full 679-test market-data suite (up from 656) green after every
revert; `pyflakes` clean on both touched files (confirmed via `git stash` that all pre-existing
warnings predate this change — only line numbers shifted, plus 2 more harmless `f-string is
missing placeholders` warnings matching the exact same pre-existing sibling style already
noted for Phase 2's own new section).

**T257-OVERNIGHT-FLOW-BRIEF is now fully built across all 3 phases** — futures + premarket
gappers (Phase 1), late-day options-flow persistence (Phase 2), and this scored attention list
(Phase 3). Nothing from the original Tier 257 design remains unbuilt for this specific item.

**What to check if this looks wrong**:
```bash
# Live-check the scoring function directly against real per-recipient data (safe — read-only,
# computes but does not send anything):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from src.services.scheduler import _build_attention_list, _fetch_premarket_gappers, _fetch_recent_options_flow
from db import SessionLocal
with SessionLocal() as s:
    movers = _fetch_premarket_gappers(s)
    flow = _fetch_recent_options_flow(s)
    result = _build_attention_list({'AAPL', 'NVDA', 'TSLA'}, {}, movers, flow, macro_has_high_impact=False)
    print(result)
"

# Check how many symbols qualified across all recipients on a real recent brief send:
docker logs stockai-market-data-1 --since 24h | grep 'premarket_brief.done' | grep -o 'attention_symbols_total=[0-9]*'
```
A consistently `0` attention-list total across many days is expected and correct on quiet
market days — the ≥2-signal bar is deliberately strict; an empty attention list most days is
the bar working as intended, not a sign the feature is broken (matching the same
"most cycles qualify zero picks" honesty already documented for T257-TOP3-CONVICTION-ALERT).

---


## Feature Reference: Market Pulse Dashboard — a Real "What's Happening Right Now" Page (Built 2026-08-25)

**Closes §4.3 of `docs/recomm_or_audit/REALTIME_NEWS_EVENTS_INTELLIGENCE_2025-08-22.md`**
(already reviewed once this session — Tier 299) — the doc's own proposed "Market Pulse
Dashboard": a single page composing regime, macro events, top movers, sector heat map, news
pulse, and active alerts, versus the previous state where getting this same picture required
navigating 4-5 separate pages (`regime.tsx`, `reports.tsx`, `sector-rotation.tsx`,
`intelligence.tsx`) and assembling it manually.

**Verified this wasn't already built under a different name before starting**: `intelligence.
tsx`'s existing `MarketPulseCard` (T249-MARKETMOVER-P4, 2026-07-18) is narrow — headlines +
one sentiment score + 3 theme chips, nothing else. `index.tsx`'s "Dashboard" (the app's `/`
route) is a watchlist-management console — add/remove stocks, per-stock rankings/signals,
ingestion controls — not a market-wide overview. Neither covers the doc's 6-section ask.

**A real, separate finding: the doc's companion §4.1 "FOMO Composite Score" proposal doesn't
hold up, and was dropped rather than built on a false premise.** Its formula's first term,
`squeeze_score * 0.30`, is presented as "all inputs exist" — traced directly into
`services/market-data/src/services/scheduler.py` and found squeeze scoring only exists as a
calibration-bucket lookup (`_build_squeeze_family_calibration()`) keyed to symbols that have
ALREADY fired a real squeeze/gamma alert — there is no live, callable, per-symbol 0-100 score
for an arbitrary symbol on demand the way the doc's formula assumes. Building a "FOMO score"
for every symbol on the dashboard would have silently zeroed out (or crashed on) this term for
the vast majority of symbols that have never fired an alert. Correctly abandoned rather than
shipped with a broken input.

**All 6 real sections, each independently verified against actual working endpoints before any
UI was written — zero new backend work needed for any of them**:
1. **Regime banner** — `api.regime(market)` (`RegimeStatus`: state/vix/spy_20d_ret/notes) +
   `api.fearGreed()` (US only).
2. **Macro events today** — `api.eventsCalendar(1)` filtered to the 11 real macro `type`s
   (`fomc`/`cpi`/`nfp`/`pce`/`gdp`/`ppi`/`retail_sales`/`consumer_conf`/`housing_starts`/
   `jobless_claims`/`fed_funds`), excluding `earnings`/`dividend`/`split`.
3. **Top movers** and **4. Sector heat map** — both derived client-side from **one** existing
   `api.sectorPerformance()` call. `SectorGroup[]`'s own `.stocks: SectorStock[]` already
   carries per-stock `change_pct` (confirmed by reading the real type in `api.ts` before
   assuming a new endpoint was needed) — flattened across every sector, sorted by
   `|change_pct|` descending, top 10 → "Top Movers"; `SectorGroup.avg_change_pct` per sector,
   sorted descending → the heat map. This is the same data source `reports.tsx`'s existing
   Money Flow tab already reads, just recomposed for a different purpose.
5. **News pulse** — reuses `api.marketPulse()` (the same endpoint `MarketPulseCard` already
   uses) and the existing `NewsCard` component directly, rather than reimplementing headline
   rendering a second time.
6. **Active alerts** (admin-only bonus section, gated on `session.role === 'admin'`) —
   `api.getSqueezeAlertPerformance({ days_back: 7 })`'s `recent_alerts` field. Narrower than
   the doc's "all alert types fired in last 4h" framing (this only covers the squeeze/gamma
   alert family, since that's the one real "recently fired alerts" feed that actually exists),
   stated honestly in the section's own subtitle rather than silently implying broader
   coverage.

**HK scope note, stated explicitly in the UI rather than silently degrading**: macro events,
news pulse, sector heat map, and active alerts are all US-only data sources today — switching
the market toggle to HK shows regime status only, with a visible note explaining why, matching
this app's established honesty convention for partial-coverage features (CAPE, options-flow
sentiment, cross-asset signals all follow the same pattern).

**Nav**: added to the `Markets` group in `_app.tsx`, right after "Dashboard" — a public,
non-admin page (only the bonus alerts section is gated), matching every other non-admin
Markets entry.

**A pre-existing hooks-order concern noticed, not replicated**: `intelligence.tsx`'s own
`IntelligencePage` component calls a conditional early `return null` (on missing session)
BEFORE its own `useState` call further down — a real React hooks-order violation in existing
code, out of scope for this task, but deliberately NOT copied into the new page. The new
`MarketPulseDashboardPage` calls `useState` unconditionally at the top, before the session
guard, matching the safer pattern already used in `etrade-transactions.tsx`.

**Tests**: no dedicated test file — nothing imports this page directly (matching this
codebase's own established precedent for standalone-page-only changes, e.g. `_app.tsx`/
`PriceChart.tsx`-only fixes elsewhere in this file), so verification is `npx tsc --noEmit` +
a full `next build` + a direct grep of the COMPILED output (not just source) confirming the
real section headers landed in the compiled JS chunk, the new `.market-pulse-two-col` CSS
class and its `@media (max-width: 767px)` override both compiled correctly, and the new nav
label appears in the compiled `_app-*.js` chunk. Full 132-test frontend vitest suite
unaffected (green, unchanged).

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'market-pulse-two-col[^}]*}' /app/.next/static/css/*.css"
docker exec stockai-frontend-1 sh -c "grep -l 'MARKET REGIME\|TOP MOVERS' /app/.next/static/chunks/pages/market-pulse-dashboard-*.js"
```
If the Top Movers / Sector Heat Map sections look empty despite real market activity, check
`GET /stocks/sector_performance` directly first — both sections are pure derivations of that
one response; an empty response there means both sections are correctly showing nothing, not
a bug in this new page's own composition logic.

---

