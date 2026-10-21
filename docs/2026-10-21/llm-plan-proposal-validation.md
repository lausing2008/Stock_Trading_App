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
