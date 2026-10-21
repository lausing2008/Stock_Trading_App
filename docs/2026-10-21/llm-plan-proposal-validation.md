# LLM-proposed Game Plan: real-model and end-to-end validation (2026-10-21)

**Status: deployed; live-model validation partial.** 11 of the 12 approved Sonnet calls were used. Not independently re-reviewed. Raw evidence is in [`llm-plan-eval/`](llm-plan-eval/) (and on EC2 under `/home/ec2-user/llm-plan-eval/`). The script is `scripts/eval_llm_plan_proposals.py`.

Path exercised: production `C.answer` with the admin shared key, model `claude-sonnet-4-6`, the shared `copilot_chat` budget and usage logging. Questions are tagged `[eval]` in the admin's Copilot history. A counter refused calls past the cap. Every proposal was re-derived independently (each price must equal anchor + k × ATR from the packet; a conditional proposal must pass every gate), and the model's own prose was scanned for digits.

## Run 1 — 8 calls, 73,457 input / 5,719 output tokens

| Scenario | Outcome | Proposal (code-priced) |
|---|---|---|
| MU entry | **rejected** after 2 calls | valid pullback: entry low20, stop low20 − 0.5 ATR, targets high20 / high252 |
| NVDA entry | **rejected** after 2 calls | pullback; target 2 = high20 + 0.5 ATR (trend rule would have blocked it) |
| MU holding (10 @ 800, stop 780) | grounded, 1 call | hold: stop $833.86 (low20 − 0.5 ATR), targets $1,108.55 / $1,254.61 |
| 0005.HK entry (falling trend) | grounded **wait**, 2 calls (one format repair) | — |
| SSNLF (no bar since 2025-11-07) | refused before admission: "a fresh, supported plan is required"; **0 calls** | — |
| MU forced rejection, then a real repair | scripted pullback-above-close rejected by code; real repair returned a **valid** proposal; answer rejected | — |

**Defect found (mine):** each rejected answer contained a valid proposal, but its prose failed the evidence check, which refuses quantities in words and numerals. The model wrote "half an ATR", then "a fraction of an ATR", "twenty" and "target 1". The prompt had asked it to explain ATR offsets without giving it a citable way to do so. **Fixed in `5e904903`:** the proposal's code-computed values and derivations became placeholders (`{{proposal.stop_derivation}}` → "the 20-bar low ($858.00) − 0.5 × ATR ($48.27) = $833.86"). They are rebuilt per attempt, and an answer must cite the stop that way.

## Run 2 (after the fix) — 3 calls, 32,500 input / 2,691 output tokens

- **MU entry:** grounded, **conditional**, after one repair. The explanation renders the code's own figures: entry $858.00 = the 20-bar low, stop "the 20-bar low ($858.00) − 0.5 × ATR ($48.27) = $833.86", first target 10.38R. The independent re-check found 0 problems.
- **Forced rejection:** the repair produced a valid proposal again, but its prose used "twenty" and fell back. With 2 attempts, a code rejection leaves one attempt to get both a valid proposal and clean prose.

Total: 11 calls, about 106k input / 8.4k output tokens (≈ $0.44 at Sonnet list prices).

## What this establishes, and what it does not

- **Retries never bypassed validation.** No invalid proposal was shown or saved as conditional in any run; every code rejection and format error went through the repair or fallback path.
- **Stale data never reaches the model:** the comparison is refused before admission, so no call is billed.
- **The model converged on the rules' levels** in every entry case (pullback at the 20-bar low, stop half an ATR below). The prompt shows the rules' setups for reference, which may anchor it. The same numbers are an observation, not evidence of a better or worse plan.
- **Prose quality:** the model often inlines whole code-written rule sentences as nouns, which reads awkwardly once rendered.
- **Not established:** behaviour across more symbols and markets, a proposal that comes back `blocked` from a real model, consistency across repeated runs, or any outcome or profitability of either plan.

**Recommended (not built):** allow a third attempt only after a code rejection; consider omitting the rules' setups from the proposal prompt to test independence; a larger budgeted run (e.g. 25 calls, repeated scenarios).

## End-to-end: chart → Trade Board → reload (production)

`llm-plan-eval/e2e_llm_save.py` built MU's live plan, selected the LLM plan, generated, saved and reloaded. The model reply was **replayed from Run 2's stored MU answer** (no new call); everything else was live. Results:

- Chart: Entry 858.00, Stop 833.86 and TP1 1,108.55 plotted ([screenshot](llm-plan-eval/e2e_chart.png)); TP2 is above the visible range.
- After a page reload, the saved board row matched exactly on: entry, stop, take profit, source `gameplan-llm`, reasoning text, model, price time, derivation, targets and playbook.
- Found and fixed: the board card showed the raw source `gameplan-llm` (now "LLM plan"), and every new card read "-1d ago" in US time zones because the API's zone-less UTC timestamps were parsed as local time (all board cards, older than this work).
- The test row (board id 84) was deleted afterwards.

## Follow-up (2026-10-21, release `b39800d9`)

**Proposal and prose are validated separately.** Once code accepts a proposal (conditional, blocked or wait), its numbers are frozen. If the model's prose then fails the evidence check, no further model call is spent: the answer shows a **platform-written explanation** beside the validated plan, labelled as such (`narration: code`, with the prose rejection reasons kept). A proposal that code rejects, or a malformed tag, still gets its one repair. Tests: a valid proposal with "half an ATR" prose uses 1 call and is narrated by code; the evaluation's forced case (code rejection, then a valid proposal with bad prose) uses 2 calls; a "wait" with bad prose is narrated by code. The saved board snapshot records `explanation_written_by`.

**Hidden-rules experiment — prepared, not run.** `TurnRequest.hide_rules_reference` (evaluation only; unreachable from the API) withholds the rules plan — setups, rules, playbook and rule-derived stop/target levels — while keeping the same market evidence, constraints and typed inputs. A test confirms the rules are absent and the evidence present. Fixed matrix (`EVAL_SET=matrix` in the script): MU, NVDA, AAPL, 0005.HK and 0700.HK entries plus MU and 0700.HK holdings, each with rules visible and hidden = 14 requests, **cap 28 calls and about $2** (≈ $0.04 per call measured). Metrics per arm: valid-plan rate, fallback rate, code-narration rate, independent constraint violations, prose-digit answers, and save fidelity (one replayed browser save per arm). Price differences are reported only descriptively; matching prices are acceptable. **Awaiting approval of the matrix and cap.**

**Browser-test token.** The token minted for the save/reload test was deleted from disk but **not revoked**: its unique ID was not kept, and the only revocation that does not need it (`revoke_user_tokens`) would sign the account out everywhere. It expires one day after issue (about 07:05 UTC on 2026-10-22; `JWT_EXPIRE_DAYS=1`). The E2E script now calls `/auth/logout` at the end, which blacklists that run's own token ID.

## Hidden-rules experiment (run 2026-10-21 13:24 UTC, approved matrix)

Seven cases × two arms (rules shown / hidden), **18 provider calls, ≈ $0.65** (137,194 input / 16,202 output tokens). Neither the 28-call nor the $2 cap was reached; retries counted. Each case's evidence packet was built **once** and deep-copied to every request, so all 7 pairs had identical evidence (fingerprints equal) while live signals refreshed. Model `claude-sonnet-4-6` at temperature 0 in both arms. **Preflight exclusions: none.** The production prompt is unchanged. Raw: `llm-plan-eval/eval-20261021T132424Z.json`.

| Case | Rules shown | Rules hidden |
|---|---|---|
| MU entry | conditional: low20 / low20 − 0.5 ATR / high20 / high252 (model prose) | conditional: low20 / **low20 − 0.75 ATR** / **sma50** / high20 (code narration) |
| NVDA entry | blocked (trend rule), same levels as rules | **fallback**: first reply had no tag and a numeral ("52"); repair still failed the prose check |
| AAPL entry | conditional: low20 / low20 − 0.5 ATR / high20 / high20 + 0.5 ATR | conditional: same stop, **target 1 = high20 − 1.5 ATR** (code narration, after a missing-tag repair) |
| 0005.HK entry | blocked (trend rule) | blocked (trend rule), different stop/targets |
| 0700.HK entry | wait | wait |
| MU holding | **rejected**: kind=pullback for a held position, twice | **rejected**: same |
| 0700.HK holding | wait | wait |

| Metric | shown | hidden |
|---|---|---|
| Valid plan (conditional / blocked / wait) | 6/7 | 5/7 |
| Fallback (prose failed with no accepted proposal) | 0/7 | 1/7 |
| Code-written explanation | 3/7 | 3/7 |
| Constraint violations (independent check) | 0 | 0 — see note |
| Save fidelity | proven for this flow in the earlier E2E; not re-run per arm | same |

**Note on the one flagged "violation".** The checker re-built the evidence live instead of using the frozen packet. Between the two, a 13:30 UTC yfinance daily fetch **restated MU's past completed closes** (sma50 984.22686 → 984.23126). The proposal had been priced correctly from the frozen evidence (price = anchor + k × ATR held). The checker now uses the frozen packet. That daily restatement after a provider fetch belongs with OD-01's evidence (tracker 457); so far OD-01 has covered 5m bars.

**Reading (preliminary, 7 pairs; not a superiority claim).** With the rules hidden, the model chose different, still-valid levels in 3 of 4 priced entry cases (wider stop, nearer targets such as the 50-bar average), so the earlier convergence was at least partly anchoring on the shown rules. Both arms agreed on every wait and blocked case. Hidden had one more fallback. Both arms failed the same way on holdings.

**Defects found (not changed, for decision):**
1. **Holding mode is not stated in the proposal prompt.** The model proposed `kind=pullback` for a held position in both arms, and the repair did not fix it. In the first evaluation it chose `hold` only by reading the format help. The fix is to state the mode and the allowed kinds explicitly per mode.
2. **Prose still fails often** (6/14 code-narrated, 1 fallback). The common causes are numerals in window names ("52-week", "5-bar") and missing tags. Frozen proposals now protect the plan; the explanation quality is the remaining gap.
