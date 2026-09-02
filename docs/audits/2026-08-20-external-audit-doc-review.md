## Review: docs/recomm_or_audit/DEEP_PLATFORM_AUDIT_2026-08-20_VERIFIED.md — Accurate Data,
## Stale Implementation-Status Claims (Reviewed 2026-08-20)

**Ask**: review this external ("Amazon Q"-generated) audit doc and, if the findings are true,
document them and add real action items to the tracker. Same discipline applied to every prior
external audit document reviewed in this repo's own history: never trust a claim at face value
just because it cites a line number or a production statistic — re-verify everything
independently against current code and live production data before acting on it.

**Method**: 2 parallel verification passes — my own direct re-queries against production
Postgres/Redis and direct code reads for the highest-stakes claims, plus a dedicated
verification agent covering the remaining batches (silent-exception-handler content, the
full 15-item "not done" list, the 3-item bug-status table, delisted-filter line citations,
job-status key). Both passes independently converged on the same verdicts.

### What held up — genuinely accurate, checkable facts

- **God-file line counts**: `scheduler.py` 10,131 / `paper_trading_engine.py` 5,939 /
  `signals.py` 2,921 / `outcomes.py` 3,040 / `routes.py` (signal-engine) 1,261 — every single
  one an **exact match**.
- **35 silent `except Exception` handlers** in ml-prediction (trainer.py 21, tuner.py 6,
  meta_trainer.py 8) — exact count match, though see the finding below for why "all 35 silent"
  overstates the real gap.
- **`paper_trades` performance numbers** — re-ran the audit's own aggregate query directly:
  95 closed trades, 31.58% win rate, -0.28% avg return, -$7,478.97 total P&L, and the full
  exit-reason/entry-score breakdowns — **every figure an exact match**.
- **The confidence-calibration inversion itself** (higher confidence = lower win rate) — real,
  confirmed by re-running the query, and confirmed to hold even when split by direction
  (BUY: 45.23%→32.92% monotonically declining 0-55%→85%+; SELL noisier but broadly similar) —
  not a BUY/SELL-pooling artifact of the kind documented elsewhere in this file (`AUD261`'s
  unsigned-SELL bug, confined to a different table/function).
- **Delisted-filter line-number citations** (`routes.py:194`/`:215`, `scheduler.py:7232`) — all
  exact matches to current code, with `scheduler.py:7232` even carrying the matching
  `# BUG-DELISTED-GENERATION-BLIND` tag.
- **`scheduler:job:paper_trading` Redis key** — exists, `status: "ok"`, plausible recent
  timestamp.

### What was stale — implementation-status claims, materially wrong

**All 3 of Part 9's "documented/unfixed" bugs are already fixed**:
- `BUG-PROXYGAP-CONDITIONALORDERS` — `api-gateway/proxy.py:89` has the `"conditional-orders"`
  route entry, fixed in the immediately preceding session's own `BUG-RISKSNAP-NOSERVICETOKEN`
  work (see that entry above).
- `BUG-VOLANOM-STALEMARKET` — `scheduler.py:2489-2495` has the full market-hours gate, tagged
  with the exact bug-id comment, fixed 2026-07-21.
- `BUG-NEWSCLASSIFY-REPEATCOST` — `news-intelligence/storage.py:88-133` dedupes URLs before the
  classify call.

**6 of the 15 "not done" tracker items in Part 10 are actually `done`**: `T217-MENTAL-MODELS-
AUDIT`, `T171-PREMARKET-GAP-FILTER`, `T234-COMPETITIVE-RATING-2026-07`, `IF-REVIEW-SUMMARY`,
`IF-04-CROSS-ASSET-SIGNALS` (built 2026-08-19 — literally the day BEFORE this audit's own
2026-08-20 date), and `IF-10-PORTFOLIO-ATTRIBUTION`. Two more (`T241-POSITION-SCALING-DESIGN`,
`AUD232-METAMODEL-MEDIUM-GROUP`) are `in-progress` with real completed sub-phases, not
untouched todos as the audit's blanket framing implies.

**All 3 of Part 11's "P0 — Immediate Action Required" recommendations describe rebuilding
mechanisms that already exist**:
1. "FIX CONFIDENCE CALIBRATION" — the immediately preceding session
   (`AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK`, same calendar day this audit was generated)
   already found and fixed this exact inversion: `calibrated_win_rate` is now durably
   persisted, and a new, OFF-by-default score layer reads it, validated via a real walk-forward
   sweep — not the audit's own proposed unvalidated linear rescale.
2. "INVESTIGATE ENTRY SCORE 5-6 UNDERPERFORMANCE" — `_should_enter()`'s own `PT-3` comment
   (`paper_trading_engine.py:2274`) already calls `_load_entry_weights()` and uses a calibrated
   logistic-regression win-probability instead of the raw additive score whenever ≥100 closed
   trades exist (`_MIN_CALIBRATION_TRADES = 100`). The book has exactly 95 closed trades — 5
   short of the floor — so the additive fallback the audit is complaining about is CORRECTLY
   still active, not a bug; the calibrated mechanism activates automatically at 100.
3. "BLOCK RISK_OFF REGIME ENTRIES" — `regime_risk_off_gate` already defaults to `True`
   (`T226-A`, "9/30 closed paper trades entered in risk_off — 0% win rate, avg -5.0% return")
   and hard-blocks ALL new entries in risk_off. Confirmed live against all 5 real portfolios —
   every one either inherits the `True` default or has it explicitly set `True`. The 10
   risk_off trades the audit's own query found predate this gate's deployment.

### What survived as genuinely real and actionable — 3 new tracker items (Tier 291)

1. **`AUD291-SILENT-EXCEPTIONS-MLPRED`** (todo, low severity) — of the 35 counted, 6 already
   log via a real `log.warning(...)` before continuing (`trainer.py:532,643`; `tuner.py:96`;
   `meta_trainer.py:185,447,596`) — the audit's grep matched the bare `except Exception:` line
   without checking whether the block's body logs, overstating the truly-silent count by these
   6. The remaining ~29 are genuinely silent and worth a future, judgment-call-by-judgment-call
   cleanup pass (never a blanket regex replace).
2. **`AUD291-SIGNALENGINE-GODFILES-UNEVALUATED`** (todo, low severity) — the audit bundled
   `signals.py`/`outcomes.py` (signal-engine) together with `scheduler.py`/
   `paper_trading_engine.py` as if all 4 were an equally-unaddressed gap. Checked individually:
   the market-data pair was already explicitly evaluated and deliberately rejected for
   splitting (`T233-ARCH-MARKETDATA-GODSERVICE`, done, real dated reasoning). `signals.py`/
   `outcomes.py` sit in the SAME service `routes.py` was already successfully split 4 ways
   from (`T233-ARCH-INSERVICE-SPLITS`, done, 2026-07-22) but were never themselves evaluated
   either way — a genuinely open question, not a considered-and-rejected one.
3. **`AUD291-STOPTARGET-HITRATE-REVIEW`** (done, low severity, verification-only — no code
   change) — the 54.7%-stop/6.3%-target exit-reason breakdown is real, current data, but it
   now reflects an ALREADY-FIXED labeling issue (`AUD262-EXITREASON-CONFLATION-ROOT`) rather
   than a live bug: `stop_hit` used to conflate profitable trailing-stop exits with genuine
   loss-cuts (14 of 49 "stop_hit" trades used to be profitable, up to +13.96%); that's now
   correctly split into a clean loss-only `stop_hit` bucket (avg -2.33%) and a clean
   profit-only `trailing_stop` bucket (avg +5.44%). Documented so a future session doesn't
   re-flag this exact data as evidence of a new bug. The genuinely open, separate question
   this DOES raise — whether stop/target distances themselves need retuning — is left for a
   future pass requiring the same walk-forward validated-promotion discipline every other
   live-decision parameter change in this codebase already requires.

**Design invariant reinforced (the Nth recurrence of this exact class in this tracker's own
history)**: an external audit's precise, verifiable data (line counts, DB queries, line-number
citations) being accurate does NOT mean its higher-level "is this fixed/built" claims are —
those need independent verification against current code every single time, in both directions,
regardless of how confidently or precisely the surrounding data is presented. This audit is a
particularly clean illustration: its RAW DATA was essentially perfect, while its
INTERPRETATION of what that data means for "what's still broken" was wrong on nearly every
higher-level claim it made.

**What to check if this needs re-verifying**:
```bash
# Re-confirm the 3 "unfixed" bugs are still actually fixed:
docker exec stockai-api-gateway-1 grep -n '"conditional-orders"' /app/src/api/proxy.py
docker exec stockai-market-data-1 grep -n "BUG-VOLANOM-STALEMARKET" /app/src/services/scheduler.py
docker exec stockai-news-intelligence-1 grep -n "BUG-NEWSCLASSIFY-REPEATCOST" /app/src/services/storage.py

# Re-check whether the book has crossed the 100-closed-trade calibration floor yet:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*) FROM paper_trades WHERE stage = 'closed';"
# Once this crosses 100, _should_enter() automatically switches from the additive score to the
# calibrated logistic-regression win-probability — no code change needed, just data accumulation.
```

---

