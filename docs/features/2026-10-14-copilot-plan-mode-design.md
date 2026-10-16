# Copilot Plan mode: forecasts with track records, and a textbook playbook

**Status:** updated 2026-10-15. Slice P1 (backend), including C1–C5 and follow-up fixes, is
deployed in research-engine from `89756b83`; tracker tiers 444–445 are served. P2 (stock-page
Game Plan, chart overlays and confirmed alert drafts) has not started. Final validation and
deployment evidence: `docs/audits/2026-10-15-copilot-plan-final-remediation.md`.

**Asked for (2026-10-14).** The user wants to ask the same copilot:
- for a prediction for the next few days or the next week;
- what to do next, as a textbook operation, when they hold the stock or want to buy it.

**Operator decisions (do not re-litigate):**

| Question | Decision |
|---|---|
| How direct is the advice? | **Conditional rules.** If→then rules with levels calculated by code, plus the platform's own BUY/SELL call and its measured track record. Never a flat "buy now". |
| Where does the position come from? | **The user types it** ("I bought 100 at 182.50"). Nothing is read from accounts or portfolios. |
| What happens to the stock page's Game Plan? | **Rebuilt on copilot rules**, in the same place. Every level is calculated by code and cited; the model only writes wording. |
| Order of work | **Plan mode first**, chart commands after. |

Earlier decisions still hold: the copilot is read-only; writes happen only as user-confirmed
drafts; the model never writes a digit.

## 1. What the platform can honestly say (measured 2026-10-14, production)

**There is no price forecast anywhere in the platform.** The signal engine stores a direction
call per horizon (`signals`: one row per stock, horizon and day). ML predictions are computed on
request, are binary, and carry no expected return. So "a prediction for next week" can only mean
three things:
- the platform's latest **call** for each horizon;
- how those calls have **turned out** before;
- the range this stock's **past** moves have covered.

None of these is a price target, and the answer must not turn them into one.

**Track records are thin and unverified.** Counted from `signal_outcome_horizons`:

| Fact | Value |
|---|---|
| Resolved outcomes | 23,702 over signals from 2026-09-03 to 2026-10-09 (about 5 weeks, one market regime) |
| Evidence status of those outcomes | **0 verified**; 18,743 with no status, 4,388 unverified, 571 provisional |
| Per-stock depth, SWING BUY at 5 days | median **4** resolved outcomes per stock, maximum 19 |
| Per-cohort depth (horizon × direction × window) | 214 to 2,145 resolved |

Earlier audits found BUY calls with negative average excess return and confidence bands that are
flat (`docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md`,
`2026-09-30-three-week-deferred-checkpoint.md`). A track-record display must therefore show:
- the count;
- the date span;
- the evidence status;
- the cohort, not just the stock.

Below a minimum count, it must say "too few to judge" rather than print a rate.

**Horizon names do not mean one thing.** Outcome windows (calendar days), ML label horizons
(bars) and the frontend labels all differ. The copilot therefore never says "SWING means 10
days". It names the outcome **window** it measured (5 or 10 calendar days) with its unit.

## 2. Design

### 2.1 One deterministic plan engine (`copilot/plan.py`)

A pure function of stored data, `now`, and an optional typed position:
`build_plan(session, symbol, now, position=None) -> Plan`. There is no model call in it. Its
output is a set of **Fields and Quantities** in the existing evidence contract, so citations,
evidence expansion, staleness and the digit rule all apply unchanged.

**It has two consumers:**
- the copilot packet, which adds the plan's quantities when the question asks for a forecast or
  a plan (§2.4);
- `GET /copilot/plan/{symbol}`, which returns the plan as structured JSON with no model call and
  no ledger charge. The stock page's Game Plan card renders from it (slice P2).

#### Inputs, all from stored data

| Input | Source | Rule |
|---|---|---|
| Daily bars | `prices` D1, up to 260 bars ≤ `now` | Unadjusted. A single-bar move above 35% in the window marks levels and ranges **STALE**: "possible split or corporate action" |
| Price | latest bar | Existing price field and staleness |
| ATR(14) | Wilder, from bars | Calculation record citing every input bar |
| Support / resistance | lowest low / highest high of the 20 bars **before** the latest one (the Donchian convention; see §5) | Named "20-bar low/high", not "support" as a judgement |
| 52-week range | highest high / lowest low over 252 bars | Unavailable below 200 bars |
| SMA20 / SMA50 and slope | existing trend field; slope = SMA50 now vs 5 bars ago | |
| Past N-bar moves | this stock's overlapping 5-bar and 10-bar returns over the last 250 bars | 10th / 50th / 90th percentile. "Past moves, not a forecast" |
| Signal per horizon | latest `signals` row for each of SHORT / SWING / LONG / GROWTH | Each with its own time and staleness (36 h) |
| Track record | `signal_outcome_horizons`, resolved, signal date within 180 days, same horizon + direction, windows 5 and 10 | §2.2 |
| Earnings | existing earnings field | |
| Position | typed by the user, parsed by grammar (§2.3) | Evidence source "stated by you in this question" |

**Rule parameters are textbook conventions, fixed in code, and documented as NOT tuned on this
platform's data:**
- stop below support = support − 0.5 × ATR;
- breakout stop = resistance − 1 × ATR;
- initial stop from entry = entry − 2 × ATR (the paper engine's default multiple);
- minimum reward:risk = 2.0;
- earnings caution window = 10 calendar days;
- breakout volume = 1.5 × the 20-bar average.

#### 2.2 Forecast block: the platform's calls and how they have turned out

For each horizon with a stored signal, the block shows:
- the call and its time (STALE past 36 h);
- **cohort record**: every resolved outcome for that horizon and direction, at windows of 5 and
  10 calendar days. For each:
  - n;
  - hit rate (`is_correct`, the engine's own ±0.5% hurdle);
  - median and mean raw move;
  - signal-date span;
  - evidence-status counts.
  Shown only when **n ≥ 30**, the floor `/signals/outcomes/summary` already uses for "reliable";
- **this stock's record**, the same figures for this symbol. Shown only when **n ≥ 8**, the
  per-symbol floor the signal email uses. Otherwise: "too few resolved outcomes for this stock
  (n = 4) to judge it alone";
- **baseline**: the median raw move and up-share of *every* resolved outcome in the same horizon
  and window, both directions. Labelled "all calls in this horizon, a rough baseline for the
  period, not a market benchmark". A BUY hit rate means little without knowing how often
  anything went up then.

**Raw move, not sign-corrected.** For a SELL call, a correct outcome is a fall, and the display
says "the stock fell in X% of cases". The SELL `pct_return` is stored unsigned (inventory), so
direction is applied in code and stated in the quantity's meaning.

**Every record line carries the status suffix "(none verified)"** while that is true. It is
computed, not hard-coded.

#### 2.3 Typed position grammar (`intent.parse_position`)

A bounded grammar, like `parse_alert`. Nothing is guessed.

| Recognised | Example |
|---|---|
| Entry price | "bought at 182.50", "my entry is $182.5", "avg cost 182.50", "got in at 182" |
| Share count | "100 shares", "bought 100 at 182.50" |
| Risk budget | "risk $500", "I can lose 500 dollars", "account 50,000 risk 1%" (a percentage is allowed only with a stated account size) |
| Intent without a position | "want to buy", "should I buy", "thinking of buying" → entry plan |

**Refused, with the reason shown and no plan figures built on it:**
- two or more different entry prices ("50 at 10 and 50 at 12": give the average cost);
- an entry outside 0.2× to 5× the current price (possible typo or split);
- short positions or options ("shorted", "puts", "calls": the copilot covers long stock only;
  the Options Game Plan is separate);
- a percentage risk with no account size;
- incomplete numbers (the same whole-token rule as `parse_alert`, C1).

The parsed position is stored only as part of the question text and the message's grounding
record. It is never written to a portfolio.

#### 2.4 Rules (the playbook), rendered by code

Each rule is a **Quantity whose display is a complete code-written sentence** with its figures,
e.g. `rule.entry.pullback`. The model cites rules by key and may introduce or order them. It
cannot alter a rule's conditions or numbers, because it never writes digits. The Game Plan card
renders the same sentences with no model at all.

**Entry rules** (no position typed, or "want to buy"):
- **E1 pullback.** If price pulls back toward the 20-bar low L and a daily close holds above it,
  a textbook entry puts the stop at L − 0.5×ATR = S.
- **E2 breakout.** If a daily close is above the 20-bar high H on volume above 1.5× its 20-bar
  average, a textbook breakout entry puts the stop at H − 1×ATR.
- **E3 reward:risk at today's price.**
  - Stop S from E1; first target = the 20-bar high.
  - If R:R < 2.0, the rule says wait for a better price: the distance to the stop is more than
    half the room to the target.
  - If price is already at or above the 20-bar high, there is no first target from this
    structure, and the rule says the entry is a breakout case (E2), not a pullback case.
- **E4 trend filter.** If the close is below a falling SMA50, textbook trend-following rules do
  not open a new long.
- **E5 earnings.** If earnings fall within 10 calendar days, textbook practice is to wait or size
  down: an earnings gap can jump past a stop.
- **E6 sizing.**
  - With a risk budget R: shares = floor(R / (entry − stop)), with the position value.
  - Without one: the loss to the stop per 100 shares.

**Holding rules** (entry typed):
- **H1 status.** Gain or loss in currency and percent at the latest close, and how many ATRs from
  entry.
- **H2 protective stop.** Two candidates, both shown: entry − 2×ATR, and 20-bar low − 0.5×ATR.
  The rule names the higher one as the tighter stop, and the loss at each (× shares when given).
- **H3 breakeven.** Once the close is more than 1×ATR above entry, a textbook rule moves the stop
  to the entry price. The display says whether that is already true.
- **H4 trailing.** Latest close − 2×ATR is a candidate, not a maintained stop. With a stop typed
  by the user, the proposed stop is kept or raised; without one the current stop is unknown.
- **H5 targets.** Stored highs above cost are profit-taking candidates; levels at or below
  cost are described as breakeven or loss reduction, never as taking profit.
- **H6 exit.** The daily-close condition is evaluated against the user's stated stop. Without
  one, only an illustrative candidate is shown; the platform does not retain or maintain it.
- **H7 earnings.** Same as E5, for a held position: reduce or accept the gap risk knowingly.
- **H8 signal check.** If the platform's call for the user's horizon turns SELL, the rule says
  review the position. The current calls are cited from the forecast block.

**Never present:**
- a rule whose inputs are UNAVAILABLE; the rule itself becomes unavailable with the reason;
- a sentence telling the user to buy or sell now.

Rules are conditions, so each display begins "If…" or "Once…", or states a fact about the
position.

#### 2.5 Routing: when the plan enters the packet

Deterministic, in code: `intent.plan_kind(question)` returns one of:
- `None`;
- `"forecast"` (predict, forecast, next week, next few days, outlook, where is it going);
- `"entry"` (should I buy, want to buy, entry, buy point, stop loss);
- `"holding"` (a parsed position, or "I own", "I bought", "should I sell", "take profit").

The packet then carries the forecast block, or the forecast block plus the rules. A question
without a plan intent gets the existing packet, plus one unavailable field: "playbook not loaded
for this question; ask for a plan or a forecast". This keeps ordinary answers' token cost
unchanged.

**PACKET_VERSION goes to 4.**

#### 2.6 Model rules (prompt and grounding)

- **Prompt rule 6 changes.** It currently says "Do not tell the user what to buy or sell". The
  new version: the user may ask what to do. Answer only by citing the playbook's rule keys and the
  platform's calls with their records. Never add a rule, level, target or timing of your own.
  Never say "buy now", "sell now" or that a price *will* move.
- **New grounding check, all answers: unconditional forecast language is refused.** This covers:
  - "will rise/fall/drop/climb/hit/reach/go up/go down" in a sentence without "if", "once",
    "unless" or "when";
  - "guarantee(d)", "certain(ly) to", "sure to", "expected to reach".
  Negations ("does not forecast where it will go") are exempt, like the probability rule. The
  rate of new rejections on ordinary answers is measured in the eval before shipping.
- The digit rule, staleness, and the third-party headline rule are unchanged.

### 2.7 Game Plan rebuild (slice P2, after P1 is reviewed)

`generateGamePlan()` in `pages/stock/[symbol].tsx` builds a prompt in the browser and asks a
model for entries, a stop and targets. Those figures are model-written and uncited. It is
replaced as follows:
- **Card.** The card calls `GET /copilot/plan/{symbol}` (optional `entry`, `shares`, `risk`) and
  renders the rules, levels, calls and records directly. Each figure links to its evidence. There
  is no model call, so it is instant and free.
- **Optional wording.** A button asks the copilot ("Explain this plan"), which uses the normal
  ledger.
- **Chart overlay.** The overlay draws the plan's levels: 20-bar low/high, stops, 52-week high.
- **"Save to board".** It keeps its existing endpoint, mapping entries = [pullback level, breakout
  level], stop = E1 stop, targets = [20-bar high, 52-week high].
- **Price-alert drafts.** The rules can offer price-alert drafts at a stop or target, using the
  existing draft kind and confirmation.

The separate Options Game Plan card is untouched.

## 3. What this does not do

- **No price targets, expected returns or probabilities.** The past-move percentiles are labelled
  as history.
- **No reading of accounts, portfolios, paper trades or broker positions.**
- **No short-selling or options rules.**
- **No tuning of rule parameters on platform data in this phase.** Doing so would need the
  walk-forward machinery and the outcome volume the platform does not yet have.
- **No benchmark-relative track record.** Excess return is not stored, so it is stated as a
  limitation.

## 4. Slices and acceptance

**P1, backend.** Plan engine, position grammar, routing, packet v4, prompt and grounding
changes, `GET /copilot/plan/{symbol}`. Tests:
- every rule's arithmetic on a hand-computed fixture;
- floors (n = 29 / 30 and n = 7 / 8);
- the evidence-status suffix;
- STALE propagation (old signal, corporate-action jump);
- grammar accept/refuse tables, including reviewer-style counterexamples;
- the forecast-language check, with negations;
- routing;
- endpoint authorization.

Plus a real-model eval with fresh cases (forecast, entry, holding with and without risk,
refusals), reported tuned versus held-out.

**P2, frontend.** Game Plan card, chart overlay, save mapping, rule-key rendering in copilot
answers; jsdom tests.

Independent review after each slice; nothing is deployed until its review's findings are fixed.

## 5. As built (slice P1): changes from the design, with the reason for each

1. **The 20-bar low and high exclude the latest bar.** The first probe run showed that, with
   the latest bar inside the window, the close can never be below the low or above the high.
   Rule E3's "support has broken" branch was therefore unreachable. The Donchian convention
   (the prior 20 bars) makes both breakdowns and breakouts observable.
2. **The refusal lines are citable.** A position refusal is `plan.position_note`, and the plan
   gaps are `plan.gaps`. They are UNAVAILABLE quantities whose display is the code's reason.
   Measured: the model cited the *field* names to relay them and was refused.
3. **Three narrow grounding exemptions**, each tested beside the case that must still be
   refused (`test_held_out_fixes_are_narrow`):
   - **A window label beside a track record.** "5-day", "five days" or "5 calendar days" is
     exempt only in the same sentence or bullet as a `record.*` / `baseline.*` placeholder whose
     key carries that window. It is still refused:
     - beside a mismatched record;
     - beside a 5-*bar* return;
     - without a record;
     - next to any other figure.
   - **"N calendar days"** is exempt wherever a packet display names that window.
   - **"I bought…" as a quoted example** of how to phrase a position, only directly after an
     opening quote. Unquoted, it is still refused as order language.
4. **The forecast-language check applies to every answer**, not only plan answers. Ordinary
   answers' rejection rate is measured on the existing eval before any deployment.
5. **The rejected-answer fallback includes the plan lines.** They are code-written, so a plan
   question gets its plan even when the model's wording fails.

Evaluation results (held-out first run, then tuned) are in
`docs/audits/2026-10-14-copilot-plan-mode-validation.md`.

## 6. Stop contract and review fixes (2026-10-15, independent review C1–C5)

**The stop contract, settled.** The platform keeps no position state, and the operator chose
typed positions over reading accounts. So:

| User typed | What the plan says |
|---|---|
| No stop | Every stop is a **candidate calculated from today's data**. H6 says the current stop is **unknown**, gives today's candidate, and states the rule: keep the higher of your existing stop and the candidate, never lower it, and exit on a daily close below the stop you hold. |
| A stop ("my stop is 95", or `stop=` on the endpoint) | It is evidence ("stated by you"). H6 raises it to the candidate when the candidate is higher, keeps it otherwise ("a stop is never lowered"), and says when the latest close is already below it. |

The first build called a recalculated value "the active stop" that "never moves down". The
reviewer measured it falling from $226 to $220.50 between two snapshots of the same position.

**The other fixes:**
- **C2, options and shorts.** Any derivative word in a sentence that states a position marks
  the request unsupported, except the platform's own "BUY/SELL/SWING calls". An unsupported
  request gets **no** stock rules, in the packet or the fallback. The calls and records are
  still shown.
- **C3, amounts.** Read whole. `k` and `m` suffixes are supported explicitly ($10k = 10,000).
  Malformed or glued amounts are refused, never truncated.
- **C4, forecasts.** A condition word no longer exempts a prediction. Model prose may not
  assert a future move at all; conditions come from the rule keys. Only a negation or "whether"
  in the same clause exempts. "Will not fall" counts as a prediction.
- **C5, targets.** Each target is compared with the typed entry. A level at or below cost is
  "at best gets back to your cost … resistance", never "partial profit".

The Game Plan card (P2) must render this distinction: a calculated candidate versus a stop the
user holds.


## Final audit remediation — 2026-10-15

The independent C1–C5 review and follow-up are resolved in the final remediation record:
`docs/audits/2026-10-15-copilot-plan-final-remediation.md`. Packet v5 distinguishes a calculated
stop candidate from the stop the user says they hold. The API includes `position.stop` and an
explicit `unsupported` flag. No account or broker state is inferred. Options/short requests
get no stock rules, including in fallback. Complete risk quantities preserve money/percentage
units; malformed amounts and conflicting budgets are refused. Percentage risk requires an
account size; percentage stops are not treated as absolute prices. Model price predictions
are refused even after unrelated conditional or negative phrases; code-rendered rule keys
provide the conditions. Targets below entry are loss reduction, not profit-taking.

P2 (the stock-page Game Plan card, chart overlays and confirmed alert drafts) is implemented
in packet v6; see `docs/features/2026-10-15-completable-followups.md` for validation and release status. The October 23 price-risk review, MU-02 and broker-fill evidence remain separate open
checks. No alert enrollment or delivery setting changes are part of this release.
