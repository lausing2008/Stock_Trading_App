# DEEP SYSTEM AUDIT — Architecture, Technical Correctness, Trading Logic

**Date:** 2026-09-06
**Scope:** Full-system audit covering architecture/design and trading logic, at the user's request.
**Method:** Multi-agent parallel audit across independent domains. Every finding below was required
to cite file:line and a concrete failure scenario. The two highest-severity findings were
independently re-verified by hand before being recorded here.

> **Audit was cut short by an API spend limit.** Roughly half the planned agents completed; the rest
> terminated mid-run. Domains marked INCOMPLETE below were not finished and should be re-run.
> Partial findings from terminated agents are recorded separately at the end, clearly labeled as
> unverified.

---

## CONTEXT: why this audit was scoped the way it was

This system was audited exhaustively 2026-09-02 → 2026-09-05 (a 6-part platform audit plus a
17-document deep-dive cycle). That cycle concluded: *"Every open question about whether this system
has edge is now data-blocked, not build-blocked"* — recommending ~3 weeks of measurement rather than
building (`docs/2026-09-05/WHAT_TO_DO_NEXT.md`).

Consequently this audit deliberately targeted **what those audits did not cover**:
- Architecture and design (never had a dedicated pass — prior audits were domain-by-domain on
  trading logic)
- Technical/engineering correctness (concurrency, sessions, locks, silent failures)
- The last 48 hours of shipped changes (never reviewed as a whole)
- **Verification that prior audit claims still hold in current code** — which is where the single
  most important finding came from.

---

## ⚠ CRITICAL FINDING — the anti-chasing filter is not actually filtering

**Severity: HIGH. Production-reachable today. Independently hand-verified.**

`services/market-data/src/services/scheduler.py:6709`

```python
if prev == current:
    if current == "BUY":
        all_pass, _tier, passed, failed = _is_conviction_buy(...)   # computed
        _store_conviction(alert.symbol, style, True, passed, failed, current, ...)
        #                                     ^^^^ hardcoded — all_pass discarded
```

`_store_conviction`'s 3rd positional parameter is `sent: bool` (`scheduler.py:301`). The stable-BUY
refresh path computes `all_pass` and then **throws it away**, always writing `sent=True`. The
transition path 55 lines later does it correctly (`scheduler.py:6764` passes `False`).

Both consumers gate strictly on `sent is False`:
- `services/decision-engine/src/api/core/hard_rejects.py:599`
- `services/market-data/src/services/paper_trading_engine.py:5778`

### Why this matters more than it first appears

`check_signal_alerts()` runs 5×/day. A stock that has been BUY for more than one cycle takes the
`prev == current` branch on **every subsequent cycle**. The correct `sent=False` write only happens
on the single cycle where a signal *transitions* into BUY — and is overwritten by the very next
cycle. **For any BUY older than one refresh cycle, the conviction gate is effectively disabled for
both consumers.**

Blast radius is wider than the anti-chasing filter alone: `failed=failed` is written faithfully, so
the diagnostic payload looks correct while the boolean the gates actually read is wrong. Every layer
inside `_is_conviction_buy()` is nullified identically — including `AUD232-BUY-FROM-TOP`
(`scheduler.py:995-1001`), the fix that `PHASE_B2_INVERSION_ROOT_CAUSE_FOUND.md` identifies as the
resolution of the confidence inversion. (That fix also lives in signal-engine's generation path, so
this does not fully revert it — but its conviction-gate enforcement half is nullified.)

### Consequence for the 3-week measurement plan

`docs/2026-09-05/TRADING_SYSTEM_AUDIT.md` §E.4-P1 states the anti-chasing filter is "SHIPPED
TODAY … live now." **That claim should be treated as unverified.** Any measurement of that filter's
effect taken since 2026-09-05 needs re-checking against whether it was actually enforcing.

### Fix
One token: `scheduler.py:6709`, `True` → `all_pass`. Highest impact-per-risk in this report.

---

## HIGH — `AUD-CHASE-ROC10-PAPERPORT` was ported onto a dead code path

**Severity: MEDIUM-HIGH. Production-reachable.**

Commit `2c6112d` (2026-09-05) added the ROC10 guard to `_should_enter()`
(`paper_trading_engine.py:2026-2032`). But `_should_enter()` is **not** the authoritative entry gate:

- `paper_trading_engine.py:645` — `_DEFAULT_CONFIG` sets `"decision_engine_mode": "primary"`
- `paper_trading_engine.py:5858-5872` — under `"primary"`, `should_enter/score/notes` come from
  `de_result`; `se_result` feeds only `_record_de_shadow_comparison()`
- `gate_harness.py:575` states it plainly: *"`_should_enter()` (the DE-outage fallback gate) —
  `decision_engine_mode='primary'` is the live default"*

`grep -rn "roc_10\|roc10" services/decision-engine/` returns **zero matches**. DE has no ROC10 gate.

**Verified NOT a problem:** the mirrored constants are correctly in sync — `scheduler.py:781`
`_MAX_ROC10_FOR_ENTRY = 10.0` and `paper_trading_engine.py:1855` `_MAX_ROC10_FOR_ENTRY_PAPER = 10.0`,
same value, same `>=` operator, both fail open on `None`. The problem is **placement, not
calibration.**

**Interaction with the critical finding above:** the one path that *would* have caught this — the
`conv_gate` Redis check at `paper_trading_engine.py:5768-5785`, which runs before the DE call and
carries ROC10 transitively — is precisely the path the `sent=True` bug disables. **With both defects
present, ROC10 has no live enforcement point on the primary paper-trading path at all.**

Recommendation: fix the `sent=True` bug first, then measure before adding a second gate — it may be
sufficient on its own.

---

## HIGH — `current_cash` read-modify-write race (money corruption, silent)

**Severity: HIGH. Reachable today through normal UI use. Hand-verified.**

`grep -rn "with_for_update" services/ shared/` returns **zero results**. No row locking anywhere, and
no non-default isolation level is set on any engine or session.

`current_cash` is mutated read-modify-write **in Python** at:
- Scheduler: `paper_trading_engine.py:3027, 3130, 3158, 4458, 5403`
- Conditional orders: `conditional_orders.py:386, 452`
- **HTTP request handlers:** `api/paper_portfolio.py:500, 1216, 1255`

The `_PAPER_TRADING_LOCK` serializes scheduler-vs-scheduler only. **A user HTTP request is covered by
no lock at all.**

**Failure scenario:** user clicks "exit trade" (`manual_exit_trade`, line 511) at the same moment
`paper_trading_step` processes an exit. Both read `current_cash = 10000`. The handler computes
`10000 + 500` and commits; the engine computes `10000 + 300` and commits. Final value is `10300`
instead of `10800` — **$500 of exit proceeds silently vanishes, with no error logged.** Read-committed
isolation does not prevent this because the arithmetic happens in Python, not SQL.

**Fix:** `with_for_update()` on the `PaperPortfolio` row in every cash-mutating path.

---

## HIGH — deploy-drift checker has a gap exactly where the incidents happened

**Severity: HIGH.**

`scripts/check_deploy_drift.sh` (shipped 2026-09-05) hashes `*.py` under **`/app/src` only**.
`grep -n "shared" scripts/check_deploy_drift.sh` matches on **line 9 — inside a comment** describing
the decision-engine stale-`models.py` incident that motivated the script. The script body never reads
`/app/shared`.

This matters because `shared/db/` is the **more dangerous** surface: a stale `/app/src` usually just
runs old logic, whereas a stale `shared/db/models.py` **crash-loops on next restart**. On 2026-09-04,
9 of 10 backend containers were confirmed stale on `shared/db/` — none had crashed yet; all carried
an identical latent crash-on-next-restart.

**A monitoring tool with a gap precisely where the documented failures cluster provides false
assurance.** Fix is small: add `/app/shared` to the same `find | sort | xargs cat | md5sum` pipeline
at lines 73-76.

---

## HIGH — schema migration discipline has degraded to four parallel mechanisms

**Severity: HIGH.**

| Mechanism | Status |
|---|---|
| **A. Alembic** | Scaffolded, **never run**. 4 version files; `alembic_version` pinned at `003`; `004` applied by hand and back-documented. Zero non-scaffold invocations anywhere. **4 migration files against 110 commits touching `models.py`.** |
| **B. `_run_migrations()`** | The live path. 515 lines of inline idempotent DDL (`shared/db/session.py:49-563`), called by only 3 of 12 services. Well-commented, but unordered, append-only, no version tracking, re-executes every statement on every boot — including data mutations like a `DELETE FROM earnings_events` dedup (`session.py:460-465`). |
| **C. `scripts/migrations/*.sql`** | 13 files; `run_migrations.sh` executes **only 8**. Five orphans, including **`012_add_broker_fill_confirmed_to_paper_trades.sql`** — the very file CLAUDE.md cites as the model for a properly-migrated column. |
| **D. Loose SQL + prose** | 4 `scripts/migrate_*.sql` files with no runner, plus ≥20 docs instructing a manual `ALTER TABLE`. |

### Drift, quantified

Parsing all 943 columns in `models.py` against every mechanism, then using `git blame` to isolate
columns added to **already-existing** tables (where `create_all()` provably cannot help):

**37 columns have no migration in any mechanism.** Discounting 15 that are covered but invisible
(see below), **~20+ columns on pre-existing tables rest entirely on someone having run a manual
`ALTER TABLE` at the time.** Highest-risk recent ones:

| Date | Column | Table created |
|---|---|---|
| 2026-09-05 | `squeeze_alert_outcomes.gex_corroborated` | 2026-08-15 |
| 2026-09-05 | `squeeze_alert_outcomes.gex_nearest_level_pct` | 2026-08-15 |
| 2026-09-04 | `insider_transactions.is_10b5_1` | 2026-06-21 |
| 2026-09-04 | `earnings_events.management_tone` | 2026-06-21 |
| 2026-09-01 | `prebreakout_alert_outcomes.return_3d` / `is_correct_3d` / `evaluated_at` | 2026-08-15 |
| 2026-08-18 | `fundamentals.target_price`, `fundamentals_snapshot.target_price` | 2026-06-16 / 06-30 |
| 2026-06-01 | `rankings.rs_score` | 2026-04-17 |

**Reviewability hazard:** `session.py:511-525` covers 15 columns via f-string interpolation
(`f"...ADD COLUMN IF NOT EXISTS price_{_w}d FLOAT"`), so the literal column names never appear in
source. Correct at runtime, but **invisible to any textual audit** — including to a maintainer
grepping a column name to check whether it was migrated.

**Production schema cannot be reconstructed from the repo.** No applied-migration ledger exists
(`grep` for `schema_migrations|migration_history|applied_migrations` → zero hits), no schema dump,
and `alembic_version` is known-wrong. Schema is knowable only by inspecting production.

Note: the *decision* to abandon Alembic is a **defensible tradeoff** for a single-operator
deployment, and `docs/SHARED_LAYER.md:163-176` already documents it honestly. The finding is that the
situation has **degraded** since that doc was written — it describes two mechanisms; there are now
four.

---

## MEDIUM — falsy-zero bug class: 5 new instances, clustered in a never-swept file

This bug class has bitten **four times** previously. A sweep of ~165 `or`-default candidates and 22
`if not <numeric>` guards across 199 files found 5 new confirmed instances — the strongest cluster in
`services/market-data/src/services/rl_agent.py`, a file no prior fix ever touched.

### Finding 1 — BLOCKER: `rl_agent.py:172-176`

```python
rr     = float(t.rr_ratio_at_entry  or 2.0)
conf   = float(t.confidence_at_entry or 50.0)
score  = float(t.entry_score         or 3)
kscore = float(t.kscore_at_entry     or 50.0)
```

**Decisive proof:** the training query at lines 236-243 *already* filters
`rr_ratio_at_entry.is_not(None)`, `confidence_at_entry.is_not(None)`, `entry_score.is_not(None)`.
NULL is **impossible** by the time these lines run — so for three of four fallbacks, `or` can *only*
fire on a genuine 0. They serve no purpose and are pure data corruption.

All three zeros are reachable: `confidence = round(abs(fused - 0.5) * 200, 2)` yields **0.0** at a
fused probability of exactly 0.5 (maximum uncertainty → rewritten to 50.0); `entry_score = 0` is the
accumulator's initializer; K-Score 0.0 is a valid clipped value — *the same value already fixed once
as `T247-MARKETDATA-KSCORE-FALSY`*.

**Impact:** a trade entered at confidence 0.0 that lost 20% teaches the Ridge Q-function that
*mediocre* conditions (50/3/50) produce −20% returns. That policy adjusts live entry scores ±1 at
`paper_trading_engine.py:2187-2200`, so corrupted labels propagate into real entry decisions.

**Asymmetry proving the miss:** the *inference* site at line 2193 correctly uses
`kscore if kscore is not None else 50.0`. Only the training path was left on the falsy pattern.

### Findings 2-5 (abbreviated)

| # | Location | Issue | Sev |
|---|---|---|---|
| 2 | `paper_trading_engine.py:5509` | `confidence_at_entry or 50.0` feeds a **subtraction** (`signal_confidence_delta`), so a true 0.0 shifts the ML feature 50 points and can invert the delta's sign, biasing the pyramiding gate | HIGH |
| 3 | `signal-engine/src/api/outcomes.py:312` | **Double** `or` collapse: `overall_score=0.0 → None`. 0 maps to `"SELL"` (strongest bearish verdict), made indistinguishable from a research-engine outage — permanently, since backfill only retries `research_rec is None`. Producer side; consumer side was already fixed twice | MEDIUM |
| 4 | `scheduler.py:917` | `stoch_rsi_k or 50` — 0 is the *most meaningful* extreme (maximally oversold), reported as neutral | MEDIUM |
| 5 | `signal-engine/src/api/analytics.py:1057` | `(act_wr or 0) - (inact_wr or 0)` conflates "no samples" with a genuine 0.0% win rate, hiding the worst-performing filters on the dashboard | LOW |

---

## MEDIUM — Redis lock release: the T232-PT5 fix was never propagated

`_PAPER_TRADING_LOCK_KEY` was hardened (T232-PT5, `paper_trading_engine.py:1316-1329`) with a token
compare-and-delete Lua script. Its own comment states the problem precisely: *"If run A's lock
expires (TTL) and run B acquires a new lock, then run A finally-finishes late, run A's `finally`
block deletes the key — which is now run B's lock... This cascades."*

**That fix was never propagated to the other 13 locks**, which all still do unconditional
`_get_redis().delete(KEY)` with no ownership check:
`scheduler.py:2920, 3389, 3683, 4108, 4468, 4780, 5104, 5368, 5542, 6235, 6453, 7079, 7432`.

**Additionally, 5 locks are never released at all:**
- `conditional_orders.py:530` — acquires with `ex=55`, file ends at 597 with **no `finally`**. This
  job mutates `portfolio.current_cash` (lines 386, 452), so two concurrent runs could double-credit
  exit proceeds.
- `scheduler.py:1532, 1624, 1723, 1858` (earnings-reaction, macro-reaction, earnings-impact,
  early-earnings-news) — each has exactly 2 references to its lock key (definition + `set`), no
  `delete` anywhere. The lock always survives its full 55s TTL against a 60s interval, leaving a 5s
  acquisition window; a slightly-late fire lands inside the still-held TTL and the tick is silently
  skipped.

**Important caveat:** all of these are **masked today by APScheduler's `max_instances=1`, as long as
market-data runs as a single process.** They become live bugs the moment that service is scaled to a
second container — which is exactly the scenario these Redis locks exist to handle.

---

## MEDIUM — scheduler: 97 jobs across 3 services, with misfire gaps

**Job inventory (documentation is substantially wrong):**

| Service | Scheduler type | Jobs |
|---|---|---|
| market-data | `BackgroundScheduler` | 69 registrations → **78 live jobs** |
| event-intelligence | `AsyncIOScheduler` | 16 |
| news-intelligence | `AsyncIOScheduler` | 3 + a long-lived Alpaca WebSocket task |

`docs/Schedule.md:3-4` claims *"All scheduled jobs are registered in market-data"* and documents
"15 jobs total" — against ~97 actual across 3 services. `scheduler.py:12255` logs a hardcoded
`jobs=20` against ~78 actual.

### The misfire mechanism (re-verified)

APScheduler 3.10.4's **scheduler-level** default is `misfire_grace_time=1` (one second) — *not*
inherited from the file's own `_JOB_DEFAULTS` (`scheduler.py:11397`, which sets 60). Jobs registered
by spreading `**_JOB_DEFAULTS` get 60s; jobs passing `max_instances=1, coalesce=True` inline get 1s.
With `max_instances=1`, a still-running execution makes the next tick wait; when a slot frees,
APScheduler discards the run if the delay exceeds the grace window. With a 1s window and a ~15s job
body, essentially every overlapping tick is dropped — the job stays registered and *looks healthy*
while producing no executions.

**64 of 69 registrations are guarded. 5 are not.** The important one:

### `gamma_unwind_alert_check` (`scheduler.py:11744`) — live latent instance

It is by a wide margin the **longest-running job in the file**: up to 40 symbols × (a yfinance
options-chain fetch — described at `scheduler.py:2131` as *"the most rate-limit-fragile call this app
makes"* — plus an explicit `time.sleep(1.0)` at `:4349`), then a **second** per-symbol loop making
Unusual Whales calls at `:4385`. **Floor runtime ≥40s of pure sleep; realistically 60-120s+** — i.e.
4-8× longer than the ~15s jobs confirmed silently dead in production.

It was excluded from the AUD-MISFIREGRACE fix on the reasoning that *"a 1-second default grace window
is a materially different risk at that cadence [hours=4]"* — but **the trigger condition is execution
duration exceeding the grace window; cadence only determines how often you roll the dice.** The
exclusion is now locked in by a *passing test* (`test_scheduler_minute_job_misfire_grace.py:87-92`)
that asserts the guard's absence.

Compounding: this job has **no liveness check either** — unguarded *and* unmonitored, so a silent
stall is doubly invisible.

### Other misfire gaps
- `avg_volume_cache_refresh` (`:12031`) — full-universe `yf.download`; the code immediately below its
  registration documents a *confirmed prior incident* where this cache going empty *"silently
  break[s] every RVOL read app-wide … with zero visible error."*
- **event-intelligence (16 jobs) and news-intelligence (3 jobs) have ZERO misfire configuration on
  any job** — the fix never crossed the service boundary. Highest-risk: news-intelligence's three
  minute-cadence network pollers contending for a **3-worker** ThreadPoolExecutor, and
  event-intelligence's FOMC/CPI release-day pollers (`minute="*"`, `minute="*/2"`) which fire
  precisely when a dropped tick costs most.

### Other scheduler findings
- **No catch-up on restart.** `MemoryJobStore` + `coalesce=True` means a job whose trigger passed
  while the container was down is **silently skipped entirely**. A restart spanning 16:30 ET means
  `us_post_close` — the day's final bar confirmation *and* ML retrain — never runs until the next
  weekday. Only 3 targeted guards exist (`_avg_volume_startup_check`, `signal_alert_startup`,
  `AUD263-TUNEALL-STALE-GUARD`); the pattern is proven but not generalized.
- **`_record_job_status()` is opt-in per job** — no `add_listener(EVENT_JOB_ERROR|EVENT_JOB_EXECUTED)`
  backstop. This gap has recurred twice (AUD266 for 5 jobs, then `check_conditional_orders`). A
  single listener would make it structural.
- **`scheduler_jobs` is a phantom table** — referenced by two code comments (`:11371`, `:12084`)
  claiming the purge deletes from it. No such model or table exists; the real trail is Redis with
  self-purging 14-day TTLs. Comments are wrong; the design is sound.
- **event-intelligence has genuine DST drift** — 13 of 16 jobs use bare `"cron"` inheriting UTC while
  3 use explicit `America/New_York`, so their relative ordering shifts an hour each DST transition.
  `EI-F10` documents a previously-fixed bug of exactly this shape; the invariant now holds only by
  coincidence of anchoring.
- **`start_scheduler()` partial-failure path**: all 69 `add_job()` calls run before
  `_scheduler.start()` with no try/except. An exception in any one registration aborts mid-way,
  leaving `_scheduler` non-`None` but never started — and the idempotency guard at `:11385` then
  makes every retry a silent no-op. The app boots and serves HTTP with **zero jobs running**, the
  only symptom being an absent log line.

---

## MEDIUM — decision-engine gate divergences (new, not in the dual-scorer debt doc)

| Gate | `_should_enter()` | DE `check_hard_rejects()` | Impact |
|---|---|---|---|
| `max_entry_gap_pct` | per-style: SWING **0.03** / GROWTH 0.04 / LONG **0.05** | always **0.04** | Not in `_ENTRY_GATE_KEYS` (`paper_trading_engine.py:868`) nor in `config_overrides`, so DE silently uses its own default for every style |
| gap + `volume_z>=1.5` half-gap branch | present (`:2005-2008`) | **absent** | The `AUD-GAPCHASE-EARNINGSVOL` volume-confirmed branch was never ported; `_reasons` already carries `volume_z` at `hard_rejects.py:315`, so it's a free port |
| `roc_10` | present but on dead path | **absent entirely** | See the HIGH finding above |

**SWING is the concern:** it's the only positive-expectancy book (+0.59%, n=40) per the prior audit,
and DE loosens its chase filter from 3% to 4%. Concrete: `AAPL` SWING, `last_price=200.00`,
`live_price=207.00` → gap 3.5%. `_should_enter()` rejects at 0.03; DE passes at 0.04; under
`"primary"`, DE wins and the trade opens.

**Caveat consistent with the 09-05 guidance:** these two change *which trades open* and should be
measured, not assumed — that cycle documented three findings that reversed when samples widened.

### Verified CORRECT (no action)
- **T196 anti-chasing gate live-bar contamination is FIXED** — `paper_trading_engine.py:5256-5264`
  (`AUD-LIVEBAR-T196`) caps the reference bar at the last settled close via
  `_ref_cutoff = min(_sig_date, date.today() - timedelta(days=1))`. The prior audit's
  characterization is **no longer true**. DE inherits the hardened value via `sig_ref_price`.
- **`sizer.py` — no defect**, verified by numeric reproduction: equity=0 → no ZeroDivisionError;
  stop>price → no negative shares (T237-DE1 floor at `sizer.py:106`); stop==price → no div-by-zero;
  penny stock → capped by `max_pos_value`, not oversized. Division by zero on stop distance is
  *impossible* (`stop_dist = max(live_price - stop_price, live_price * 0.01)` with `live_price > 0`
  enforced at `routes.py:83-84`).
- **No contradictory gate pairs** among the 31 hard rejects. One dead-but-harmless branch: gate 14's
  `risk_off` R:R stiffening is unreachable because gate 15 hard-blocks `risk_off` 11 lines later —
  deliberate layering, not a bug.

---

## MEDIUM — `shared/` coupling and docs

- **10 of 12 services must be redeployed for any `models.py` change.** `shared/db/__init__.py:77`
  imports session unconditionally, so `from db import X` eagerly constructs a SQLAlchemy engine.
  `technical-analysis` pulls in all 68 models and an engine for **one import line**
  (`routes.py:10`).
- **api-gateway and portfolio-optimizer are cleanly decoupled** (zero DB imports) — though
  portfolio-optimizer still ships `sqlalchemy` + `psycopg2` in requirements despite having no DB
  code, which misleads dependency audits.
- **`docs/SHARED_LAYER.md` is 43% understated** — says "39 tables (+7 enums)"; reality is **68 tables
  and 8 enums**, missing 29 tables and the `UserTier` enum. Its migration analysis (§5), by contrast,
  is accurate.
- **No version marker anywhere** — all 12 services return `version: "0.1.0"` from `/health`
  unconditionally. No schema fingerprint, no compatibility assertion.
- `__pycache__/*.pyc` files are checked into `shared/`, despite
  `docs/incidents/docker-deploy-staleness.md:61` explicitly instructing their removal after a
  `docker cp`.

---

## MEDIUM — DB sessions held across slow network calls

`services/event-intelligence/src/services/earnings.py:537-570` wraps a `for` loop in
`with SessionLocal() as s:` where each iteration awaits an Unusual Whales HTTP call *and* an
Anthropic API call (`timeout=15` at line 239) before committing.

**Failure scenario:** 40 pending earnings events × up to 15s ≈ **10 minutes** holding 1 of only 15
available connections (`pool_size=5, max_overflow=10`). Same pattern at `macro_reaction.py:238`,
`earnings.py:757, 888`. Concurrent long-holds plus request traffic can exhaust the pool.

---

## LOW-MEDIUM — Redis discipline (otherwise excellent)

**One real defect:** `scheduler.py:284-296` does read-modify-write on
`position_scaling_gate:promotion_history` — **the identical bug already fixed** for its sibling
(`T247-MLPREDICTION-PROMOTIONHISTORY-RACE`, `meta_trainer.py:473-481`, converted to atomic
`RPUSH`/`LTRIM`/`EXPIRE`). Confirmed known: `admin.py:1398-1405` documents the divergence in its own
docstring and made the *reader* bidirectional to tolerate the unfixed writer.

**The highest-value hardening in this section:** `stockai:admin:*` (~16 keys) holds **all** LLM/data
provider API keys and feature flags with **no DB mirror and no backup** — there is no settings table
in `models.py` (verified across all 60+ `__tablename__` entries). AOF persistence makes this
acceptable, but an accidental `FLUSHALL`, a lost `redisdata` volume, or a host migration silently
un-configures every LLM feature and paid data provider — with **no error**, because every reader is
fail-open by design (`ai_keys.py:28-39` returns `""` on failure). This failure mode already occurred
once via a different route (2026-09-04 `registry.py` incident).

### Confirmed clean (initial suspicions disproven)
- **Zero unbounded TTL-less keys.** Every per-symbol/user/date/trade/portfolio key uses `setex`,
  `set(nx=True, ex=...)`, or an immediate `expire()`. The handful of no-TTL writes are all to fixed,
  enumerable key sets. Verified across all 12 services.
- `--maxmemory-policy volatile-lru` evicts only TTL'd keys, so every no-TTL key is eviction-immune
  **by construction** — correct pairing.
- All 20+ locks use `set(key, token, nx=True, ex=TTL)` with cadence-tuned TTLs. No lock is TTL-less.
- All 9 `incr` sites apply a TTL; 7 use the `if r.ttl(key) == -1` idiom so a steady stream doesn't
  perpetually reset the window — a subtle detail done right.
- All 4 Redis lists are `LTRIM`-bounded at 2000 entries.
- The no-TTL `_mark_tuned()` marker (`signals_shared.py:86-93`) is **deliberate** —
  `AUD263-TUNED-PARAMS-SILENTLY-REVERT-ON-TTL` requires it to outlive the TTL'd value key.

---

## VERIFIED HEALTHY

- **Zero deploy drift across all 12 services; zero restarts** (measured via
  `scripts/check_deploy_drift.sh`, 2026-09-06) — though note the `/app/shared` gap above means this
  clean result covers only half the risk surface.
- **No session leaks.** All 56 `SessionLocal()` sites in `scheduler.py` use `with`; both non-`with`
  sites in `paper_trading_engine.py` use correct `try/finally` guarded by `own_session`. **210** uses
  of `Depends(get_session)` and **zero** raw `SessionLocal()` in any `api/` module.
- **No `max_instances > 1` anywhere** — the failure mode is silent skipping, never concurrent
  double-writes. Deliberate and correct.
- **Excellent per-stage isolation** in `_refresh_market()` (`:690-736`) — each of 4 stages has its own
  try/except; ingest failure still lets alerts and paper trading run against the last good bar.
- **Deliberate fail-open vs fail-closed split**: most alert jobs fail open (duplicate email preferred
  over a lost alert); `check_conditional_orders` inverts to fail-closed because it is
  *"real-money-adjacent"* (`scheduler.py:11728-11730`). A genuinely well-made distinction.
- **Every market-data cron carries an explicit IANA timezone** — verified programmatically, zero rely
  on the UTC default. No DST risk in market-data.
- **No vendored/divergent shared code** — zero services define their own `Base`.
- **The `AUD-IGNITION-NEVERFIRES` instinct is exemplary**: on discovering a job that had fired zero
  times while reporting `status=ok`, the response was to add four per-rejection-reason gauges because
  *"'correctly found nothing' and 'silently broken' are indistinguishable from outside."*

---

## ROUND 2 — resumed domains (3 of 9 complete)

The killed agents were **not resumable** — they died with their context, and no partial output was
persisted. Re-run sequentially, each seeded with the partial signal it died on. Findings below are
NEW relative to everything above.

### R2-1 — VERIFIED DEFECT (P1): the 17:00 paper-portfolio digest has never run, once

`services/market-data/src/services/scheduler.py:11251-11252`

```python
from ..db import SessionLocal              # → src.db, which does not exist
from ..db.models import User, PaperPortfolio, PaperTrade
```

`db` is a **top-level** package from `shared/` (`PYTHONPATH=/app/shared:/app`). The other **11**
`db` imports in this same 12,255-line file all correctly use `from db import`. Only lines
11251, 11252, and 11295 use `..db`.

**Proven by direct in-container execution:** `ModuleNotFoundError: No module named 'src.db'`.
These are the function's **first executable statements — before the `try:` at 11274** — so it is an
unguarded crash, not a degradation. Registered at `:11636` on `CronTrigger(hour=17, minute=0,
day_of_week="mon-fri")`; prod logs show repeated registration and **zero executions**.

Consequence worth noting: the `AUD301-PAPERPORTFOLIODIGEST-SENDLOOP` hardening documented in its own
docstring at 11253-11271 **has never executed** — that fix was written against unreachable code.
Line 11294's `from ..api.paper_portfolio import ...` is correct, which explains the error: the author
read `..` as "the src package" without accounting for `db` living in `shared/`.

### R2-2 — VERIFIED DEFECT (P2): Bug Class 1 resolved — the stale entry-weights cache is the SAME wrong-import bug

`services/market-data/src/api/paper_portfolio.py:2230` — `from .paper_trading_engine import
reload_entry_weights` inside a bare `except: pass`. The engine is in `src/services/`, not `src/api/`.
The same file uses the correct `..services.paper_trading_engine` **17 times**, including the exact
sibling pattern at `:2331`. Introduced 2026-06-19 (`16fdcfb`); no test covers the reload path.

`_load_entry_weights()` (`paper_trading_engine.py:490`) caches into a module global with **no TTL** —
process-lifetime. `_should_enter()` reads it at `:2370` to gate every paper-trade entry, in the same
process as the scheduler. **Second-order:** decision-engine's `_get_entry_weights()`
(`aggregator.py:304`) has a correct 15-min TTL, but it fetches `/stocks/entry-weights`, which
delegates to that same never-invalidated cache (`routes.py:633`) — so DE's TTL is defeated and it
re-fetches identical stale data forever.

**Latent today, armed for later:** `/data/models/entry_weights.json` does not exist in prod and only
**37** eligible closed trades exist against a `_MIN_CALIBRATION_TRADES = 100` floor. But
`_load_entry_weights()` caches `{}` permanently on a missing file, so **even the first successful
calibration will not take effect until a restart** — while the endpoint returns HTTP 200 with the new
weights.

### R2-3 — VERIFIED DEFECT (P2, security): JWT logout revocation is a permanent no-op in 3 services

`redis` is undeclared in `strategy-engine`, `technical-analysis`, and `portfolio-optimizer`
requirements. **Verified live:** all three raise `ModuleNotFoundError: No module named 'redis'`.
`shared/common/redis_client.py:20` defers the import *inside* the function, so containers boot clean.

`_check_blacklist()` (`shared/common/jwt_auth.py:28`) → raises → bare `except` at `:34` → falls back
to `_BLACKLIST_MEM`, which is **only written on a successful Redis read** (`:30`). It is therefore
permanently empty and always returns `False`. A logged-out but unexpired JWT is still accepted on
`strategy-engine/src/api/routes.py:41,55,67,81,118,231,265,299` and
`portfolio-optimizer/src/api/routes.py:121`, `risk.py:210,330,401`.

**Severity genuinely reduced:** api-gateway is the sole external ingress and runs its own independent
`_is_blacklisted()` (`proxy.py:127`) with working redis, so internet-facing revoked tokens are
correctly rejected. The gap is real only for direct Docker-network calls. Note `jwt_auth.py:5-6`
explicitly comments that `jose` was hoisted to module level to prevent exactly this class — the
`redis` half never got the same treatment, *because a lazy import fails silently instead of loudly.*

### R2-4 — VERIFIED DEFECT (P3): `optuna` missing from market-data

`paper_portfolio.py:1915`, function-body import in `try/except ImportError`. Verified absent in
`stockai-market-data-1`; present (3.6.1) in ml-prediction — likely how it was missed. `POST
/paper-portfolio/tune-params` returns `{"status":"started"}` then errors in the background;
`is_tuned: False` forever. Paper-trade `stop_pct`/`tp_pct`/`max_hold_days` have **never** been
Optuna-tuned per style.

### R2-5 — VERIFIED DEFECT (P3, loaded gun): `boto3` missing, entire SES email path dead

`email_service.py:59`, `import boto3` inside `_send_ses()`. Verified absent in prod. Masked today by
`EMAIL_PROVIDER=smtp`. If ever flipped to `ses`, **every** email silently fails — the `ImportError`
is swallowed by the broad `except` at `:102`, killing ~25 alert call sites. The code already has
Gmail-quota-exceeded handling (`_QUOTA_MARKERS`), making SES the natural failover someone reaches for
under pressure — the worst moment to discover it is missing.

### R2-6 — VERIFIED DEFECT (P1, LIVE): bearish pillars are corrupted AND the gate that consumes them is armed

Two independent defects in the bearish pillar computation, plus a production config that makes them
live rather than latent.

**(a) `pb_volume` is active in 3 of 4 states** — `signals.py:1479-1485`. `obv_bear_signal = not
obv_trend_bullish`, but `vol_z_signal` (`:1378`, `_vz > 0.5`) is reused **unchanged** from the
bullish pillar — pure magnitude, no sign. Truth table:

| obv_trend_bullish | vol_z>0.5 | pb_volume | active |
|---|---|---|---|
| True | True | 0.6 | **yes** ← textbook accumulation scores as bearish |
| True | False | 0.0 | no |
| False | True | 1.0 | yes |
| False | **False** | **0.6** | **yes** ← *no bearish evidence whatsoever* |

**(b) `pb_momentum` peaks at neutral RSI 50** — `signals.py:1457-1462`, `rsi_bear_score = 1.0 if 35 <
rsi_val < 55`. RSI 50 → 1.0; RSI 70 → **0.0**. Momentum-topping stocks — the actual SELL candidates —
score zero on bearish momentum, while neutral stocks score maximum. Not the mirror of `rsi_score`
(`:1346-1352`, peaks 45-65), and the `rsi_val >= 72` overbought kill at `:1368` has no bearish
counterpart.

**(c) The gate is ARMED in production.** The prior agent could not check this (permission classifier
blocked it) and assumed latency. Verified directly against prod Redis:

```
stockai:style_tune:SWING:min_pillars_for_sell = 3   (tuned 2026-08-31)
stockai:style_tune:SHORT:min_pillars_for_sell = 3   (tuned 2026-08-31)
```

`signals.py:2105` reads that exact key at generation time; `:2106-2108` compresses any SELL with
fewer than 3 bearish pillars by 0.70× toward neutral. The gate's own `fused < 0.5` guard is correct,
so it only touches SELL candidates — but it is selecting on a corrupted feature.

**The compounding failure:** `POST /signals/tune_sell_pillars` (`calibration.py:1680`) swept this
same corrupted feature to choose 3. Inflated counts on neutral stocks made pillar counts look
discriminating; deflated counts on RSI-70 stocks made real SELL candidates look weak. The gate now
passes the wrong SELLs and compresses the right ones. The author's comment at `:2096-2099` explicitly
refused to copy the bullish threshold because "the bearish pillars are NOT calibrated to the same
base rate" — that caution was correct, and the tuner then calibrated against corruption instead.

**Also:** any `SignalOutcome.bearish_pillars_active` already backfilled via
`POST /signals/backfill_bearish_pillars` holds corrupted values and needs recomputing after the fix,
not just the forward path.

### R2-7 — VERIFIED DEFECT (P2): missing `fused > 0.5` guards on news-sentiment and RS compression

The T232-SIG5 direction-blind class, at the *apply* site rather than the comparison.

- `signals.py:2276` — news compression has no `and fused > 0.5`. **Every** sibling gate has one
  (`:2189`, `:2200`, `:2322`, `:2404`, `:2590`, `:2599`). Scenario: `news_sentiment=20`, `fused=0.30`
  (clean SELL), `nc[25]=0.75` → `fused = 0.35`. `_SELL_THRESHOLD_FALLBACK = 0.35` and the test at
  `:1949` is `>= sell_t → "WAIT"`. **Strongly negative news converts a SELL into a WAIT.** SWING and
  GROWTH only (SHORT/LONG have `news_compression: None`).
- `signals.py:2303` — RS compression, same missing guard, and worse: both escape hatches are
  bullish-only by construction, so they fire backwards on the SELL side. `rs_absolute_floor` (`:2296`)
  is `stock_20d_ret_pct > 5.0` — on a SELL candidate, a stock *up 5% in 20 days* is exactly the one
  whose SELL you least want to weaken, yet the floor exempts it from compression while the genuinely
  weak laggard gets its SELL muted. Polarity inverted relative to intent. Active for SHORT/SWING/LONG.

**Negative result — the sign checks themselves are correct.** Only negative sentiment compresses;
positive is a no-op. This is not an `abs()` bug. 15 other gates verified correctly direction-aware
(listed in the round-2 agent output); the undirected ones (`adx_compression`, `stale_price`,
`insufficient_history`, `ml_oos_suppressed`, `earnings`) are data-quality penalties where
compressing both sides is correct.

### R2-8 — VERIFIED DEFECT (P2): `cost_basis` inflated when a scale-IN follows a scale-OUT

`paper_trading_engine.py:3010` and the identical copy at `conditional_orders.py:438`:
`_cost_basis = entry * (trade.entry_shares or trade.shares)`. The two factors are weighted over
**different share populations** — `entry_price` blends over *currently held* shares (post-scale-out),
`entry_shares` is *cumulative* including already-sold shares. The higher scale-in price is
retroactively applied to shares sold before that buy happened.

Worked example (100sh @ $100 → sell 33 @ $110 → buy 16.75 @ $120): engine cost basis
`104.00 × 116.75 = $12,142.00`; true capital deployed `$12,010.00`; **inflation $132.00**, exactly
`33 × ($104 − $100)`.

**Direction matters:** the denominator is inflated, so `pct_return` is **understated** — trades look
*worse* than they were. On a realistic SWING config (`partial_tp_pct=0.10` and
`scale_in_trigger2_pct=0.10` **overlap**, so both fire on the same move): reported 8.9698% vs true
9.0045%, understated 0.035 pts. Reachable in a single tick — `paper_trading_step()` at `:6234` runs
`_monitor_positions()` (scale-out) then `_scan_for_entries()` (scale-in). Reaches ML ground truth via
the `SignalOutcome` writeback at `:3054`. **Real but low-severity — technical debt, not a fire.**

**Proven correct, do not re-investigate:** dollar P&L reconciles **exactly** to true cash delta in
every mixed sequence tested; `avg_entry_price` blending (`:5419-5422`) is standard weighted-average
cost basis; commissions are not double-counted; stop recomputation after scaling is correct and
monotonic; `max_position_pct` headroom truncation is correct.

### R2-9 — VERIFIED DEFECT (P2): `total_unrealized_pnl` loses realized scale-out P&L entirely

`api/paper_portfolio.py:270-272`. For an open trade that has scaled out, `t.entry_price` is blended
over *remaining* shares, so the sold tranche's gain sits in `t.realized_pnl` — which is never added to
`total_realized` (`:259` sums only **closed** trades). It appears in **neither** bucket.

100sh @ $100, sold 33 @ $110 (`realized_pnl = $330`), price $110, still open: reported total `$670`,
true economic gain `$1,000` — **$330 missing.** Same omission at `:439`/`:440`. Reporting only; does
not affect trading decisions or `current_cash`.

### R2-10 — VERIFIED DEFECT (P3): conditional `sell_partial` re-arms the organic scale-out

`conditional_orders.py:366-389` — confirmed by grep that neither `PARTIAL1_TAKEN` nor
`PARTIAL2_TAKEN` appears anywhere in the file. A user conditional `sell_partial` shrinks
`trade.shares` but sets no marker, so `_monitor_positions()` (`:3114`) still computes `p1_done =
False` and trims **again**. 100sh → user sells 50% → 50sh → price hits +10% → organic L1 sells 16.5sh
→ 33.5sh, then L2 → 16.75sh. Position cut to 33.5% of original where the design intends one 33% trim.

### R2-11 — VERIFIED DEFECT (P3): `fraction = 1.0` strands a zero-share "open" position

`conditional_orders.py:365` — `max(0.01, min(1.0, fraction))` permits 1.0, setting `trade.shares =
0.0` while `stage` stays `"open"`; `sell_partial` has no close branch. The row still occupies a
`max_positions` slot and blocks re-entry via the `open_symbols` check at `:5322`. Does not crash —
both scale-out blocks guard `shares > 0.01` and the scale-in path's `> 0` guard skips it.

### Round-2 negative results — CLEAN, do not re-audit

- **Service-to-service auth headers — CLEAN.** All 23 files making internal calls pass a Bearer token
  where required. The 2 documented exceptions behave as intended. *Latent risk:* ~19 headerless calls
  in `signals.py` sit in `except: pass` with None fallbacks; their targets are unauthenticated today,
  but putting any behind auth turns all 19 live simultaneously and silently.
- **Router ordering / catch-all shadowing — CLEAN.** Verified two ways: a 363-decorator static pass,
  and dumping the **actual `app.routes` table from all 12 running containers**. Zero shadowing pairs.
  Both prior-incident guards intact. *Fragile-but-fine:* `news.py` is included after `routes.py` with
  the same `/stocks` prefix, so `GET /stocks/market/pulse` survives only by being 2 segments deep
  against `GET /stocks/{symbol}` — the exact BUG233-ROUTERORDER shape, one route addition from
  breaking.
- **Wrong relative imports — only 2 exist repo-wide** (R2-1 and R2-2), both in market-data. The other
  11 services are clean. R2-2 is a direct miss of the `AUD-GAMEPLANBATCH-WRONGIMPORT` fix sweep.
- **AUD232-BUY-FROM-TOP intact** — `stoch_rsi_still_hot` (`:1153-1156`) and `near_recent_high_hot`
  (`:1176`) still computed and still consumed at `scheduler.py:993`/`:998`.
- **No unreachable thresholds in the style profiles**; `_decide_style`'s 4 branches partition the
  range with no gap or overlap; `ml_weight_floor` cannot resurrect an inverse model.
- **No new falsy-zero defects in the scaling arithmetic** — all `or 0.0` fallbacks there are benign
  (fallback equals the falsy value).

---

### R2-12 — VERIFIED DEFECT (P1, user-visible): the weekly Trade Pattern Coach email overstates every return 100×

`services/market-data/src/services/trade_coach.py:104` and `:133`

```python
avg_return_pct = round(sum(returns) / len(returns) * 100, 2)   # returns are ALREADY x100
```

`returns` comes from `paper_trades.pct_return` (`:101`), which the writer already scales:
`paper_trading_engine.py:3018` and `:283` both do `round(total_pnl_pct * 100, 4)`. Multiplying again
gives **100× the true figure**.

**A portfolio averaging +3.2% is emailed as +320.00%.** A `stop_hit` bucket averaging −5.0% renders as
−500.00%.

**Two consumers, both user-facing:**
- `email_service.py:3442` renders `f"{avg_return:+.2f}%"` with no correction (and `:3456` per exit
  reason) — the number appears literally in the weekly email.
- `trade_coach.py:186` interpolates `avg return {r['avg_return_pct']}%` **directly into the Claude
  prompt**, so the LLM reasons about and repeats fabricated 300%+ returns as fact in its prose summary.

**Why it survived:** `avg_giveback_pct_on_winners` (`:147`) is computed from raw prices
(`(peak - exitp) / peak * 100`) and is therefore correctly scaled — it sits in the same email beside
the broken figure, making the inflation look like a formatting quirk. The existing test
(`test_trade_coach_scheduling.py:29`) hardcodes a synthetic `avg_return_pct` and never exercises the
aggregation. The `> 0` / `<= 0` win filters at `:114`/`:140` are sign-only and thus scale-invariant —
correct as-is. **Only the two `* 100` sites are wrong.**

### R2-13 — VERIFIED DEFECT (P1, live now): `check_deploy_drift.sh` is reporting a drifted container as OK

The `/app/shared` gap recorded earlier in this document is not theoretical — it is **actively hiding
real drift right now.** Verified live 2026-09-06:

```
stockai-ranking-engine-1   /app/shared → e2e1137f06bbb0ef0af757a4325852f3   ← DRIFTED
stockai-signal-engine-1    /app/shared → 2c564505b33e3b515b80a6c74bf87f06
stockai-market-data-1      /app/shared → 2c564505b33e3b515b80a6c74bf87f06
(all 11 others match 2c564505...)
```

Per-file, exactly two files are stale in ranking-engine:

| file | ranking-engine | market-data |
|---|---|---|
| `shared/common/ai_keys.py` | `e4ed9b48` | `857edd4b` ← **stale** |
| `shared/db/session.py` | `5f3a14a2` | `83befb2f` ← **stale** |
| `shared/db/models.py` | `99e1889d` | `99e1889d` ✓ |
| `shared/common/jwt_auth.py` | `96bd5f55` | `96bd5f55` ✓ |
| `shared/common/llm_usage.py` | `607f0fb4` | `607f0fb4` ✓ |

And the tool built to catch exactly this says:

```
$ bash scripts/check_deploy_drift.sh ranking-engine
OK    ranking-engine
Checked 1 service(s), 0 drifted/errored.   (exit 0)
```

`models.py` matching is the good news — **no crash-on-restart risk.** But `ai_keys.py` is where the
Unusual Whales and Claude API keys are resolved, and `session.py` carries the 515-line inline
`_run_migrations()` DDL. Tier 348's claim that "all 12 services report OK" was measured through this
blind spot. A false OK is worse than no tool — it converts an unknown into a false assurance.

### R2-14 — VERIFIED DEFECT (P2): LLM spike detector's floor is ~40-160× the real baseline

`scheduler.py:8715` — `_LLM_USAGE_MIN_TOKENS_TO_EVALUATE = 50_000`, with an early return at
`:8769-8772` **before any baseline math**. Measured production hourly totals from `llm_call_log`:
**~250-1,300 tokens/hour**.

**Failure scenario:** a regression reintroducing the `BUG-NEWSCLASSIFY-REPEATCOST` pattern at a slower
rate — 49,000 tokens/hour — is **196× the real baseline** and burns ~1.18M tokens/day (about a fifth
of the incident that motivated building this detector), yet `current_total < 50_000` returns early on
every 15-minute run. It never reaches the multiple calculation. At 49k the computed multiple *would*
be 49.0× against a 5.0× trigger — the check simply never gets there.

The docstring justifies the floor with "baseline=50, current=300 is technically 6× but not a real
incident" — but that is **already fully handled** by `baseline_floor = max(baseline_median, 1000.0)`
at `:8816`, which caps that example at 0.3×. The floor is redundant with the guard that actually
works, and costs a two-orders-of-magnitude blind spot. ~5,000-10,000 would preserve the stated intent.

### R2-15 — VERIFIED DEFECT (P2): two UW dashboard fields display 0 during the exact conditions they exist to reveal

Both are TTL-arithmetic bugs, distinct from the already-documented restart-reset caveat — these fire
on a healthy, never-restarted container.

- **"Yesterday (this app's count)" reads 0 for ~23 of every 24 hours.** `unusual_whales.py:387` sets
  `_CALL_COUNTER_TTL_S = 25 * 3600` on the **first write of that day**, so a key first written at
  00:00:30 UTC expires 01:00:30 the *next* day. Verified live at 18:19 UTC: scanning
  `stockai:metric:uw_calls:*:20260905` returns **zero keys** despite heavy real usage yesterday. The
  panel renders `Yesterday: 0`, which reads as "no usage," not "the data expired." A 49-hour TTL fixes it.
- **"429s (48h)" is a sawtooth, not a rolling window.** `unusual_whales.py:375-382` — `INCR` plus
  expire-only-when-TTL-is-−1 accumulates for 48h from the *first* increment, then vanishes and
  restarts at zero. Verified live: value `109184`, TTL `3021`s — so within ~50 minutes that field
  reads **0** even with 429s continuing at full rate. An admin checking during an active rate-limit
  incident sees "429s (48h): 0". Predates this window (tier 344), but AUD-UWUSAGE put it on a
  dashboard where it now actively misleads, and the new `_incr_call_counter` copies the same idiom.

**Context:** the app's own per-endpoint counters currently sum to **12,704** against UW's
authoritative **20,370** for the same day (~38% undercount). `real_usage` reads the header and is
unaffected — it remains the number to trust.

### R2-16 — VERIFIED DEFECT (P2, latent): live-price cache is delisted-blind, exposing 7 alert scanners

`services/market-data/src/api/routes.py:825` (and the sibling at `:792`):

```python
select(Stock.symbol, Stock.currency).where(Stock.active.is_(True))   # no .delisted filter
```

Correctly-filtered sites pair both conditions — e.g. `scheduler.py:2160`:
`Stock.active.is_(True), Stock.delisted.is_(False)`.

`refresh_live_price_cache()` runs **every minute during market hours** and is the sole writer of
`stockai:live_prices`. Seven alert scanners iterate that blob *instead of* querying the DB, so they
inherit the gap and their own correct DB-side filtering never applies: `check_volume_anomalies`
(`scheduler.py:2748`), `check_short_squeeze_alerts` (`:3101`), `check_squeeze_ignition_alerts`
(`:3466`), `check_prebreakout_alerts` (`:4025`), `check_value_area_breakdown` (`:6127`),
`check_price_alerts` (`:7004`), post-open digest (`:10405`).

**Failure scenario:** a delisted stock's last bar freezes in the cache. `check_volume_anomalies`
divides a frozen numerator by a decaying `stockai:avg_volume` denominator, crosses the threshold, and
emails an abnormal-volume alert every minute for an untradeable stock. `check_short_squeeze_alerts` is
worse — delisted names are typically heavily shorted, so a frozen price plus stale
`short_percent_of_float` keeps it permanently qualifying. Each cycle also wastes a `yf.download()`
slot on a ticker Yahoo already reports missing.

**LATENT, not live:** verified in prod — `SELECT count(*) FROM stocks WHERE delisted` = **0 of 193**,
zero pending delisting signals, zero delisting log lines in 168h. Nothing is misfiring today. It arms
itself the first time the detector confirms a delisting, and the detector is genuinely wired
(`raise_errors=True` → `YFTickerMissingError` → `_record_delisting_signal`). One filter on the cache
writer protects all seven scanners.

**Do NOT "fix" `_symbols_for()` (`scheduler.py:461`).** It is unfiltered *deliberately* — it feeds
daily ingestion, which is what detects delisting in the first place; filtering it would make the flag
permanently unsettable (documented at `:12023`). This is very likely what the killed agent had flagged
as its "one confirmed live scheduler job."

**Survivorship bias unchanged:** `ml-prediction/src/api/routes.py:98,167,333,373,413` correctly use
`or_(Stock.active.is_(True), Stock.delisted.is_(True))` to deliberately include delisted names in
training. With 0 delisted rows that `or_` is currently a no-op, and the comment at `:91` claiming
"nothing sets delisted=True" is now **stale** — `ingestion.py:76` does set it.

### Round-2 negative results, part 2 — CLEAN, do not re-audit

- **Timezone consistency in the new UW/LLM code — CLEAN.** The killed agent's lead was a dead end.
  `admin.py:1479` uses naive `utcnow()` against a naive column; Postgres TZ **and** the market-data
  container TZ are both verified UTC in production, so `now() == utcnow()`. `scheduler.py:8753`
  correctly normalizes aware→naive before comparing. Latent debt (it silently depends on both being
  UTC), no bug today.
- **`pct_return` scale confusion — frontend and api-gateway are CLEAN.** All 7 target sites verified:
  each page's `fmtPct` matches its own source scale. `EarningsForecastPanel.tsx:141-145` is the only
  frontend formatter that multiplies, and the only one whose source
  (`earnings_events.post_earnings_return_1d/5d`, a fraction) needs it. api-gateway performs no numeric
  transformation. **All threshold comparisons repo-wide on these columns are sign-only** (`> 0`,
  `< 0`) and therefore scale-invariant — the `if pct_return > 5` vs `> 0.05` bug does not exist
  anywhere.
- **Backend `pct_return` readers verified correct:** `brinson_attribution.py:128` (both sides ×100),
  `rl_agent.py:178` (scale-invariant Ridge + percentile), `paper_portfolio.py:256-257,1324,2686-2687`,
  `ml-prediction/features/builder.py:244-251`, `calibration.py:1095-1129`, `admin.py:552`,
  `analytics.py:806,814`, `routes.py:5048`.
- **New instrumentation fails open correctly.** `shared/common/llm_usage.py:79-101` wraps the entire
  DB write including its imports, logs a warning, returns `None`. Confirmed importable in all 5
  relevant containers. Falsy-zero handled properly: `(usage or {}).get("input_tokens")` stores `None`
  rather than a fabricated `0`, and aggregation uses SQL `coalesce`.
- **The tier-351 double-logging fix is correct across all 7 sites** (`llm_scorer.py`, `risk_agent.py`,
  `earnings.py` ×2, `macro_reaction.py`, `theme_signals.py`, `trade_coach.py`, `classify.py`).
- **`endpoint=` threading is complete** — all 16 `_get()` call sites pass an explicit static template;
  cardinality bounded, verified live (only 4 distinct endpoint keys in Redis). Retry double-counting
  checked: `_incr_call_counter` sits inside `_get()`, counted exactly once per real HTTP request.
- **`_FLOW_ALERT_TTL` 45→150 worked** — live data shows flow-alerts at 7,407 calls today against a
  projected ~68,100/day pre-fix.
- **`AUD-CHASE-ROC10-PAPERPORT` uses `is not None`, not a falsy check** — so a genuine `roc_10` of
  `0.0` doesn't skip the guard. Verified `roc_10` is actually populated: **387/387** recent BUY signals
  carry the key. (Its placement on the non-authoritative path remains the separate HIGH finding above.)
- **ML US/HK split, `count_48h` gauge fix, and `real_usage` null handling all verified correct.**
  `real_usage` properly requires non-null `daily_count` *and* `daily_limit`, falls back to the
  estimate, and labels which one is showing plus its age — a good example of a number being honest
  about its own staleness.
- **Division-by-zero guards all safe**: `max(median, 1000.0)`, `/(all.length || 1)`,
  `Math.max(..., 1)`, null-guarded `daily_limit`.
- **Delisted filtering is correct at 16 verified generation sites** — signal-engine refresh/reset,
  ranking-engine refresh/leaderboard/peers, `_bounded_options_flow_symbols()` (protects all 7
  options/gamma/dark-pool jobs), paper-trade entry scan, HK breadth, watchlist auto-curation, market
  screener, squeeze screener, technical-analysis patterns, `avg_volume_cache_refresh`, `theme_signals`.

**Minor (P4):** `frontend/src/pages/horizon-compare.tsx:359` — `LIVE_WIRED_LABELS` is dead code; the
only reference is its own declaration, all real logic lives in `isLiveWiredRow()` at `:366`. Its own
comment acknowledges it is superseded. Not a runtime bug, but the next person editing the live-wiring
list may edit the dead Set and see nothing change.

**Technical debt (P3):** `tune_history.validation_ev_pct` stores **fractions** in a `_pct`-named
column while the sibling `realized_ev_pct_after` stores true percent (`calibration.py:490,513,757,799,
1002,1036` vs `outcomes.py:86`). A real +3% EV persists as `0.03`, and two-decimal rounding collapses
most rows to `0.0`, destroying audit-trail resolution. **Not a live defect** — every promotion gate is
a pure comparison (`candidate_ev > baseline_ev`), which is scale-invariant.

**Technical debt (P3):** `squeeze_alert_outcomes`/`prebreakout_alert_outcomes` `return_Nd` columns are
**fractions in the DB** (written raw at `scheduler.py:5653`); the `* 100` is applied only at the
serializer (`admin.py:740-745`). Correct end-to-end today, but this refines the earlier premise — "×100"
holds for the **API response**, not the column. Any future Python reader touching
`SqueezeAlertOutcome.return_5d` directly would be off 100×.

---

### R2-17 — VERIFIED DEFECT (P1): `sizer.py`'s confidence tiers rest on a false premise, under-sizing every LONG and most GROWTH positions by 15%

`services/decision-engine/src/api/core/sizer.py:149-159`. The comment states:

```
# T232-DE2: the hard-reject floor in hard_rejects.py is min_confidence(62) * 0.90 = 55.8,
# so every trade that reaches this function already has confidence >= 55.8 — the old
# `>= 50` branch always fired ... Rescaled to sit entirely above the floor so the tiers
# are actually reachable.
```

with the tier boundary `elif confidence >= 62`. **The premise is false for three of four styles.**
Verified against the real per-style config (`paper_trading_engine.py:813,826,839,853`):

| Style | real `min_confidence` | real floor (×0.90) | comment assumes |
|---|---|---|---|
| LONG | 40.0 | **36.0** | 55.8 |
| GROWTH | 45.0 | **40.5** | 55.8 |
| SWING | 50.0 | **45.0** | 55.8 |
| HK | 65.0 | 58.5 | 55.8 |

Confidence values in 36-61 therefore **do** reach this function for LONG and GROWTH, and all land in
`else: confidence_mult = 0.85`. T232-DE2 set out to fix "one branch always fires with zero variation by
conviction" and **reintroduced exactly that, inverted** — LONG and most GROWTH positions are
systematically under-sized 15% with no conviction variation at all. `sizer.py:25` already flags this
defect class ("the confidence-multiplier tier tables ... differ") without connecting it to the wrong 62.

This is a fourth mirror of `62.0`, baked into a **comparison operator** rather than a config read, so
no config change can correct it.

### R2-18 — VERIFIED DEFECT (P2): the `min_confidence=62.0` fix fails closed onto the literal it exists to eliminate

`min_confidence` is 62.0 in three decision-engine locations (`routes.py:47`, `hard_rejects.py:181`,
`aggregator.py:253`) — a value matching **no real style**. This is the documented
`T234-CONFIG-DECIDE-DEFAULT-MISMATCH`, and the fix at `routes.py:170-183` overwrites `cfg` with real
resolved values from market-data's `/stocks/entry-gate-params`.

**The hole:** `aggregator.py:283` — when that httpx fetch fails, `_get_entry_gate_params()` returns
`_ENTRY_GATE_FALLBACK`, re-injecting **62.0**. The "real value" path fails closed onto the same
disconnected literal it was built to remove. During a market-data outage or 5s timeout, `/decide/{symbol}`
applies a hard floor of `62.0 × 0.90 = 55.8` to every style — a **19.8-point** over-tightening for LONG
(real floor 36.0) and 15.3 for GROWTH. Silent to the caller (only a
`decision.entry_gate_params_fetch_failed` warning), and the 900s `_ENTRY_GATE_TTL` at `aggregator.py:259`
makes it persist.

### R2-19 — VERIFIED DEFECT (P2): `max_signal_age_hours` — three copies, two values

- `paper_trading_engine.py:611` — canonical `_DEFAULT_CONFIG` = **72**, with the inline comment
  `# was 96h (4 days) — 3 days is sufficient with 5×/day refresh`
- `paper_trading_engine.py:5610` — the **enforcing gate**: `float(cfg.get("max_signal_age_hours", 96))`
- `hard_rejects.py:242` — `float(cfg.get("max_signal_age_hours", 72))`

A T222-C regression never propagated to the read site. The stale comment at `:5604` still says
`default 96h / 4 days`, contradicting `:611`. Normally 72 wins via the `_DEFAULT_CONFIG` merge, but any
path building `cfg` without the full merge falls back to 96h and admits signals a full day staler than
policy — while decision-engine, in the same fallback case, rejects an 80h-old signal as stale. The two
services disagree. `hard_rejects.py:228` claims it was "ported from `_scan_for_entries()`" — it was
ported from `_DEFAULT_CONFIG` (72), not from the read site it names (96).

### R2-20 — VERIFIED DEFECT (P3): `max_sector_pct` monitor and enforcer describe different caps

- Enforcer, `paper_trading_engine.py:4422`: `cfg["max_sector_pct"]` — **no default**, always the real 0.25
- Monitor, `paper_trading_engine.py:3333`: `cfg.get("max_sector_pct", 0.30)`
- Four other copies (`:580`, `:3493`, `hard_rejects.py:493`, `portfolio_backtest.py:79`) all 0.25

In the fallback case, sector concentration between 25% and 30% is hard-blocked at entry but produces
**no** `paper.sector_cap_exceeded` warning — an operator watching the alert stream sees a clean book
while entries are silently rejected. It also reaches the UI:
`frontend/src/pages/watchlist-performance.tsx:323` renders this value as the displayed cap, so the
frontend can show 30% while the engine enforces 25%. The 0.30 literal appears to have leaked from
`paper_portfolio.py:983`'s validation help text (`e.g. 0.30 for 30%`) rather than from the spec.

**Root cause common to R2-17 through R2-20:** every one is a `cfg.get(key, <copied literal>)` instead
of `cfg.get(key, _DEFAULT_CONFIG[key])`. The correct, drift-proof pattern is **already in use in the
same file** at `paper_trading_engine.py:1927` and `:3458` — it would have prevented all four.

### Mirrored constants verified IN SYNC — do not re-audit

`_SQUEEZE_MIN_SHORT_FLOAT` (15.0) · `_OUTCOME_WIN_HURDLE_PCT` (0.005) · `_OUTCOME_CENSOR_GRACE_DAYS`
(10) · `_COMPRESSION_LOOKBACK_DAYS`/`_PERCENTILE`/`_VOLUME_WINDOW` (126/0.20/20) ·
`_MIN_PROMOTION_EV_LIFT_PCT`/`_LIFT_SD_RATIO` (0.5/0.5) · calibrated entry `threshold` (0.52) ·
`max_open_risk_pct` (0.12, five copies) · `max_loss_per_trade_pct` (0.02, seven copies) ·
`max_position_pct` (0.10, seven copies; the HK 0.07 override at `:856` is intentional) ·
`max_positions` (6) · `_BLACKLIST_MEM_TTL` (3600) · `_RECONNECT_BASE_DELAY`/`_MAX_DELAY` (5/300) ·
`_MAX_SYMBOLS_PER_CLIENT` (30) · `_CALL_COUNTER_TTL_S` (25).

---

### R2-21 — VERIFIED DEFECT (P0, LIVE, SILENT): every `len(symbols) > 1` flat-column branch is inverted

**The grounding fact, verified by executing inside `stockai-market-data-1`:**

```
yfinance version: 1.5.2
auto_adjust default: True
raise_errors in yf.download params: False
yf.download(["TSLA"], ..., group_by="ticker")
  columns: [('TSLA','Open'), ('TSLA','High'), ('TSLA','Low')]
  raw["Close"] -> KeyError: 'Close'
```

Since yfinance 0.2.51, `yf.download()` defaults to `multi_level_index=True`; the flat-column collapse
now happens **only** when that is explicitly `False` (`yfinance/multi.py::_download_impl:101-102`).
**No call site in this repo passes it.** So a single-ticker download returns a MultiIndex, and every
`len(symbols) > 1` special case in this codebase now fires exactly when it must not.

Four copies, all `group_by="ticker"`, all carrying the identical now-false conditional:

| Copy | Location | Status |
|---|---|---|
| A (reference) | `api/routes.py:222-238` `_fetch_live_bulk()` | reachable (1 active stock); second instance at `:238` for Volume |
| **B** | `paper_trading_engine.py:1741` `_fetch_live_prices()` | **LIVE — see below** |
| C | `scheduler.py:2006` `_fetch_overnight_futures()` | safe by accident (`_FUTURES` is hardcoded 4-element); else-branch is dead-but-wrong |
| D | `api/routes.py:314` `refresh_avg_volume_cache()` | reachable |

**Copy B is live, user-facing, and silent.** `api/paper_portfolio.py:591` calls
`_fetch_live_prices([t.symbol for t in open_trades])` scoped to **one portfolio's** open trades. A
portfolio holding exactly one open position passes `["NVDA"]` → `len(symbols) > 1` is False →
`raw["Close"]` on a `('NVDA','Close')` MultiIndex → `KeyError`.

That KeyError is caught by the per-symbol `except Exception: continue` at `:1747`, so **the loop
completes normally and returns `{}` with no log line at all** — the outer `except` at `:1749` never
fires. The paper-portfolio page falls through to `_best_price()`'s stored-DB fallback and displays a
**stale close as the live price**, indefinitely. The same path at `pte.py:6039`
(`snapshot_equity_curve`) writes that stale price into the **persisted equity curve**.

The `scheduler.py:1967` docstring explicitly advertises that it "mirrors `_fetch_live_bulk()`'s exact
multi-ticker column-shape handling." The mirroring is faithful — that is the problem.

### R2-22 — VERIFIED DEFECT (P0): the test fixtures encode the false invariant, so CI is green on the bug and will reject the fix

`services/market-data/tests/test_fetch_live_prices_batching.py:27-31`:

```python
def _single_symbol_download_df(prev_close, close):
    """A single-symbol yf.download() result has FLAT columns, not a MultiIndex — matches
    _fetch_live_bulk()'s own `len(symbols) > 1` branch distinction exactly."""
    return pd.DataFrame({"Close": [prev_close, close]})
```

`test_single_symbol_uses_the_flat_column_branch` (`:52`) monkeypatches `yf.download` to return that
flat frame and asserts `{"TSLA": 51.5}`. **The mock contradicts the installed library**: the test
passes green while production raises KeyError, and its docstring cements the false invariant as fact —
which is precisely why it propagated to three other files. Same mocks at
`test_overnight_futures_brief.py:37` and `test_fetch_live_bulk_fallback_cap.py:29`.
**Any fix must correct these fixtures first, or CI will reject it.**

### R2-23 — VERIFIED DEFECT (P1): `auto_adjust` unstated for SPY, redefining ML macro features on a library upgrade

`ml-prediction/src/features/builder.py:444-449` (SPY) passes **no `auto_adjust` at all**; `:450-456`
(VIX) and `:506-512` (HSI) pass explicit `auto_adjust=False` with the correct reasoning ("VIX is an
index — no dividends/splits to adjust"). The default flipped to `True` in 0.2.51 (verified above), so
**SPY is now dividend-adjusted while VIX/HSI are not.**

`spy_ret_1` / `spy_ret_5` / `spy_vol_20` / `is_bear_market` (`builder.py:476-490`) are **ML training
features**. Their values silently changed the day the container crossed 0.2.51 — SPY's ~1.3%/yr
dividend is now folded into the return series. Models trained before and after that upgrade sit on
different definitions of the same named feature, and `ml-prediction/requirements.txt:18` is
`yfinance>=0.2.40` — **unpinned**, so it will drift again. The VIX/HSI asymmetry is correct and
deliberate; the defect is the *unstated* SPY argument. This is the only site in the repo that leaves
`auto_adjust` implicit.

### R2-24 — VERIFIED DEFECT (P1): the BUG-YFCALLVOL2 amplification pattern survives, uncapped, at 3 sites

The incident fix is `_LIVE_BULK_FALLBACK_MAX = 20` (`routes.py:161`, applied `:267-279`): if more than
20 symbols are missing from a bulk result, **skip the per-symbol fallback** rather than fire 150+
requests into an active throttle. That guard is absent at:

- **`paper_trading_engine.py:968-974`** `_batch_compute_atr()` — **two nested unbounded fallbacks**:
  the inner `except` calls `_compute_atr(sym)` per failing symbol; the outer calls it for **every**
  symbol. `_compute_atr` (`:923`) is one `yf.Ticker(...).history(period="40d")` HTTP request each.
  Callers pass the full candidate universe (`pte.py:5247`, `:2653`). Strictly worse than the original
  bug — the inner path also fires when the shape is merely unexpected, i.e. **R2-21 can trigger it.**
- **`scheduler.py:7226-7234`** `check_price_alerts()` — still uses `yf.Tickers(...)` then loops
  `.fast_info.last_price` per symbol. This is *literally the pattern BUG-YFCALLVOL removed from
  `_fetch_live_prices()`*, documented at `pte.py:1720-1727` ("`yf.Tickers(...)` does not actually
  batch `.fast_info`; each access is a separate HTTP request"). The fix was applied there and never
  swept to this sibling. **Runs every minute**, with `except Exception: pass` making a throttle invisible.
- **`scheduler.py:4296`** — `_yf.Ticker(symbol).options` in a per-symbol loop over the gamma-unwind
  universe, no cap, no backoff.

### R2-25 — Structural (P1, library-forced): `raise_errors=True` cannot exist on any bulk path

`raise_errors` is **not a parameter of `yf.download()`** in 1.5.x (verified: `False` above) — it exists
only on `Ticker.history`. The single site that has it is `adapters/yfinance_adapter.py:88`, paired
with a tenacity policy (`:34-45`) that excludes `YFTickerMissingError` from retry so delisting surfaces
immediately.

**Consequence:** the delisting detector can only ever fire through `YFinanceAdapter.fetch_ohlcv`.
A symbol touched only by bulk paths (`_fetch_live_prices`, `_batch_compute_atr`, `_fetch_market_regime`)
produces a silently-absent column, indistinguishable from a transient miss. Not fixable in place —
record as a real coverage limit. Note `_fetch_live_prices()`'s `p >= 0.50` filter (`pte.py:1745`) is
doing informal delisting-ish filtering with no connection to `Stock.delisted` (see R2-16).

### R2-26 — Fragile (P2): 12 sites, 5 hand-rolled unwrap idioms, 2 inverted `group_by` conventions

Four sites use `group_by="ticker"` → `(ticker, field)`; eight use the default → `(field, ticker)`.
`multi.py:97-98` swaps the levels, so the conventions are **level-inverted**, and each site hand-rolls
its own unwrap:

| Idiom | Sites | Correct under 1.5.x? |
|---|---|---|
| `if len(symbols) > 1` | `routes.py:222`, `pte.py:1741`, `scheduler.py:2006` | **BROKEN (R2-21)** |
| `raw["Close"] if "Close" in raw.columns else raw` | `pte.py:1241,6007,1015`, `hmm_regime.py:47` | ✓ correct |
| `isinstance(raw.columns, pd.MultiIndex)` | `pte.py:952` | ✓ correct |
| `try: raw["Close"] except KeyError: raw` | `routes.py:1894-1897` | ✓ but now-unreachable dead branch |
| `raw["Close"] if len(tickers) > 1 else ...` | `brinson_attribution.py:229` | dead-but-wrong (11 hardcoded tickers) |
| `isinstance` + droplevel | `risk.py:253-256`, `builder.py:467-470` | ✓ correct |

**No shared helper exists** — `shared/common/` has no yfinance frame normalizer. Twelve independent
unwraps of one library quirk is the root cause of R2-21 and R2-26 both.

**Suggested fix shape for R2-21 + R2-22 + R2-26 (one change, not four):** add a normalizer to
`shared/common/` taking a raw frame plus the requested symbol list, returning `{symbol: DataFrame}`,
handling both `group_by` conventions and both `multi_level_index` states. Route all 12 sites through
it. Correcting the four branches in place fixes today's bug but leaves the twelfth copy of the quirk
standing. **Fix the test fixtures first or CI will reject it.**

### yfinance divergences verified DELIBERATE AND CORRECT — do not re-flag

`period` vs `start`/`end` (live paths want "newest"; model-fitting/training/attribution paths need a
reproducible window) · lookback lengths (2d/5d/15d/40d/300d/4mo/14mo — each sized to its own
indicator's warm-up; the futures 5d is documented at `scheduler.py:1970`) · `threads=` (unset
everywhere) · `timeout=` (unset everywhere; 10s library default) · `progress=False` (set at **every**
site) · `yfinance_adapter.py:59-60`'s `use_adjusted = (timeframe == "1d")` · `admin.py:352-366`'s
tenacity retry existing only on the user-initiated `add_stock` path (`BUG-ADDSTOCK-NORETRY`, guarded by
`test_add_stock_yfinance_retry.py:163`) · `routes.py:129-130`'s `hist.index` MultiIndex guard
(`Ticker.history()` never returns MultiIndex *columns* — correct for its own call shape, do not
"harmonize" it with the download sites).

---

## R2-27 — TEST COVERAGE (domain 9 of 9): the suite is green on every bug in this document

**Local suite passes everywhere** — market-data **2,879 passed**, decision-engine 306,
technical-analysis 66, portfolio-optimizer 59. Every defect in this document passes CI today.

### Structural root cause: 44% of tests never import the code they test

**172 of 392 test files** `read_text()` the source and assert on substrings rather than executing it.
In market-data, **87 files scrape `scheduler.py`** and **no test file imports it at all** — so all 28
of its `check_*`/`send_*` alert functions are untested by execution.

**The stated reason for this is false.** The disclaimer says `scheduler.py` "can't be imported in this
test environment." The only blocker is `apscheduler` — a declared production dependency
(`requirements.txt:18`) that was never added to `conftest.py`'s stub list beside
`sqlalchemy`/`redis`/`yfinance`. A ~10-line stub makes it import cleanly, exposing **37** alert/digest
functions. Calling one immediately raised the real production bug:
`ModuleNotFoundError: No module named 'src.db'` — i.e. **R2-1 would have been caught on the first run.**

**This is the highest-leverage single fix in the entire audit:** a ~10-line `conftest.py` addition
converts 87 brittle source-scraping files into a suite that can execute the alert layer.

### Tests that assert the WRONG thing — these BLOCK their own fix

| # | Test | What it pins |
|---|---|---|
| 1 | `test_trade_coach.py:135-142` | Feeds `pct_return=0.10` (a **fraction**) and asserts `avg_return_pct == 10`. Production writes that column as `pnl_pct * 100`, so a real +10% trade is stored as `10.0`. **The test encodes the opposite convention from production — fixing R2-12 turns this green test red.** |
| 2 | `test_scheduler_minute_job_misfire_grace.py:87-92` | Asserts `misfire_grace_time` is **absent** on `gamma_unwind_alert_check` / `prebreakout_alert_check`. The rationale inverts the real risk: APScheduler's default grace is 1s regardless of interval, so a missed 4-hour tick costs 4 hours of silence. **Note `prebreakout_alert_check` is the pillar awaiting outcome data per `SESSION_INDEX` — a silent death corrupts that evaluation as "no signal."** |
| 3 | `test_fetch_live_prices_batching.py:27-31, :52` | The false flat-column invariant (R2-22). |
| 4 | `test_entry_exit_commission_pnl.py:83-92` | **A pure tautology** — never imports production code; every value is a test-local literal, asserting `a-b-c == a-b-c`. Would pass if the engine were deleted. |
| 5 | `test_should_enter_de_parity.py` | **Tests no parity.** 708 lines, 60+ tests, imports only `_should_enter` — never `hard_rejects` or `scorer`. Asserts hand-copied constants, so DE's scorer can change arbitrarily and it stays green. Directly relevant to the DE-divergence findings above. |

### TWO CORRECTIONS to earlier entries in this document

**(a) R2-22 was overstated — only ONE test file carries the false mock, not three.**
`test_overnight_futures_brief.py:37` and `test_fetch_live_bulk_fallback_cap.py:29` build **correct
multi-index fixtures**; their docstrings explicitly say `group_by="ticker"`-shaped multi-index. Only
`test_fetch_live_prices_batching.py:27` encodes the false invariant.

**(b) R2-21 is narrower than stated — the break is `group_by`-dependent.** Verified: `group_by="ticker"`
raises `KeyError: 'Close'`, while the default `group_by="column"` works, because `"Close"` matches the
outer level there. So the broken sites are **`paper_trading_engine.py:1741`** and
**`routes.py:228,238,314`** (fed by `group_by="ticker"` at `:201`/`:296`). The severity of the live
Copy-B path is unchanged; the blast radius is smaller than "all 12 yfinance sites."

### High-risk paths with NO real test

- **`_monitor_positions()` — every stop-loss, take-profit, and trailing stop — is never called by any
  test.** ~10 files name it; all explicitly disclaim exercising it. Same for `_scan_for_entries()`
  (~15 files, zero calls). **Both halves of the trading engine are untested by execution.**
- **ML/TA fusion is 100% unexecuted.** All 7 `_apply_style_signal` call sites pass `ml_prob=None`,
  forcing the `else` at `signals.py:2027`. The entire fusion block (`:1987-2026`) — AUC ramp,
  `ml_weight_cap`, disagreement dampener — never runs. `"boosted_4_pillar_confluence"`
  (`signals.py:2080`) appears **only in production, never in any test**.
- **Zero tests reference `_store_conviction`** — which is why R2's Finding A survived.
- **`paper_portfolio.py:477`** — NEW: manual liquidation computes `pnl` with **no commission term**,
  despite computing `exit_commission` two lines above at `:475`. The AUD262 defect surviving in the one
  path where it was never fixed.

### Which confirmed bugs are BLOCKED vs UNBLOCKED by tests

**Unblocked — no test pins them, fix freely:** the bearish volume pillar (R2-6) — though
`test_bearish_pillars.py:84-91`'s `bearish_pillars_active <= 1` tolerance is exactly wide enough to
hide it; `sizer.py:149-159` (R2-17) — the sole sizer test discards the `PositionPlan` and only ever
passes `confidence=60.0`; `scheduler.py:6709` (Finding A); both wrong-import crashes (R2-1, R2-2).

**Blocked — fix the fixture first:** `trade_coach.py` (R2-12), the yfinance branch (R2-21),
`gamma_unwind_alert_check` misfire grace.

### Genuinely well tested — do not re-audit

- **`hard_rejects.py` is the standout** — 306 real tests, ~41 gates with both positive *and* negative
  cases, exact-boundary values, plus real gate-ordering tests.
- **`_open_paper_trade`** — asserts a hand-computed `shares == 100.0`, all 7 skip reasons.
- **`_close_one_paper_trade`** — the one genuinely-executed money assertion: real SQLite, non-zero
  commission, hand-computed cash (`test_liquidate_portfolio.py:213`).
- **Pure-computation layers** — technical-analysis (66), portfolio-optimizer (59),
  `test_signal_generator.py` (45), all real execution.
- **`_size_aware_slippage_pct`**, **`_is_conviction_buy`**, and the **SELL-side pillar gate** (the
  correct pattern the BUY gate lacks).

---

## AUDIT COMPLETE — 9 of 9 domains

All nine domains finished across two rounds. Round-1 agents were killed by a spend limit and proved
**not resumable** (context died with them, nothing persisted); each was respawned fresh, seeded with
the partial signal it died on. Every respawned agent confirmed its predecessor's dying suspicion and
then found more.

**27 verified defects**, each citing file:line with a concrete failure scenario. Two findings were
verified by executing against the live production container rather than by inference (R2-13's drift,
R2-21's column shape). Two earlier entries were corrected by later agents (recorded above in R2-27).

**One fix was applied during this audit** — not a code change: `stockai-ranking-engine-1` was
`docker cp`'d back into sync on `shared/common/ai_keys.py` and `shared/db/session.py`, `__pycache__`
cleared, container restarted, health `ok`, hash now matching all 11 siblings. **That is a
session-scoped hotfix — any `--force-recreate` or reboot reverts it** until
`check_deploy_drift.sh` actually covers `/app/shared`.

No other code was modified. Everything else is documented, not fixed, per the standing 2026-09-05
guidance.

Round 2 completed 7 of 9 (paper trading, signal generation, auth/routing/imports, `pct_return` scale,
delisted-blindness, last-48h changes, plus the production-runtime checks folded into R2-13/R2-15/R2-16).
Code-duplication and test-coverage audits are still in flight.

| Domain | Partial signal at termination |
|---|---|
| Signal generation logic | Was verifying whether news-sentiment and RS-compression gates are direction-blind where the T232-SIG5 pattern requires bullish-only |
| Paper trading engine | Was confirming a **denominator-inflation bug** in mixed scale-in/scale-out sequences |
| `pct_return` scale sweep | Mostly clean: `_passes_promotion_margin` verified correct; all alert-outcome readers confirmed ×100. Frontend/gateway sweep unfinished |
| Auth headers / routing / imports | Confirmed `calibrate_entry_weights()` stale-cache issue is production-reachable via the weekly Sunday chain (`scheduler.py:7922`) and `POST /paper-portfolio/calibrate-entry` |
| Delisted-blindness sweep | Confirmed one live scheduler job affected; remaining sites unchecked |
| Code duplication | Was classifying the yfinance divergence as drift vs. deliberate |
| Test coverage gaps | Was distinguishing real test exercise from incidental name mentions |
| Last-48h changes | Was checking `datetime.utcnow()` vs timezone-aware consistency in the new UW usage code |
| Production runtime (jobs/DQ/DB) | Only deploy-drift + container health completed |

---

## RECOMMENDED PRIORITY — REVISED AFTER ROUND 2

Round 2 found items that outrank the original list. Consolidated ordering:

| # | Fix | Why first | Size |
|---|---|---|---|
| **0** | yfinance `len(symbols) > 1` branches + test fixtures (R2-21/22) | **LIVE & SILENT** — 1-position portfolios show stale prices as live, and it poisons the persisted equity curve | medium |
| **1** | `scheduler.py:6709` `True` → `all_pass` | Conviction gate is disabled for every BUY older than one cycle | 1 token |
| **2** | `signals.py:1479-1485` + `:1457-1462` bearish pillars | **LIVE** — `min_pillars_for_sell=3` is armed in prod against a corrupted feature | small |
| **3** | `scheduler.py:11251-11252` `..db` → `db` | 17:00 digest has never run, not once | 2 lines |
| **4** | `trade_coach.py:104,133` drop the `* 100` | Weekly email shows +320% for +3.2%, and feeds it to the LLM | 2 lines |
| **5** | `check_deploy_drift.sh` add `/app/shared` | **Currently reporting a drifted ranking-engine as OK** | small |
| **6** | `with_for_update()` on `PaperPortfolio` | Silent money corruption via normal UI use | medium |
| **7** | `paper_portfolio.py:2230` fix the import | Calibration silently never takes effect | 1 line |
| **8** | `rl_agent.py:172-176` falsy-zero | Corrupted RL labels feed live entry scoring | small |
| **9** | `routes.py:825,792` add `.delisted` filter | Latent; one line protects 7 alert scanners | 2 lines |
| **10** | `signals.py:2276,2303` add `fused > 0.5` | Negative news / weak RS currently *rescue* SELLs | 2 lines |
| **11** | `conditional_orders.py:530` missing `finally` | Lock never released; mutates real cash | small |
| **12** | `scheduler.py:8715` lower the 50k floor | Spike detector blind to 196×-baseline regressions | 1 constant |
| **13** | `unusual_whales.py:387` TTL 25h → 49h | Dashboard reads 0 for 23 of 24 hours | 1 constant |
| **14** | `gamma_unwind_alert_check` misfire grace | Longest job in the file, 1s window | 1 line + test |
| **15** | `sizer.py:156` derive tier from `cfg["min_confidence"]` | LONG/GROWTH under-sized 15%, no conviction variation | small |
| **16** | `aggregator.py:253` fallback → real style defaults | Outage fails closed onto a no-style literal for 900s | small |
| **17** | `paper_trading_engine.py:5610` `96` → `72` | Enforcer disagrees with its own documented default | 1 literal |
| **18** | `paper_trading_engine.py:3333` `0.30` → `0.25` | Monitor and enforcer describe different caps; UI shows the wrong one | 1 literal |

| **19** | Add an `apscheduler` stub to `market-data/tests/conftest.py` | **~10 lines.** Converts 87 source-scraping test files into a suite that can execute 37 alert functions — would have caught R2-1 on the first run | ~10 lines |

**Do #19 first if you intend to fix anything else.** It is the cheapest item on this list and the only
one that makes the *other* fixes verifiable. Three fixes are currently blocked by tests that pin the
wrong behavior (`trade_coach`, the yfinance branch, `gamma_unwind_alert_check`) — correct those
fixtures as part of each fix, not separately.

**Systemic fix worth more than 15-18 individually:** replace every `cfg.get(key, <literal>)` with
`cfg.get(key, _DEFAULT_CONFIG[key])`. That pattern is already used correctly at
`paper_trading_engine.py:1927` and `:3458`, and it is drift-proof by construction — it would have
prevented all four constant divergences and will prevent the next one.

**Immediate operational action, independent of any code fix:** re-`docker cp` `shared/common/ai_keys.py`
and `shared/db/session.py` into `stockai-ranking-engine-1`. That container is drifted **right now**.

Also worth noting: fixes 1, 2, and 10 all touch signal/gate behavior. Per the 2026-09-05 guidance,
**re-baseline any measurement taken since 2026-09-05 after they land** — several were measured against
gates that were not actually enforcing.

---

### Original round-1 ordering (superseded above, retained for reference)

Ordered by impact-per-risk. Items 1-4 restore already-validated behavior or prevent silent
corruption; none introduces a new untested threshold.

1. **`scheduler.py:6709`: `True` → `all_pass`.** One token. Restores conviction-gate enforcement for
   both consumers. **Also invalidates any measurement of the anti-chasing filter taken since
   2026-09-05** — re-check those before trusting them.
2. **`with_for_update()` on `PaperPortfolio` in every cash-mutating path.** Silent money corruption,
   reachable through normal UI use today.
3. **`rl_agent.py:172-176`** — delete the three provably-impossible fallbacks; fix the fourth to
   `is not None`. Corrupted RL training labels feed live entry scoring.
4. **`check_deploy_drift.sh`** — add `/app/shared` to the hash pipeline. Would have caught the
   9-of-10 stale-container state of 2026-09-04 automatically.
5. **`conditional_orders.py:530`** — add the missing `finally` release (mutates real cash).
6. **`gamma_unwind_alert_check`** — add `misfire_grace_time=60` (`scheduler.py:11744`) and update the
   test that currently asserts its absence.
7. Backup/mirror `stockai:admin:*` — only finding with production-outage potential; has already bitten
   once in related form.
8. Propagate the T232-PT5 token-release pattern to the other 13 locks; add `misfire_grace_time` to
   event-intelligence and news-intelligence.
9. **Measure, don't assume:** `max_entry_gap_pct` threading and the volume-confirmed gap branch change
   which trades open — per the 09-05 guidance, ship behind measurement.

---

## RELATIONSHIP TO THE 3-WEEK MEASUREMENT PLAN

`docs/2026-09-05/WHAT_TO_DO_NEXT.md` recommends ~3 weeks of measurement over building. **This audit
does not contradict that — it strengthens the case for items 1-5.**

Every one of those is either a *correctness fix that protects the measurement itself* (the `sent=True`
bug silently disables the very filter being measured; the deploy-drift gap silently corrupts every
measurement) or a *silent-corruption fix* (cash race, RL labels). They are precisely the class of work
that cycle endorsed: **"the only finding that silently corrupts every other measurement."**

Item 9, by contrast, changes trading behavior and belongs *after* the measurement window.
