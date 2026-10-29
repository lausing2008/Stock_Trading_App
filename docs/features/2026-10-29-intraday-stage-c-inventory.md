# Intraday Stage C — inventory of existing components (before any contract reuse)

Date: **2026-10-29**. Branch: `intraday-contracts` (from `prod` at `67385bda`).
Status: **inventory only; nothing implemented**. It decides which existing functions can be
reused as Intraday contracts, which need wrapping, and which do not satisfy the requirement.
Plan: [Intraday and signal-performance plan](2026-10-26-intraday-and-signal-performance-plan.md),
stage C. Reviewer requirements (2026-10-29): timing, sessions, data, persistence; INTRADAY
distinct from the daily SHORT horizon; Stage C adds contracts and storage with production
generation disabled; the generator follows in shadow mode.

Verdicts: **reuse** (meets the requirement as is), **wrap** (correct core, but the Intraday
contract must add or restrict something), **gap** (nothing existing satisfies it).

## 1. Timing

Five instants the contract must keep separate. What exists for each:

| Instant | Existing source | Verdict |
|---|---|---|
| **Bar open** | `prices.ts`. All three US providers label a bar by its OPEN, stored as naive UTC: Unusual Whales `start_time` (`adapters/unusual_whales_adapter.py:257`), Polygon `t` (`polygon_adapter.py:106`), yfinance index (interval start). `rzy_data.prepare_bars` treats `ts` as the open. | **reuse**, with the convention stated in the contract and naive values read through `common.signal_time` rules (naive = UTC, explicitly). |
| **Bar close** | `prepare_bars`: `min(ts + 5m, session close)` — the clip handles the early-close final bar. `bar_provenance._completed_at` does the same for `1h` only, NOT for 5m/15m on an early close (it uses `ts + step`; harmless on the 5m grid because no 5m bar straddles 13:00, but it is a second definition). | **reuse** `prepare_bars`' definition; the contract owns one `bar_close(market, ts, tf)` and the provenance copy is not used. |
| **Settlement cutoff** | RZY's **2-minute** settle, hard-coded three times: `prepare_bars` (`closed + 2m > now` → `incomplete_or_settling`), `aggregate_completed_bars`, `latest_completable_close`. | **wrap**: a named contract constant (`SETTLE = 2 min`) with the same value; the three literals stay as RZY's own until RZY adopts it. The cutoff is a floor on *when a bar may be read*, not evidence the provider had published it. |
| **Provider availability** | Not recorded per bar. `bar_provenance.first_written_at` / `last_written_at` record when **this platform wrote** the bar (ingest time), not when the provider published it; `prepare_bars` reports `delay: 'unknown'`, `provider: 'stored_prices_upstream_not_retained'`. `bar_ingest_observations.observed_at` is per batch. Ingest cadence: `us_5m_intraday` every 5 min 09:00–15:55 ET; `hk_5m_intraday` same HKT. | **gap** for provider availability. The contract records **observed availability = `bar_provenance.first_written_at`** (an upper bound on when the platform could have known), labelled as such; provider publish time stays `unknown`. A setup's `data_available_at` is the max over the bars it read. |
| **Detection** | RZY scan: `now` is the data cutoff, and `clock()` gives each stream's actual evaluation time (F09). Median scan duration 282 s (OD-02). | **reuse the pattern** (data cutoff ≠ evaluation time; both stored). Not the RZY scan itself. |
| **Entry expiry** | RZY mail expiry `signal + timeframe + 5 min`, and "too late" `signal + timeframe + 3 min` — both mail rules, not plan expiry. No plan-level entry expiry exists. | **gap**: the contract defines `entry_expires_at` per setup (from the trigger bar's close), never later than the session-end rule below. |

Ordering invariant the contract enforces on write: `bar_open < bar_close ≤ bar_close + SETTLE ≤
data_cutoff`, `data_available_at ≤ detected_at`, `detected_at < entry_expires_at ≤
last_entry_at(session)`. A violation is refused, not stored.

## 2. Sessions

| Requirement | Existing source | Verdict |
|---|---|---|
| Session bounds, early closes | `common.market_calendar.session_bounds(market, d)`: venue-local open/close, NYSE early closes (13:00 ET) from an official list, coverage through 2027 (`MIN_COVERAGE_YEAR`, `assert_calendar_coverage`). | **reuse**. Coverage expiry is a hard stop (RZY already returns `calendar_coverage_expired`). |
| **HK lunch break** | `session_bounds` returns only the OUTER bounds (09:30–16:00) by design; `is_regular_session` knows the 12:00–13:00 closure. `rzy_data` is **US-only** (hard-coded `'US'` and New York time); its grid, gap and staleness logic would treat lunch as a gap or accept lunch bars. | **gap** for HK. The contract needs `session_spans(market, d)` (both HK spans). **Recommendation: Intraday v1 is US-only**, and HK is refused explicitly (`unsupported_market`) until spans-aware bar preparation exists. |
| Session-end exits | None for intraday holds. RZY has no managed hold. | **gap**: `forced_exit_at = min(trigger_close + max_hold, session_close − exit_buffer)`; a setup never survives `session_close`. |
| No carry across an invalid boundary | `prepare_bars` / `break_indices` detect cross-session discontinuities and never bridge; RZY evaluates only the latest contiguous segment. | **reuse** for inputs. The contract adds: a setup's `session_date` is the venue date of its trigger bar; every window (forecast, hold, expiry) is clipped to that session; a window that would cross the close resolves as `session_end`, never on the next session's bars. |
| Daily SHORT intraday refresh | `scheduler._check_short_intraday_triggers` (TIER83) recomputes the **daily SHORT** signal intraday on a 1.5×ATR move. Uses host-local `date.today()` and hand-written 09:30–16:00 hours (no early close, no HK lunch). | **not reused**. It is SHORT, not Intraday; the two must not trigger each other. Its clock defects are recorded here as a separate finding, not fixed in Stage C. |

## 3. Data

| Requirement | Existing source | Verdict |
|---|---|---|
| Native vs derived bars | `prices.timeframe` holds native 5m/15m/1h. `rzy_workflow.load_bars` derives 15m/1h from complete, contiguous 5m bins (`aggregate_completed_bars`, `source='derived_from_5m'`) and uses native only when current and at least as recent — one source per evaluation, never spliced (RZA-07). | **wrap**: Intraday records which source each input came from (`native` / `derived_from_5m`) in the input contract. 15m context must be complete at detection (a forming 15m bin is never context). |
| Freshness | `latest_completable_close` + `stale` (one missing bin is stale); derived frames judged on their own completed bins (RZA-02). | **reuse**. Stale input → `DATA UNAVAILABLE`, not a setup. |
| Gaps | `prepare_bars` gaps (`missing_intraday_candles`, `missing_intraday_session`), `break_indices`, `missing_aggregate_constituents`. | **reuse**. A gap inside the lookback → no setup for that bar. |
| Revisions | `bar_provenance`: per-bar writer, `bar_revisions` (before/after, class, age), `bar_session_authority` (authority protection, fail-closed). RZY rebuilds on `stored_completed_bar_changed`. | **wrap**: a setup is **immutable**. A later revision of an input bar does not edit it; it marks the setup `input_revised` (with the revision ids) for outcome evaluation. Outcomes are computed on bars as stored at evaluation time and record that. |
| Off-grid / quote rows | 457b: off-grid rows rejected at ingest; `prepare_bars` excludes `off_session_bar_grid`. | **reuse**. |
| Execution readiness | `prepare_bars` sets `execution_eligible=False`, `delay='unknown'`, and `paper_blockers = provider_delay_unverified, borrow_availability_not_stored, execution_basis_unverified`. | **reuse as-is and keep**. Storage is not readiness: Stage C setups are `research_only`; long paper and short execution stay blocked by these named blockers. |

## 4. Persistence and readers

Source: a read-only consumer audit of `services/*/src`, `shared/` and `frontend/src` at
`67385bda` (tests excluded). The seven high-risk sites below were re-read at the cited lines
and confirmed before being recorded here.

### What exists

- `SignalHorizon` (`shared/db/models.py:82`) is the PostgreSQL enum `signalhorizon`, used by
  `signals`, `signal_outcomes` and `signal_outcome_horizons` through `SAEnum(SignalHorizon)`.
  The only existing value migration is `ALTER TYPE signalhorizon ADD VALUE IF NOT EXISTS
  'GROWTH'`, run on every startup (`shared/db/session.py:405`).
- `signals` holds **one row per (stock, horizon, UTC day)** (`ON CONFLICT (stock_id, horizon,
  date_trunc('day', ts))`, signal-engine `routes.py:469, 1240`), overwritten ~5 times a day.
  That is the daily model. It cannot hold several same-session setups.
- F03's `signal_occurrences` / `signal_snapshots` (observation phase, deployed) key a run per
  `(stock, horizon, session_date, seq)`, with at most one open run per `(stock, horizon)`. It is
  the right *pattern* (immutable first observation, content hash, venue session date), but its
  one-open-run rule does not fit coexisting Intraday setups.
- `little_rzy_occurrences` is RZY's own immutable first-observation record. It belongs to the
  `LITTLE_RZY_INSPIRED_V1` strategy and is not relabelled as Intraday.

**Verdict: gap.** No existing table stores Intraday occurrences, plan snapshots or outcomes.
F03's pattern is reused; its tables are not.

### Readers: what an INTRADAY row would do today

Nothing iterates `SignalHorizon`; every horizon loop is a hard-coded four-tuple, so adding the
enum value alone starts no daily job. The risk is (a) images built before the enum value exists
and (b) readers that select from the daily tables **without a horizon filter**.

**Crash risk (stale images).** `SAEnum(SignalHorizon)` is baked into each service image
(`COPY shared /app/shared`). An image built before the value exists raises `LookupError` on any
ORM load of an INTRADAY row. So every service must run a build that knows the value before any
INTRADAY row can exist in a shared table.

**High-risk readers** (confirmed) — each would mix an INTRADAY row into daily logic or abort a
batch:

| Site | Effect of an INTRADAY row |
|---|---|
| signal-engine `api/outcomes.py:229-237` → `:378-380`, `:683-684` | unfiltered pending-signal read, then `_OUTCOME_HOLD_DAYS[horizon]` outside the per-row `try`: `KeyError` aborts `/outcomes/evaluate`. Adding INTRADAY to the map instead grades it on daily T+1 closes. |
| signal-engine `api/calibration.py:697-711` (`calibrate_ta_weights`) | BUY outcomes of every horizon fitted into the global live TA weights |
| signal-engine `api/calibration.py:969-982` (`calibrate_conviction_weights`) | same, into the global conviction weights |
| ml-prediction `training/meta_trainer.py:128-143` | raw SQL over all `signal_outcomes`, `LIMIT 20000`: pooled into meta-model training (horizon feature −1, 10-bar fallback) and can crowd daily rows out of the limit |
| event-intelligence `scheduler.py:128-133` | newest signal of any horizon supplies `ta_score` and `atr_14_pct` to the daily catalyst recompute |
| market-data `services/paper_trading_engine.py:3205-3213` | newest signal of any horizon supplies RSI to the `momentum_fade` exit and trail tightener on live paper positions |
| signal-engine `api/analytics.py:1517-1545` (`/outcomes/summary`) | pooled `overall`/`by_symbol`, read by the morning digest and the alert-email win-rate badge |

**Medium:** unknown-style fallbacks that return all horizons or SWING (signal-engine
`routes.py:60, 633, 1131, 1311`; decision-engine passes an unvalidated style into the last two;
`/signals/{sym}?style=INTRADAY` falls through to a live regeneration that persists four daily
rows); free-text `Watchlist.trading_style` reaching `SignalHorizon(style)` in the curation job
(`scheduler.py:11555`); `/consensus` and `/recent_changes`; analytics `/accuracy`,
`/rolling_accuracy` (drift alarm), `/factor-exposure`, `/walkforward`; the data-quality
freshness check `MAX(sig.ts)` (`scheduler.py:13472`) that INTRADAY writes would mask;
research `intel_reports/adapters.py:391` taking the newest signal of any horizon; the 30,000-row
cap in `calibration_store.py`. ml-prediction's `.get(style, 5|10)` fallbacks would accept
INTRADAY and use a daily label.

**Maps that must NOT gain INTRADAY:** `_HORIZON_BY_STYLE` (`trainer.py:35`; nightly train-all
would train INTRADAY on daily bars), `_STYLE_PROFILES` (`signals.py:1711`; pulls it into the ML
weight policy with a 14-day hold), `_OUTCOME_HOLD_DAYS` / `_SELL_OUTCOME_HOLD_DAYS`
(`signals_shared.py:512, 531`).

**Low:** explicit-set validators already reject it (analytics regexes, paper-portfolio create,
`signal_alerts` 4-set); frontend unions, arrays and switches all have defaults.

**Unrelated pre-existing bug found by the audit:** `analytics.py:584-585` — the regex admits
GROWTH but `_default_max_hold` has no GROWTH key, so `/trade_performance?horizon=GROWTH`
without `max_hold_days` raises `KeyError`. Recorded, not fixed here.

## 5. Summary of reuse decisions

| Component | Decision |
|---|---|
| `market_calendar.session_bounds`, early closes, coverage stop | reuse |
| HK sessions | gap → **Intraday v1 US-only**, HK refused as `unsupported_market` |
| `rzy_data.prepare_bars` (open/close, grid, session filter, gaps, staleness, blockers) | reuse through the contract (US only) |
| `aggregate_completed_bars` / native-vs-derived selection | wrap: record the source; forming 15m never used as context |
| 2-minute settle | wrap as a named contract constant; the cutoff is not availability evidence |
| Provider availability | gap: Stage C records `input_read_at` only; exact-version first availability stays unknown (design §4, CDR-01); provider publish time `unknown` |
| Detection vs data cutoff (F09 clock pattern) | reuse the pattern |
| Entry expiry, max hold, session-end exit | gap: defined by the contract |
| Revisions (`bar_provenance`, `bar_revisions`) | wrap: setups immutable; revised inputs flagged, never edited |
| Execution readiness blockers | reuse unchanged; Stage C is research-only |
| `_check_short_intraday_triggers` | not reused (daily SHORT); clock defects recorded separately |
| Daily tables (`signals`, `signal_outcomes`) | **not used for Intraday** (see decision below) |
| F03 occurrence/snapshot pattern | reuse the pattern in new Intraday tables |

### Persistence decision for the design

> **Superseded (2026-10-29).** Item 1 below (adding `INTRADAY` to `signalhorizon`) was rejected
> in review: Intraday uses separate storage and the shared enum stays unchanged. See the
> [contract design](2026-10-29-intraday-stage-c-contract-design.md) §1. Items 2–4 stand, except
> that `bar_provenance.first_written_at` is **not** used as availability evidence (design §4,
> review CDR-01).

The inventory supports this shape, which the Stage C design will specify:

1. ~~Explicit enum migration~~ — **withdrawn.** `INTRADAY` is **not** added to `signalhorizon`;
   the shared enum stays unchanged (contract design §1).
2. **Intraday rows never enter the daily tables.** They go to new tables (setups/occurrences,
   immutable plan snapshots, forecast and managed-trade outcomes). The `signals` upsert and the
   outcome writers refuse `INTRADAY` explicitly. This is what makes the high-risk readers above
   safe by construction rather than by each one remembering a filter.
3. **Compatible readers before producers**: all 12 backends rebuilt with the new enum value;
   the seven high-risk readers given an explicit daily-horizon filter anyway (defence in depth
   against a future writer); unknown-style fallbacks changed to reject rather than return all or
   SWING; then, separately, a shadow producer. Production generation stays disabled in Stage C.
4. A shared registry names the four **daily** horizons and INTRADAY separately, so no daily map
   or loop can pick INTRADAY up by iterating the enum.
