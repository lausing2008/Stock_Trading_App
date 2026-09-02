## Next-Improvement Batch (2026-08-25) — 4 Real Fixes From 3 Parallel Survey Angles

**Trigger**: "next batch of improvements" — 3 background agents surveying frontend UX/error-
handling, decision-engine dual-scorer parity, and ml-prediction/ranking-engine validation gaps.
The dual-scorer parity survey came back genuinely clean (a real, exhaustive line-by-line
cross-reference against `hard_rejects.py` — every gate in `_should_enter()` has a verified,
correctly-implemented twin; the T232-DL-DUALSCORER-DEBT porting work is complete). The other
two angles each surfaced real findings, personally re-verified against current code before
building anything, matching this repo's own standing discipline that a background agent's
report is a claim to check, not a fact to act on directly.

### 1. AUD301-ML1B — `predict_latest_ensemble()`'s falsy-zero AUC coercion + missing oos_suppressed exclusion

**Root cause**: `predict_latest_ensemble()` (`services/ml-prediction/src/training/trainer.py`,
the 2-model XGBoost+RandomForest ensemble — the fallback `POST /ml/predict_ensemble` uses when
the 3-model `/ml/predict_ensemble_three` fails/404s) had 2 real defects, both siblings of bugs
already fixed once in `predict_latest_ensemble_three()` under `T237-ML1`:

1. `xgb_auc = float((xgb.get("metrics") or {}).get("auc") or ... or 0.55)` — a real, legitimate
   `auc=0.0` (a perfectly rank-inverted model — rare but real) is falsy in Python, so `or`
   silently substituted `0.55` (a plausible-looking "coin flip + edge" default), giving a
   degenerate model **near-normal ensemble weight** instead of the ~zero weight it deserves.
2. A model already flagged `oos_suppressed=True` by `predict_latest()` (CV-AUC < 0.52, coin-flip
   territory — `predict_latest()` already neutralizes its OWN `bullish_probability` to 0.5 in
   this case) still had its own real held-out `auc` metric feed the weighting formula at full
   strength. The top-level `oos_suppressed` flag on the RETURNED dict only informs
   signal-engine's downstream SA-27 compression AFTER the blend already happened — it never
   stopped a suppressed model's real AUC from pulling the blended probability toward its own
   value with disproportionate weight.

**Confirmed reachable in production**: `POST /ml/predict_ensemble`'s `mean_model_test_auc` is
consumed again with the same `or` pattern at `services/signal-engine/src/generators/signals.py:402`,
driving the ML/TA fusion weight — a corrupted ensemble weighting doesn't stay contained to this
one function's own return value.

**Fix applied**: `_model_auc()`/`_model_thr()` helpers use `is not None` (the correct presence
check — the 0.55/0.5 fallback should only fire when the metric is genuinely absent, never
merely equal to 0). A suppressed model's AUC is zeroed out for weighting purposes (mirroring
`predict_latest_ensemble_three`'s own `T237-ML1` exclusion), with a rescue path for the
genuinely-both-suppressed case (fall back to using both models' real AUCs) AND a further
rescue for the edge case where NEITHER model is suppressed but both happen to report a real
`auc=0.0` (no meaningful AUC signal to weight by at all — split evenly rather than divide by
zero or fabricate a preference). The `metrics.cv_auc_mean` field on the returned dict is now
computed via a separate `_cv_auc_mean()` reading each model's OWN real metric — reusing the
(possibly-zeroed-for-suppression) weighting values here would have misreported a suppressed
model's real CV AUC as 0.0 to any caller reading this diagnostic field.

**Tests**: `services/ml-prediction/tests/test_predict_latest_ensemble_falsy_zero.py` (9 cases).
`trainer.py` can't be imported directly in this test environment (its import chain pulls in
`lightgbm`, not installed locally — the identical constraint already documented for
`meta_trainer.py`'s own tests) — `predict_latest_ensemble()`'s real source is extracted via
`exec()` with `predict_latest`/`_artifact_path` faked, and exercised BEHAVIORALLY with real
dict inputs, not source-text regex checks. Covers: a real `auc=0.0` correctly gets near-zero
weight (not the 0.55 fallback), both-real-zero-AUC falls back to an even split without a
crash, a real nonzero-but-low AUC (0.52) isn't confused with "absent", the genuine
metric-absent case still correctly falls back through `cv_auc_mean` → 0.55, a suppressed model
with a nonzero reported AUC still gets zeroed weight, both-suppressed correctly restores real
AUCs rather than crashing, the top-level `oos_suppressed` flag still propagates when only one
model triggers it, `cv_auc_mean` reporting isn't corrupted by the zeroed weighting value, and
the sibling `buy_threshold` falsy-zero fix.

**Adversarial verification** — 3 sabotage/revert cycles, all caught correctly: reverting
`_model_auc()`'s `is not None` check back to a bare `or` (caught with a real, concrete
assertion showing a rank-inverted model getting `0.46` weight instead of `0.0`); removing the
`oos_suppressed` weight-zeroing entirely (caught with a suppressed model wrongly getting `0.45`
weight instead of `0.0` — and confirming the two fixes are independently tested, since the
both-real-zero-AUC test correctly stayed green since neither model was suppressed in that
case); reverting the `cv_auc_mean` reporting fix to reuse the zeroed weighting values (caught
with `0.3` reported instead of the correct `0.565`). All 3 reverted and confirmed
byte-identical via `md5sum` before moving on.

Full 91-test ml-prediction suite green (up from 82); pyflakes clean (the 2 remaining warnings
— unused `db.Signal`/`..features.SECTOR_COLUMNS` imports — confirmed via `git stash` to predate
this change).

**What to check if this looks wrong**:
```bash
docker exec stockai-ml-prediction-1 grep -n "_model_auc\|_cv_auc_mean\|oos_suppressed by predict_latest" /app/src/training/trainer.py
```

### 2. `alerts.tsx` — Signal Alert subscription rows showed the WRONG horizon's signal/confidence

**Root cause**: `SignalAlertsTab`'s `sigMap` was built from a SINGLE `useSWR` fetch —
`api.allSignals(getSignalStyle())` — using the viewer's own global default UI style, keyed
ONLY by `symbol` (no horizon dimension at all). Every subscription row shows its own real
`sub.horizon` badge (e.g. `GROWTH`, which uses deliberately relaxed thresholds per
`_STYLE_PROFILES`) right next to a signal/confidence pulled from whatever the viewer's OWN
default style happened to be — a user subscribed at `GROWTH` while their default UI style is
`SWING` saw SWING's signal/confidence displayed on that row, potentially showing `HOLD` while
the real GROWTH signal actually driving their email alert was `BUY` (or vice versa). The page's
own summary counts (Buys/Holds/Sells/Waits totals) were wrong for the identical reason.

**Fix applied**: replaced the single-style fetch with a parallel fetch across all 4 real
horizons (`SHORT`/`SWING`/`LONG`/`GROWTH`) via one `useSWR` wrapping `Promise.all`, building
`sigMap` keyed by `` `${symbol}|${horizon}` `` instead of `symbol` alone. New `sigFor(sub)`
helper resolves each row's lookup against that row's OWN `sub.horizon` (falling back to
`'SWING'` only when `sub.horizon` is itself null, matching the badge's own pre-existing
`sub.horizon ?? 'SWING'` fallback). The now-unused `getSignalStyle` import was removed.

**Verification**: `npx tsc --noEmit` clean, full 132-test frontend vitest suite unaffected
(no test imports `alerts.tsx` directly), a full `next build` clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'SIGNAL_HORIZONS\|sigFor' /app/.next/static/chunks/pages/alerts-*.js"
```
If a subscription row's displayed signal/confidence looks inconsistent with what
`GET /signals/{symbol}?style={horizon}` reports directly for that symbol/horizon, that's the
regression this fix closes — confirm `sigFor(sub)` is genuinely keyed by `sub.horizon`, not a
global style.

### 3. `paper-portfolio.tsx` — 3 broker-link/unlink actions had zero error handling (one real-money)

**Root cause**: 3 separate call sites independently duplicated the same
`api.brokerAssignPortfolio(...).then(() => api.brokerGetPortfolioBroker(...).then(setPortfolioBroker))`
promise chain with **no `.catch()` anywhere** — a failed request (network blip, 403, a stale-
but-locally-valid JWT returning "Unauthorized" per this app's own documented smart-401
handling) became a silently-swallowed, unhandled rejection. The badge showing broker-link
status is a plain `useState`, not SWR-polled, so on a failed mutation it stayed showing the
STALE pre-mutation state indefinitely, with zero indication to the user that anything went
wrong. The highest-severity of the three: the `RealMoneyConfirmDialog`'s `onConfirm` handler —
the exact confirmation step for routing REAL MONEY through a live E*Trade account — had this
same gap; a failed real-money link attempt gave the user no feedback at all, leaving them
unable to tell whether their real-money broker was actually linked or not.

**Fix applied**: factored all 3 call sites into one shared `assignPortfolioBroker(brokerId:
number | null)` helper with real `try/catch/finally`, using the file's own already-established
`reAuthError`-style inline error-text convention (a small `<span>`/`<div>` in
`#f87171`/red next to the relevant control). Added `brokerAssignPending`/`brokerAssignError`
state; the Unlink button, the sandbox/manual `<select>` dropdown, and the real-money confirm
dialog's `onConfirm` now all route through this one helper — disabled/busy states and error
text are consistent across all 3 rather than each site inventing (or omitting) its own.
Also fixed the CSV export button (a raw `fetch()` with no try/catch and no feedback on a
non-2xx response — the button previously just silently did nothing) with the same
disabled/error-text pattern, matching this same batch's discipline of fixing the whole class
of finding in one file rather than leaving a lower-severity sibling instance behind.

**Verification**: `npx tsc --noEmit` clean, full 132-test frontend vitest suite unaffected, a
full `next build` clean (`/paper-portfolio` compiled at 30.1 kB).

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'assignPortfolioBroker\|brokerAssignError' /app/.next/static/chunks/pages/paper-portfolio-*.js"
```
If a real-money broker link fails and shows no error, confirm the compiled bundle actually
contains `assignPortfolioBroker` — a stale cached build would still show the old, silent
failure mode.

### 4. `stock/[symbol].tsx` — 3 mutation handlers had `try { ... } finally { ... }` with no `catch`

**Root cause**: `toggleListItem()` (watchlist add/remove via the ★ Watch dropdown),
`handleFundRefresh()` (Analyst Ratings force-refresh button), and `removeAlert()` (delete
price-alert "×" button) all either used `try { ... } finally { ... }` with no `catch` clause,
or (in `removeAlert`'s case) had no `try`/`catch` at all — a rejected `api.*()` call (this
app's `request()` in `api.ts` always `throw`s on failure, never returns a falsy value) became
an unhandled promise rejection with zero user-visible indication anything failed.
`toggleListItem` is the highest-traffic of the three: clicking a watchlist row to add/remove a
stock, on failure, silently reverted the star/list-membership UI to its pre-click state with
no explanation — indistinguishable from "nothing happened because you didn't actually click
anything."

**Fix applied**: added `catch` clauses to all 3, each setting a new dedicated error-state
string (`listError`, `fundRefreshError`, `deleteAlertError`) rendered as small inline
`#f87171`-colored text next to the relevant control — matching the SAME established
`alertMsg`-style convention `createAlert()` already uses elsewhere in this file. `removeAlert`
additionally gained a `deletingAlertId`/busy-state guard (the delete "×" button now shows `…`
and disables itself mid-request, matching every other busy-guarded button on this page) — it
previously had no busy indicator of any kind.

**Verification**: `npx tsc --noEmit` clean, full 132-test frontend vitest suite unaffected, a
full `next build` clean (`/stock/[symbol]` compiled at 56.3 kB).

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'listError\|fundRefreshError\|deleteAlertError' /app/.next/static/chunks/pages/stock/\[symbol\]-*.js"
```

---


## Next-Improvement Batch (2026-08-25b) — Squeeze/Gamma Alert Family Re-Audited Clean + Real Revenue-Actual Gap Found and Fixed in event-intelligence

**Trigger**: "next batch of improvements" — 3 background survey agents launched (squeeze/gamma
alert family, research-engine/event-intelligence, remaining frontend pages), then a user
mid-turn message asked to run them one at a time instead, after all 3 hit the org's monthly API
spend limit simultaneously (the same class of interruption already documented twice this
session — Tier 301/302's own "interrupted survey" incidents).

### Squeeze/gamma alert family — personally re-audited, confirmed genuinely clean

The squeeze-alert survey agent got partway through (confirming `AUD292-SQUEEZEWATCH-REVERT-
NOTOLERANCE` already correctly fixed) before being cut off by the spend limit. Rather than
re-launch it, personally read the 3 functions its own final note said it hadn't reached yet:
`check_squeeze_watch_reverts()`, `evaluate_squeeze_alert_outcomes()`, `evaluate_prebreakout_
alert_outcomes()`. All 3 confirmed clean on direct read — careful, well-guarded logic
throughout: `check_squeeze_watch_reverts()`'s `_GAMMA_UNWIND_MIN_OI_CONCENTRATION * 100`
comparison specifically checked and confirmed correctly unit-matched (both `_GAMMA_UNWIND_MIN_
OI_CONCENTRATION`, a 0-1 fraction, and `concentration_pct`, written on a 0-100 scale at its
one real write site in `check_gamma_unwind_alerts()`, are properly converted before comparison
— NOT a sign/unit bug, despite this being exactly the class of bug the survey was tasked to
find); the absence-of-evidence-is-not-evidence-of-fade discipline (`_bearish_cache_fresh`
gating) is correctly applied; both `evaluate_*_outcomes()` functions have correct T+1 entry
discipline, per-window independence (never re-evaluates a closed window), and direction-aware
win hurdles matching `SignalOutcome`'s own established convention exactly.

### Real finding — `EarningsEvent.revenue_actual`/`revenue_surprise_pct` were real columns,
### read by the LLM prompt and the frontend, but NEVER WRITTEN anywhere in this codebase

**Root cause**: `generate_earnings_impact()` (`services/event-intelligence/src/services/
earnings.py`) reads and prompts the LLM with `revenue_actual`/`revenue_surprise_pct`;
`check_earnings_impact_poll()` passes both straight from the DB row; `_row_to_dict()` returns
`actual_revenue` to the frontend. But `_fetch_earnings_for_symbol()` — the ONLY function that
writes `EarningsEvent` rows — only ever wrote `revenue_estimate` (from `ticker.calendar`'s
forward-looking, pre-report "Revenue Estimate" field). `ticker.earnings_history` (the
historical-actuals loop this function's own EPS logic already iterates) has NO revenue column
at all — confirmed directly against a real live yfinance response before writing any code, not
assumed from the DataFrame's schema alone. This meant every single earnings-impact LLM prompt
always said `"Revenue actual: unavailable"` / `"Revenue surprise: unavailable"` regardless of
the real print — a permanent, silent half-feature gap, not a crash. The closest documented
precedent for this exact bug class is `EconomicEvent.expected_value` ("column exists but never
populated," already investigated and left unfixed pending a real data source) — this one had
simply never been noticed for `EarningsEvent`.

**Fix**: real historical revenue lives in `ticker.quarterly_financials`'s own `"Total Revenue"`
row — verified directly (not assumed) that its column dates align EXACTLY with `ticker.
earnings_history`'s own index (both are real period-end dates), so a simple period-end-keyed
dict join works with zero date-matching logic needed. Added a best-effort fetch/join
(`revenue_actual_by_period_end`), wrapped in its own isolated try/except matching the
pre-existing `earnings_dates` join's established fail-open convention right above it (a
failure here degrades to an empty dict, never aborts the whole historical sync for that
symbol). `revenue_surprise_pct` is computed from the row's own ALREADY-SET `revenue_estimate`
— confirmed via re-reading the `existing_pending` matching logic that a pending row's
`revenue_estimate` (written earlier by the separate calendar-path write, when the report
hadn't happened yet) survives untouched when the historical path later fills in the actual on
that same row, exactly the same 2-phase write pattern `eps_estimate`/`eps_actual`/`surprise_
pct` already use. Extracted a new `_compute_surprise_pct(estimate, actual)` pure helper
(matching `_compute_strength()`'s own established precedent for pure, separately-testable
scoring logic) and refactored the pre-existing EPS surprise formula to use it too — deduplicating
what would otherwise have been a second, near-identical inline copy of the same divide-by-abs
formula.

**Tests**: `services/event-intelligence/tests/test_earnings_revenue_actual.py` (14 cases) —
`_compute_surprise_pct()` is pure and dependency-free, tested directly via source-text `exec()`
extraction (a genuine beat, a genuine miss, both-None-degrades-safely, a zero-estimate
divide-by-zero guard, and — a real edge case worth its own test — a NEGATIVE estimate, e.g. a
loss-making quarter, correctly reports a POSITIVE surprise when the loss shrinks, since the
formula divides by `abs(estimate)` specifically to avoid the sign flip a naive `estimate`
divisor would produce). The join/wiring inside `_fetch_earnings_for_symbol()` makes real
yfinance + DB calls end-to-end, so it's covered via source-text regression checks, matching
`test_earnings_report_date_wiring.py`'s own already-established pattern for this exact
function. A real, self-caught test-writing trap during development, matching this repo's own
documented history of the identical mistake elsewhere: the first version of the try/except
isolation test anchored on the bare substring `"ticker.quarterly_financials"`, which matched
this SAME fix's own explanatory comment ABOVE the real code line before it ever reached the
actual `qf = ticker.quarterly_financials` call — fixed by anchoring on the real assignment
line specifically, the identical class of "matched the docstring, not the call" trap already
documented multiple times elsewhere in this file's history.

**Adversarial verification** — 3 sabotage/revert cycles, all caught correctly and reverted
(confirmed byte-identical via `md5sum` before moving on): removing the try/except isolation
around the `quarterly_financials` join (caught by the dedicated isolation test); removing
`revenue_actual`/`revenue_surprise_pct` from the `existing_pending` update branch (caught by
the dedicated both-fields-set test); removing `revenue_actual` from the fresh-insert path's
`on_conflict_do_update` `set_=` clause specifically — the "silently drops on re-run" regression
class this test exists to guard against (caught by the dedicated values-and-conflict-clause
test).

Full 284-test event-intelligence suite green (up from 270); pyflakes clean (the sole remaining
warning, an unused `db.get_session` import, confirmed pre-existing via `git stash`).

**What to check if this looks wrong**:
```bash
docker exec stockai-event-intelligence-1 grep -n "def _compute_surprise_pct\|revenue_actual_by_period_end" /app/src/services/earnings.py

# Live-check a real symbol's earnings row now carries revenue_actual (needs a real, already-
# reported EarningsEvent row for that symbol — won't backfill rows synced before this deploy
# until their next daily re-sync):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT s.symbol, e.report_date, e.eps_actual, e.revenue_actual, e.revenue_estimate, e.revenue_surprise_pct FROM earnings_events e JOIN stocks s ON s.id = e.stock_id WHERE e.eps_actual IS NOT NULL ORDER BY e.report_date DESC LIMIT 10;"
```
If `revenue_actual` is still `NULL` for a symbol you'd expect real coverage on, check whether
`ticker.quarterly_financials` genuinely has a `"Total Revenue"` row for that symbol's own real
period-end dates first — a thin/newly-listed/foreign-filer symbol can legitimately lack this,
which is a correct "no data" state, not a bug in this join.

---


## Next-Improvement Batch (2026-08-25c) — board.tsx's 9 Silent Mutation Handlers + journal.tsx's 2 (Real-Money-Adjacent Actions Included)

**Trigger**: continuing "next batch of improvements" — a frontend-pages survey agent (run solo,
after the prior batch's parallel 3-agent run hit the org's spend limit) audited 9 previously-
unchecked pages (`journal.tsx`, `portfolio.tsx`, `board.tsx`, `decide.tsx`, `insider.tsx`,
`congress.tsx`, `strategies.tsx`, `regime.tsx`, `sector-rotation.tsx`) for the same silent-
mutation-failure bug class already fixed this session in `alerts.tsx`/`paper-portfolio.tsx`/
`stock/[symbol].tsx`. `board.tsx` was the standout — 9 total unguarded handlers across the
page (6 primary trade-board mutations + 3 alert-modal actions), several touching irreversible
or financially-consequential actions (position close, real fill recording, permanent delete).

**All findings personally re-verified against current source before fixing** — confirmed
`portfolio.tsx`/`decide.tsx`/`insider.tsx`/`congress.tsx`/`regime.tsx`/`sector-rotation.tsx`
are genuinely clean (either proper existing try/catch, or read-only pages with no mutations at
all).

### `board.tsx` — 9 handlers fixed, plus a real structural finding

Confirmed a nuance the survey summary understated: `handleCloseConfirmed()` and
`handleFillConfirm()` DID already have `try/catch` — but only around their SECONDARY sync-to-
Positions/cash-update side effects (both explicitly commented `/* best-effort */`). The
PRIMARY mutation (`await api.updateBoardPlan(...)` — the actual close/fill/stage-change itself)
had **zero** error handling in every one of the 6 trade-board handlers
(`handleStageChange`/`handleCloseConfirmed`/`handleFillConfirm`/`handleFillSkip`/
`handleDelete`/`handleAdd`). A failed primary call threw before reaching any of the function's
own cleanup (`setCloseConfirmId(null)`, `setFillTarget(null)`, etc.), leaving modals stuck open
indefinitely with zero indication anything failed — worst for `handleCloseConfirmed`, whose own
pre-existing comment already flags it as "confirm before marking a trade closed (irreversible
PnL record)."

**Fix**: generalized the page's pre-existing `fillSyncMsg` toast (a bottom-center, auto-
dismissing notification only `handleFillConfirm` used) into a shared `boardMsg`/`showBoardError`
mechanism every handler now uses — a genuinely reusable pattern already half-built on this page,
extended rather than a 3rd notification convention invented from scratch. Every primary mutation
now wraps in `try/catch`, calling `showBoardError()` and `return`ing early on failure so the
confirm-modal/fill-target state stays exactly as it was (never silently cleared as if the action
had succeeded). `handleAdd()` changed its return type to `Promise<boolean>` so `AddCardForm`'s
own `submit()` only clears/closes the add-card form on a REAL success — a failed add now leaves
the user's typed symbol/notes in place to retry, instead of silently discarding them. The 3
`AlertModal` handlers (`handleAddPriceAlert`/`handleSetAll`/`handleToggleSignal`, all pre-
existing `try { } finally { }` with no `catch`) gained a new local `alertModalError` state
rendered as an inline banner near the modal header.

### `journal.tsx` — 2 handlers fixed

`handleDelete()` had **no try/catch at all** (a bare `await api.deleteJournalTrade(id)`) — a
real unhandled promise rejection on failure left the inline "Yes/No" delete-confirm buttons
stuck showing "Yes/No" forever with no error shown. `handleSave()` used `try { } finally { }`
with no `catch` — the exact same shape already fixed for `stock/[symbol].tsx`'s `toggleListItem`/
`handleFundRefresh` earlier this session. Fixed both with dedicated error states (`saveError`
rendered inside the add/edit modal; `deleteError` + a `deletingId` busy-state guard rendered
inline next to the Yes/No buttons, matching the busy-state convention already established
elsewhere on this page).

**Not fixed, deliberately**: `strategies.tsx`'s `toggleCompare()`'s silent `catch {}` — real,
but the survey's own weakest finding (a read-only backtest-comparison fetch, spinner correctly
clears via `finally` regardless, no data mutation at stake). Left as a documented, low-priority
item rather than padding this batch with a fix whose severity doesn't warrant the added
complexity.

**Verification**: no test file imports either `board.tsx` or `journal.tsx` directly (confirmed
via grep) — matching this codebase's established precedent for large, stateful, untested pages
(`PriceChart.tsx`/`_app.tsx`-only changes elsewhere in this file). Verified via `npx tsc
--noEmit` (clean), the full 132-test frontend vitest suite (unaffected, unchanged), and a full
`next build` (`/board` compiled at 15 kB, `/journal` at 8.35 kB, all 51 routes clean).

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'showBoardError\|boardMsg' /app/.next/static/chunks/pages/board-*.js"
docker exec stockai-frontend-1 sh -c "grep -o 'saveError\|deleteError' /app/.next/static/chunks/pages/journal-*.js"
```
If a close/fill/delete/add action fails on `board.tsx` and the modal silently closes anyway
(rather than staying open with a visible red-error toast), that's the exact regression this fix
closes — confirm the compiled bundle actually contains the fix, not a stale cached build.

---


## Recurring Sweep: Silent-Mutation-Handler Bug Class — 3 More Real Instances Found + Fixed, 4 False Positives Rejected (2026-08-26)

**Continues this session's own established sweep** (already fixed in `alerts.tsx`, `paper-portfolio.tsx`, `stock/[symbol].tsx`, `board.tsx`'s 9 handlers, `journal.tsx`'s 2) — a survey agent checked the remaining pages and reported 6 candidates. **Every single one was personally re-verified against the real source before touching anything** — 4 turned out to be fabricated or wrong, only 2 were fully real, and a 5th was real-but-overstated (already had a `catch`, just a silent one).

**Rejected, confirmed false via direct grep**:
- `settings.tsx`'s claimed `revokeApiKey()`/`api.revokeUserApiKey` — no such function or API call exists anywhere in the file. Fabricated.
- `insider.tsx`/`congress.tsx`'s claimed `handleAddToWatchlist()`/`api.addToWatchlist` — both files have exactly ONE `api.` call each (`eventsCongressRecent`, a pure read-only fetch). No mutation of any kind exists in either file. Fabricated.
- `settings.tsx`'s `handleDeleteBroker()` — real function, but already has a genuine `catch` block (line 302) setting a real `brokerMsg` error state. Already correctly fixed; the agent's claim was simply wrong.

**Real, confirmed, and fixed**:
1. **`watchlist.tsx`'s `remove()`/`moveToList()`** — both had ZERO try/catch around real `api.removeFromWatchlist`/`api.addToWatchlist` calls. Worse than the usual shape: since the busy-state-clearing lines (`setRemoving(null)`/`setMoving(null)`) ran only AFTER the awaited call with no `finally`, a failed request left the busy spinner stuck forever, not just silently reverted. `moveToList()` also has a real, honestly-flagged partial-failure edge case: if `addToWatchlist` succeeds but the subsequent `removeFromWatchlist` fails, the symbol ends up on BOTH lists — the new catch surfaces this rather than hiding it. Fixed using the page's own pre-existing `alertToast` mechanism (already used by 6 other handlers on this same page, e.g. the signal-alert toggle at line ~735) rather than inventing a new one.
2. **`positions.tsx`'s `toggleWatch()`** — same zero-try/catch shape. Fixed using the page's own pre-existing `showToast()` helper, matching the exact `.catch(e => showToast(...))` convention already used by 4 sibling handlers on the same page (`handleModalConfirm`, `removePosition`).
3. **`conditional-orders.tsx`'s `handleCancel()`** — DID have a `catch`, but a fully silent one (`catch { /* swallow — a stale row on next poll is harmless */ }`) with no user-visible feedback at all. Re-examined the comment's own reasoning and found it doesn't hold: `mutate()` is never called on the failure path either, so nothing refreshes until the next 15s poll regardless — a user has zero way to tell whether their cancel of a live, financially-consequential conditional order actually worked. Added a `cancelError` state (matching the sibling `CreateOrderForm` component's own existing error-styling convention, `color: '#f87171'`, reused rather than invented) rendered above the orders table, auto-clearing after 5s.

**Verification**: `npx tsc --noEmit` clean, full 132-test frontend vitest suite unaffected (no test imports any of these 3 pages directly), a full `next build` clean across all 51 routes — confirmed via direct grep that all 3 fixes' distinctive error strings reached the actual compiled chunks (`positions-*.js`, `watchlist-*.js`, `conditional-orders-*.js`), not just source.

**Design invariant reinforced (the Nth recurrence of this exact discipline in this session's own history)**: a background survey agent's report is a claim to verify, not a fact to act on — this pass alone had a 33% outright-fabrication rate (2 of 6 findings referenced functions/API calls that don't exist anywhere in the named files) and a further 17% wrong-verdict rate (1 of 6 claimed "no catch" for a handler that already has one). Every finding was checked with a direct `grep`/`Read` against the real current file before any code was touched — this is not optional diligence, it's the only thing that prevented 3 of 6 "fixes" from being pure hallucination.

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'Failed to update watchlist for' /app/.next/static/chunks/pages/positions-*.js"
docker exec stockai-frontend-1 sh -c "grep -o 'Failed to remove\|Failed to move' /app/.next/static/chunks/pages/watchlist-*.js"
docker exec stockai-frontend-1 sh -c "grep -o 'Failed to cancel order' /app/.next/static/chunks/pages/conditional-orders-*.js"
```

---

