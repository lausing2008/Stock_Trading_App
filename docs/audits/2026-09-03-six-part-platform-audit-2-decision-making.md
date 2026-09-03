## Deep Audit Series (2026-09-03): Decision-Making — 2 of 6

**Scope**: decision-engine service (`services/decision-engine/src/api/core/{scorer.py,
hard_rejects.py,aggregator.py,sizer.py,regime.py}`, `routes.py`, `risk_agent.py`,
`llm_scorer.py`), plus its integration with market-data's `paper_trading_engine.py` — the
composite entry/exit gate for real trades. Sequential platform audit series (AI Signal → **this
domain** → Paper Trading → Model Training → Short Squeeze Alerts → Options Trading & Alerts),
per `docs/AUDIT_DOMAIN_SERIES_TEMPLATE.md`.

### Critical architectural context established before dispatching

`decision-engine`'s `/decide/{symbol}` verdict is the LIVE, PRIMARY gate for real trades —
`decision_engine_mode` defaults to `"primary"` in code and was confirmed set that way on the
actively-traded production portfolio. `paper_trading_engine._should_enter()` (a separate
service) is only a FALLBACK when decision-engine is unreachable. A prior, extensive parity
effort (`T232-DL-DUALSCORER-DEBT`, documented across `docs/features/decision-engine-
dualscorer-parity.md`, 1522 lines, multiple sessions) had already cross-checked all ~23
hard-reject gates and 7 score layers line-by-line against `_should_enter()` with no further
divergence found as of its last pass — the dispatched audit was explicitly told NOT to re-run
that specific sweep, and to focus on genuinely new ground instead.

### Ground truth (queried directly against production before dispatching)

Only 118 total paper trades exist in production (110 closed), all carrying a real
`entry_score` on a small additive scale (3-9; `min_entry_score=4` is the real code default, NOT
0-100 as first assumed — corrected before handing to the subagent). Overall win rate 33.6%,
overall average return -0.15 percentage points (flat, not dramatically negative like AI
Signal's own domain). Per-score win rate was non-monotonic (scores 5-6 had the WORST win rates
and negative returns; scores 4 and 9 the best) — flagged to the subagent as possibly noise
given thin per-bucket samples (3-33 trades).

### Headline findings (10 total; top 3 independently re-verified by me before recording/fixing)

1. **CRITICAL, independently re-verified, FIXED — production portfolio configs override the
   entry gates far below the code's own real defaults.** 4 of 5 active portfolios had
   `min_confidence: 15.0` / `min_entry_score: 3` configured — verified directly against the
   live `paper_portfolios.config` column. `hard_rejects.py`'s own `min_conf * 0.90` hard-floor
   formula (confirmed at the exact cited line) makes this a **13.5% confidence floor**, versus
   the code's real, calibration-aware per-style/market defaults (queried live via
   `resolve_entry_gate_params()`: GROWTH/US 45.0/4, SWING/HK 65.0/6, SWING/US 50.0/5, GROWTH/HK
   65.0/6). This directly explains the non-monotonic score/win-rate pattern — scores 5-6 are
   not "just above the pass bar," they are the modal band 2-3 points above a bar set far too
   low, admitting confidence-33%-48% candidates the system's own design never intended to
   admit. **Fixed**: all 4 portfolios updated live via the real, validated
   `POST /paper-portfolio/configure` admin endpoint (not raw SQL) to their real
   `resolve_entry_gate_params()`-computed values, confirmed via each response's echoed config.

2. **HIGH, independently re-verified, FIXED — the shadow-comparison audit trail recorded the
   caller's OWN pre-call config, not decision-engine's real regime-adjusted min_score.**
   `_call_decision_engine()` read `result.get("score", 0)` from decision-engine's response but
   never `result.get("min_score")` — confirmed the response model (`DecisionResponse.min_score`,
   computed by `min_score_for_regime()`) genuinely carries this value and it was simply never
   read. Both call sites instead passed `cfg.get("min_entry_score", ...)` — the value BEFORE
   any regime adjustment — into `_record_de_shadow_comparison()`, making the
   `/paper-portfolio/de-divergences` "DE Audit" UI systematically wrong (production evidence
   cited by the subagent: the same symbol logged 2 different `de_min_score` values 12 seconds
   apart, neither matching decision-engine's own real `min_score` for that period). **Fixed**:
   `_call_decision_engine()` now returns a 5-tuple including the real `de_min_score`; both call
   sites prefer it over the stale cfg value, falling back only when the response omits it. 5
   new regression tests (source-text extraction, matching this file's established convention).

3. **HIGH, independently re-verified, FIXED — `BatchDecisionRequest` silently disabled the
   T201 equity-floor circuit breaker on the entire `/decide/batch` path.** Confirmed the model
   had no `initial_capital` field at all, so `decide_batch()`'s inner `DecisionRequest`
   defaulted to 10,000 regardless of the real portfolio's actual starting capital — for an HK
   portfolio seeded at 300,000, this fabricates a wildly wrong `equity/initial_capital` ratio,
   defeating the drawdown-suspension gate non-deterministically. **Fixed**: added the field to
   `BatchDecisionRequest` (same default as `DecisionRequest`'s own), threaded it through
   `decide_batch()`'s construction. Separately fixed the one real caller
   (`frontend/src/lib/api.ts`'s `decideBatch`, a standalone watchlist scanner with no real
   portfolio context) to send `initial_capital` equal to its own already-fabricated `equity`
   value, producing a real, self-consistent 1.0 ratio instead of an accidental 10.0. 4 new
   tests.

4. **MEDIUM, FIXED — `research_score_val or 0` in `sizer.py`, the same falsy-zero bug already
   fixed once in this exact sibling variable at `scorer.py`'s own
   `T247-DECISIONENGINE-RESEARCHSCORE-FALSY`, never swept to `sizer.py`.** Verified the fix is
   behaviorally harmless in the `>= N` direction (a genuine 0 fails every real threshold either
   way, confirmed via adversarial sabotage — reverting the fix caused zero test failures,
   correctly reflecting that this specific comparison shape has no observable behavior change)
   but corrected anyway for consistency with the established fix pattern and to close the
   latent risk if a comparison is ever inverted. **Fixed**: replaced with explicit
   `is not None` checks.

5. **MEDIUM, FIXED — `INSUFFICIENT DATA` (a real, production-occurring research-engine
   verdict that already caused a `StringDataRightTruncation` crash once, Tier 247) was absent
   from all 3 decision-engine research-vocabulary tables**, silently scoring/sizing it
   identically to "no research was attempted" rather than "research explicitly failed to
   gather fundamentals." **Fixed**: `sizer.py` now maps it to the same 0.60 de-weighted
   multiplier as a recommendation that exists but misses its own confidence gate; `scorer.py`'s
   `_RESEARCH_SCORE` table now scores it -1 (distinct from WATCH's 0). `hard_rejects.py`
   deliberately left unchanged — "unknown" should not hard-block like a confirmed AVOID/SELL
   would. 4 of the 10 new sizer/scorer tests target this specifically; adversarial sabotage
   (removing both fixes) was caught cleanly by exactly those 4 tests.

6-10. **CONFIRMED, documented as tracker items, not fixed this pass** (full detail in
   tracker): decision-engine performs zero database writes anywhere in its codebase, so
   rejected candidates have no durable record beyond a 2000-item Redis ring buffer and a
   4-hour-TTL summary that's deleted the moment one entry succeeds — the score-bucket table
   that grounded this whole audit is a survivors-only sample with no denominator (needs a real
   design pass, not a quick fix); scale-in blends 3 of 5 `*_at_entry` columns but leaves
   `entry_score`/`rr_ratio_at_entry` frozen, and the entry-weights calibration fit consumes all
   4 as one snapshot, feeding back into decision-engine's own verdict once ≥100 closed trades
   exist (production now has 110); the entry-gate fallback used when market-data is
   unreachable is materially looser than every real gate it stands in for and omits
   `regime_min_rr_ratio` entirely; `compute_score()`'s `recent_win_rate` parameter is dead
   (declared, passed, never read — the real logic lives in `cfg` inside a different function).

### Answers to the audit's own lettered questions (full detail in tracker AUD-DECIDE-REF)

- **Is the non-monotonic score pattern real or noise?** Real, with a traceable mechanism — but
  not the one first hypothesized. It survives slicing by market, style, and month (all
  near-flat baselines), ruling out a regime effect. The true discriminator is Finding 1: with
  `min_entry_score=3` live in 4 of 5 portfolios, scores 5-6 are the modal band, not "just above
  the bar," and are dominated by confidence-33%-48% candidates the code's real defaults would
  have rejected. One genuine caveat the subagent could not eliminate: scores 3-4 are US-only
  and concentrated in a single 6-day June bulk-entry window (46 of 118 trades), so that specific
  sub-finding (scores 3-4 outperforming) is confounded and should not be trusted as strongly as
  the scores-5-6 deficit.
- **Is the pipeline exercised at meaningful volume?** No — measured directly from
  decision-engine's own logs: 2,399 `decision.blocked` vs. 4 `decision.evaluated` over 24h
  (99.83% reject rate). Most of that is structurally-guaranteed "Market closed" no-ops, but of
  genuine in-session rejects, "R:R below minimum" dominates at a suspicious, clustered exactly
  `2.00:1` — an artifact of the fixed style-default game plan when ATR is unavailable, not a
  measured property of the setup, colliding with a calibrated floor. Separately, 53 of 55
  divergence events share the identical reason ("Consecutive loss cooldown: 3 straight
  losses") — with a 33.6% win rate, this cooldown re-arms faster than it clears, a
  self-reinforcing volume suppression.
- **Are the LLM layers enabled/validated?** Neither `llm_scoring_enabled` nor
  `risk_check_enabled` is true on any of the 5 production portfolios — confirmed directly.
  Both disabled paths are clean no-ops (verified: return before any API call/Redis write/state
  mutation). No calibration/walk-forward harness exists for the LLM score layer if it's ever
  enabled (unlike `calibration_feedback_enabled`, which has one) — recorded as a
  recommendation, not acted on, since the feature stays off today.
- **Same 3 recurring AI-Signal bug classes?** Falsy-zero: found (Finding 4). Stale vocabulary:
  found (Finding 5). Per-day-upsert/recorded-moment divergence: `entry_score` and
  `PaperTradeDecisionLog` are both confirmed insert-only with no upsert path at all — clean on
  that specific axis — but the same class of defect appears in two different shapes instead:
  as absence (rejected candidates have no record) and as sibling-column divergence (the
  scale-in/calibration-fit gap, item 6 above).
- **Is Unusual Whales used/usable beyond the confirmed squeeze/pressure layers?** No further
  use found. One of the two already-wired layers (`options_pressure_score`) is confirmed DEAD
  in production today — `paper_trading_engine.py` deliberately never sends it on the real
  trading path (its own inputs require a live options-chain yfinance fetch this repo's
  rate-limit discipline forbids inside the per-candidate scan loop). The dominant real reject
  reason (a fabricated 2.00:1 R:R from a missing-ATR fallback) is a well-matched, real
  opportunity for UW's options-derived expected-move data — but explicitly secondary to fixing
  the gating problems found here first.
- **`aggregator.py` caching/error-handling gaps?** `fetch_all()` cannot distinguish "no
  signal" from "signal-engine down" — both produce the same misleading "open the stock detail
  page first" message during a real outage. Stale research (>24h) is dropped and treated as "no
  opinion," failing in the permissive direction (a stale AVOID silently stops blocking) — this
  is correct-as-designed but worth knowing. Three independent 15-minute caches expire on their
  own schedules with no single as-of stamp to detect a mixed-freshness read.

### Checked and found CLEAN

Router ordering (no catch-all shadowing in decision-engine's routes); `PaperTradeDecisionLog`
and `paper_trades.entry_score` overwrite risk (both confirmed insert-only, single write site
each); regime vocabulary (both `_REGIME_SCORE` and `_REGIME_MULT` cover exactly the 5 real
states market-data emits); the broader falsy-zero sweep (22 `or`-default sites reviewed, only
the 2 already listed are real candidates); `llm_scorer.py`/`risk_agent.py` disabled-path
cleanliness (verified no partial state, no expected-but-missing `reasons` key); `aget_entry_
weights()`'s fail-open behavior; the T247 signal-symbol-misattribution fix; the 2 T232-DL-
DUALSCORER-DEBT items spot-checked (regime_min_rr_ratio threading, max_confidence_decline
sign direction) both still hold.

### Config change applied (not a code fix — recorded explicitly per this app's own discipline
around risky/hard-to-reverse actions)

```
Portfolio 1 (GROWTH Paper Portfolio, US):  min_confidence 15.0 -> 45.0,  min_entry_score 3 -> 4
Portfolio 2 (HK SWING Portfolio):          min_confidence 15.0 -> 65.0, min_entry_score 3 -> 6
Portfolio 3 (US SWING Portfolio):          min_confidence 15.0 -> 50.0, min_entry_score 3 -> 5
Portfolio 4 (HK GROWTH Portfolio):         min_confidence 15.0 -> 65.0, min_entry_score 3 -> 6
```
Portfolio 5 (ETrade Sandbox SWING) was already at 30.0/4 — a deliberate sandbox-specific
choice, left untouched. Applied via the real, validated `POST /paper-portfolio/configure`
admin endpoint (not raw SQL), confirmed via each response's echoed config.

### What was NOT independently verified

Findings 5-10 (the "documented, not fixed" batch) were trusted at the subagent's own
CONFIRMED tag after 3 of the most consequential, differently-shaped findings (1, 2, 3) checked
out exactly as reported under direct verification — each cited exact file:line, matching the
pattern of the 3 spot-checked. The production log/Redis numbers behind "Is the pipeline
exercised at meaningful volume" (2,399 blocked / 4 evaluated, the 560-candidate/22-symbol
divergence sample) were not independently re-queried by me — recorded as reported.

**What to check if this needs re-verifying:**
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "SELECT id, name, config->>'min_confidence', config->>'min_entry_score' FROM paper_portfolios ORDER BY id"
docker exec stockai-market-data-1 grep -n "de_min_score" /app/src/services/paper_trading_engine.py
docker exec stockai-decision-engine-1 grep -n "INSUFFICIENT DATA" /app/src/api/core/sizer.py /app/src/api/core/scorer.py
```
