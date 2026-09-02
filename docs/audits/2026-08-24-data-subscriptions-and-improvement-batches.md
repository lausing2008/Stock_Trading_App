## Review: docs/AI Stock Intelligence Data & Decision Engine.md — FMP + Unusual Whales Paid
## Data Subscriptions, Deliberately Deferred (2026-08-24)

**Not the same document as `docs/recomm_or_audit/PAID_DATA_SERVICES_RECOMMENDATION_2025-08-22.md`**
— a genuine mix-up worth recording so a future session doesn't repeat it. The user first asked
about FMP + Unusual Whales, got an answer built from the `recomm_or_audit/` pricing doc (which
never mentions FMP at all, and prices Unusual Whales' Flow tier at $57/mo), then corrected: the
document they actually meant is `docs/AI Stock Intelligence Data & Decision Engine.md` — a
Claude-prompt-shaped architecture spec (not a numeric audit) proposing a full provider-adapter
upgrade with FMP for fundamentals and Unusual Whales for options/flow/squeeze, written as
instructions to hand an AI coding agent.

### What the doc got right about the codebase, verified directly

- **A provider-adapter abstraction matching the doc's own §2 ask already exists** —
  `services/market-data/src/adapters/base.py`'s `DataAdapter(ABC)` (`fetch_ohlcv()`/
  `supports()`) plus `registry.py`'s priority-ordered fallback (`polygon → alpha_vantage →
  yfinance`, first-success-wins, never crashes the platform on a provider failure — exactly
  §3's "never allow a temporary provider failure to crash the entire trading platform").
  **Scoped to OHLCV price bars only** — no `FundamentalDataProvider`/`OptionsDataProvider`
  interface exists, and neither FMP nor Unusual Whales has an adapter anywhere.
- **Walk-forward validation, point-in-time feature joins, and a real promotion gate** (the
  doc's §18-19, §29, §45 "mandatory" sections) — all already exist and are unusually rigorous
  for a project this size: `gate_harness.py`/`ev_gate.py`/`promotion_gate.py` require a
  candidate to beat the live baseline on held-out, chronologically-split data before promoting;
  `builder.py` has explicit PIT joins with a documented history of catching and fixing
  lookahead bugs.
- **A 0-100 configurable-weight scoring system** (§12-15) — already built (K-Score, TA
  weights), Redis-backed override support.
- **News is already genuinely multi-source** (contradicting the doc's implicit yfinance-only
  framing) — PR Newswire, Business Wire, SEC EDGAR real-time filings, and Alpaca are all live
  (`T259-NEWS-INTELLIGENCE`).

### What's missing relative to the doc

- **Fundamentals, options, and short-interest are 100% yfinance-sourced** —
  `get_fundamentals()`/`get_options_flow()`/`get_options_chain()`/`shortPercentOfFloat` all pull
  straight from yfinance's `info`/`option_chain()`. No FMP or Unusual Whales adapter exists.
- **No typed Data Quality enum** (§7's VALID/PARTIAL/STALE/INVALID/UNAVAILABLE) — current
  handling is ad-hoc fail-open `None` returns plus a monitoring job (`run_data_quality_checks()`),
  not a per-field status consumed by callers.
- **No dedicated model-versioning table** (§30) — models persist as joblib bundles with
  embedded metadata (`trained_at`/`metrics`/`feature_columns`/hyperparams), plus a real
  `TuneHistory` table tracking tuning attempts — partial, not the doc's full `model_version`
  entity.

### The real usage check — why $125/mo wasn't a clear yes

Before treating "the doc's architecture is sound" as "therefore subscribe," a dedicated
usage-first check was run: of 6 mechanisms in this codebase touching squeeze/options/gamma data
(`check_short_squeeze_alerts`, `check_squeeze_ignition_alerts`, `check_prebreakout_alerts`,
`check_gamma_unwind_alerts`, `GET /{symbol}/options-flow`, `GET /{symbol}/options-chain`), only
**one** — `check_gamma_unwind_alerts()` — actually consumes options-chain data at all. The other
two squeeze alert types use short-interest + price/volume only, no options; `check_
prebreakout_alerts()` uses options positioning as a reported context field only, never a gate,
per its own comment ("too little history exists to validate it as anything more than a minor
tilt yet").

`check_gamma_unwind_alerts()` computes an open-interest-concentration ratio in a ±5% strike band
around the current price (`_GAMMA_UNWIND_STRIKE_BAND_PCT`) — its own docstring states explicitly
this is **NOT** a real gamma-exposure (GEX) calculation; neither Black-Scholes gamma nor a
dealer-positioning assumption is computed anywhere in this app. This was already the subject of
a prior audit (IF-05, `docs/recomm_or_audit/` review) which deliberately deferred building true
GEX — not for lack of a data source, but because true GEX needs a dealer-positioning
**assumption** layered on top of raw gamma numbers, which is a real engineering build, not
something a data subscription alone supplies.

The whole squeeze/gamma alert family (`SqueezeAlertOutcome`, `PreBreakoutAlertOutcome`) has
fired only **~107 alerts across all three alert types combined**, over ~9 days since launch,
with **zero closed forward-return outcomes** yet (the 1d/2d/3d windows added in Tier 296 help
this resolve faster going forward, but nothing has closed as of this review). Paying for
real-GEX-grade precision on a feature line that hasn't yet earned its first statistically
meaningful sample is a sequencing problem, not a wrong idea.

### The real pricing, live-fetched — correcting a stale figure

`unusualwhales.com/pricing?product=api&interval=annual` was fetched directly rather than
trusted from either document's own text:

| Tier | Price | Notes |
|---|---|---|
| API Trial | Free (1-week only) | Includes Spot GEX, dark pool, 1-min SPX Market Maker Exposure |
| **API Basic** | **$125/mo** ($150 regular) / $1,500/yr | Same feature set as trial, 2-year lookback, 80k req/day |
| API Advanced | $315/mo ($375 regular) / $3,780/yr | Unlimited requests + CME futures live tape |

**$125/mo is the real floor for API access — there is no cheaper paid tier.** The earlier
$57/mo figure (from `docs/recomm_or_audit/PAID_DATA_SERVICES_RECOMMENDATION_2025-08-22.md`,
which recommended the "Flow" tier) is stale relative to Unusual Whales' current published
pricing structure. Notably, even the entry $125/mo tier already includes "Spot GEX" and
"1-minute SPX Market Maker Exposure" as named, pre-computed metrics — potentially closing the
exact dealer-positioning gap IF-05 flagged as unbuilt, rather than only supplying raw inputs a
future build would still need to process. **Not independently verified this session**: whether
these metrics cover individual equity symbols (vs. SPX/index-level only) and their real update
cadence/methodology — worth checking directly before assuming they slot into
`check_gamma_unwind_alerts()` cleanly.

### Decision

User explicitly chose to document this and wait: *"let's document all these, and wait for some
times and we may come back for it."* No code changes made. FMP has no comparable urgency —
yfinance fundamentals already work, just less reliably for smaller/international names, so FMP
would be a reliability upgrade, not a new capability, and is correctly lower-priority regardless
of the Unusual Whales timing question.

**Revisit criteria** (both should hold before reconsidering the subscription):
1. The squeeze/gamma/prebreakout alert family has enough closed, resolved forward-return
   outcomes to judge whether the current OI-concentration heuristic is materially wrong or
   missing real squeezes — check `SqueezeAlertOutcome`/`PreBreakoutAlertOutcome` row counts and
   `return_1d/2d/3d/5d/10d/20d` population directly.
2. "Spot GEX"/"SPX Market Maker Exposure" methodology and per-symbol coverage have been checked
   directly against Unusual Whales' own docs (not assumed from the pricing page's marketing
   copy) to confirm they cover the actual tickers this app's squeeze alerts watch.

**What to check when revisiting**:
```bash
# Closed-outcome coverage across the squeeze/gamma/prebreakout alert family:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT alert_type, COUNT(*) total, COUNT(*) FILTER (WHERE return_5d IS NOT NULL) has_5d, COUNT(*) FILTER (WHERE return_20d IS NOT NULL) has_20d FROM squeeze_alert_outcomes GROUP BY alert_type;"
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*) total, COUNT(*) FILTER (WHERE return_5d IS NOT NULL) has_5d FROM prebreakout_alert_outcomes;"

# Real win-rate/avg-return once enough outcomes exist:
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/admin/squeeze-alert-performance?days_back=180' \
  -H "Authorization: Bearer <admin token>" | python3 -m json.tool

# Confirm current Unusual Whales pricing hasn't changed again before committing:
# fetch https://unusualwhales.com/pricing?product=api directly rather than trusting this note.
```

---


## Next Improvement Batch: 3 Real Fixes From a Background Survey Interrupted by a Spend Limit
## (2026-08-24)

**User ask, verbatim**: "continue what was left from last time" — resuming a 5-part background
survey (spawned via a parallel-agent fan-out looking for a genuinely-big improvement batch,
per an earlier "one big batch" mid-turn instruction) where 2 of 5 sub-agents (scheduler.py
alert-job audit, signal-engine/ml-prediction gap check) hit the org's monthly API spend limit
mid-run and were terminated with only partial findings. Rather than wait for a limit reset or
retry those 2, the 3 COMPLETED sub-agents' findings were personally re-verified against
current code and built into 3 real, shipped fixes.

**A real background-agent-orchestration gotcha hit and worked around this session**: the
parent survey agent, once its 5 children were dispatched, twice ended its own turn reporting
"I'll wait for background children" instead of a real answer — a documented red-flag pattern
in this file's own history (see "Process Note: Background Agents Can Drift Scope" and
"lesson reinforced" entries elsewhere) where an agent's own claim of deferring to background
work is itself a non-answer, not progress. Resolved by bypassing the parent entirely — polling
its 5 children directly via `ListAgents` and pulling each one's own completion report
individually once ready, rather than relying on the parent's own (repeatedly non-)synthesis.

### 1. T232-DL-DUALSCORER-RESTRICTEDSYMBOL — RestrictedSymbol blacklist never checked on decision-engine's live path

**The gap**: `_scan_for_entries()` (`paper_trading_engine.py`) checks the operator-maintained
`RestrictedSymbol` table FIRST in its candidate loop, before any other candidate-specific
computation — its own comment states "a user-banned symbol should never even be considered."
8 real symbols were added to this exact table under Tier 297, each with a confirmed 0%
historical win rate (47/40/31/28/26/18/18/14 trades respectively: SNDK, AMKR, KMT, 6809.HK,
3323.HK, SOXL, AAON, WMT). But `decision-engine`'s `hard_rejects.py` had zero equivalent check
— since `decision_engine_mode="primary"` is the live default, any call to `/decide/{symbol}`
for one of those 8 banned symbols could return an ENTER verdict with nothing to stop it.

**Fix**: a direct DB query inside `check_hard_rejects()` — decision-engine already has real DB
access (`from db import SessionLocal`, used identically by the pre-existing macro-blackout
check a few lines below) — querying `restricted_symbols` by exact symbol match, placed
first thing in the function right after the BUY-direction check, matching `_scan_for_entries()`'s
own ordering exactly. Deliberately a per-symbol DB lookup rather than threading a whole
restricted-symbols SET through `config_overrides` (unlike `open_sector_counts`/`short_signal`/
etc.) — `symbol` is already always sent, and this needs no per-scan-cycle aggregate the caller
would otherwise have to batch-compute for a value decision-engine can look up itself in one
query. Fails open on any DB error, matching every other DB-backed gate in this file.

**A real, self-caught test-isolation bug during development**: the first version of the
"check is skipped when symbol is absent" test registered a fake `db` module globally in
`sys.modules` and used a call-counting mock to prove zero DB calls happen without a symbol —
but `_base_kwargs`' own default `reasons={"macro_blackout": None, ...}` ALSO routes through
`from db import SessionLocal` (the pre-existing macro-blackout gate's own fallback path, which
in every OTHER test harmlessly hits a real `ImportError` since `db` isn't stubbed there). Once
a fake `db` module was registered for THIS test, it intercepted the macro-blackout gate's call
too, inflating the counter for a reason unrelated to the restricted-symbol check under test —
a real `1 == 0` assertion failure that had nothing to do with the code being tested. Fixed by
giving that one test a real `macro_blackout=False` value so the macro-blackout gate's fast
path short-circuits before ever reaching its own DB fallback, isolating the counter to the
restricted-symbol check alone.

**Tests**: `services/decision-engine/tests/test_hard_rejects.py` gained 6 cases — blocks with
the real reason text, blocks even with no reason on file ("no reason on file" fallback), a
non-restricted symbol passes clean, the check is skipped entirely (zero DB calls) when symbol
is `None`, fails open on a genuine DB error, and — matching the conviction-gate's own
established ordering-verification discipline — a dedicated test confirming the restricted
check fires BEFORE the bear-regime check, not after (so a banned symbol's rejection reason is
unambiguous even when other gates would also fire).

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: disabling the
whole restricted-symbol block (`if symbol:` → `if False and symbol:`, caught by 3 tests); and
moving the check to fire AFTER the bear-regime check instead of before (caught by exactly the
dedicated ordering test, no others — confirming that test genuinely isolates the ordering
property rather than overlapping with the block-existence tests). Full 134-test
`hard_rejects.py` suite and 255-test decision-engine suite green; pyflakes clean.

### 2. T232-DL-DUALSCORER-WEEKLYPNL-EXPOSURE — weekly loss/gain circuit breakers + open-exposure cap never ported

**The gap**: 3 more real, portfolio-wide gates in `_scan_for_entries()` with zero
decision-engine equivalent — `max_weekly_loss_pct` (default 8%), T191's `max_weekly_gain_pct`
(default 6%, "protect a good week"), and T194's `max_open_exposure_pct` (default 40%, the one
aggregate-exposure sibling of the already-ported sector-$ cap and open-risk cap that was never
closed). A bad week — or a good week worth protecting — could be traded through entry-by-entry
on the live DE-primary path, since each individual candidate only needed to clear DE's own
per-candidate gates with no portfolio-wide weekly-P&L awareness at all.

**Fix — weekly P&L**: `weekly_net_pnl_pct` (a signed % of equity, matching the fallback gate's
own negative-for-loss/positive-for-gain convention) was hoisted OUT of its enclosing `if
_needs_weekly and equity > 0:` block into a typed `float | None = None` local computed BEFORE
that block — so it survives to the per-candidate `_call_decision_engine()` call site
regardless of whether the earlier block ever early-returned or was even entered (the original
`weekly_net_pnl` variable only ever existed INSIDE that conditional, which would have raised a
`NameError` at the later call site whenever the block's own guard was false).

**Fix — open exposure**: reuses the SAME already-summed `_open_sector_values` dict the
sector-$ cap already computes (summing across every sector bucket gives the portfolio-wide
total) rather than a second, independent `entry_price * shares` pass over the open book that
could silently drift from the sector-cap's own number if either were ever changed without the
other.

Both threaded through `_call_decision_engine()`'s signature + `config_overrides` via the
established conditional-inclusion pattern (`**( {...} if X is not None else {} )`). Both new
`hard_rejects.py` checks are pure DIRECT-comparison gates (unlike the sector-$/open-risk caps,
which project the candidate's own not-yet-sized worst-case contribution on top of the
already-open aggregate) — weekly P&L and total open exposure are both already-realized
portfolio state with nothing left for a single candidate to add before the comparison.

**A real, self-caught test-writing mistake during development, not shipped**: the first
version of both new write-side wiring tests' "is this key conditionally guarded" check
searched BACKWARD from each dict key for the guard clause — copying
`test_index_trend_config_wiring.py`'s own established pattern verbatim. That pattern only
works when the guard sits on the SAME line as the first dict key (a single-line
`**( {"key": val} if val is not None else {} )` form). The new code's guard trails AFTER a
multi-line dict body (`**( {"key1": ..., "key2": ...,\n  ...}\n  if val is not None else {} )`),
so the backward-lookback window never found it, and both tests failed against genuinely-
correct code. Fixed by searching FORWARD from the first key to the closing guard clause
instead — a check robust to either formatting, not tied to one specific code shape.

**Tests**: `hard_rejects.py` gained 10 cases (weekly-loss-blocks, weekly-loss-within-limit,
gain-lock-blocks, gain-within-threshold, gate-skipped-when-absent, exactly-0.0%-does-not-block-
either-side, open-exposure-blocks, open-exposure-within-cap, gate-skipped-when-absent,
custom-threshold-respected). New `services/market-data/tests/test_weekly_pnl_and_open_
exposure_config_wiring.py` (11 cases, source-text extraction — `paper_trading_engine.py` can't
be imported directly in this test environment) verifying the write-side wiring: both values
are threaded, both are conditionally guarded (via the corrected forward-search technique
above), the weekly threshold fallbacks match the real `cfg.get(...)` defaults exactly,
`_weekly_net_pnl_pct` is hoisted with a typed `None` default BEFORE the conditional block, the
sign convention is a signed percent (not an absolute dollar value), and `open_exposure_pct`
reuses the sector-values sum rather than a second independent computation.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted: disabling the
weekly-loss comparison specifically (caught by exactly 1 of 6 weekly-P&L tests); disabling the
open-exposure comparison (caught by exactly 2 of 4 dedicated tests); and removing the
write-side call-site argument entirely (caught by the dedicated call-site test). Full
144-test `hard_rejects.py` suite, 255-test decision-engine suite, and 1993-test market-data
suite green; pyflakes clean on both touched files (all 3 remaining `paper_trading_engine.py`
warnings confirmed pre-existing via `git stash` — only line numbers shifted).

### 3. T230-UX-MOBILE-RESPONSIVE-SETTINGS-PASS2 — settings.tsx's 2 missed grids + a stale CLAUDE.md tracker entry

**The gap**: the same background survey batch, checking all 12 pages this file's own most
recent `T230-UX-MOBILE-RESPONSIVE` entry (2026-08-02) names as "remaining deferred"
(`journal.tsx`, `portfolio.tsx`, `board.tsx`, `strategies.tsx`, `decide.tsx`, `regime.tsx`,
`insider.tsx`, `congress.tsx`, `sector-rotation.tsx`, `intelligence.tsx`, `forecast.tsx`,
`settings.tsx`) — found ALL 12 already have real, dedicated responsive-grid classes in
`globals.css` with matching `@media` overrides. This work simply never received its own
CLAUDE.md entry, leaving the tracker's own "still deferred" list stale in the OPPOSITE
direction from the more common failure mode documented elsewhere in this file (claiming
something is fixed when it isn't) — here, claiming something is STILL BROKEN when it's
actually fixed. Two genuine exceptions were found on `settings.tsx` specifically, missed by
its own otherwise-thorough original fix pass: a Position Sizing grid (line ~948) and an
Export/Import card-row grid (line ~1703), both with no `className`/matching CSS at all.

**Also found, lower-confidence since never individually named by CLAUDE.md before, and
deliberately left OUT of this pass's scope**: `admin-health.tsx`, `signal-accuracy.tsx`,
`paper-portfolio.tsx`, `horizon-compare.tsx`, `watchlist-rotation-explainer.tsx`, and
`improvements.tsx` itself all still have real unfixed rigid grids — a genuine future
candidate, not silently dropped, just out of scope for this specific batch.

**Fix**: the Position Sizing grid was byte-identical to the page's own pre-existing shared
`grid2` style object (already reused at 8 other call sites via `className="settings-grid2"`)
— folded directly into that existing pattern rather than inventing a redundant new class. The
Export/Import grid uses a distinct 12px gap (not `grid2`'s 16px), so it got its own new
`settings-export-import-grid` class + matching `@media (max-width: 767px)` override, following
the exact same page-scoped-class convention every prior pass in this series established.

**Verification**: a full `next build` (all 51 routes compile clean, `/settings` at 16.3 kB)
plus a direct grep of the compiled output confirming BOTH the base CSS rule and the
`!important` mobile override for `settings-export-import-grid`/`settings-grid2` are present in
`.next/static/css/*.css`, and the class name is present in the compiled `settings-*.js` chunk
— not just correct-looking source. No dedicated test file, matching this whole series'
established precedent that CSS/JSX-only page fixes are verified via typecheck + a full
production build rather than unit tests (nothing imports `settings.tsx` directly). Frontend
typecheck and the full 132-test vitest suite unaffected.

**What to check if this looks wrong**:
```bash
docker exec stockai-decision-engine-1 grep -n "restricted_symbols\|weekly_net_pnl_pct\|open_exposure_pct" /app/src/api/core/hard_rejects.py
docker exec stockai-market-data-1 grep -n "_weekly_net_pnl_pct\|_open_exposure_pct" /app/src/services/paper_trading_engine.py

# Confirm a real restricted symbol is actually blocked live (needs a valid service/admin JWT):
docker exec stockai-decision-engine-1 curl -s -X POST 'http://localhost:8009/decide/SNDK' \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"style":"SWING","equity":100000,"open_positions":0,"max_positions":6,"live_price":10.0,"game_plan":{},"market":"US","daily_pnl_pct":0.0}'
# Should return a BLOCKED verdict citing the restricted-symbol list.

docker exec stockai-frontend-1 sh -c "grep -o 'settings-export-import-grid[^}]*}' /app/.next/static/css/*.css"
```

**Design invariant reinforced (the Nth recurrence of exactly this class in this file's own
history)**: a tracker/CLAUDE.md entry claiming a page/feature is "still deferred" or "still
open" needs the same re-verification against current code as one claiming something is
"done" — this session's own finding #3 is the mirror image of every prior "stale tracker
entry" incident documented elsewhere in this file, just caught from the opposite direction.

---


## Completing the Interrupted Survey: meta_trainer.py Scaler Leakage + 2 More Digest
## Send-Loop Gaps (2026-08-24)

**Continues Tier 301** (documented immediately above) — after deploying that batch, resumed
the 2 checks the earlier org spend limit had cut off mid-run: the `scheduler.py` alert-job
audit and the signal-engine/ml-prediction gap check. Both were re-dispatched fresh and this
time ran to genuine completion.

### 1. AUD301-METASCALER-LEAKAGE — StandardScaler fit on the full dataset before the train/validation split

**The gap**: `meta_trainer.py:293` did `scaler.fit_transform(X)` on the ENTIRE dataset (train
+ validation combined, up to 20,000 rows) BEFORE the 80/20 chronological split ran. The held-
out validation rows' own mean/variance leaked into the normalization applied to the training
rows (and vice versa) — a real, if noisy, form of train/test contamination. Since the AUC
computed on `X_val` directly gates whether this retrained meta-model is promoted over the
currently-deployed bundle (`SELFIMPROVE-PROMOTION-GATES-INCOMPLETE`'s own `MIN_AUC_IMPROVEMENT`
check a few lines below), an inflated AUC could let a genuinely worse model pass the gate.

**Confirmed not already fixed**: this exact function has been touched by at least 6 prior
`AUD232-0xx` fixes (query join, point-in-time fundamentals, feature-column ordering, the
promotion-gate logic itself) plus `T247-ML-META-FEATURE-ORDER` — none mention the scaler
fit/transform ordering. The sibling per-symbol trainer, `trainer.py`, already gets this right
in 2 separate places (`fit_transform` on `X_train` only, then `.transform()` on `X_es`/`X_cal`/
`X_test`/`X_te`) — confirming this was a genuine, isolated deviation from an already-established
convention, not a systemic unaddressed assumption.

**Fix**: moved the 80/20 chronological split to run FIRST, on the raw unscaled feature matrix,
then fit `StandardScaler` only on the resulting train slice and `.transform()` (never re-fit)
the validation slice. The scaler persisted into the model bundle for live inference
(`predict_meta()`, which already only ever calls `.transform()`) is completely unaffected.

**Verification**: existing `test_meta_trainer.py` (9 cases) and `test_meta_trainer_feature_
dedup.py` suites both green after the fix — no behavioral regression at any call site that
already exercises this function. Full 82-test ml-prediction suite green; pyflakes clean (the
one pre-existing unused-import warning in this file confirmed via `git stash` to predate this
change).

### 2. AUD301-POSTOPEN-PAPERPORTFOLIO-DIGEST-SENDLOOP — 2 more unguarded send loops in scheduler.py

**Re-confirmed most of the codebase already fixed this class of bug**: `AUD256` fixed exactly
`send_premarket_brief`/`send_morning_digest`; `Tier 266`'s own 2026-08-05 doc-only audit
flagged "the other 7 alert loops still lack [this]" as future work, never shipped at the time.
Re-checking all ~24 candidate multi-recipient loops directly found nearly all of them (`check_
volume_anomalies`, `check_short_squeeze_alerts`, `check_squeeze_ignition_alerts`, `check_
prebreakout_alerts`, `check_gamma_unwind_alerts`, `check_value_area_breakdown`, `check_top3_
conviction`, `check_signal_alerts`, `check_price_alerts`, `check_technical_alerts`, `check_
earnings_beat_screener_alerts`, `check_portfolio_drawdown_alerts`, `send_weekly_theme_
forecast`, `send_weekly_trade_coach`, `check_sector_rotation_alerts`, `check_early_earnings_
news_alerts`) have SINCE received the fix under their own `AUD266` tags — quietly closing most
of Tier 266's own backlog since it was written, without a dedicated CLAUDE.md entry.

**2 genuine gaps remained**:

1. **`send_post_open_digest(market, window)`** (`scheduler.py:8515`, send loop ~8791-8807) —
   missing BOTH halves entirely: no per-recipient Redis dedup key, no try/except around the
   send call — a bare `for user in users:` loop under only the function-level except.
2. **`send_paper_portfolio_digest()`** (`scheduler.py:9334`) — also missing both halves, and
   WORSE than every sibling: the per-portfolio metrics computation itself (risk metrics,
   closed-trade queries, unrealized-P&L math) sat unguarded inside the same nested loop, not
   just the send call — a single portfolio's data anomaly (e.g. `initial_capital == 0` →
   `ZeroDivisionError`) could abort the digest for every OTHER user and portfolio still left
   in that cycle. Also found, while restructuring this same loop: `portfolios` (a query with
   NO per-user filter at all — every user gets the identical active-portfolio list) was being
   re-executed once per user in the outer loop, a pure `O(n_users)` redundant-query waste.

**Fix**: ported the exact `AUD256`/`send_morning_digest` pattern to both — a per-(user,
market, window, date) / per-(user, portfolio, date) Redis dedup key checked before the send
and set only after a confirmed successful send, plus a dedicated try/except around the send
call. For `send_paper_portfolio_digest()` specifically, the try/except was widened to wrap the
ENTIRE per-portfolio block (metrics computation + trade queries + send), not just the send
call, matching the wider blast radius this specific loop's own structure demanded. The
redundant `portfolios` query was hoisted to run exactly once, before the outer per-user loop.

**A real, self-caught test-quality bug during adversarial verification, not shipped**: the
first version of the paper-portfolio-digest isolation test used
`body.rindex("try:", 0, metrics_call_idx)` to locate the wrapping try block — copying the
naive "find the nearest preceding `try:`" idea without checking whether it was unique. There
are actually TWO `try:` blocks before the metrics call in this function: the outer function-
level one (wrapping the whole `with SessionLocal()` block) and the real per-portfolio
isolation try. Sabotaging the fix (removing the per-portfolio `try:`, replacing it with `if
True:`) did NOT fail this test — the search silently fell back to the unrelated, earlier
function-level `try:`, which still happened to precede the metrics call regardless of whether
the real fix was present. Caught by noticing the sabotage produced a "still passes" result
(this repo's own standing discipline treats that as a finding in its own right, not a shrug) —
fixed by anchoring the test on the exact structural adjacency between the dedup-check's own
closing `except Exception:\n    pass` and the per-portfolio isolation try's opening clause
immediately after it, a marker that can only exist if the real fix is genuinely present.
Re-verified: the corrected test now fails with a clear `ValueError` when the fix is removed,
rather than silently passing for the wrong reason.

**Tests**: `services/market-data/tests/test_post_open_and_paper_portfolio_digest_send_loop.py`
(11 cases, source-text extraction — `scheduler.py` can't be imported directly in this test
environment, matching `test_morning_digest_send_loop.py`'s established pattern exactly) —
dedup-checked-before-send, dedup-key-set-only-after-success, per-recipient error isolation,
errors logged/counted without re-raising, dedup key correctly scoped (per market+window for
`post_open_digest`; per user+portfolio for `paper_portfolio_digest`, so a user with 2 active
portfolios still gets 2 separate digests), and the redundant-query fix.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted (confirmed byte-
identical via `diff` before moving on): the corrected isolation-scope test described above;
removing `send_post_open_digest()`'s dedup check entirely (caught by 2 dedicated tests); and
re-introducing the per-user redundant `portfolios` query (caught by the dedicated query-count
test). Full 2004-test market-data suite green (up from 1993); pyflakes clean on both touched
files (all 5 pre-existing warnings across the two files confirmed via `git stash` to predate
this change).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "post_open_digest\|paper_portfolio_digest" /app/src/services/scheduler.py | grep -i "redis_key\|recipient_send_error"
docker exec stockai-ml-prediction-1 grep -n "AUD301-METASCALER-LEAKAGE" /app/src/training/meta_trainer.py

# Confirm no duplicate post-open digests fire within the same market+window+day:
docker exec stockai-redis-1 redis-cli keys 'stockai:post_open_digest:*'
docker exec stockai-redis-1 redis-cli keys 'stockai:paper_portfolio_digest:*'
```

---

