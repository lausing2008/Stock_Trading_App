# Market & Stock Intelligence Engine — architecture

Companion to [the fit-gap analysis](market-stock-intelligence-gap-analysis.md), 2026-10-07.
Design only. No code in this commit implements it.

---

## 1. The decision this design turns on

The specification's §2 proposes a greenfield pipeline: Market Data → Normalized Layer → Feature
Engine → twelve parallel engines → Evidence Aggregation → Multi-Timeframe → Scenario → Trade
Setup → Portfolio Risk → AI Explanation → Dashboard.

**Most of that pipeline already exists under different names**, spread across twelve services.
The spec itself anticipates this: *"Prefer this logical architecture unless the existing
architecture provides a better equivalent"* and *"Do NOT blindly rebuild functionality that
already exists."*

So this design does not create a new engine. It adds **the one layer that is genuinely absent —
evidence aggregation into named buckets (§24)** — and fills gaps behind it. The reason is
structural, not pragmatic: §24 is the component every downstream section depends on. Scenarios
(§26), confidence (§25), trade status (§32) and alerts (§35) all consume bucketed evidence, and
none of them can be built coherently on top of twelve engines that each emit their own shape.

```
                       EXISTING (12 services, 93 tables)
  ┌───────────────────────────────────────────────────────────────────────┐
  │ market-data      prices, indicators, regime, breadth, backtest        │
  │ research-engine  intel_reports, quality_value, direction_screen,      │
  │                  issuer_facts (EDGAR), valuation, assessments         │
  │ event-intel      earnings, economic_events, catalysts, macro reaction │
  │ news-intel       realtime_news_items, classification, scope, budget   │
  │ signal-engine    signals, outcomes, horizons (24,128 rows)            │
  │ ml-prediction    models, tune_history, suppression                    │
  │ portfolio-opt    exposure, correlation, risk metrics                  │
  │ decision-engine  scorer, sizer, regime consumer                       │
  └───────────────────────────────────────────────────────────────────────┘
                                    │
                                    │  adapters emit Field(statement_class, provenance)
                                    ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │                   ★ EVIDENCE BUCKET LAYER  (new, §24)                 │
  │  13 buckets: market · sector · industry · fundamentals · earnings ·   │
  │  revisions · valuation · technical · relative_strength · options ·    │
  │  news · catalysts · risk                                              │
  │  each: direction, strength, confidence, evidence[], contradictions[], │
  │        as_of, inputs_digest, bucket_status                            │
  └───────────────────────────────────────────────────────────────────────┘
          │                 │                  │                  │
          ▼                 ▼                  ▼                  ▼
   Multi-timeframe    Confidence (§25)   Scenario (§26)    Trade status (§32)
       (§18)                                                     │
                                                                 ▼
                                               Risk validation → Paper → (broker, disabled)
```

---

## 2. Why a bucket is not a score

§24 is explicit: *"Do NOT simply create one arbitrary AI score."* The platform has already
learned this the expensive way — a pooled win-rate badge mixed BUY with SELL, which point in
opposite directions (−1.22% vs +1.03%, n=18,561), and a confidence band that is flat across its
whole range.

So a bucket **never aggregates into a number**. Composition rules, carried over from the Quality
& Value gate contract which already works this way:

1. **A bucket's direction is its own.** Buckets are never averaged or weighted. A reader sees
   thirteen directions and the contradictions between them.
2. **`UNKNOWN` is never `NEUTRAL`.** An unmeasured bucket is a gap in our work; a neutral one is
   a finding about the market. The existing `GateStatus` taxonomy — `not_implemented`,
   `not_collected`, `stale`, `conflicting`, `insufficient`, `not_assessed` — is reused verbatim,
   because it already distinguishes "refresh a job" from "build an analysis".
3. **Contradictions are first-class and never dropped.** Every bucket stores them; the summary
   layer is forbidden from rendering a claim without its counterevidence. This is already
   enforced for `Finding` and `issuer_assessments`.
4. **Confidence is derived, not asserted** — from data completeness, freshness, independent
   agreement and contradiction count (§25). Never a free-floating percentage.

---

## 3. New data model

Additive only. The spec's §41 names twenty schemas; seventeen already exist under other names
(see the gap analysis). Three are genuinely new:

```
evidence_buckets          one row per (subject, bucket, as_of, policy_fingerprint)
  subject_key             "stock:MU" | "market:US" | "sector:XLK"
  bucket                  one of the 13
  direction               STRONG_BULLISH..STRONG_BEARISH | NEUTRAL | UNKNOWN
  strength                STRONG | MODERATE | WEAK
  confidence              LOW | MEDIUM | HIGH          (derived, §25)
  status                  reuses GateStatus
  evidence                JSON  [{claim, source, source_ref, as_of}]
  contradictions          JSON  [{claim, source, source_ref, as_of}]
  inputs                  JSON  frozen VALUES, not references
  inputs_digest           sha256(inputs)               (tamper-detectable)
  policy_fingerprint      digest of the rules that produced it
  UNIQUE (subject_key, bucket, as_of, policy_fingerprint)   -- immutable, additive

intelligence_predictions  what the system believed, and when            (§36)
  subject_key, as_of, horizon (1-5d | 1-4w | 1-3m)
  direction, confidence, trade_status
  entry, stop, target_1, target_2, risk_reward
  bucket_ids              JSON — the exact rows this rested on
  frozen_inputs_digest
  UNIQUE (subject_key, as_of, horizon, policy_fingerprint)

prediction_outcomes       what actually happened                        (§36/§37)
  prediction_id, horizon_days (1|5|10|20|60)
  forward_return, benchmark_return, excess_return
  resolved_at, resolution_basis
```

`intelligence_predictions` deliberately mirrors `signal_outcome_horizons`, which already holds
24,128 rows across four horizons and has a working resolution job. The new table is for
*intelligence* verdicts rather than *signals*; the resolution mechanics are reused, not rebuilt.

---

## 4. Multi-horizon regime (§4) — the one breaking change

§4 requires five states across three horizons, classified independently. The platform has one
label (`BULL`/`NEUTRAL`/`CHOPPY`/`BEAR`) across all horizons, and **it gates real paper trades**.

Changing it in place would alter trading behaviour as a side effect of an analysis change. So:

- The existing classifier stays exactly as it is and keeps gating trades.
- A **new, additive** `market_regimes` row is computed per `(market, horizon)` with the spec's
  five states, consumed only by the intelligence layer.
- Divergence between the two is surfaced, not hidden. If the new multi-horizon reading and the
  trading regime disagree, that is a finding about the classifier and worth seeing.
- Only after the new reading has a measured track record does replacing the trading classifier
  become a separate, evidenced decision.

This mirrors `independent-horizon-resolution`, where an additive table was built rather than
patching 201 references to an existing one.

---

## 5. Phasing, ordered by what unblocks what

The spec's §48 ordering assumes a greenfield. This ordering puts the blocking layer first and
the data-blocked sections last.

| Phase | Work | Unblocks | Blocked on |
|---|---|---|---|
| **1** | Evidence bucket layer: schema, composition rules, the 13 buckets wired to **existing** adapters only | §25, §26, §32, §35 | nothing |
| **2** | Bar coverage: derive `W1` and `H1` from `D1`/`M5`; decide a 4H convention | §17, §18, §19 | nothing |
| **3** | Multi-horizon regime (additive); breadth ratios and divergence; sector rotation labels | §4, §5, §9 | nothing |
| **4** | Relative strength matrix; semiconductor subgroup taxonomy (a mapping table) | §10, §19 | nothing |
| **5** | FRED ingest: macro series, liquidity (balance sheet, TGA, RRP, M2); VIX complex | §6, §7, §8 | FRED key — free |
| **6** | `intelligence_predictions` + outcome resolution; confidence calibration against it | §25, §36, §37 | needs Phase 1 and time |
| **7** | Scenario engine; trade status; Market & Stock Intelligence dashboards | §26, §32, §33 | Phases 1–4 |
| **8** | Company status engine (structured events from `sec_filings`, `insider_transactions`) | §15 | nothing |
| **9** | Estimate revisions | §14 | **a consensus-history provider** |
| **10** | Memory/HBM module | §11 | **a memory-pricing provider** |
| **11** | AI learning loop | §39 | needs Phase 6 outcome data |

Phases 1–4 and 8 need **no new data source**. Phase 5 needs one free key. Phases 9 and 10 cannot
start until a purchasing decision is made, and the spec's own rule governs the interim: do not
infer memory pricing without data, and do not substitute price targets for earnings revisions.

---

## 6. What this design deliberately does not do

- **It does not build a new service.** The bucket layer belongs in research-engine, which
  already owns `intel_reports`, the gate contract and the assessment store.
- **It does not replace the trading regime classifier** — see §4 above.
- **It does not introduce an overall score**, a blended conviction number, or a calibrated
  probability. §25 forbids fake precision, and the platform has measured its existing confidence
  as meaningless.
- **It does not let an LLM compute anything deterministic.** Per §2 and §51, the LLM's role is
  summarisation, synthesis and contradiction analysis over values that code produced — which is
  already how the narration validator and the interpretation layer are built.
- **It does not add a broker path.** §47's separation stands; the broker route remains disabled
  with its blocker recorded.

---

## 7. Acceptance, restated measurably

§50 asks that selecting a ticker returns a full report. That is not a single acceptance test, so
it decomposes into conditions that can each fail individually:

1. For a named ticker, **all 13 buckets return a row** — each either a verdict or an explicit
   `not_collected` / `not_implemented` with a remedy. No bucket is silently absent.
2. **Every conclusion traces to a timestamped source.** Spot-checkable: each evidence entry
   carries `source`, `source_ref` and `as_of`, and the frozen inputs verify against their digest.
3. **The three horizons may disagree**, and a test asserts they are not forced into agreement.
4. **Contradictions are present wherever they exist**, and a supporting claim cannot render
   without its counterevidence.
5. **A prediction is stored with its bucket ids**, and its outcome resolves at 1/5/10/20/60 days.
6. The data-quality header (§3) names every stale and missing source **before** any conclusion.

Condition 5 is the one that makes the rest falsifiable: without stored predictions and resolved
outcomes, none of this can be shown to work, and the honest status would remain "unmeasured".
