## Feature Reference: Tier 287 — 5-Item Improvement Batch (Goals, Tiered Pyramid, Drawdown
## Alert, Trade-Pattern Coach, Earnings Playbook) + 1 Deferred (2026-08-17)

**Same session as BUG-YFCALLVOL2 above.** After fixing that rate-limit bug, the user asked to
"start a new batch of improvements, can be a big one." A new, untracked
`docs/FEATURE_ROADMAP_PYRAMID_GOALS_2026-08-16.md` (user-authored, not written by this
session) proposed 13 features. Applying this session's own established roadmap-verification
discipline (per the earlier `STRATEGIC_IMPROVEMENT_ROADMAP_2026-07-25.md` precedent), a
research agent verified all 13 against real code FIRST — 8 already existed under different
names (pyramid scale-in, trailing stops, T232-PT6 partial exits, the pillar-based entry
scoring system, `/paper-portfolio/kelly`, T258 sector rotation, the already-live Sharpe/Sortino
dashboard, T258-TRADE-POSTMORTEM) and 5 were genuinely new. Presented via `AskUserQuestion`:
user delegated the choice of which 4 lower-risk candidates to build ("You can decide for me")
and explicitly deferred the 5th, higher-risk candidate (Conditional Order Chains: "Leave it out
of this batch (Recommended)"), then confirmed via a mid-turn message ("the 5th item document it
for later") that it should be documented, not silently dropped. Mid-batch, a direct user
question ("do we have multiple tiers Pyramid Trading on AI Signal alerts depends on the
situation?") led to investigating the real pyramid scale-in code, finding only a single tier
existed, and — after reporting this precisely — the user authorized adding a real 2nd tier as a
5th build item in the same batch ("yes add it please").

### 1. T286-STOCK-GOALS — User-Defined Price/Share/Date Goals Per Stock

**New `StockGoal` model** (`shared/db/models.py`) — `id`, `user_id`, `symbol`, `title`,
`target_price`/`target_shares`/`target_date` (all optional, but at least one required at
creation), `start_price` (snapshotted from the real live price at creation time),
`start_shares`, `notes`, `status` (`active`/`achieved`/`cancelled`). A brand-new table,
`create_all()`-friendly — no manual migration needed. Deliberately simpler than the roadmap
doc's own proposed schema (no `goal_type` enum categories).

**Backend**: 4 new routes in `services/market-data/src/api/routes.py` —
`GET /stocks/goals` (optional `symbol` filter), `POST /stocks/goals`, `PUT
/stocks/goals/{goal_id}`, `DELETE /stocks/goals/{goal_id}`. A pure `_compute_goal_progress()`
helper computes `price_progress_pct`/`days_remaining` FRESH on every read from the current
live/last-close price (`(current_price - start_price) / (target_price - start_price) * 100`,
`None` on a degenerate zero-distance target) — never persisted/stored, matching this app's own
established "don't persist a value that can be cheaply recomputed" discipline.

**Frontend**: new self-contained `frontend/src/components/StockGoalsPanel.tsx` (not inlined
into the already-4000+-line stock detail page), mounted as a new "Goals" tab alongside the
existing Overview/Research tabs on `frontend/src/pages/stock/[symbol].tsx`.

**A real latent bug found and fixed during this session's own comprehensive pyflakes sweep**
(not by a targeted bug report — caught by running `pyflakes` across every file touched this
whole session, not just the ones a specific task pointed at): `_stock_goal_out()`'s type hint
used a forward-reference string `"StockGoal"` with `StockGoal` never imported at module level
in `routes.py` (only inside each route function body, a redundant local `from db import
StockGoal` repeated 4 times) — pyflakes correctly flagged `undefined name 'StockGoal'`.
Harmless at runtime (Python never evaluates a string type annotation unless something calls
`get_type_hints()`), but a real correctness/consistency defect in the code as written. Fixed by
adding `StockGoal` to the module-level `from db import ...` line — matching
`EarningsAlertSubscription`'s own established convention exactly (imported once at module
level, no local re-imports anywhere) — and removing the 4 now-redundant local imports.

### 2. T286-PYRAMID-TIERS — Single-Tier Scale-In Extended Into a Real 2-Level Pyramid

**Before this fix**: `_scan_for_entries()`'s scale-in mechanism (`paper_trading_engine.py`)
could only ever add to a winning position ONCE — a fixed 5%-gain-trigger, 25%-of-position-value
add, gated on a single `"SCALE_IN"` note marker. The scale-OUT side of the SAME function
already had a proven 2-level structure (`partial_tp_pct`/`partial_tp2_pct` with
`PARTIAL1_TAKEN`/`PARTIAL2_TAKEN` note markers) that scale-in had never been extended to mirror.

**Fix**: added a genuine Level 2 — a new 10%-gain-trigger/15%-size add
(`scale_in_trigger2_pct`/`scale_in_size2_pct`, new config keys, defaults `0.10`/`0.15`), gated
on Level 1 already having fired (checked via the pre-existing `"SCALE_IN"` marker) and using a
new `"SCALE_IN2"` marker — an already-open trade with only the old single-marker history still
round-trips correctly, no data migration needed. Both levels independently re-check the ≥60%
confidence requirement. The pre-existing Level 1 config keys (`scale_in_trigger_pct`/
`scale_in_size_pct`, defaults `0.05`/`0.25`) are unchanged — this is purely additive. The SAME
cost-basis-blending (`T234-PT-SCALEIN-COST-BASIS-BUG`) and confidence/kscore/regime-at-entry
share-weighted blending (`AUD232-010`) already proven for Level 1 is reused identically at
Level 2, not re-derived.

**A stray dead line self-caught before shipping**: an early draft of the Level 1/Level 2
marker-check block left a duplicated, needlessly convoluted first assignment to `_si_l1_done`
immediately followed by the correct, simple one (`"SCALE_IN" in _si_notes_list`) — caught on a
follow-up read of the edited block and removed via a targeted edit.

### 3. T286-DRAWDOWN-ALERT — Real Email When a Portfolio Crosses Its Own Drawdown Limit

**The gap**: `_scan_for_entries()`'s existing drawdown circuit breaker
(`max_portfolio_drawdown_pct`, default 20%) already correctly BLOCKS new entries once a
portfolio's drawdown-from-peak crosses the configured limit, and already writes a passive
`_write_gate_block()` badge — but nothing ever actively told a user this happened; they'd only
see it if they happened to check the `/paper-portfolio` list page. `PaperPortfolio` has **no
`user_id` column** (paper portfolios are app-wide, not per-user — confirmed by reading the
model directly), so any new notification needed to go to the established `PriceAlert`-
subscriber audience, matching every other market-wide alert in this app, rather than a
portfolio "owner."

**Fix**: factored the circuit breaker's own peak-vs-current-equity computation out into a new,
reusable `_compute_portfolio_drawdown(session, portfolio_id, equity)` helper
(`paper_trading_engine.py`) — the new alert reads the EXACT same number the existing silent
badge already shows, never a second, independently re-derived computation that could drift
from it. New `check_portfolio_drawdown_alerts()` (`scheduler.py`, 1-minute interval, same
registration pattern as every other fast alert job, gated inside the existing
`if _is_alerting_enabled():` block covering every 1-minute alert job) with **state-transition
dedup** — a Redis key (`stockai:drawdown_alert_active:{portfolio_id}`, 30-day TTL) set only
while actively breached and cleared the moment drawdown recovers below the threshold, so a
portfolio that stays breached for hours doesn't re-email every single cycle, but a genuine
LATER re-breach after recovering fires again — mirroring `check_squeeze_watch_reverts()`'s own
state-based (not permanent one-shot) dedup reasoning. Per-recipient send isolation (a single
recipient's send exception must not abort the whole remaining recipient loop, matching
`check_earnings_beat_screener_alerts()`'s own established pattern). New
`send_portfolio_drawdown_alert_email()` in `email_service.py`.

### 4. T286-TRADE-PATTERN-COACH — Weekly Cross-Trade Behavioral-Pattern Digest

**The gap**: T258-TRADE-POSTMORTEM already reviews ONE closed trade at a time (plan-vs-actual,
exit-reason classification, max favorable excursion) — nothing rolled that up into a
cross-trade behavioral read across the whole account's own trading history.

**New module**: `services/market-data/src/services/trade_coach.py` — closely mirrors the
already-proven `theme_signals.py` architecture (T270) exactly: a pure
`compute_trade_patterns(session, window_days=90)` aggregation over ALL closed `PaperTrade` rows
across every portfolio (skips entirely below a 10-trade floor — `_MIN_TRADES_FOR_PATTERNS` —
rather than fabricating a "pattern" from a handful of trades), computing win rate/avg return by
exit reason, **avg giveback on winning trades** (`(peak - exit) / peak * 100` — reuses
`PaperTrade.highest_price`, already tracked LIVE during the hold, never a second `Price`-range
query the way T258-TRADE-POSTMORTEM's own max-favorable-excursion field needs), and avg
hold-days-vs-each-style's-own-expected-window (reuses `_STYLE_OVERRIDES`, the SAME value
T258-TRADE-POSTMORTEM's own per-trade field already uses — never a second, independently
re-derived expectation). A Claude Haiku call (`generate_trade_coach_summary()`) writes prose
EXPLAINING these real numbers, explicitly instructed to never give generic advice or predict
future performance — same fail-open/markdown-fence-stripping/honesty discipline as every other
LLM call site in this codebase (`theme_signals.py`, `macro_reaction.py`, `earnings.py`,
`risk_agent.py`).

**Delivery**: new `send_weekly_trade_coach()` (`scheduler.py`), gated behind a new
`trade_coach_email_enabled` admin flag (default OFF, matching every new opt-in Claude feature
since `CLAUDE-API-COST-AUDIT`), scheduled Sunday 17:45 ET (right after the existing weekly
theme forecast at 17:30), delivered to ALL users with an email set (matching
`send_weekly_theme_forecast()`'s own all-User audience — this is a single account-wide
aggregate across every portfolio, not scoped to any one symbol subscription, so
`PriceAlert`-subscriber scoping would be the wrong fit). New `send_trade_coach_email()` in
`email_service.py`. New toggle in `frontend/src/pages/admin-ai-features.tsx`'s "Global" card,
alongside the existing Auto Research / Macro Reaction / Earnings Impact / Weekly Theme Signals
toggles.

**A real ternary-expression bug self-caught before shipping, not shipped**: the first draft of
`generate_trade_coach_summary()`'s prompt construction used a Python conditional expression
spanning multiple f-string lines
(`prompt = (f"..." f"..." if result.win_rate is not None else f"...")`) that silently discarded
the win-rate line regardless of which branch was taken — Python's `if`/`else` binds at the
tightest scope, so the ternary only ever selected between two nearly-identical first-line
strings, never actually appending the win-rate line in either branch. Caught on a direct
re-read of the code (not by a test — none had been written yet at that point) and rewritten as
an explicit `win_rate_line` variable computed via a simple ternary BEFORE string concatenation.
A dedicated regression test
(`test_prompt_construction_does_not_crash_when_win_rate_is_none`) guards against this exact
class of bug recurring.

### 5. T286-EARNINGS-PLAYBOOK — Mechanical Hold/Reduce/Exit/Add Layer on Earnings Impact Emails

**The gap**: T249-EARNINGS-LLM-IMPACT already writes a real, LLM-generated impact paragraph
once `eps_actual` lands for a watched stock — but the email never translated that into a
structured decision a holder could act on.

**Design decision, made explicitly**: built as a MECHANICAL (rules-based, no second Claude
call) layer, matching T258-TRADE-POSTMORTEM's own established "mechanical fields are what this
repo's calibration-loop discipline says to trust first" precedent — reusing ONLY already-real
fields (event-intelligence's own 0-100 `earnings_strength_score`, `surprise_pct`, and the
recipient's own real open-position unrealized P&L%) rather than a fresh LLM interpretation.
**Deliberately does NOT compute an "expected move %"** — no real options-implied-volatility or
historical post-earnings-move data source exists anywhere in this app
(`EarningsEvent.post_earnings_return_1d`/`_5d` columns are DEFINED but never populated by any
sync job — confirmed via grep before deciding not to depend on them) — fabricating one would
violate this app's own established honesty discipline (CAPE, options-flow sentiment, every T249
alert) around real hold/reduce/exit decisions.

**New `_build_earnings_playbook()`** (`scheduler.py`): no open position → `WATCH`
(informational only). With a position: strength ≥70 → `ADD` ONLY if the position is ALREADY in
the green (a strong beat while still red is read as "the market hasn't confirmed the thesis
yet," not a reason to chase into an unconfirmed move — otherwise `HOLD`); strength 40-70 →
`HOLD`; strength 20-40 → `REDUCE`; strength <20 → `EXIT`. A missing `strength_score` defaults
to neutral `50.0` (the in-line/`HOLD` band) rather than crashing.

**Wiring**: `check_earnings_impact_alerts()` now does ONE bulk open-position query per cycle
(`_open_by_symbol`, built once before the per-event loop — never a per-recipient re-query,
avoiding an N+1 pattern) and builds a genuinely PER-RECIPIENT playbook section (since whether
a specific recipient holds a position, and at what P&L, is inherently per-user) appended AFTER
the existing LLM `impact_text` paragraph — never replacing it.

### Verification (whole batch)

**Tests**: `test_drawdown_alert.py` (20 cases), `test_trade_coach.py` (22 cases),
`test_trade_coach_scheduling.py` (17 cases), `test_earnings_playbook.py` (16 cases) — all new.
`_compute_portfolio_drawdown()` and `_build_earnings_playbook()` extracted via `exec()` from
real source (pure functions, no DB/network dependency at the extraction boundary);
`compute_trade_patterns()` tested against a real in-memory SQLite `PaperTrade` table using the
established real-sqlalchemy-via-stub-pop-and-restore technique
(`test_correlation_preentry.py`/`test_theme_signals.py`'s own precedent). Scheduler wiring
covered by source-text regression checks throughout (`scheduler.py` can't be imported directly
in this test environment — its import chain pulls in `apscheduler`).

**Adversarial verification** — every guard sabotaged and confirmed to fail correctly, then
reverted and confirmed byte-identical via `md5sum`:
- Drawdown alert: PA-D2 current-intraday-equity-counts-as-peak removed (2 tests caught it);
  state-key-cleared-on-recovery removed (1 test caught it); per-recipient try/except removed
  (1 test caught it).
- Trade coach: the 10-trade minimum floor removed (1 test caught it); the
  peak-greater-than-exit giveback guard removed (1 test caught it, correctly expecting `None`
  and getting `0.0` — a real, if less severe, artifact); the dedup-key-set-after-success
  ordering reversed (1 test caught it).
- Earnings playbook: the ADD-requires-already-green gate removed (1 test caught it);
  the appended-not-replacing playbook HTML reverted to plain LLM text only (1 test caught it).

Full 1,541-test market-data suite green (up from 1,466 at the start of this batch); `npx tsc
--noEmit` clean; full 132-test frontend vitest suite green; a full `next build` compiled all 51
routes clean. `pyflakes` clean on every touched backend file (confirmed via `git stash` that
every remaining warning predates this batch — only line numbers shifted from earlier code
additions in the same files this same session).

Also fixed along the way, unrelated to any single feature: `frontend/src/pages/
improvements.tsx`'s `TIER_COLOR` map was missing an entry for tier 286 (`BUG-YFCALLVOL2`'s own
tier) — the tier-section header rendering (`TIER_COLOR[tier]`, no fallback, unlike the filter
dropdown's own `?? '#6366f1'` guard) would have silently rendered `undefined` as the section's
accent color. Found while adding tier 287's own color and fixed both in the same edit.

**Deferred, not built**: **T286-CONDITIONAL-ORDER-CHAINS-DEFERRED** — the roadmap doc's 5th
candidate ("if X breaks $140, buy Y with stop at Z" chained conditional orders across different
symbols). Explicitly excluded from this batch per the user's own choice
("Leave it out of this batch (Recommended)"), confirmed via a follow-up mid-turn message that
it should be documented, not silently dropped. No design work has started. A future session
should scope this as its own dedicated task — real-money-adjacent risk, since it would touch
this app's live paper-trading entry pipeline directly, unlike any of the other 4 items in this
batch: what triggers a chain (a price cross, a signal change, a volume event), how many hops
deep a chain can go, how it interacts with the existing per-portfolio entry gates
(`min_confidence`, `min_kscore`, sector caps, the drawdown circuit breaker, etc.), and what
happens if an earlier link in the chain fails or the market gaps past its trigger price.

**Tracker**: `improvements.tsx` Tier 287 / ids `T286-STOCK-GOALS`, `T286-PYRAMID-TIERS`,
`T286-DRAWDOWN-ALERT`, `T286-TRADE-PATTERN-COACH`, `T286-EARNINGS-PLAYBOOK` (all `done`), and
`T286-CONDITIONAL-ORDER-CHAINS-DEFERRED` (`todo`).

**What to check if this looks wrong**:
```bash
# Confirm the Goals endpoints exist and StockGoal is imported at module level (not just locally):
docker exec stockai-market-data-1 grep -n "from db import.*StockGoal\|def _stock_goal_out" /app/src/api/routes.py

# Confirm the tiered pyramid scale-in markers:
docker exec stockai-market-data-1 grep -n "SCALE_IN2\|scale_in_trigger2_pct" /app/src/services/paper_trading_engine.py

# Confirm the drawdown alert job is registered and check its current active-breach state:
docker exec stockai-market-data-1 grep -n "portfolio_drawdown_alert_check" /app/src/services/scheduler.py
docker exec stockai-redis-1 redis-cli keys 'stockai:drawdown_alert_active:*'

# Confirm the trade coach feature flag and job registration:
docker exec stockai-redis-1 redis-cli get stockai:admin:feature:trade_coach_email_enabled
docker exec stockai-market-data-1 grep -n 'id="trade_coach_weekly"' /app/src/services/scheduler.py

# Confirm the earnings playbook is wired into the impact-alert email:
docker exec stockai-market-data-1 grep -n "_build_earnings_playbook\|_open_by_symbol" /app/src/services/scheduler.py
```

---

