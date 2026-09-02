## Next Improvement Batch — 4 Real Fixes Found by Re-Running Established Bug-Class Sweeps (2026-08-21)

**User ask**: "next batch of improvements." Launched a background research agent to survey the
codebase, explicitly instructed to verify every candidate against `improvements.tsx`'s current
status and this file's own documented history before proposing anything — the exact discipline
this repo's own history shows is necessary, since re-proposing already-closed work is a real,
recurring failure mode. The agent's own bottom line: **the tracker is extraordinarily current**
— most candidates that looked open on first grep were already closed, several multiple times
over. Of 6 candidates reported, I personally re-verified all of them against current code before
building anything (one of the agent's own citations — a `_VAR_EPS` constant supposedly at
`risk.py:90` — turned out not to exist at all; the real finding underneath it did). A 4th fix
in this same batch — `AUD292-SQUEEZEWATCH-REVERT-NOTOLERANCE` — closes a real design gap this
session had already DOCUMENTED (but deliberately not fixed) the day before; see that item's
own dedicated section elsewhere in this file for the full original discovery, and its
`improvements.tsx` entry for the fix itself.

### 1. AUD293-RB-ALIAS-REDIS-POOLING-BLINDSPOT — 8 sites evaded the pooling audit's own closing grep

**Root cause**: this repo's own multi-session Redis-connection-pooling audit (documented at
length elsewhere in this file) ends with a "closing the loop" verification pass whose own final
grep pattern is `redis\.Redis\.from_url\|redis\.from_url\|redis_lib\.Redis\.from_url\|
redis_lib\.from_url` — confirmed via direct re-run that this STILL returns zero matches against
current code. But 8 sites (`paper_trading_engine.py`'s `_write_gate_block()`, `_write_no_entry_
summary()`, `_clear_no_entry_summary()`, the DE-shadow-comparison logger, and the T241-P6
position-scaling shadow writer/resolver's 3 call sites, plus `scheduler.py`'s position-scaling
drift-check job) instead did `import redis as _rb; _rb.Redis.from_url(...)` — an alias
INVISIBLE to that exact grep pattern. Confirmed via `grep -rn "import redis as \w\+"` that `_rb`
is used nowhere else in the repo, meaning these 8 sites were written AFTER the audit already
declared the codebase clean, not missed by it.

**Why it matters**: `_write_gate_block()` fires on every gate check per portfolio scan cycle
(drawdown, daily_loss, weekly_loss, consecutive_losses, regime_bear, regime_risk_off,
heat_brake, index_trend, market_cluster_cap, ...) and the position-scaling shadow writer/
resolver run on every real BUY candidate — the hot trading-decision loop, not a cold path.

**Fix**: swapped each site for the shared pooled helper — `from common.redis_client import
get_redis as _get_pool_redis` (matching 5 other already-correct sites in the same file) in
`paper_trading_engine.py`, and the module's own pre-existing `_get_redis()` wrapper in
`scheduler.py` (matching every other site in that file). Both are drop-in replacements with
zero behavior change, since `common.redis_client`'s connection pool already bakes in the
identical `decode_responses=True`/timeout settings the raw construction used.

**A real, pre-existing test broken as a direct consequence, found and fixed in the same pass**:
`test_position_scaling_shadow_resolve.py` mocks Redis by stubbing `sys.modules["redis"]` and
patching `redis.Redis.from_url` — a mechanism that silently stops actually testing anything the
moment the real code no longer imports `redis` directly at all. Fixed by re-pointing the
injection to `sys.modules["common.redis_client"]` instead, matching this repo's own documented
"a fresh import against a stubbed parent silently misses whatever was registered under the OLD
module path" gotcha (already hit and fixed multiple times elsewhere in this codebase's history)
— confirmed all 4 of that test's cases now genuinely re-exercise the real fixed code, not a
mock the fix made permanently unreachable.

**Tests**: new `test_redis_pooling_and_delisted_sweep.py` includes a repo-wide re-run of the
audit's own canonical closing grep — scoped to `src/` directories only this time, since several
test files legitimately reference the exact pattern in their own mocking-setup docstrings/
comments (a naive repo-wide re-run false-positived on these before the scoping fix). This is
now a standing regression guard against the identical blind spot recurring under yet another
alias in the future. Adversarially verified: reintroduced a raw `redis.Redis.from_url()`
construction and confirmed the grep-regression test caught it; removed the fix at one specific
site and confirmed the dedicated test caught it; both reverted, confirmed byte-identical via
`diff`. Full 1961-test market-data suite green (up from 1950); `pyflakes` clean.

### 2. AUD293-DELISTED-SWEEP-ROUND-N — `_scan_for_entries()`'s own BUY-candidate query, missed by every prior "exhaustive" sweep

**The headline finding**: `BUG-DELISTED-GENERATION-BLIND` already has 10+ fixed sites across 5
services from prior sweeps in this repo's own history, each one explicitly framed as an
"exhaustive" check. Re-running the identical check (every `Stock.active.is_(True)` site, testing
for the paired `Stock.delisted.is_(False)` filter) found **5 more real, previously-missed
instances** — most importantly `paper_trading_engine.py`'s `_scan_for_entries()`, THE function
that decides which real new paper trades actually get opened. Confirmed by direct trace (not
assumed from the function name) that this is the genuine entry point every real BUY decision in
this app flows through — the single highest-stakes site this whole bug class could exist at,
and it had simply never been touched by any of the "exhaustive" prior passes.

**The other 4 real sites**: `_compute_hk_breadth()` (a delisted stock frozen at its last real
price could still count toward the market-wide breadth %, which feeds regime classification —
a real trading-relevant signal, not cosmetic); `paper_trading_step()`'s watchlist-candidate
price pre-fetch (wasted work fetching a price for a symbol that can never become a real
candidate); `admin.py`'s watchlist-rotation candidate query (a real, live recommendation feed —
a confirmed-delisted stock could be recommended into a user's watchlist) and its
index-membership backfill (lower-stakes, metadata-only, fixed for consistency).

**Two look-alike sites investigated directly and correctly left untouched, not silently
skipped**: `candidate_event_mining.py` (ML training-data mining for the T241 position-scaling
gate) deliberately RETAINS delisted stocks — confirmed directly that `Stock.delisted` is set
independently of `Stock.active` in `ingestion.py`'s `_record_delisting_signal()` (a delisted
stock's `active` flag is never touched), so adding this filter here would have introduced
exactly the survivorship bias this repo's own CLAUDE.md "Known Ongoing Limitations" section
already documents as a deliberate, accepted gap. `signal-engine`'s `walkforward_backtest()`/
`trade_performance()` are read-only historical-research endpoints with no live trading action
resulting from their output, matching this bug class's own established "display-blind is
harmless, generation-blind is real" distinction already drawn multiple times in this file's
history.

**Fix**: added `Stock.delisted.is_(False)` alongside the existing filter at all 5 confirmed-real
sites, matching the exact convention already established at every other fixed site in this
codebase.

**Tests**: source-text regression checks for each of the 5 fixed sites, PLUS a dedicated
negative-check confirming `candidate_event_mining.py` was correctly left untouched (the string
`Stock.delisted` never appears in that file at all) — proving this was a deliberate decision,
not an oversight. Adversarially verified: removed the filter from `_scan_for_entries()` and
confirmed the dedicated test caught it with a real substring-not-found failure; removed it from
`admin.py`'s watchlist-rotation query and confirmed the same; both reverted and confirmed
byte-identical via `diff`.

### 3. AUD293-BETA-VAREPS — portfolio-optimizer's `_beta()` had AUD292-SHARPE-VAREPS's own bug, one function away from the fix

**Root cause**: `AUD292-SHARPE-VAREPS` (documented at length above) found and fixed a real bug
in `paper_portfolio.py`'s Sharpe/Sortino computation — a bare `variance > 0` gate lets
floating-point NOISE (a near-zero-but-nonzero variance, not an exact `0.0`) through as a valid
divisor, exploding the resulting ratio toward absurd values. `portfolio-optimizer/src/api/
risk.py`'s `_beta()` — a completely separate function in a sibling service — has the IDENTICAL
`var > 0` gate, never updated with the same epsilon-threshold fix.

**A citation correction, worth recording**: the research agent's own report cited "`_VAR_EPS`
established at line 90" in this same file as the sibling convention `_beta()` should have used
— checked directly and found NO such constant exists anywhere in `risk.py` at all (the agent's
citation was simply fabricated/misremembered). The real, correct finding underneath the
imprecise citation held up on direct inspection: `_beta()`'s own bare `var > 0` guard, sitting a
few lines above the `# IF-01: VaR/CVaR + stress testing` section header, is real and unfixed —
just not for the reason originally cited.

**Fix**: added `_BETA_VAR_EPS = 1e-9` (matching `AUD292`'s own established epsilon convention
exactly) and changed the guard from `var > 0` to `var > _BETA_VAR_EPS`.

**Tests**: 4 new cases appended to `test_portfolio_risk.py` — a deliberately-constructed
float-noise fixture (perturbing a target daily rate by `1e-17` per step, matching
`test_sharpe_variance_epsilon.py`'s own established construction for this exact bug class)
confirming the fix falls back to the neutral `beta=1.0` rather than exploding; a genuine-
variance case confirming the normal path is unaffected; the pre-existing `len(s) < 5` sample
floor confirmed unrelated/unaffected; and a source-text check confirming a real epsilon
constant is used, not a hardcoded literal. Adversarially verified: reverted to the bare
`var > 0` guard and confirmed exactly the 2 dedicated tests failed correctly (the other 2,
testing unrelated properties, correctly stayed green); reverted and confirmed byte-identical
via `diff`. Full 59-test portfolio-optimizer suite green (up from 55); `pyflakes` clean (zero
warnings, before and after).

**What to check if any of these 3 look wrong**:
```bash
# 1. Redis pooling — confirm the alias is gone and the canonical grep is clean:
grep -rn "import redis as _rb" services/market-data/src/
grep -rln "redis\.Redis\.from_url\|redis\.from_url" services/*/src/

# 2. Delisted sweep — confirm all 5 sites carry the filter:
docker exec stockai-market-data-1 grep -n "Stock.delisted.is_(False)" /app/src/services/paper_trading_engine.py /app/src/api/admin.py

# 3. Beta epsilon — confirm the constant is present and in use:
docker exec stockai-portfolio-optimizer-1 grep -n "_BETA_VAR_EPS" /app/src/api/risk.py
```

---


## Review: docs/recomm_or_audit/AI_SIGNALS_SQUEEZE_ALERTS_AUDIT_2025-08-21.md + Its Own _VERIFICATION.md — the SECOND, "Fully Verified" Document Itself Contains Fabricated Statistics (2026-08-21)

**Ask**: review both documents and, if correct, turn the findings into tracker action items and
document everything. **Not a rubber-stamp review** — every major numeric claim in BOTH
documents was independently re-derived via direct SQL against real production Postgres, plus a
direct scheduler-job-status Redis check for the one claim that needed it, exactly matching this
session's own repeatedly-applied discipline for external audit docs (several prior entries in
this file document the same pattern: a doc's own "verified" framing is not itself evidence of
accuracy).

### The ORIGINAL audit (`AI_SIGNALS_SQUEEZE_ALERTS_AUDIT_2025-08-21.md`) holds up well

Re-ran the underlying queries directly (BUY-only where the doc's own methodology implied it,
matching this repo's own established discipline of never pooling BUY+SELL sign-unaware into one
aggregate — see the `AUD261-OUTCOMESSUMMARY-UNSIGNED-SELL` fix elsewhere in this file for why
that mixing produces meaningless numbers):

- **Confidence-band inversion — real and reproduces cleanly.** BUY-only, last 180 days:
  `45.01% -> 42.14% -> 37.91% -> 37.59% -> 32.79%` as confidence rises through the 0-55/55-65/
  65-75/75-85/85+ bands — a genuine, monotonic decline, not a methodology artifact of one
  particular slice.
- **By-style/by-direction BUY win rates** (SHORT 43.33%, SWING 42.54%, LONG 44.61%, GROWTH
  40.75%, all n>1800 on the current, larger sample) reproduce closely against the audit's own
  smaller, dated snapshot (SHORT 41.78%, SWING 43.31%, LONG 40.25%, GROWTH 41.94%, n=127-159).
- **Entry-score win rates**: a direct query gives `{3: 37.50%, 4: 55.00%, 5: 13.79%, 6: 27.78%,
  7: 40.00%, 8: 20.00%, 9: 66.67%}` — this matches the ORIGINAL audit's own table almost exactly
  (55.00%, 13.79%, 28.57% at scores 4/5/6).
- **Trading-style P&L**: a direct query gives GROWTH `{38.00% win rate, +$2,010.79 total P&L}`
  and SWING `{26.09%, -$9,302.60}` — an EXACT match to the original audit's own numbers.
- **R:R-band win rates**: a direct query gives `{1.5-2.5: 24.39%, 2.5-3.5: 38.64%, 3.5+:
  36.36%}` — again matching the original closely (the original's own small "1.0-1.5" bucket
  with 10 trades no longer exists in current data, a minor, explainable sample-window drift,
  not a fabrication).
- **Exit-reason win rates**: real query gives `stop_hit 26.92%`, `breakeven_stop 13.79%`,
  `trailing_stop 100%`, `target_reached 100%` — the original audit's own claim of "0% win rate"
  for `stop_hit` was the one place the ORIGINAL document itself got a number wrong (the real
  figure is clearly nonzero), though its headline claim ("54% of trades hit stop_loss") is
  accurate. Worth noting this repo's own already-documented `AUD262-EXITREASON-CONFLATION-ROOT`
  finding is directly relevant here too — `stop_hit` is a mixed bucket containing both genuine
  losses and profitable trailing-stop-adjacent exits, so a flat "hit stop_loss = bad" framing
  (used by both audit documents) is itself an oversimplification of an already-known nuance.

### The SECOND document (`_VERIFICATION.md`) — titled "FULLY VERIFIED AGAINST PRODUCTION DATA,"
### the one a reader would trust MORE — is itself LESS trustworthy on 2 of its own tables

This is the actual headline finding of this review pass: the document that presents itself as
the rigorous, re-verified-against-real-data source is the one that fails an independent
re-check.

- **Entry-score win rates — fabricated.** The second document claims `{4: 15.00%, 5: 6.90%,
  6: 11.11%}`. The real database gives `{4: 55.00%, 5: 13.79%, 6: 27.78%}` — confirmed via TWO
  independent win-definitions (`pnl > 0` and `pct_return > 0`, which agree with each other
  exactly), ruling out a win-rate-definition mismatch as the explanation. The second document's
  own numbers simply do not match the real data at all.
- **Trading-style P&L — fabricated.** The second document claims GROWTH `$6,008.61` / SWING
  `$554.64` (with different win rates too). The real database gives GROWTH `+$2,010.79` / SWING
  `-$9,302.60` — not a rounding or windowing difference, a completely different number with the
  wrong sign implication (the second document's own SWING figure, `+$554.64`, reads as mildly
  positive; the real figure is a large, clear loss).
- **R:R 3.5+ "0% win rate" — fabricated, and used to justify a real recommendation.** The
  second document states "3.5+ has 0% win rate with 11 trades — strong signal to cap R:R" and
  builds its own "add a max R:R cap" recommendation directly on that number. The real win rate
  for that same 11-trade bucket is `36.36%` — comparable to the neighboring 2.5-3.5 band's own
  38.64%, not a striking outlier at all. The recommendation's own stated justification is false.
- **The squeeze-outcome-evaluator "may have a bug" claim — an avoidable overclaim, not a lie,
  but still wrong.** Both documents correctly observe 0/0/0 coverage across all 107
  `squeeze_alert_outcomes` rows (confirmed directly). The second document escalates this to
  "Root Cause: The `evaluate_squeeze_alert_outcomes()` job exists but may not be running or has
  a bug" — without ever checking the one piece of evidence that would answer the question
  directly. Checked it: `docker exec stockai-redis-1 redis-cli get scheduler:job:evaluate_
  squeeze_alert_outcomes` returns `{"status": "ok", "last_run": "2026-08-20T22:15:00Z", "error":
  null}` — exactly matching its own 18:15 ET cron schedule (`scheduler.py`, job id
  `squeeze_alert_outcome_eval_daily`). A direct query of `squeeze_alert_outcomes.fired_date`
  shows every single alert (across all 3 alert types) is dated between `2026-08-15` and
  `2026-08-21` — under 6 calendar days old. The evaluator's own shortest window (5 TRADING
  days) hasn't been reached by even the oldest alert yet, let alone the 10d/20d windows. **Zero
  coverage right now is the correct, expected state given the data's age — not a broken
  evaluator.** The job's own health was one Redis GET away and neither document made that
  check before escalating to "may have a bug."

### Two recommendations in both documents are stale, unrelated to the data-fabrication issue

- **"Implement a symbol blacklist"** — a real, working mechanism for exactly this
  (`RestrictedSymbol`, `shared/db/models.py:1894`) already exists, with real admin CRUD routes
  (`paper_portfolio.py`) AND is already consulted inside the real entry-scan function
  (`paper_trading_engine.py:4395`, inside `_scan_for_entries()`). The real remaining gap, if
  any, is a DATA/ops decision about which symbols to actually add to the already-existing
  table (e.g. TSLA, AMD per the audit's own worst-performer list) — not a missing feature.
- **"Invert confidence weighting"** — both documents propose a hand-picked `ml_bonus`/`ta_bonus`
  multiplier applied directly to the confidence formula. This codebase already has a real,
  materially more rigorous fix for exactly this problem
  (`AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK`, documented at length elsewhere in this file): a
  `calibrated_win_rate` score layer, gated behind `calibration_feedback_enabled` (default off),
  validated via a genuine chronological train/validation walk-forward sweep before ever being
  turned on for a real portfolio — never a hand-tuned multiplier applied unvalidated. Neither
  audit document was aware this already exists.

### What was correctly confirmed as genuinely unimplemented by both documents

- **Risk-off entry blocking IS live** (`hard_rejects.py:186`, `regime_risk_off_gate` defaults
  `True`) — both documents correctly call this fixed, confirmed via direct code read.
- **No entry-score cap exists** — confirmed via grep, correctly identified as unimplemented by
  both documents. (Whether one SHOULD be built, given the small, non-monotonic samples at
  scores 5-9, is a separate design question neither this review nor either document resolves
  definitively — the real data does show score 4 outperforming 5-6, but scores 7-9 climbing
  back up on very thin samples (5, 5, 3 trades) makes "cap at 4" a less clean recommendation
  than either document's own framing suggests.)
- **No `max_rr_ratio` cap exists** — confirmed via grep, correctly identified as unimplemented,
  though (per the fabricated-0%-win-rate finding above) the REAL data does not currently
  justify building one.
- **`_STYLE_PREFERENCE` still lists SWING first**, not GROWTH — confirmed via direct grep
  (`signal-engine/src/api/routes.py:40`), correctly identified as unchanged by both documents.

**Design invariant reinforced (a repeated pattern across this session's own history, now
proven true of a document specifically claiming to be the RIGOROUS, re-verified check)**: a
document's own "verified against production data" framing is not itself evidence the numbers
inside it are real — the re-verification pass has to be independently re-checked with the same
skepticism as the original claim, every time, regardless of how confident or detailed the
re-verification document's own presentation looks. This is the same discipline already applied
throughout this file's history to `DEEP_PLATFORM_AUDIT_2026-08-20_VERIFIED.md`,
`COMPREHENSIVE_SYSTEM_AUDIT_2026-08-16.md`, and `STRATEGIC_IMPROVEMENT_ROADMAP_2026-07-25.md` —
this is simply the first case where the SECOND, ostensibly more rigorous pass was the one that
failed the check, not the first.

**Tracker**: `improvements.tsx` Tier 295 / ids `AI-SIGNALS-SQUEEZE-AUDIT-REVIEW-SUMMARY` (done,
reference), `AUD295-SQUEEZE-EVALUATOR-FALSE-ALARM-CLEARED` (done, verification-only — no code
change, the job is healthy), `AUD295-RRBAND-MAXCAP-UNJUSTIFIED-BY-DATA` (todo — documented as a
real, data-checked non-finding, not silently dropped; a future max-R:R-cap decision should
start from the real 36.36% figure, not either document's own number).

**What to check if this needs re-verifying**:
```bash
# Confirm the squeeze-evaluator job is still healthy and re-check coverage once the oldest
# alerts (fired 2026-08-15) cross the 5-trading-day mark — has_5d should start populating:
docker exec stockai-redis-1 redis-cli get scheduler:job:evaluate_squeeze_alert_outcomes
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT alert_type, COUNT(*) FILTER (WHERE return_5d IS NOT NULL) AS has_5d, COUNT(*) AS total FROM squeeze_alert_outcomes GROUP BY alert_type;"

# Re-run the entry-score / trading-style / R:R queries directly rather than trusting either
# audit document's own numbers, including this review's own (data changes over time):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT entry_score, COUNT(*), ROUND(AVG(CASE WHEN pnl>0 THEN 1.0 ELSE 0.0 END)::numeric*100,2) FROM paper_trades WHERE stage='closed' AND entry_score IS NOT NULL GROUP BY entry_score ORDER BY entry_score;"
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT trading_style, COUNT(*), ROUND(SUM(pnl)::numeric,2) FROM paper_trades WHERE stage='closed' GROUP BY trading_style;"
```

---

