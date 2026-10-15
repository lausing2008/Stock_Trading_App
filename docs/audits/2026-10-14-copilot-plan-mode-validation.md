# Copilot Plan mode, slice P1: validation before review

**Scope:** the backend for "what's the prediction for next week?" and "what should I do, I bought
at $X?". Design: [Plan mode design](../features/2026-10-14-copilot-plan-mode-design.md).

**Status:** committed for independent review. **Not deployed. No frontend yet** (that is slice P2).

## What was built

- **`copilot/plan.py`**: the plan engine. It is deterministic and contains no model. It produces:
  - levels from stored bars: ATR(14), the prior 20-bar low/high, the 252-bar range, and the
    50-bar average with its slope;
  - this stock's past 5- and 10-bar move ranges;
  - the latest call for each horizon;
  - cohort and per-stock track records, with floors of n ≥ 30 for a cohort and n ≥ 8 for a stock;
  - a baseline;
  - textbook entry rules E1–E6 and holding rules H1–H8, each worded and calculated by code.
- **`intent.parse_position`**: a bounded grammar that reads the position the user types:
  - entry, shares, risk amount, or account plus a risk percentage;
  - routing to forecast, entry or holding.

  It refuses, with a reason: options, short positions, two or more entry prices, incomplete
  numbers, and a percentage with no account size.
- **Packet v4**: the plan lines are added only when the question asks for a forecast or a plan.
  Ordinary questions get the v3 content plus one model-only note.
- **Grounding** refuses unconditional forecast language in every answer ("will rise", "should
  bounce", "is expected to reach"). Conditions and negations pass.
- **The rejected-answer fallback** shows the plan lines, so a plan question still gets its plan
  when the model's wording fails.
- **`GET /copilot/plan/{symbol}`**: the same lines as JSON, for the Game Plan card. It makes no
  model call and charges nothing to the ledger.
- **Storage**: each evidence record is stored once per message. One plan answer measured 416 KB
  before and 138 KB after. The API's shape is unchanged, and messages stored in the old format
  still serve.

## Tests

`tests/test_copilot_plan.py`: 58 tests over `_copilot_plan_probe.py`. Each runs on a real
database, with only the model scripted.

**Hand-computed fixtures:** with a true range of 2 on every bar, ATR = 2.00. A bar with a
true range of 4 gives ATR = 30/14, so the stop is $97.93. On a rising series, the trailing stop
rises from $218 to $226.

**Covered:**
- the floors at 29/30 and 7/8;
- the computed verification status ("3 of 47 verified");
- no look-ahead, and a bounded lookback;
- staleness: from a stale price, from a split-sized jump, and per call;
- thin history;
- each earnings case;
- the grammar and forecast-language tables;
- the turn-level behaviour;
- the endpoint's validation and authorisation.

| Run | Result |
|---|---|
| Full research-engine suite, SQLite | 679 passed, 8 skipped (PostgreSQL-only) |
| Full research-engine suite, PostgreSQL 15 (throwaway container) | 687 passed |

PostgreSQL caught one fixture defect that SQLite cannot: outcome rows need their parent signal
under the foreign key.

**Sabotage checks: 12 of 12 caught.** Each of these was broken on purpose and made at least one
test fail:
- the window includes the latest bar;
- the cohort floor set to 29;
- the stock floor set to 7;
- the look-ahead filter removed;
- the lookback removed;
- the forecast check turned off;
- the window exemption ignoring the key;
- the order-language rule turned off;
- a stop allowed to move down;
- no entry plausibility check;
- a stop read as the entry;
- the verification status hard-coded.

**Defects found by these tests and fixed before this record:**
- The 20-bar window included the latest bar, which made rule E3's breakdown branch dead code.
- A rule's evidence repeated shared bars.
- Two record lines rendered identically, because the horizon was not named.

## Real-model evaluation (synthetic fixtures, 14 fresh questions p01–p14)

**The questions were written before the first run**, so that run is the held-out measurement.
Its refusals changed the checker, so the second run is **tuned**.

| Run | Sonnet 4.6: grounded / first draft / fallback | Sonnet 5.5: grounded / first draft / fallback |
|---|---|---|
| **Held-out (first contact)** | **18/28 / 11/28 / 10** | **14/14 / 12/14 / 0** |
| Tuned | 27/28 / 17/28 / 1 | 14/14 / 13/14 / 0 |

**Held-out refusals, by cause.** All but the last were the checker being too blunt, not the
model inventing figures:
1. Window labels beside a record, such as "SWING BUY, 5-day: {{record.swing.5d}}". These are
   accurate, since those records are 5 calendar days.
2. Citing field names (`{{plan_gaps}}`) to relay a refusal.
3. Quoting the example phrase "I bought 100 shares at …".
4. Retyping the user's own figures. This was refused correctly and repaired on retry.

The fixes are narrow, and each is tested beside a neighbouring case that must still be refused
(`test_held_out_fixes_are_narrow`). The remaining tuned-run refusals are mostly the model
retyping figures the user typed, which is correct and repaired on retry. The one fallback is a
"5" outside any record sentence.

**Citations (tuned run):**
- every entry or holding answer cites rule keys (18/18 on Sonnet 4.6, 9/9 on Sonnet 5.5);
- every forecast answer cites the calls and the records.

**Ordinary questions under packet v4**, the existing 34 questions on Sonnet 4.6:
- 34/34 grounded with 0 fallbacks (baseline run 7: 32/34 first draft, 1 fallback);
- 26/34 first draft;
- the new forecast rule refused nothing.

Two first-draft "unknown key" refusals came from the eval harness checking against a packet
built without plan routing. The harness now routes like the service.

**Latency (p50):** about 11 s on Sonnet 4.6 and 8.5 s on Sonnet 5.5 for plan answers, which carry
more lines than ordinary answers (7.5 s).

**Spend for this validation:** about $3.35. Raw results are in `2026-10-14-copilot-plan-eval/`.

## Semantic read (tuned answers, read by the implementer)

- **Forecast questions** ("Where will it be next week?", "What are the odds?") open by saying the
  platform stores no price forecast. They then give the calls, the records with "none
  verified", the past-move range labelled as history, and the earnings date.
- **Holding answers** give P&L, both stops, the active stop and the exit condition, all as
  code-written rules.
- **MU (SELL calls, falling 50-bar average)** answers cite the trend filter and say the platform
  "does not support a new long". That reading follows from the rule; the model did not add a
  rule of its own.

**Residuals:**
- The wording around the rules is not verified; the UI badge already says so.
- Sonnet 4.6 sometimes repeats a rule's full text and then paraphrases it.
- The eval fixture gave TESTCO an artificially perfect 25-call record. This is a fixture
  artefact, but it shows that a per-stock record above the floor can still be one short,
  lucky span. The display states the date span for that reason.

## Not done / open

- **P2:** the Game Plan card rebuild, the chart overlay, the save mapping and the draft offers.
- **Production query cost of the cohort read:** measured at 18 ms per horizon on production,
  but not yet as part of a full request.
- **Thresholds and multiples are textbook values.** They are not tuned, and the endpoint says so
  in `parameters.note`.
