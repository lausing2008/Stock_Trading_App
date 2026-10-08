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
4. **Support quality is not predictive confidence, and they are stored as different fields.**
   Data completeness, freshness and source independence establish *how well evidenced a reading
   is*. They say nothing about how likely it is to be right — a perfectly sourced, perfectly
   fresh reading of a weak signal is well supported and still wrong. So:

   - `support_quality` (LOW / MEDIUM / HIGH) is derived from completeness, freshness and source
     independence. It is computable today and asserts only what it measures.
   - `predictive_confidence` is **left null until it is calibrated against resolved outcomes**
     (`prediction_outcomes`, below). The platform has already measured its existing signal
     confidence as flat across its whole range over n=19,256 — a number that moves with nothing
     is worse than an absent one, because it looks like information.

   **Contradictions are weighed, not counted — and never discarded.** A count treats one fatal
   objection and three trivial ones as "three beats one". Each contradiction therefore carries
   `materiality` (would it change the direction, or only its strength?) alongside `source_ref`
   and a `source_group`.

   **Grouping is for independence only; every finding is preserved.** An earlier draft of this
   design said contradictions sharing a source "collapse to one", which would have thrown away
   distinct findings — one 10-K legitimately contains several independently relevant risks, and
   MU's does exactly that (competitor consolidation, new entrants, government assistance to
   competitors, the May 2023 CAC decision, shortening product life cycles — five separate
   objections from one filing). All of them are stored and all of them render. What the group
   affects is **independence**: five objections from one filing are five findings backed by one
   source, so they count once toward "do independent sources agree", and `support_quality` must
   not read as HIGH merely because one document said five things.

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

intelligence_predictions  what the system believed, when, and HOW IT WILL BE SCORED   (§36)
  subject_key, observed_at           -- the instant the conclusion was formed, not the day
  horizon_sessions                   -- TRADING SESSIONS, not calendar days
  horizon_label                      -- "1-5d" | "1-4w" | "1-3m", display only
  direction, support_quality, trade_status
  reference_price, reference_price_as_of, reference_price_source
  reference_price_basis              -- formed_at_close | next_tradeable_open
  return_basis                       -- price_return | total_return; benchmark uses the SAME
  confirmation_rule, invalidation_rule     -- the exact observable conditions, as text + params
  benchmark_symbol                   -- what the excess return is measured against
  resolution_policy                  -- see below; frozen WITH the prediction
  policy_fingerprint                 -- digest of the rules that produced the direction
  bucket_ids              JSON — the exact evidence rows this rested on
  frozen_inputs, frozen_inputs_digest
  UNIQUE (subject_key, observed_at, horizon_sessions, policy_fingerprint)

prediction_outcomes       what actually happened                        (§36/§37)
  prediction_id, horizon_sessions
  forward_return, benchmark_return, excess_return
  descriptive_return      from reference_price — "was the reading right?"
  simulated_executable_return   from the next plausibly tradeable price
  execution_assumptions         JSON — spread, slippage, timing, liquidity and halt
                                treatment, all UNMODELLED at first delivery and listed as such
  delisting_cause         acquisition | bankruptcy | exchange_transfer | NULL
  resolution_state        RESOLVED | RESOLVED_ACQUISITION | RESOLVED_BANKRUPTCY |
                          UNRESOLVED_INSUFFICIENT_SESSIONS | UNRESOLVED_PRICE_MISSING |
                          UNRESOLVED_DELISTED_NO_TERMINAL_PRICE |
                          UNRESOLVED_ADJUSTMENT_MISSING
  resolved_at, sessions_elapsed, resolution_basis
```

`intelligence_predictions` deliberately mirrors `signal_outcome_horizons`, which already holds
24,128 rows across four horizons and has a working resolution job. The new table is for
*intelligence* verdicts rather than *signals*; the resolution mechanics are reused, not rebuilt.

**One deliberate difference from the existing table.** `signal_outcome_horizons.horizon_unit`
defaults to `calendar_days`. This one counts **trading sessions**, because a calendar horizon
silently shortens across a holiday week and differs between US and HK — two markets this
platform already serves, with different holiday calendars and an HK lunch break the session
helper already models.

### The resolution policy is frozen with the prediction, before any result exists

Deciding how to handle a gap, a missing price or an unresolved horizon **after** seeing outcomes
is a bias vector, and the convenient choice is always available in hindsight. So the policy is a
field on the prediction, written at observation time:

| Case | Rule, fixed in advance |
|---|---|
| Fewer than `horizon_sessions` have elapsed | `UNRESOLVED_INSUFFICIENT_SESSIONS`. **Not** a partial return, and excluded from every aggregate rather than counted as flat |
| The closing price for the resolution session is missing | `UNRESOLVED_PRICE_MISSING`, retried; never substituted with a neighbouring session |
| **Delisting** | **Never an automatic void — see below** |
| **Split or dividend** | **Not a void at all — an adjustment. See below** |
| The benchmark is missing | The absolute return resolves; `excess_return` stays NULL. One missing input does not void the other measure |
| An intervening policy change | Irrelevant to resolution. The prediction is scored under the `policy_fingerprint` it was made with — a later rule change files a new prediction, never a re-score of an old one |

A prediction whose resolution policy cannot be stated is not ready to be stored.

#### Delisting is four different events, and voiding all of them is itself a bias

An earlier draft of this design had a single `VOIDED_DELISTED` state, retained in the coverage
denominator. Keeping the row visible helps, but **excluding its return still biases the
results** — a position that went to zero in bankruptcy and one acquired at a 40% premium are
not the same outcome, and dropping both removes the tails in opposite directions. So the cause
is recorded and resolution is attempted before anything is voided:

| Cause | Resolution |
|---|---|
| **Acquisition / merger** | Resolve at the documented cash consideration, or the cash-equivalent value of share consideration on the effective date. This is a real, knowable return |
| **Bankruptcy / liquidation** | Resolve at the terminal traded price, or at zero where the equity was documented as cancelled. A −100% is a result, not a missing value |
| **Exchange transfer or re-listing** | **Not a delisting at all.** The security continues under a new venue or symbol; follow the identifier and resolve normally |
| **Terminal pricing unavailable** | `UNRESOLVED_DELISTED_NO_TERMINAL_PRICE` — the only remaining void. The row stays in the coverage denominator **and** every return statistic computed from the set must disclose how many rows were excluded this way and why |

The last row is the honest residue: it is still a bias, it is just a *disclosed* one. A
performance table that does not state its unresolved count is not reportable.

#### Splits and dividends are an adjustment basis, not a void

They are accounting events, not reasons to discard a prediction. What matters is that both
endpoints are measured on the **same** basis, and that the basis is declared:

- `return_basis` is stored on the prediction: `price_return` (split-adjusted, dividends
  excluded) or `total_return` (split- and dividend-adjusted). The first delivery uses
  `price_return` because that is what the stored daily series supports, and says so.
- The benchmark is measured on the **same basis**. Comparing a price return against a
  total-return benchmark understates excess return by roughly the dividend yield, every time.
- **Missing adjustment data stays unresolved** — `UNRESOLVED_ADJUSTMENT_MISSING`. The direction
  screen already refuses a setup when the adjustment factor changes across its window rather
  than silently combining raw highs across a split; resolution applies the same rule.

#### A conclusion formed after the close cannot execute at that close

Two different measurements, and conflating them is how a backtest flatters itself:

- **Descriptive forward return** — from the `reference_price` the conclusion was formed
  against, for answering *"was the reading directionally right?"*. Honest, and not a tradeable
  result.
- **Simulated executable return** — from the next price at which a participant could
  *plausibly* have transacted, given `observed_at`. A conclusion formed at 21:00 after a 16:00
  close references that close but could only have been acted on at the next session's open.

  **"Simulated" is not hedging — it is the accurate word.** The next tradeable price is an
  **execution assumption**, not proof of an achievable fill. None of the following is modelled
  yet, and each can move the result: the bid/ask spread, slippage against displayed size, order
  timing within the session, available liquidity at the chosen price, and trading halts or
  auction imbalances that remove the assumed fill entirely. Until they are modelled, the field
  is named `simulated_executable_return` and the assumptions travel with it in
  `execution_assumptions`, so a later reader can see what was taken for granted rather than
  inferring it from an unqualified "executable".

The prediction stores `reference_price` with `reference_price_as_of` and
`reference_price_basis` (`formed_at_close` | `next_tradeable_open`), and **both returns are
reported, separately labelled**. This platform has already recorded the cost of not doing
this — three datasets measured returns from a date nobody could act on.

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
| **1** | Evidence bucket layer **plus `intelligence_predictions` and outcome resolution from day one** — see below | §25, §26, §32, §35, §36, §37 | nothing |
| **2** | Bar coverage: derive `W1` and `H1` from `D1`/`M5`; decide a 4H convention | §17, §18, §19 | nothing |
| **3** | Multi-horizon regime (additive); breadth ratios and divergence; sector rotation labels | §4, §5, §9 | nothing |
| **4** | Relative strength matrix; semiconductor subgroup taxonomy (a mapping table) | §10, §19 | nothing |
| **5** | FRED ingest: macro series, liquidity (balance sheet, TGA, RRP, M2); VIX complex | §6, §7, §8 | FRED key — free |
| **6** | Calibrate `predictive_confidence` against the outcomes Phase 1 has been accumulating | §25 | elapsed time, not code |
| **7** | Scenario engine; trade status; Market & Stock Intelligence dashboards | §26, §32, §33 | Phases 1–4 |
| **8** | Company status engine (structured events from `sec_filings`, `insider_transactions`) | §15 | nothing |
| **9** | Estimate revisions | §14 | **a consensus-history provider** |
| **10** | Memory/HBM module | §11 | **a memory-pricing provider** |
| **11** | AI learning loop | §39 | needs Phase 6 outcome data |

**Prediction capture belongs in the first delivery, not phase 6.** The first version of this
plan deferred it, which would have thrown away every month of measurement history between the
first directional conclusion and the phase that started recording. A conclusion that is rendered
but not stored is unmeasurable forever after — the inputs move, and no later work recovers what
the screen concluded on a date it was not written down. So the moment any bucket emits a
direction, the row is written with its horizon, its rules fingerprint, its frozen input values
and its outcome definition. Calibration can wait for data; *capture* cannot.

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

## 6b. Phase 1 shape: one complete stock-summary flow

Thirteen buckets are the substrate, not the deliverable. A reader should not have to compose
thirteen conclusions themselves — that is the same reading-order mistake the Quality & Value
page already made and fixed, where each gate rendered its assessment as prose and again as a
structured box, leaving the answer below two copies of the working.

So Phase 1 delivers **one stock-summary flow end to end, over existing data only**, with the
buckets underneath it:

```
STOCK SUMMARY — {TICKER}, as of {cutoff}
  Direction        {direction} over {horizon}        support: {support_quality}
                   (predictive confidence: not calibrated)
  Three factors    the three highest-materiality supporting findings, each with its source
  Strongest        the single highest-materiality contradiction, named — not a count
    counterevidence
  Confirms if      {observable level or event}
  Invalidates if   {observable level or event}
  Frozen inputs    {digest} · {n} source references
  ──────────────────────────────────────────────────────────────────────
  ▸ Evidence buckets (13)                                    [collapsed]
```

Everything in that block exists today or is derivable without a new provider: direction and
levels from the direction screen, factors and counterevidence from `issuer_assessments` and the
interpretation layer, frozen inputs from `freeze_inputs()`. The summary is **assembled from the
buckets**, never written separately, so it cannot drift from the detail below it — the same
constraint the Quality & Value company summary already operates under.

And the row is written to `intelligence_predictions` at the moment it is rendered, with its
horizon, rules fingerprint and outcome definition. That is what makes the next twelve months of
this work measurable rather than retrospective.

## 6c. First delivery: one stock, end to end

Coverage is not the first milestone; a complete chain is. The first delivery takes **one stock**
from source inputs to a resolved outcome, and nothing is called done until the last link exists:

```
  source inputs            EDGAR facts + stored statements + completed daily sessions
        ↓                  each with source_ref and as_of
  evidence buckets         13 rows, each a verdict or an explicit gap with a remedy
        ↓                  contradictions preserved, grouped by source for independence
  stock summary            direction · horizon · 3 factors · strongest counterevidence
        ↓                  confirmation / invalidation levels · frozen-input digest
  stored prediction        observed_at, horizon_sessions, reference_price, benchmark,
        ↓                  resolution_policy, policy_fingerprint, bucket_ids
  resolved outcome         forward + excess return, or an explicit UNRESOLVED/VOIDED state
```

MU and CRDO already have the left-hand side — issuer-verified financials, versioned assessments
with sourced findings and counterevidence, frozen inputs that verify against their digest. What
neither has is a stored prediction or any resolution. That is the gap the first delivery closes,
on one name, before coverage grows.

Expanding to more companies then does not wait on resolving every research uncertainty in the
first one. MU's Strategic Customer Agreement terms are **not publicly disclosed** and may never
be; a pipeline that blocks until that closes never ships.

**A permanent unknown blocks only the conclusions that need it — nothing else.** Specifically it
does not block storage, price observation or outcome measurement:

| Still runs with a permanent unknown present | Blocked by it |
|---|---|
| Storing the bucket, with the gap named | A durability verdict that rests on the undisclosed terms |
| Observing prices and resolving outcomes | Entry-review eligibility, where that bucket is required |
| Publishing `support_quality` | `predictive_confidence`, until calibrated — for unrelated reasons |
| Rendering the summary, with the gap visible in it | — |

So MU accumulates price observations and resolved outcomes from day one while its durability
bucket stays `insufficient` indefinitely. The unknown is visible in the summary the whole time;
it just does not stop the clock.

## 7. Acceptance, restated measurably

§50 asks that selecting a ticker returns a full report. That is not a single acceptance test, so
it decomposes into conditions that can each fail individually:

0. For a named ticker, the **stock summary renders above the buckets** — direction, horizon,
   three factors, strongest counterevidence, confirmation and invalidation levels, frozen-input
   digest — and a prediction row is stored for it.
1. For the same ticker, **all 13 buckets return a row** — each either a verdict or an explicit
   `not_collected` / `not_implemented` with a remedy. No bucket is silently absent.
2. **Every conclusion traces to a timestamped source.** Spot-checkable: each evidence entry
   carries `source`, `source_ref` and `as_of`, and the frozen inputs verify against their digest.
3. **The three horizons may disagree**, and a test asserts they are not forced into agreement.
4. **Contradictions are present wherever they exist**, and a supporting claim cannot render
   without its counterevidence.
5. **A prediction is stored with its bucket ids from the first rendered conclusion**, and its
   outcome resolves at 1/5/10/20/60 sessions. No `predictive_confidence` is published until it
   has been calibrated against those resolutions; `support_quality` may be published
   immediately, because it asserts only what it measures.
6. **Two acceptance runs, labelled separately and never merged:**
   - a **historical replay** over a past window, which exercises every resolution branch —
     insufficient sessions, missing price, each delisting cause, a split inside the window, a
     missing benchmark. This proves *the machinery works*. It is **not** evidence of predictive
     performance, because the rules were written with the outcomes already in existence.
   - a **newly captured prospective prediction**, resolved only once the sessions elapse.

   **Prospective capture makes future measurement possible; it establishes nothing by itself.**
   Predictive skill requires resolved observations, an appropriate benchmark, and enough of
   them to separate a result from noise — none of which exists at capture time. A stored
   prediction is a precondition for the claim, not a weak form of it, and at first delivery
   prospective performance is **explicitly unmeasured**.
6. The data-quality header (§3) names every stale and missing source **before** any conclusion.

Condition 5 is the one that makes the rest falsifiable: without stored predictions and resolved
outcomes, none of this can be shown to work, and the honest status would remain "unmeasured".
