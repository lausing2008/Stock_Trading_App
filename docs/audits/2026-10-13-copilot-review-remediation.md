# Copilot: remediation of the independent review (2026-10-13)

This responds to [the independent review](2026-10-13-copilot-independent-review.md) of `9a48aca8`
and `ab3c0dd7`. All six findings and the five additional improvements are fixed. Each has a test
that reproduces the reviewer's counterexample.

**Not deployed. Implementer-verified only; the fixes have not been re-reviewed.**

The separately approved ranking-permissions fix is its own commit, `4844b8bf`
(`docs/incidents/authentication-used-as-authorization.md`).

## Findings

| # | Finding | Fix | Proof |
|---|---|---|---|
| R1 | "twenty dollars" and "will double" passed as grounded | Refused outside placeholders: number words (small counts only when a unit follows; large ones always), multipliers unless negated or conditional, fractions (not "third-party"), plural currency words (and a singular one after a number), ordinal periods. The UI badge now says what the check proves: "figures checked against stored data; the wording is the model's and is not verified" | `test_r1_…`: 13 held-out refusals, 12 must-pass counts |
| R2 | A limit of one admitted three concurrent answers; deleting a conversation restored the allowance | `copilot_usage_day` ledger, not tied to conversations, incremented by one conditional UPSERT in the same transaction as the question. **Refund policy:** an admission is returned only when no model call was made | SQLite: 3 concurrent → 1 call, 2 × 429. **PostgreSQL**: 8 distinct ids at limit 2 → 2 calls, 6 × 429, ledger 2; ask again after deleting every conversation → 429 |
| R3 | "New" reloaded the conversation just left | Explicit `fresh` mode. The load effect no longer depends on `convId`/`msgs`. A generation counter drops results that arrive after a symbol change or New | jsdom interaction tests; both guards sabotage-checked (each turns its test red) |
| R4 | Request ids were not bound to their payload | `request_fingerprint` (symbol and normalised question) is checked before replay and before claim; the conversation must match. A conflicting payload gets 409. **Model or provider changes on retry are allowed** (they do not change what was asked) | Changed question, symbol or conversation on a completed request → 409. Changed question on a failed request → 409 with no model call. Same question → grounded |
| R5 | Stored-row returns were labelled as exchange-session returns | Keys are `return.5b`/`return.20b`, the display carries both endpoint dates, and the meaning says "N stored bars apart, unadjusted, not checked against an exchange calendar". When the endpoints span more weekdays than N+1, the display says "possible missing bars". The trend text says bars, not sessions | `test_r5_…` with four bars deleted |
| R6 | The 50-bar average cited 20 inputs and returned 3 | Every average and return cites a calculation record (method, every input id, result) plus each input bar. The full set is stored; the API returns 5 inline plus `evidence_count`, and `GET /copilot/messages/{id}/evidence?key=` (owner only) returns all of it. The shared adapter is unchanged, because changing it would re-version every intel report | `test_r6_…` **recomputes** SMA20, SMA50 and the 5-bar return from the cited closes; 51 records for SMA50; another user gets nothing |

## Additional improvements

- **Freshness.** A report older than 7 days and its changes are STALE, and `report.age` is citable.
  `data.as_of` is renamed `price.as_of` (it only ever described prices), and answers carry
  `source_times` (price, signal, report, newest headline) separately.
- **Stop.** The panel distinguishes three outcomes: cancellation confirmed; nothing left to
  cancel; could not be confirmed, so the answer may still be generated. Reopening the panel
  refreshes the conversation, so a stopped request's saved answer appears. All three paths are
  tested with deferred promises.
- **Conversation history.** Earlier answers are sent back marked "[Earlier answer from …: its
  figures are as of then and are NOT current evidence]". A follow-up asked **after the price
  changed** (eval `h09`) used the new close.
- **Deletion during generation.** The conversation row is locked and re-checked before writing.
  A deleted conversation is not resurrected; the answer is discarded with 409, and its admission
  and budget stay spent (it was paid for). Verified on PostgreSQL with the foreign key enforced:
  409, zero rows, no FK error.
- **Phase 2 decisions** are recorded in `docs/features/2026-10-13-copilot-phase2-design.md` §5.

## Real-model evaluation: tuned and held-out reported separately

The earlier 17 questions have been tuned against repeatedly and are **not** an independent
holdout. Two held-out rounds were added:

- **Round 1 (h01–h09)** was written after the review. Its first run is the independent measurement for
  that round. Its refusals were then used to fix the checker, so later runs of it are tuned.
- **Round 2 (h10–h17)** was written after those fixes and had never been seen when measured.

| Run | Set | Sonnet 4.6: first draft / fallback | Sonnet 5.5: first draft / fallback |
|---|---|---|---|
| 6 (R1 rule as first written) | tuned | 26/34 / 3 | 11/17 / 0 |
| 6 | **held-out round 1, first contact** | **8/18 / 6** | **6/9 / 1** |
| 7 (after the round-1-driven fixes) | tuned | 32/34 / 1 | 17/17 / 0 |
| 7 | held-out round 1 (now tuned) | 18/18 / 0 | 8/9 / 0 |
| 7 | **held-out round 2, first contact** | **14/16 / 0** | **5/8 / 0** |

**Round 1's first contact showed the first R1 rule was far too blunt.** Most of its refusals were
correct wording:

- "a **third**-party headline" (read as a fraction);
- "**Two** recent headlines (…)";
- "I cannot give a **dollar** figure";
- "cannot confirm whether it has **doubled**".

The current rule is the restructure described under R1. Round 2's first-contact refusals were all
correct: figures copied from an unavailable-data reason ("the last fourteen days"), an
un-negated "probability of profit", and "percent" written in words. Every one was repaired on
retry.

Final-run figures (run 7, Sonnet 4.6, all 68 answers):

| Metric | Value |
|---|---|
| Latency p50 / p95 | about 7 s / 10 s |
| Tokens per answer, mean / max | about 3,400 / 7,600 |
| Cost per answer, mean | $0.0134 |

Spend for this round of evaluation: $2.78. Raw results are in `2026-10-13-copilot-eval/run6-*` and `run7-*`.

### Semantic residuals (held-out round 2, read by the implementer)

What held:

- no injected instruction followed;
- no advice, target or probability given;
- fundamentals and K-Score history reported as unavailable rather than invented;
- the follow-up after a price change used the new close.

New residuals, not fixed by any figure check:

- **h16** asserts a cause it cannot know: "these are recalculations against updated closes, not new price moves".
- **h09** cites the report's version-to-version changelog as if it showed the change since the last chat answer.
- **h10** gives unlabelled interpretation: "the market may have priced in a lot already".

The figure checks prove provenance, not soundness of reasoning. That limit is stated in the UI.

## Validation

| Suite | Result |
|---|---|
| research-engine | 598 passed, 7 skipped (the PostgreSQL-only tests) |
| PostgreSQL 16 (copilot probe and concurrency) | 29 passed |
| Frontend | 582 passed (including 6 jsdom interaction tests), `tsc` clean |
| `make test`, 12 services | all passed (market-data 5,371; ranking-engine 188; research-engine 598) |

The reviewer's probe (`evidence/2026-10-13-copilot-review/probe.py`) now stops at its R4
scenario: a completed request replayed with a changed question is refused with 409, which is the
fix. The probe file is left as the reviewer's evidence of the old behaviour.

**Added dev dependencies (frontend, test-only):** `jsdom@24.1.3`, `@testing-library/react@16.0.1`,
`@testing-library/dom@10.4.0`. They are used only by `CopilotPanel.interaction.test.tsx` via a
per-file `@vitest-environment jsdom` directive; every other test still runs in the node environment.
