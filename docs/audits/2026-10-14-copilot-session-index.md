# START HERE: copilot session, 2026-10-12 to 2026-10-14

This session took the TradingView Copilot / Tenji review from plan to a deployed, evidence-linked
stock copilot with screening and confirmed action drafts. It went through two independent reviews
and their fixes, and closed a separate ranking-permissions gap. This page indexes every document,
commit and decision. The details live in the linked files.

## What exists now

| Capability | Where the user sees it | Tracker |
|---|---|---|
| Evidence-linked copilot: server-built evidence, figures inserted by code, citations, data age, limitations | Stock page, "Ask the copilot" | 437 |
| Ranking refresh and K-Score tuning require admin or the scheduler's credential | — | 438 |
| Natural-language screening with validated filters and coverage | Screener page, "Copilot screen" (old "AI Screen" retired) | 439 |
| Action drafts (price alert, signal alert, watchlist add), saved only on confirm | Copilot answer cards | 440 |
| Phase 2 review fixes (save recovery, grammar-bound interpretation, scoped preconditions, consent binding, honest alert wording) | — | 441 |

## Documents, in reading order

1. [Review and plan: TradingView Copilot and Tenji](../features/2026-10-12-tradingview-copilot-tenji-review.md)
   is the starting plan (Orders 0–5).
2. [Copilot evidence contract (Orders 0–1)](../features/2026-10-13-copilot-evidence-contract.md)
   covers what the copilot is and its rules.
3. [Validation before deployment](2026-10-13-copilot-validation.md) covers the real-model,
   semantic, PostgreSQL and retention/cancellation checks. The cost is stated as an *estimate*,
   not a ceiling.
4. [Independent review #1](2026-10-13-copilot-independent-review.md) and its
   [remediation (R1–R6)](2026-10-13-copilot-review-remediation.md) cover tuned versus held-out
   evaluation.
5. [Authentication used as authorization](../incidents/authentication-used-as-authorization.md)
   is the ranking-permissions fix (RANK-AUTHZ) and the bug class.
6. [Phase 2 design and decisions D1–D4](../features/2026-10-13-copilot-phase2-design.md), with
   slice 1 (screening) and slice 2 (drafts) in §6a/§6b.
7. [Independent review #2 (phase 2)](2026-10-13-copilot-phase2-independent-review.md) and its
   [remediation (S1–S6)](2026-10-14-copilot-phase2-review-remediation.md).

Raw evaluation results (synthetic fixtures; no API key): `docs/audits/2026-10-13-copilot-eval/`.

## Commits (`prod`)

| Commit | What |
|---|---|
| `9a48aca8` | Phases 0–1 checkpoint (labelled unvalidated) |
| `ab3c0dd7` | Phases 0–1 validated locally |
| `4844b8bf` | Ranking refresh/tune admin gate (separate, as requested) |
| `fb1bc52b` | Review #1 remediation (R1–R6) |
| `41fab0b5` | Phase 2 slice 1: screening |
| `18d27c9a` | Phase 2 slice 2: drafts, price-alert idempotency |
| `1691875e` | Review #2 remediation (S1–S6) |
| `e757ccaa` | Retire the old AI Screen box |
| (this commit) | This index, the tracker entries 437–441, and the CLAUDE.md pointer |

## Operator decisions recorded

- The copilot is read-only. Writes happen only as user-confirmed drafts through existing endpoints.
- **D1:** watchlist adds keep the signal-email enrolment, disclosed with its *effective* result.
- **D2:** price alerts get database-backed, per-user idempotency; reusing a key with different
  parameters is refused.
- **D3:** non-admins screen their own watchlists. This is a product boundary, not access control:
  `GET /rankings` is readable by every user.
- **D4:** order is screening, then drafts, then chart commands.
- The 600k daily token cap is **provisional**; about $75/month is an estimate.
- Watchlist moves are deferred until an atomic endpoint exists.
- The old AI Screen is retired.

## Schema changes this session (all applied at service startup)

- **New tables:**
  - `copilot_conversations`
  - `copilot_messages` (with `claimed_at` and `request_fingerprint`)
  - `copilot_usage_day`
  - `copilot_draft_receipts`
- **Existing table `price_alerts`:** `idempotency_key` and `request_fingerprint` columns, plus
  the partial unique index `uq_price_alerts_user_idempotency`. Applied by `session.py`'s isolated
  DDL; the `copilot_draft_save` capability refuses draft saves if they are missing.
- **Model-only declarations** of indexes that production already has: `idx_uq_wl_item`, and the
  price-alert index.

## Not built / still open

- **Chart commands (phase 2, slice 3).** Next. Start with the chart state/command boundary and
  manual-control regression tests, one command family at a time with acknowledgment and undo.
- **Event connections (Order 3), the DSL builder (Order 4), analogues and macro (Order 5).**
- **Fundamentals** are explicitly unavailable in the copilot. Not tested with DeepSeek.
- **Known semantic residuals:** prose around correct figures is not verified. The UI says so.
- **Production rejection and repair rates are unmeasured.** Watch `llm_call_log` for
  `copilot_chat` / `copilot_screen` and the per-user ledger.

## Deployment

Deployment status and the post-deploy verification are recorded in the section appended below
after the deployment runs.
