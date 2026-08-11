# Design Doc: 6-Item Feature/Review Batch (2026-08-11)

Status: **research complete, awaiting scoping decisions before any implementation.**
Nothing in this batch has been coded yet. Each section below ends with the specific
choice(s) needed from the user before work starts. Item 3 (E*Trade real account) and the
admin-auth gap under item 6 are flagged **capital-risk / do-not-build-silently** — they
need explicit sign-off, not just a nod, before touching code.

---

## 1. Weekly prod→local DB sync + alert kill switch

### What exists today
- `scripts/backup_db.sh` does a one-directional `pg_dump` on the EC2 host → S3, with a
  documented (but not yet cron'd) nightly schedule. No script pulls a dump down to a local
  machine or restores it into local Postgres — that half doesn't exist.
- Postgres is not publicly reachable (bound to `127.0.0.1:5432` on the EC2 host); the only
  externally reachable admin path is SSH, which this app's own deploy workflow already uses.
  So the realistic sync mechanism is: SSH in, `pg_dump` via `docker exec` (reusing
  `backup_db.sh`'s exact invocation), pipe/pull the dump to the local machine over that same
  SSH connection, then `psql`-restore it into the local Postgres container.
- Local dev Postgres is a docker container (same `docker-compose.yml`), not a native install.

### The real, present-tense risk this uncovered
Local `.env` **already has live SMTP credentials configured** (`EMAIL_PROVIDER=smtp`, real
values), and `scheduler.py` registers all ~19 alert/digest jobs unconditionally on startup
with **zero environment awareness** — `Settings.env` exists (`development`/`staging`/
`production`) but the scheduler never reads it. That means: if the full stack is run locally
today and a prod dump (with real `User.email`/`PriceAlert.email`/etc. columns) is restored
into it, the local scheduler would start sending real alert emails to real users within
minutes. This is not hypothetical — it's the exact failure mode item (1)'s second half is
asking to prevent, and it's currently wide open.

### Full inventory of alert-emitting jobs (19 total, zero currently have a kill switch)
`check_price_alerts`, `check_signal_alerts`, `check_technical_alerts`,
`check_volume_anomalies`, `check_short_squeeze_alerts`, `check_gamma_unwind_alerts`,
`check_squeeze_watch_reverts`, `check_value_area_breakdown`, `check_top3_conviction`,
`check_earnings_reactions`, `check_macro_reaction_alerts`, `check_earnings_impact_alerts`,
`check_early_earnings_news_alerts`, `check_earnings_beat_screener_alerts`,
`check_sector_rotation_alerts`, `send_morning_digest`, `send_post_open_digest`,
`send_premarket_brief`, `send_paper_portfolio_digest` — all in
`services/market-data/src/services/scheduler.py`. Two of these (`check_macro_reaction_alerts`,
`check_earnings_impact_alerts`) have a feature flag, but it only gates the upstream
LLM-generation step in event-intelligence, not this job's own delivery — the job itself
always runs.

### Recommended design (environment-scoped, not time-scoped)
The ambiguity in the ask — "turn off all alerts" could mean "during the Saturday sync window
on prod" or "permanently for any local/synced environment" — resolves cleanly once you see
that a sync-window pause on **prod** would cost real users real missed alerts for a `pg_dump`
that doesn't even need a write-lock, for zero safety benefit (prod's own alerts should keep
running independently of what a dev does locally). The actual problem is **local dev
emailing real people**, and that's an environment property, not a schedule.

Three layers, from most to least critical:
1. **Hard environment gate (the actual fix, not a toggle someone can forget)** — in
   `start_scheduler()`, skip registering/running the alert-emitting jobs entirely unless
   `Settings.env == "production"`. This makes local dev structurally incapable of sending a
   real alert, regardless of what data is loaded into it.
2. **PII scrub in the sync script itself (defense in depth)** — after restoring the dump
   locally, run one `UPDATE users SET email = NULL` (or rewrite to a `+devnull@` pattern) so
   even a misconfigured or manually-overridden local environment has no real address to send
   to. Cheap to add to the same script.
3. **Admin toggle, defaulted OFF, for the rare case a dev genuinely needs to test alert
   delivery against local data** — reuses the exact `stockai:admin:feature:<name>` /
   `ConfigRequest` / `Toggle` pattern already established for `auto_research_enabled` etc.
   This is optional polish on top of (1), not a substitute for it.

### Open questions for you
- **Sync direction/schedule**: cron'd locally on your dev machine (e.g. `cron`/`launchd`) that
  SSHes out to EC2 every Saturday, or a manual `make sync-prod-to-local` command you run
  yourself? (No strong reason to prefer one — just need to know which to build.)
- **PII scrub**: do you want emails nulled/rewritten on the synced local copy as a matter of
  course, or is (1) alone (environment gate) suffient for your comfort level? I'd lean toward
  doing both since it's nearly free, but it's your data.
- **Should the toggle (layer 3) exist at all**, or is the hard environment gate (layer 1)
  enough on its own? Adding the toggle means one more piece of UI to maintain for a rare case.

---

## 2. Call/Put graph on the stock detail page

### What exists today
Two live options endpoints already exist and are already wired into the stock detail page —
neither is a chart:
- `GET /stocks/{symbol}/options-flow` — aggregate call/put volume, `cp_ratio`, sentiment,
  top-10 "unusual activity" by premium. Rendered today as a horizontal stacked bar + a text
  table.
- `GET /stocks/{symbol}/options-chain?expiry=` — full per-strike call/put rows (strike, bid,
  ask, volume, OI, IV, ITM) for one expiry. Rendered today as a classic broker-style table,
  collapsed by default, already fetched client-side the moment a user expands it.

Both use yfinance, already documented in this codebase as "the most rate-limit-fragile call
this app makes" — both are already 15-min Redis-cached to bound that risk.

A separate, already-built daily snapshot table (`OptionsFlowSnapshot`) persists
`cp_ratio`/volumes/premiums once per day, but only for a **bounded symbol set**
(PriceAlert-subscribed + top-K by K-Score, US-only) — most individual stock-detail-page visits
would have no history in it at all.

The frontend's only charting library is `lightweight-charts` (already used extensively in
`PriceChart.tsx` — candlesticks, RSI/MACD panels, custom primitives). No other chart library
exists in the app.

### Two concrete, buildable options
**Option A — OI/volume-by-strike bar chart ("the smile"), zero backend changes.**
The `options-chain` endpoint already returns exactly the data needed
(`calls[]`/`puts[]` with `strike`/`volume`/`oi`). Render as a `lightweight-charts` histogram —
calls green above zero, puts red below zero, keyed by strike — replacing or sitting alongside
the existing table. Pure frontend work on data already fetched client-side when the Options
Chain section is expanded. This is what most people mean by "a call/put graph" (the standard
broker-style OI distribution).

**Option B — cp_ratio trend line over time, backed by `OptionsFlowSnapshot`.**
Answers a different question ("has sentiment drifted bullish/bearish over N days") rather
than "where's OI concentrated right now." Needs a new read endpoint (none exists for
single-symbol history today) and a real coverage gap — most stock-detail-page symbols won't
be in the bounded snapshot set, so this would show "no history" most of the time unless the
set is widened (a cost/scope tradeoff of its own).

### Recommendation
Build **Option A** first — it's the literal "call/put graph," uses data the page already
fetches, needs no backend change, and reuses the existing charting library. Option B is a
reasonable phase-2 if you specifically want a sentiment-over-time view later.

### Open question for you
- Confirm Option A (OI-by-strike bar chart) is what you mean by "call/put graph" — if you
  actually want a call/put ratio trending over time instead, that's Option B, which needs a
  scope decision on the bounded-symbol coverage gap first.

---

## 3. E*Trade real account for prod paper trading — ⚠️ capital-risk, needs explicit sign-off

### The code is "config-only" to flip — but that undersells the real risk
`broker_type="etrade"` vs `"etrade_sandbox"` is a genuine, already-fully-implemented
config-only switch at the adapter level (different base URL, everything else identical). But
"config-only" describes the *mechanism*, not the *safety*. Three concrete facts change the
picture:

1. **Linking a real E*Trade connection to a paper portfolio does not just sync data — it
   places real, live orders.** `_place_broker_entry`/`_place_broker_exit` in
   `paper_trading_engine.py` call `broker.place_order()` for every real BUY/SELL signal on
   that portfolio (US symbols only), in *parallel* with the simulated ledger, not instead of
   it.
2. **Order size comes entirely from the simulated ledger's equity, with no check against the
   real account's actual buying power.** If the simulated portfolio's config allows a bigger
   position than the real account can afford, E*Trade's own margin rejection is the only
   backstop — the app never checks first.
3. **The action that turns this on — picking a connection from a dropdown on the Paper
   Portfolio page — has no confirmation step and looks identical to picking the sandbox
   connection.** One `onChange` on a `<select>`, no "this will place real orders" warning.

### A newly-confirmed, independently-verified security gap (from item 6's review, directly relevant here)
I checked this myself: **every endpoint in `services/market-data/src/api/broker.py` — including
`PUT /paper-portfolios/{id}/broker`, the exact endpoint that links a broker connection to a
portfolio — uses `Depends(get_current_user)`, not `Depends(get_admin_user)`.** The frontend
hides this UI behind an `isAdmin` check, but that's cosmetic — nothing stops any logged-in,
non-admin user from calling the API directly and linking *any* connection to *any* portfolio.
This matters doubly here because `PaperPortfolio` has **no `user_id` column at all** —
portfolios are shared/global, so there's no ownership boundary to fall back on either.
**This must be fixed before real money is ever connected**, independent of anything else in
this item.

### What's needed before this should be built (not optional polish — prerequisites)
1. **Server-side admin gate on `broker.py`** — every endpoint, especially the
   connection-linking one, should require `get_admin_user`, matching the frontend's own
   existing intent.
2. **A real-money confirmation step** at the moment a connection is linked — distinct visual
   treatment for `etrade` vs `etrade_sandbox` in the picker, plus an explicit "this places
   real orders with real money" confirmation dialog before the `PUT` fires.
3. **A pre-order buying-power check** against the real E*Trade account before `place_order` is
   called, not just a size computed from the simulated ledger. (E*Trade's own `get_account()`
   call already exists in the codebase for this exact data — it's just never consulted at
   order time.)
4. **External prerequisite, not code**: E*Trade production API key approval via their
   developer portal — I found no evidence in the codebase or tracker that this has been
   requested or obtained yet. This is the actual timeline blocker, not the code.

### Open questions for you — please answer explicitly, this is the one item I will not start coding without a clear yes
- Have you already applied for / received E*Trade production API credentials? If not, that's
  step zero and gates everything else.
- Do you agree the 3 safety gates above (admin-only linking, confirmation UI, buying-power
  check) should be built **before** a real connection is ever linked, even though they add
  scope beyond "just flip the config"? I'd strongly recommend yes, but it's your capital.
- Which specific portfolio(s) would actually get the real link — presumably not all of them?
  Worth deciding the target scope now so the buying-power check and confirmation copy can
  reference the right account/portfolio explicitly rather than being generic.

---

## 4. Alert for large short positions unable to cover quickly

### Resolving the ambiguity
Short stock positions don't have an "expiry" — that's options vocabulary. The metric that
actually answers "shorts are trapped and can't get out quietly" is **days-to-cover**
(`short_ratio` = shares short ÷ average daily volume, already computed from yfinance, already
displayed as a "Days to Cover" column on the existing short-squeeze screener page). I'm
confident this — not options expiry — is what you mean, since the phrase "expiry" was likely
borrowed loosely from the neighboring options-flow UI on the same page.

### What exists today
`check_short_squeeze_alerts()` already fires on `short_percent_of_float >= 15%` AND an
intraday price move `>= 3%`, with a 30-day staleness cutoff on the short-interest reading
(this staleness check was itself a real bug found and fixed in a prior audit — short interest
can legitimately be up to 6 weeks stale, and this alert already guards against that). It
never reads `short_ratio`/days-to-cover at all today, even though the field is already sitting
in the same cached data the alert reads.

### Two buildable candidates
**Candidate A (recommended, smallest change)** — add a days-to-cover condition to the
*existing* alert as an escalation: when a candidate already clears the current bar (15% short
float + 3% move) AND `short_ratio <= 1–2 days`, treat it as a higher-urgency tier (distinct
subject line / "trapped shorts, can't cover quietly" framing). Reuses 100% of the existing
job, staleness check, and game-plan logic — only adds a threshold and a tier label.

**Candidate B** — a standalone alert that fires on days-to-cover crossing a low threshold
*even without* a price move yet (i.e., "the fuel is critical" independent of whether "the
spark" has happened). Genuinely new trigger logic, same underlying data and staleness
discipline, mirrors the existing state-transition dedup pattern.

### Open question for you
- A or B? A is cheaper and answers "squeeze in progress AND shorts are trapped." B answers
  "shorts are trapped, watch this one" even before any price move — a earlier-warning, noisier
  signal. Also: what threshold for "1 day" — literally `<= 1.0` days-to-cover, or a slightly
  wider band like `<= 2.0` to catch more real candidates (very few stocks will ever hit exactly
  ≤1.0)?

---

## 5. Sector/theme trend forecast email

### The honesty framing this needs, stated up front
This app has a strong, consistent internal culture (visible throughout its own history) of
never letting an alert or report claim more certainty than its underlying data supports —
every existing "trend" feature is explicitly labeled a measured fact, not a prediction. A
literal "which stocks will rally in the next few weeks, with reasons" email, if built as
asked, would be the first feature in this codebase to break that pattern — it would be
substantially the LLM's own market opinion, not a measured signal, because:

- Sector/industry data in this app is broad GICS-level ("Semiconductors," "Healthcare") —
  there's no existing concept of "GPU" vs "MLCC" vs "packaging" as distinct themes. That
  granularity would need new, hand-curated theme→symbol mappings; it doesn't fall out of any
  existing classification automatically.
- The only real sector-level signals that exist today (K-Score momentum trajectory, ETF-price
  momentum vs SPY) are both **backward-looking** — they measure what already happened, not what
  will happen next.
- Every other potentially-relevant signal (options flow, institutional 13F, congress/insider
  trades, catalyst scores) exists only per-symbol today, with zero sector/theme rollup — so
  "why" a theme is called out would either need new aggregation work per theme, or would just
  be the LLM's own reasoning with no real backing data cited.

### Recommended reframing (not a rejection — a more honest version of the same ask)
Report **"themes with genuinely supporting signals observed today"** rather than a forecast of
what's about to happen — i.e., surface the real, already-measured momentum/flow data for each
hand-curated theme, and have Claude write the "why" in readable prose grounded in those real
numbers, rather than asking it to predict the next few weeks unaided. This is exactly the same
honest reframing already applied to every comparable feature in this app (the CAPE bubble
warning is "macro context, not a trigger"; the options-flow alerts are "a measured fact, not a
prediction the move continues").

### Build shape (reuses an established pattern exactly)
Copy the `generate_reaction()`/`generate_earnings_impact()` skeleton wholesale: aggregate real
signals per theme → one Claude Haiku call per theme (or one call covering all themes) →
fail-open on any error → persist to new columns → a scheduler poll job → email delivery,
feature-flagged OFF by default (per this app's own prior incident where an ungated
auto-trigger burned real Claude spend — this must ship gated from day one, not added later).

### Open questions for you
- **Do you accept the reframing** ("themes with real supporting signals today, explained" vs.
  a literal multi-week forecast)? This is the main thing I need a yes/no on before designing
  further — the rest of this item is straightforward once that's settled.
- **Theme list**: you named semiconductors (GPU/MLCC/packaging sub-themes), Gold, Space,
  Healthcare. Should I propose a starter list of ~8–10 themes with hand-picked representative
  symbols for each, for you to review/edit, or do you want to supply the full list yourself?
- **Cadence**: weekly? You said "next few weeks" which reads as the CONTENT's own forward
  window, not necessarily the send frequency — confirming send cadence separately.

---

## 6. Bug review — findings so far

Two review passes were run: recent-high-churn-file review and a targeted broker/admin-safety
review. Both background review agents were cut off mid-run by an org-wide API spend limit, but
each had already surfaced one real, concrete finding before stopping, and I independently
verified the more serious one myself by reading the code directly (not trusting the agent's
unfinished output).

### Confirmed directly by me — capital/security-relevant (see item 3 above for full detail)
**Every endpoint in `services/market-data/src/api/broker.py` uses `get_current_user`, not
`get_admin_user`** — including the connection-linking endpoint. Combined with
`PaperPortfolio` having no `user_id` column, this means any authenticated user can currently
link any broker connection to any shared paper portfolio by calling the API directly,
bypassing the frontend's `isAdmin`-only UI entirely. Confirmed by reading `broker.py`'s
route decorators and `shared/db/models.py`'s `PaperPortfolio` definition directly — not
agent-reported, verified by me. **Recommend fixing this regardless of whether item 3 (E*Trade
real money) proceeds** — it's a real gap on the sandbox connection today too, just lower
stakes there.

### Surfaced by the interrupted agents, not yet independently verified — worth a follow-up pass
- A WebSocket listener (context not fully captured before the agent was cut off — likely in a
  live-quotes streaming endpoint) may call `.cancel()` on an `asyncio` future wrapping a
  blocking `run_in_executor` thread (e.g. a Redis pubsub `.listen()` loop) — cancelling that
  kind of future does not actually stop the underlying thread once it's running, which could
  mean a listener thread leaks past the point its websocket connection has closed. Needs a
  direct read of the actual file (agent was investigating but hadn't named the exact file/line
  before being cut off) to confirm whether this is real or already handled elsewhere.
- `ManualBroker.place_order()` returns `status="filled"` immediately, and
  `ManualBroker.get_order()` raises `NotImplementedError` — currently harmless because the
  caller wraps that follow-up call in a broad `except Exception: pass`, but flagged by the
  agent as worth a second look given how many other places in this codebase a swallowed
  exception has previously masked a real bug.
- Config validation for numeric portfolio parameters (`risk_per_trade_pct`, `max_position_pct`,
  etc.) — I checked this directly myself and it is **already correctly bounded** (a real
  `(min, max)` range-check table exists, with a documented past incident behind it). Not a
  finding — noting here so it isn't re-flagged in a future pass.

### Recommendation
Re-run the two interrupted bug-hunt passes once the org's spend limit resets, specifically
targeting: (a) the websocket/thread-cancellation concern to a specific file and line, (b) a
full read of the `ManualBroker` exception-swallowing path, (c) a broader sweep of every
capital-sensitive endpoint (not just `broker.py`) for the same admin-vs-user gating gap now
that one confirmed instance exists in this exact class.

### Open question for you
- Want me to prioritize fixing the confirmed `broker.py` admin-gate finding now, independent of
  the rest of this batch (it's small, self-contained, and real) — or bundle it into item 3's
  work since that's where it matters most?

---

## Summary of what I need from you to proceed

1. Sync direction/schedule (cron vs manual) + whether to scrub PII in the synced copy.
2. Confirm Option A (OI-by-strike bar chart) for the call/put graph, or ask for Option B instead.
3. **E*Trade real account**: confirm prod API access status, confirm the 3 safety gates should
   be built first, and name the target portfolio(s).
4. Candidate A or B for the squeeze alert, and your preferred days-to-cover threshold.
5. Confirm the "themes with real supporting signals" reframing, and whether you want a
   starter theme list proposed or will supply your own.
6. Fix the `broker.py` admin-gate finding now, standalone, or bundle with item 3?
