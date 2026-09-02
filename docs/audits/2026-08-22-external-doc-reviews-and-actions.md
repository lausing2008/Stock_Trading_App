## Review: docs/recomm_or_audit/AI_SIGNALS_SQUEEZE_ALERTS_DEEP_AUDIT_2025-08-22.md ("Deep Audit
## v2") — Raw Data Mostly Real, Every P0/P1 Analysis Conclusion Stale (2026-08-22)

**This is the THIRD external "verified against production" audit doc reviewed this session**
(after `AI_SIGNALS_SQUEEZE_ALERTS_AUDIT_2025-08-21.md` and its own `_VERIFICATION.md`) — same
discipline applied again: never trust a claim, however precisely cited, without independently
re-running it against the real database first.

### What actually reproduces, verified directly

- **Confidence-band inversion** (`43.12/40.52/37.10/39.92/27.81%`, n=`4177/970/690/471/356`) —
  **exact match** with a 60-day, BUY-only, `is_correct_10d IS NOT NULL` window. Real, and
  already known (Tier 261).
- **By-style paper-trading table** (SWING `30/20.00%/-$8841.08`, GROWTH `39/38.46%/+$2202.74`)
  — **exact match** with a 60-day window.
- **Risk-off regime table** (`10 trades, 0% win rate, -$10,805.54`) — the raw numbers are real,
  but see below for why the conclusion drawn from them is stale.

### What does NOT reproduce, or reproduces for the wrong reason

**The entry-score table uses a DIFFERENT, undisclosed window than its own section header
claims.** The doc's Part 5 groups the entry-score table under the same "(Last 60 Days)" header
as the by-style table above — but a 60-day-filtered query gives `{3: 4/50%, 4: 11/36.36%,
5: 25/16.00%, 6: 15/33.33%, ...}`, which does NOT match the doc's own claimed
`{4: 55.00%, 5: 13.79%, 6: 27.78%}`. Removing the date filter entirely (**all-time**) gives an
**exact match**. Two tables in the same labeled section silently used different windows.

**The "0% win rate" symbol list is only half right.** A direct all-time query
(`GROUP BY symbol HAVING COUNT(*)>=10 AND wins=0`) finds exactly **8** symbols genuinely at
0.00%: `SNDK (n=47), AMKR (n=40), KMT (n=31), 6809.HK (n=28), 3323.HK (n=26), SOXL (n=18),
AAON (n=18), WMT (n=14)`. Of the doc's other named symbols, **none** are actually 0% —
`CAT 8.75% (n=80), TSLA 2.86% (n=35), GOOG 9.52% (n=42), AMAT 5.63% (n=71), SMH 2.70% (n=37),
3986.HK 5.56% (n=72), 6082.HK 12.24% (n=49)` — all real, poor performers, but the doc's own
"15 symbols, exactly 0%" framing (and its P0 #3 "blacklist all 15" recommendation) overstates
what the data actually shows for 7 of them.

**The risk-off "STILL CRITICAL — STILL LEAKING" claim describes an already-fixed incident as a
live gap.** All 10 risk-off trades in the table entered `2026-06-25` through `2026-07-06` —
confirmed by querying `entry_time` directly. `T226-A` (the hard risk-off block, already
documented at length elsewhere in this file) was deployed **2026-06-30**, specifically *because
of* this exact incident. A direct query (`entry_time > '2026-07-07'`) confirms **zero** risk-off
entries in the 6+ weeks since. The doc re-surfaced the same historical rows every prior audit
already found, without checking whether a post-fix entry exists.

**The squeeze-outcome "0/108 evaluated — job may be broken" claim is the SAME false alarm this
session's own Tier 295 review already cleared for the PRIOR audit doc**, re-surfacing one
calendar day later. Checked `scheduler:job:evaluate_squeeze_alert_outcomes` in Redis directly:
the job ran successfully hours before this doc's own stated date. The oldest alert's 5-trading-
day window resolves exactly on `2026-08-22` (the doc's own date) — and that date's own price
bar for the relevant symbol simply hadn't landed in `prices` yet at query time. Confirmed
directly: `evaluate_squeeze_alert_outcomes()` and `evaluate_prebreakout_alert_outcomes()` were
manually re-triggered during this same session (see the `DESIGN-SQUEEZE-1D2D3D-WINDOWS` entry
above) and correctly backfilled 86 of 107 rows the moment T+1 entries existed — the mechanism
works; there was simply nothing left to fill for the newest few rows yet.

### Every P0/P1 recommendation describes something already built

- **Risk-off blocking** already exists and is MORE sophisticated than the doc's proposed
  `if state == "risk_off": return []` snippet — the real gate (`hard_rejects.py`) includes a
  time-boxed, self-expiring override the doc's version lacks.
- **Squeeze outcome tracking** already exists, runs daily, and was independently re-verified
  working end-to-end earlier this same session.
- **Symbol blacklist** already exists (`RestrictedSymbol`, real admin CRUD routes, already
  consulted inside `_scan_for_entries()` at `paper_trading_engine.py:4395`) — the doc's P0 #3
  proposes a brand-new mechanism for something already built.
- **Confidence-formula fix** — the doc's own proposed fix (a hand-picked
  `ml_penalty`/`ta_penalty`/`ml_bonus`/`ta_bonus` multiplier, chosen by eyeballing the current
  tables, zero validation) is the exact unvalidated-shortcut pattern this repo has already
  rejected twice. A real, materially more rigorous fix already exists
  (`AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK`) — a measured `calibrated_win_rate` score layer,
  gated behind `calibration_feedback_enabled` (default off on every real portfolio), meant to
  be turned on only after a real walk-forward validation, not asserted from a hand-tuned
  formula.
- **Entry-score cap** — the doc's proposed `if entry_score in [5, 6]: return False` is a
  hardcoded rule off an n=18-29 sample with zero train/validation split. A real walk-forward
  sweep with a chronological train/validation split and a promotion-margin gate already exists
  (`gate_harness.py`'s `walk_forward_min_entry_score()`).
- **"Default to GROWTH style"** (P1 #7) doesn't map onto this system's actual architecture —
  each of the 5 real paper portfolios already has its own fixed style baked into how it was
  created; there is no single global default constant to flip.

### The doc's own "gap analysis" (Part 10) reached the wrong conclusion because of this

The doc's Part 9 cross-references "153+ improvements marked done" using its own invented
lowercase shorthand ids (`sa1`, `re2`, `pt-drawdown-circuit-breaker`) that mostly don't match
this codebase's real tracker id conventions (`SA-19`, `T226-A`, `AUD292-...`, etc.) — it's a
paraphrased summary, not a direct tracker cross-reference. Part 10 then asks "why aren't these
improvements reflected in production" and proposes 5 possible causes (code not deployed,
feature flags off, scheduler jobs not running, schema mismatch, config drift) — every one of
which, checked directly against the real system, is false for every specific case the doc
raises. The improvements ARE deployed; the confidence/entry-score fixes just haven't been
*activated* yet, which is a genuinely different, much narrower gap than the doc's framing
implies.

**Design invariant reinforced (now the 3rd time in a row for this exact class of doc in this
session)**: a document's precise, directly-checkable data (raw SQL query outputs, specific
counts) being real does not mean the ANALYSIS built on top of that data — what's broken, what
needs building, what's "still critical" — is accurate. Every external audit doc reviewed this
session got the raw numbers approximately right and the "what does this mean, what should we
do" layer wrong, usually by not checking whether a described gap had already been closed.

**Tracker**: `improvements.tsx` Tier 297 / ids `AI-SIGNALS-DEEP-AUDIT-V2-REVIEW-SUMMARY` (done,
reference), `AUD297-RESTRICTEDSYMBOL-POPULATE-8-CONFIRMED-ZERO` (todo — a real, re-verified
8-symbol list, distinct from the doc's own partially-wrong 15-symbol one),
`AUD297-ACTIVATE-EXISTING-CALIBRATION-AND-ENTRYSCORE-INFRA` (todo — both mechanisms already
exist and just need to be run/activated, not built).

**What to check if this needs re-verifying**:
```bash
# Confirm zero risk-off entries since the T226-A fix:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*) FROM paper_trades WHERE market_regime_at_entry='risk_off' AND entry_time > '2026-07-07';"

# Re-verify the entry-score table's real window (all-time, not 60 days):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT entry_score, COUNT(*), ROUND(COUNT(*) FILTER(WHERE pnl>0)*100.0/COUNT(*),2) win_rate, ROUND(SUM(pnl)::numeric,2) FROM paper_trades WHERE exit_time IS NOT NULL GROUP BY entry_score ORDER BY entry_score;"

# Re-verify the real 0%-win-rate symbol list (all-time):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT s.symbol, COUNT(*) n, COUNT(*) FILTER(WHERE so.is_correct_10d) wins FROM signal_outcomes so JOIN stocks s ON s.id=so.stock_id WHERE so.signal_direction='BUY' AND so.is_correct_10d IS NOT NULL GROUP BY s.symbol HAVING COUNT(*)>=10 AND COUNT(*) FILTER(WHERE so.is_correct_10d)=0 ORDER BY n DESC;"

# Confirm the squeeze evaluator's real coverage state right now (not the doc's stale snapshot):
docker exec stockai-redis-1 redis-cli get scheduler:job:evaluate_squeeze_alert_outcomes
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT alert_type, COUNT(*) total, COUNT(*) FILTER(WHERE return_5d IS NOT NULL) has_5d FROM squeeze_alert_outcomes GROUP BY alert_type;"
```

---


## Review: docs/recomm_or_audit/PAPER_TRADING_DEEP_AUDIT_2025-08-22.md — Most Accurate External
## Audit Doc Reviewed This Session, But 2 Recommendations Would Undo Already-Shipped Fixes
## (2026-08-22)

**This is the FOURTH external audit doc reviewed this session** — but the first one where
every single numeric table reproduces exactly against a direct, no-date-filter re-query:
overall performance (111/97/32.99%/-$6,781.75), by-portfolio (all 5 rows), by-style, by-exit-
reason, by-regime, by-entry-score, winner-vs-loser characteristics (`avg_hold 10.3 vs 4.0`,
`avg_rr 3.37 vs 2.72`, `avg_score/avg_conf/avg_kscore` all flat), and by-hold-duration — no
internal date-window inconsistencies of the kind found in the two squeeze-alert docs.

### The real risk: 2 recommendations would UNDO already-shipped, data-backed fixes

**GROWTH's `trail_trigger_pct` recommendation is backwards.** The doc reads
`_DEFAULT_CONFIG["trail_trigger_pct"] = 0.05` (the module-level default) and proposes lowering
it to `0.03` for GROWTH. But `paper_trading_engine.py`'s real per-style override table already
sets GROWTH's `trail_trigger_pct` to **0.07**, not 0.05 — deliberately RAISED under a real,
dated fix (`T227-D`): *"open GROWTH trades avg +8% — they need room"* — fixing a genuine bug
where breakeven and trailing both armed from the identical peak, giving zero additional
protection. The doc's recommendation would move this parameter in the OPPOSITE direction from
what a real, already-completed audit found necessary, purely because it never checked the
per-style override that supersedes the module default it read.

**`min_rr_ratio` and the regime size multipliers are treated as bare constants, when they're
not.** `min_rr_ratio` is already resolved through `_default_min_rr_ratio()`
(`SELFIMPROVE-NEVER-CALIBRATED-PARAMS`), reading a real calibrated-override file rather than a
hardcoded `2.0` literal. `regime_risk_off_size_mult`/`regime_choppy_size_mult` already exist at
exactly `0.50`/`0.75` — matching the doc's own "current config" table — but risk-off entries
are ALREADY hard-blocked by a completely separate, stronger mechanism
(`regime_risk_off_gate`, confirmed in this same session's earlier squeeze-alert review),
making the risk-off SIZE multiplier moot for new entries regardless of its own value.
`max_consecutive_losses` is already `3`, not an unstated default the doc's proposed "add: 4"
implies — a config nudge on an already-tuned real value, not a new feature.

### What IS genuinely novel — filed as real action items, with a caveat

`blocked_entry_scores` and `min_hold_before_stop` are real gaps: neither key nor mechanism
exists anywhere in the codebase. The underlying observation is sound —
`min_entry_score` is a pure `score >= threshold` comparison, structurally unable to express
"exclude 5 and 6 specifically, but allow 7+" the way the doc's own per-score table suggests is
needed. `gate_harness.py`'s `walk_forward_min_entry_score()` only searches threshold
candidates, never an exclusion set.

**But the SAME dataset that produced the "score 5/6 disaster" framing also shows entry_score
has almost zero winner/loser differentiation on average** (`avg_score`: winners 5.0, losers
5.1 — Part 9's own table). That's a real signal worth taking seriously: the 5/6 pattern could
be driven by a handful of large losing trades rather than a genuine per-score effect, at
n=18-29 per bucket. Any exclusion-set fix needs the SAME chronological train/validation
promotion-margin discipline every other live-decision parameter in this codebase already
requires (`gate_harness.py`'s own established pattern) before being wired into
`_should_enter()` — never a reflexive hardcode off this sample size, which is exactly the class
of mistake already found and rejected twice this session for the squeeze-alert docs.

**The "short holds = losses" finding has an equally-plausible alternative reading the doc
never considers**: a working stop-loss system is SUPPOSED to cut losers fast and let winners
run — that's not evidence stops fire prematurely, it's the natural signature of stops doing
their job correctly. Adding artificial hold-before-stop delay risks turning small, correctly-
cut losses into larger ones, with no walk-forward evidence yet that it helps.

### A real, separate correction surfaced along the way

Investigating the doc's own "Kelly endpoint... zero consumers" framing (shared with the
companion news/events doc, see below) found a real, already-built consumer:
`GET /paper-portfolio/backtest/risk-per-trade-sweep`
(`backtest_risk_per_trade_sweep()`/`sweep_risk_per_trade_pct()`) — a walk-forward validation
sweep built SPECIFICALLY to resolve "should Kelly inform real sizing or stay advisory,"
confirmed via its own docstring. `risk_per_trade_pct` is still a fixed `cfg` default in
`_should_enter()` today, so the underlying observation (Kelly isn't yet driving real capital
sizing) is directionally right — but the fix is running the ALREADY-BUILT sweep and applying
its own promotion verdict, not building a new wire-in from scratch as either doc proposes.

**Tracker**: `improvements.tsx` Tier 298 / ids `PAPER-TRADING-DEEP-AUDIT-REVIEW-SUMMARY` (done,
reference), `AUD298-BLOCKED-ENTRY-SCORES-VALIDATE-FIRST` (todo — a real, novel gap, needs
validation before hardcoding), `AUD298-KELLY-SWEEP-ALREADY-BUILT-RUN-IT` (todo — run the
existing sweep instead of building a new mechanism).

**What to check if this needs re-verifying**:
```bash
# Confirm GROWTH's real trail_trigger_pct (should be 0.07, not the module default 0.05):
docker exec stockai-market-data-1 grep -A5 '"GROWTH": {' /app/src/services/paper_trading_engine.py | grep trail_trigger_pct

# Confirm min_rr_ratio's real calibration-aware resolution (not a bare literal):
docker exec stockai-market-data-1 grep -n "_default_min_rr_ratio" /app/src/services/paper_trading_engine.py

# Confirm the Kelly sweep already exists and risk_per_trade_pct is still a fixed default:
docker exec stockai-market-data-1 grep -n "def backtest_risk_per_trade_sweep\|risk_per_trade_pct.*cfg\[" /app/src/api/paper_portfolio.py /app/src/services/paper_trading_engine.py

# Re-verify entry_score's actual winner/loser differentiation (should be nearly flat):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT CASE WHEN pnl>0 THEN 'winners' ELSE 'losers' END grp, COUNT(*), ROUND(AVG(entry_score)::numeric,1) avg_score FROM paper_trades WHERE stage='closed' GROUP BY grp;"
```

---


## Review: docs/recomm_or_audit/REALTIME_NEWS_EVENTS_INTELLIGENCE_2025-08-22.md — A Capability
## Inventory, Mostly Accurate, 2 Concrete Errors Found (2026-08-22)

**Different risk profile from the 3 numeric audit docs reviewed earlier this session** — this
one is a capability inventory ("what's already built vs. what's a genuine gap"), so the check
was a representative sample of "✅ DONE" claims against real code, not an exhaustive re-run of
every SQL query (there mostly aren't any to re-run).

### What checked out

- Earnings hard-reject (`dte <= 5` → hard reject) and sizing bands (`6-10 dte` → 50% size,
  `11-20 dte` → 75% size) — **exact match** to `_should_enter()`'s real code.
- `EconomicEvent.expected_value` — confirmed read/passed through in multiple places but never
  actually populated by any real writer, matching the doc's "column exists but never
  populated" claim exactly.
- `news-intelligence`'s `_mark_hot()` hot-news-flag function — confirmed exists as described.

### What's wrong

**The Cross-Asset Signals table (§1.7) names the wrong instruments entirely.** The doc claims
`sync_cross_asset()` syncs `"TLT, HYG, LQD, DXY, GLD, USO, ^TNX"` — 7 yfinance-style ETF/index
tickers, including gold and oil. The REAL implementation (this same session's own `IF-04
Phase 1` work, documented at length earlier in this file) syncs exactly **5 FRED macro
series** — `DGS10`, `DGS2`, `T10Y2Y`, `BAMLH0A0HYM2`, `DTWEXBGS` (10Y/2Y treasury yields, the
2s10s curve, HY credit spread, and the trade-weighted dollar index) — with gold/oil/bond-ETF
coverage EXPLICITLY documented as a deferred follow-on at build time, not silently missing.
The doc also cites the wrong endpoint path (`GET /events/cross-asset/latest` — the real route
has no `/latest` suffix, confirmed via grep).

**"Kelly endpoint... zero consumers" is false** — see the companion `PAPER_TRADING_DEEP_AUDIT`
review above for the full finding: a real walk-forward validation sweep
(`backtest_risk_per_trade_sweep()`) already consumes it, built specifically to answer this
exact question. `risk_per_trade_pct` just hasn't been promoted to a live portfolio yet from
that sweep's own output — a materially narrower gap than "zero consumers" implies.

### What's genuinely good about this doc

Unlike the squeeze-alert docs' analysis layer, this one's overall framing ("~80% already
built, gaps are composition/enhancement, not greenfield") holds up, and its 4 proposed
enhancements — FOMO composite score, Market Pulse dashboard, theme/sector rotation alert,
sector news sentiment rollup — are all genuinely additive, built from data this app already
computes, not duplicates of existing mechanisms. Its own Part 5 "What NOT to Build" list
(confidence calibration, risk-off blocking, symbol blacklist, entry-score calibration) is
correct in every item checked — the one document reviewed this session whose "don't rebuild
this" section is itself accurate.

**Tracker**: `improvements.tsx` Tier 299 / id `REALTIME-NEWS-EVENTS-INTELLIGENCE-REVIEW-
SUMMARY` (done, reference).

**What to check if this needs re-verifying**:
```bash
# Confirm the real cross-asset series list (should be 5 FRED series, not 7 ETF tickers):
docker exec stockai-event-intelligence-1 grep -n "_CROSS_ASSET_SERIES" -A 6 /app/src/services/economic.py

# Confirm the real endpoint path (no /latest suffix):
docker exec stockai-event-intelligence-1 grep -n "cross-asset" /app/src/api/routes.py
```

---


## Action Items Completed: Tier 297-299 (AUD297/AUD298) — RestrictedSymbol Populated, 3
## Walk-Forward Sweeps Run Live, 1 New Sweep Built (2026-08-22)

**All 4 real `todo` action items from the Tier 297-299 doc reviews were run to completion in
one session.**

1. **`AUD297-RESTRICTEDSYMBOL-POPULATE-8-CONFIRMED-ZERO`** — re-verified the 8-symbol
   confirmed-0%-win-rate list was still current (identical n/win counts), confirmed the exact-
   string-match enforcement in `_scan_for_entries()`, then added all 8 (`SNDK`, `AMKR`, `KMT`,
   `6809.HK`, `3323.HK`, `SOXL`, `AAON`, `WMT`) via `POST /paper-portfolio/restricted-symbols`
   against the live production API. Zero code changes.

2. **`AUD297-ACTIVATE-EXISTING-CALIBRATION-AND-ENTRYSCORE-INFRA`** — ran both the
   `walk_forward_min_entry_score()` and `walk_forward_calibration_feedback()` sweeps live for
   all 4 real style/market combos (`GROWTH/US`, `SWING/US`, `GROWTH/HK`, `SWING/HK`). Neither
   promoted anything for any combo — GROWTH/US and SWING/US produced real, meaningful baseline
   numbers with no candidate clearing the train-slice sample floor; GROWTH/HK and SWING/HK had
   zero signals pass the base entry gate at all in the tested window. **A real methodology
   correction made mid-run**: the calibration-feedback sweep's first attempt at `window_days=
   365` produced a misleadingly-uninformative "0 signals" result for every combo — traced to
   `signal_outcomes` only having resolved data back to `2026-05-25`, meaning a 365-day train
   window landed entirely BEFORE any real data existed. Re-ran at `window_days=90` (aligned to
   the real data range) and got genuine results: US combos showed calibration ON and OFF
   producing IDENTICAL train-slice numbers (the layer had zero measurable effect on these
   candidates in this window) — correctly NOT promoted, per the sweep's own train-slice-first
   gate. **Net result: `calibration_feedback_enabled` should stay off for every real
   portfolio** — the sweep found no measurable benefit, which is the honest answer this
   mechanism exists to produce, not evidence it was "not yet tried."

3. **`AUD298-KELLY-SWEEP-ALREADY-BUILT-RUN-IT`** — ran `backtest_risk_per_trade_sweep()` live
   for all 4 combos using each real portfolio's own actual historically-traded symbol list (28
   symbols for GROWTH/US from portfolio 1, 26 for SWING/US from portfolio 3, 8 for GROWTH/HK
   from portfolio 4, 3 for SWING/HK from portfolio 2). No Kelly candidate promoted anywhere —
   same honest "not enough data yet" result. But the baseline-validation runs themselves
   surfaced a real, useful signal independent of the Kelly question: GROWTH/US and SWING/US
   both show genuinely positive baseline performance in this window (`total_return_pct`
   +5.18%/+2.27%, `win_rate` 0.60/0.60, `sharpe_ratio` 3.70/1.58), while GROWTH/HK and SWING/HK
   both show genuinely NEGATIVE baseline performance (-2.45%/-3.82%, `win_rate` 0.48/0.37,
   `sharpe_ratio` -1.90/-2.75) — a real, current-data confirmation that the 2 HK portfolios are
   underperforming, surfaced as a side effect of running this sweep, not something either
   audit doc's own recommendations were built to find.

4. **`AUD298-BLOCKED-ENTRY-SCORES-VALIDATE-FIRST`** — the one genuinely new build. Added
   `replay_should_enter_excluding_scores()` and `walk_forward_blocked_entry_scores()` to
   `gate_harness.py` (`services/market-data/src/backtest/`) — the sibling to the existing
   THRESHOLD-only `walk_forward_min_entry_score()`, able to test a discrete EXCLUSION set
   ("keep the current `min_entry_score` floor, but additionally reject scores 5 and 6
   specifically") via a real, held-out validation, rather than the doc's own reflexive
   hardcode off an n=18-29 sample.

   **The one new line of logic**: `_should_enter()`'s `min_entry_score` comparison is
   internal — there's no `cfg` key to inject an exclusion set through. The fix: call
   `_should_enter()` with `cfg` already carrying the real, current floor (so a genuine
   below-floor signal is rejected exactly as it is live), then additionally reject via score
   IF the returned score falls in the exclusion set —
   `if not should or score in excluded_scores: continue`. This composes correctly with PT-3's
   calibrated-logistic-regression branch too (activates once ≥100 closed trades exist for a
   portfolio — none of today's 5 real portfolios have reached that yet): a hard-reject or a
   calibrated-no is already `should=False` regardless of the exclusion check, so this can only
   ever REJECT trades the plain-threshold baseline would have entered, never admit extra ones.

   `walk_forward_blocked_entry_scores()` mirrors `walk_forward_min_entry_score()`'s exact
   chronological 70/30 split and `_passes_promotion_margin()` gate, searching a small,
   deliberately non-exhaustive candidate list (`{}`, `{5}`, `{6}`, `{5,6}`) — built
   specifically to test the doc's OWN claim, not a blind powerset search across the full
   score range, which at this sample size would be pure overfitting bait. New
   `GET /paper-portfolio/backtest/blocked-entry-scores` admin endpoint, research-only, never
   writes to `portfolio.config`.

   **Tests**: `test_walk_forward_blocked_entry_scores.py`, 15 cases — 6 behavioral tests
   directly proving the exclusion-filtering semantics via a fake `_should_enter()` injected
   into the real extracted source (the surrounding fetch/game-plan/ATR machinery is an
   unmodified copy of the already-shipped `replay_should_enter()`, not independently re-proven
   here — confirmed via grep that `replay_should_enter()` itself has no dedicated behavioral
   test anywhere in this codebase either; its pipeline was instead live-verified against real
   production data, the same discipline applied here), plus 9 source-text regression checks
   for the sweep's own orchestration, matching `test_walk_forward_calibration_feedback.py`'s
   established convention for this exact Docker-only-dependency constraint.

   **Adversarially verified**: reverted the one new line back to a bare `if not should:` and
   confirmed exactly 2 of 15 tests failed — the 2 targeting exclusion-filtering directly (a
   score-in-set-rejected case and a mixed-batch case); the other 13, testing unrelated
   properties, correctly stayed green. Reverted and confirmed byte-identical via `diff` before
   moving on.

   **Deployed and live-verified**: only `market-data` restarted (confirmed via `docker ps`
   uptime diff), clean startup log, zero tracebacks. Ran the new endpoint live for all 4 real
   combos — every `baseline_validation` result (the empty-exclusion-set case) matches the
   sibling `min_entry_score` sweep's own baseline EXACTLY (identical `n_entered`/`win_rate`/
   `avg_return_pct` per combo), confirming the shared empty-set path is a genuine no-op against
   real data, not just in the unit tests. No exclusion candidate cleared the train-slice sample
   floor for any of the 4 combos — same honest "not enough resolved data yet" result as every
   other sweep run this session.

**Tracker**: `improvements.tsx` — all 4 action items (`AUD297-RESTRICTEDSYMBOL-POPULATE-8-
CONFIRMED-ZERO`, `AUD297-ACTIVATE-EXISTING-CALIBRATION-AND-ENTRYSCORE-INFRA`,
`AUD298-BLOCKED-ENTRY-SCORES-VALIDATE-FIRST`, `AUD298-KELLY-SWEEP-ALREADY-BUILT-RUN-IT`)
flipped from `todo` to `done`, each with a full implementedNote.

**What to check if this needs re-verifying**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "SELECT symbol, reason FROM restricted_symbols ORDER BY symbol;"

docker exec stockai-market-data-1 grep -n "def replay_should_enter_excluding_scores\|def walk_forward_blocked_entry_scores" /app/src/backtest/gate_harness.py

# Re-run any of the 4 sweeps for a real combo (needs an admin JWT):
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time, json; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'<admin_username>','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://localhost:8001/paper-portfolio/backtest/blocked-entry-scores', params={'style': 'GROWTH', 'market': 'US', 'window_days': 365}, headers={'Authorization': f'Bearer {tok}'}, timeout=120)
print(r.status_code, json.dumps(r.json(), indent=2))
"
```

---

