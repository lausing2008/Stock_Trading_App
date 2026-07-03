# Design: Service Architecture Review — Regrouping & Refactor Candidates

**Status:** Design only — nothing in this document is implemented. Action items are tracked in
`improvements.tsx` under Tier 233. Produced by an 11-way parallel per-service audit, a
cross-service synthesis, and an adversarial verification pass that directly checked the codebase
rather than trusting the synthesis's claims — one of the synthesis's recommendations was found to
be **factually backwards** during verification (see §2, item 3) and corrected before reaching
this document. Treat every claim below as "checked once, not yet re-validated by a second
reviewer" — reasonable confidence, not certainty.

**Method:** 11 independent agents each audited one service against its own `skill.md` and actual
source, reporting real dependencies, scope coherence, and misplaced responsibilities. A synthesis
agent found cross-service patterns invisible from any single service's vantage point. A
verification agent then re-checked every synthesis claim directly against the codebase (file
sizes, import graphs, actual call sites) before any recommendation was trusted enough to write
down here.

---

## 1. Total inventory — one-line verdicts

| Service | Verdict | Why |
|---|---|---|
| **market-data** | **SPLIT** | Spans 3 unrelated architectural roles in one process: system orchestrator (scheduler), shared data backend (6 other services read prices/regime/fundamentals from it), and a standalone product surface with no other consumers (auth, watchlists, journal, Kanban board, broker OAuth, congress scraping, news, HK Connect, RL agent). 34 files, ~18,000 lines, top 5 files = 71% of the service. |
| **signal-engine** | SPLIT (in-service first) | 24 of 32 routes are a batch self-tuning/analytics/backtesting subsystem (`outcomes/*`, `calibrate_*`, `tune_*`, `watchdog`, `alpha_decay`, `walkforward`, `gate_backtest`) structurally distinct from the hot-path `GET /signals/{symbol}` serving logic. `routes.py` is 4,805 lines — already flagged as a review hazard in this service's own `skill.md`. |
| **research-engine** | SPLIT (in-service) | One 1,795-line file bundling report aggregation with three independently-testable quant subsystems (technical scoring, fundamental scoring, DCF valuation). The test suite already imports the scoring functions directly with zero FastAPI dependency — proof they're already decoupled in practice, just not in file layout. |
| **api-gateway** | SPLIT (minor) | Proxy/health/aggregate are correctly scoped and should stay. `ai_proxy.py` (166 lines, the largest single file) is a self-contained third-party LLM chat integration with zero shared code or state with the rest of the gateway — a business feature that happens to live in the network-egress service, not a proxy concern. |
| **event-intelligence** | SPLIT (minor) | Core scoring pipeline (earnings/insider/congress/institutional/catalyst) is coherent. `political.py` and `edgar_8k.py` are not imported by that pipeline at all — disconnected modules hosted here more by convention than by actual wiring. |
| **decision-engine** | KEEP AS-IS | Clean, narrow gatekeeper with graceful degradation on every upstream dependency. Its only real issue — drift with `paper_trading_engine._should_enter()` — is a same-service-pair sync problem (already tracked as `T232-DL-DUALSCORER-DEBT`), not a scope problem. |
| **ml-prediction** | KEEP AS-IS | Coherent train/tune/serve pipeline. `hmm_regime.py` is a candidate to move OUT (see §2) since its only consumer is elsewhere, but the core service scope is sound. |
| **ranking-engine** | KEEP AS-IS (minor extraction) | Small, single-purpose hub serving 6+ consumers well. One cosmetic piece (`_fetch_patterns_bulk`, decorates the leaderboard but never feeds the K-score) is presentation glue misfiled as scoring logic. |
| **technical-analysis** | KEEP AS-IS | Small (716 lines), cohesive, zero outbound dependencies — the one service in the system that is *under-used* rather than over-scoped. The real fix needed here is getting other services to actually call it (see §3), not restructuring it. |
| **strategy-engine** | KEEP AS-IS | Cleanest-scoped service in the audit: one outbound dependency (market-data only), zero inbound dependents besides the gateway, one coherent domain. |
| **portfolio-optimizer** | KEEP AS-IS (absorb market-data's `portfolio.py`) | Small, coherent, single-endpoint quant service — the natural single home for portfolio risk/correlation math currently duplicated in market-data. |

---

## 2. Merge/move candidates (verified against real coupling)

Applying a strict test — near-100% one-directional calls, or provably always co-deployed with no
independent value — to avoid "everything touches market-data, therefore merge everything":

1. **`api-gateway/ai_proxy.py` → research-engine.** STRONG. Zero shared code with the rest of the
   gateway, duplicates a capability research-engine already has (calling Claude, shaping
   requests/responses), and is genuinely small (166 lines) to move. **One unverified risk before
   starting:** confirm research-engine's existing Claude-calling code reads the same
   `stockai:admin:claude_api_key`/`deepseek_api_key` Redis keys `ai_proxy.py` uses today — if they
   diverge, the admin settings UI silently stops reaching whichever service didn't get the memo.
2. **`ml-prediction/hmm_regime.py` → market-data.** MODERATE (downgraded from the synthesis's
   initial "second-clearest merge candidate" — see the correction in item 3 below for why the
   confidence level matters). Verified: `paper_trading_engine.py` calls
   `http://ml-prediction:8003/regime-state` over HTTP on every regime computation
   (`paper_trading_engine.py:942`) for a value with a single consumer. Colocating would eliminate
   a real network hop. **Before starting:** inventory what `hmm_regime.py` imports from
   ml-prediction's shared feature-builder/model-loading code — if it shares nothing, this is a
   clean lift-and-shift; if it shares scaler/feature code, market-data needs either a vendored
   copy or a shared package, which needs to be scoped explicitly, not assumed.
3. **`market-data/rl_agent.py` → ml-prediction. NOT RECOMMENDED — correction from initial synthesis.**
   The first-pass synthesis justified this move with the same "eliminate an HTTP hop" argument
   used for `hmm_regime.py` above. Verification found this was **factually backwards**:
   `rl_agent.py` is already imported **in-process** within market-data
   (`paper_trading_engine.py:51: from .rl_agent import rl_recommend`;
   `scheduler.py:1934: from .rl_agent import run_rl_training`) — there is no HTTP hop today, and
   moving it to ml-prediction would **create** one in paper trading's hottest, most
   capital-sensitive decision path where none currently exists. The two recommendations'
   justifications were apparently swapped during synthesis. If this move is pursued at all, it
   should be re-justified purely on ownership/taxonomy grounds ("trained-model artifacts belong
   together") with the added network hop explicitly accepted as a cost, not claimed as a benefit.
   **Recorded here specifically as a caution against taking any single-pass audit's conclusions
   as final without a verification step** — this is the exact reason the workflow included one.
4. **`market-data/api/portfolio.py` → portfolio-optimizer.** MODERATE. Verified: both services
   independently compute correlation/covariance for the same kind of symbol universes via two
   separate price-fetch paths (market-data: direct `yf.download`; portfolio-optimizer: Ledoit-Wolf
   shrinkage via HTTP fetch from market-data). Consolidating eliminates a duplicate "what is
   correlation for this universe" computation. **Before starting:** grep the frontend's `api.ts`
   for every call site hitting market-data's portfolio endpoints, and confirm
   portfolio-optimizer's output is a drop-in replacement or needs a response-shape adapter — this
   is user-facing and would trigger a full frontend rebuild per the standard deploy pattern.

No other service pair meets this bar. `signal-engine↔ml-prediction`, `ranking-engine↔everyone`,
and `research-engine↔{market-data, technical-analysis, signal-engine, ranking-engine,
event-intelligence}` are all legitimate narrow-read fan-in/fan-out relationships with
independent, substantial responsibilities on each side — merging any of these would relocate
complexity into a larger blast radius for no reduction in total complexity, which every
per-service report that touched these relationships explicitly argued against on its own.

---

## 3. Cross-cutting duplication findings (new — not already tracked elsewhere)

These are distinct from the regime/style-params/dual-scorer duplications already tracked under
Tier 232 — found specifically by looking across all 11 service reports at once:

**A. Congressional trading data is fully duplicated, not just similarly sourced — and it's live
today.** `market-data/api/congress.py` (229 lines) and `event-intelligence/services/congress.py`
(270 lines) both scrape House/Senate Stock Watcher with near-identical amount-range parsing.
event-intelligence's own `skill.md` already calls its version canonical, but market-data's
duplicate was never removed. Two frontend pages (`congress.tsx`/`insider.tsx` vs.
`intelligence.tsx`) can show **divergent data for the same stock right now** — this is the
highest-confidence, lowest-risk, most user-visible finding in the entire audit.

**B. Indicator math (RSI/MACD/ATR/ADX/Supertrend) is independently reimplemented in six places.**
`technical-analysis` owns the canonical implementation, but `signal-engine`, `ranking-engine`,
`ml-prediction`, `market-data` (twice — two separate files), and `research-engine` each hand-roll
their own — verified directly, not just claimed. This was flagged independently by four of the
eleven per-service audits before the synthesis step even connected them, making it the single
most repeated finding in the entire exercise. `technical-analysis` — built to be the source of
truth — is bypassed by nearly everyone, apparently for hot-path/latency reasons. This is the
highest total-value fix in the whole document (six services' worth of drift-risk elimination) but
also the largest coordinated effort, and the one most likely to silently change live trading
signals if done carelessly (see the rollout caution in §5).

**C. "Call an LLM provider and normalize the response" is implemented independently three times.**
`api-gateway/ai_proxy.py` (Claude+DeepSeek chat), `research-engine`'s `_call_claude` path
(Claude+DeepSeek, report generation + chat), and `decision-engine/llm_scorer.py` (Claude Haiku
only) — three independent HTTP-to-LLM integrations, three independent admin-API-key-in-Redis
lookup patterns, no shared abstraction. Addressing item 1 in §2 (moving `ai_proxy.py` into
research-engine) is the natural first step toward consolidating this to two implementations, then
eventually one shared client.

**D. "List of trades/positions" is modeled three separate ways inside market-data alone** —
`TradePlan` (Kanban board), `UserPosition`/`PositionTrade` (buy-side ledger),
`PaperTrade` (paper trading) — plus a fourth angle in decision-engine's request/response models.
These are legitimately different concepts (planned / real / paper), so this is flagged as a
**data-model proliferation risk to watch**, not an active bug — no reconciliation layer exists
today, and if a future feature needs "all positions regardless of type" it will need one.

**E. Boundary/wiring inconsistencies worth a small, low-risk fix each:**
- `api-gateway/health.py`'s health-check list omits `event-intelligence` even though `proxy.py`
  routes to it — the admin health dashboard has a blind spot where `event-intelligence` can be
  fully down and `/health/deep` reports all-green.
- `signal-engine` calls event-intelligence's catalyst score over HTTP with a service token, but
  separately reads the `sec_filings` table (8-K data) via **direct DB query**, bypassing
  event-intelligence's own `/events/8k/{symbol}` endpoint for logically the same kind of
  question. The lower-risk fix is rewiring signal-engine to call the existing endpoint
  (matching the established `_service_token()` pattern from the INT-7 fix already documented in
  CLAUDE.md) rather than moving `edgar_8k.py`'s file ownership to match the DB-access pattern.

---

## 4. What NOT to do (explicitly considered and rejected)

Documenting these prevents relitigating them later:

- **Do not split `paper_trading_engine.py`/`scheduler.py` out of market-data.** This is 60%+ of
  the service's line count and is internally coherent — they share tight state
  (`_prefetched_open`, service tokens, live regime) and a network hop between them would land in
  the hottest, most capital-sensitive code path in the system for no complexity reduction. Every
  other service's dependency on market-data is a narrow read (prices, regime, rankings) — nothing
  needs this cluster to be its own service.
- **Do not move `rl_agent.py` to ml-prediction on performance grounds** — see §2 item 3, the
  synthesis's own justification for this was verified backwards.
- **Do not merge `strategy-engine` into anything.** Verified as the cleanest-scoped service in
  the audit (one outbound dependency, zero inbound dependents besides the gateway) — a textbook
  thin-client relationship to market-data, not a merge signal.
- **Do not split `decision-engine` or `ml-prediction`'s core train/tune/serve pipeline.** No
  per-service or cross-service evidence supports either.
- **Do not split the auth/watchlist/journal/board/positions cluster into its own service right
  now.** Internally coherent as a "user account" bundle; nothing is currently broken by
  co-location, and splitting it would add a new container/port allocation (against CLAUDE.md's
  documented port map) for no correctness or performance gain today. Revisit only if this cluster
  starts causing real deployment friction.
- **Do not split `news.py`/`hk_connect.py` into a dedicated alt-data service** — lowest
  confidence, real service-split cost, no concrete trigger today. Defer indefinitely absent one.

---

## 5. Prioritized action list

### Strong — do these

| # | Action | Effort | First step |
|---|---|---|---|
| 1 | Delete market-data's duplicate `congress.py`; repoint `congress.tsx`/`insider.tsx` to event-intelligence's canonical endpoint | S | Diff the two endpoints' response JSON shapes; confirm frontend `api.ts` call sites; check whether market-data's scheduler runs its own congress-scrape cron job that also needs removing to stop double-scraping |
| 2 | Extract `api-gateway/ai_proxy.py` into research-engine | S | Grep every reader/writer of `stockai:admin:claude_api_key`/`stockai:admin:deepseek_api_key` in Redis to confirm research-engine's existing Claude-calling path can consume the same admin-configured keys without a compatibility shim |

### Moderate — reasonable, not urgent

| # | Action | Effort | First step |
|---|---|---|---|
| 3 | Move `hmm_regime.py` from ml-prediction to market-data (colocate with its only caller) | M | Inventory every import inside `hmm_regime.py` reaching into ml-prediction's shared feature/model-loading code, to determine if this is a clean lift-and-shift or requires vendoring shared logic |
| 4 | Consolidate market-data's `portfolio.py` into portfolio-optimizer as the single correlation/VaR/beta source | M | Grep frontend `api.ts` for every call site hitting market-data's portfolio endpoints; confirm portfolio-optimizer's output is a drop-in replacement or needs a response-shape adapter |
| 5 | Split signal-engine's `routes.py` into `routes.py` (hot path) + `outcomes.py` + `calibration.py`, same service | M | Enumerate the 24 self-tuning/analytics routes vs. the 8 hot-path routes by grepping `@router` decorators; pure file split, zero behavior change, zero deployment-pattern change |
| 6 | Extract research-engine's scoring functions into `src/scoring.py` | S/M | Start with whichever functions the existing test file already imports directly (zero FastAPI dependency) — proof they're already decoupled |
| 7 | Rewire signal-engine to call event-intelligence's `/events/8k/{symbol}` endpoint instead of a direct `sec_filings` DB query | S | Replace the direct DB query with a service-token-authenticated HTTP call, following the same `_service_token()` pattern already used for the research-engine call (CLAUDE.md's INT-7 fix) |
| 8 | Create `shared/common/indicators.py` as the canonical RSI/MACD/ATR/ADX/Supertrend implementation; migrate one consumer at a time | L | Pilot with research-engine first (not on the trading hot path); validate output parity against the existing implementation on historical data BEFORE touching signal-engine or ranking-engine, since any drift here changes live trading signals — ship with an explicit multi-container deploy checklist, since `shared/` requires `docker cp` to every affected container in the same deploy window (this exact multi-container-deploy pattern has caused repeated incidents before, per CLAUDE.md's jose-missing-from-container history) |
| 9 | Fix `api-gateway/health.py`'s missing `event-intelligence` entry in the health-check list | S | Add `event-intelligence:8010` to the `_SERVICES` list in `health.py` alongside the other 9 |

### Considered but not recommended

- **Move `rl_agent.py` to ml-prediction** — verified backwards rationale (§2 item 3); would add a
  network hop to the paper-trading hot path for a purely taxonomic benefit.
- **Move ranking-engine's cosmetic pattern column to `aggregate.py`** — real but trivial; doesn't
  clear the bar against the items above. Revisit opportunistically alongside any other
  `aggregate.py` change.
- **Split auth/watchlist/journal/board cluster into a standalone service** — no current trigger.
- **Move `news.py`/`hk_connect.py` to a dedicated alt-data service** — lowest confidence, real
  cost, no trigger.
- **Split decision-engine, ml-prediction, or merge strategy-engine into anything** — no evidence.

---

## 6. Rollout guidance

Do not attempt all of this at once, and do not treat the STRONG/MODERATE labels as a strict
sequential order — items 1, 2, and 9 are independent, small, and can happen in parallel with
each other or with unrelated work. The one item requiring real sequencing discipline is #8
(indicator dedup): it is the highest-value fix in this document but also the one most capable of
silently changing live trading behavior if the parity validation step is skipped. Every other
item here changes internal wiring or file organization without changing what any single
computation produces — #8 is the exception, and should be treated with the same "verify against
outcome data before trusting it" discipline established in the self-improvement loop design
(`docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md`).
