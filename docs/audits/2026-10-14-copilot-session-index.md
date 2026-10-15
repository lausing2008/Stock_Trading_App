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

## After the deployment: re-review round (2026-10-14, NOT YET DEPLOYED)

- [Independent re-review](2026-10-14-copilot-remediation-independent-rereview.md) and its
  [remediation (C1/C2)](2026-10-14-copilot-rereview-remediation.md):
  - numbers are read as whole tokens, and named averages are matched before any number;
  - negated comparisons, "or", and percentage scores are refused with a reason;
  - the ranking authorization proof no longer needs a worker thread.
- **AUD-UW-INTRADAY-TZ** (`ae2765f3`, separate commit, entry in
  `docs/incidents/external-data-source-liveness.md`): every intraday Unusual Whales fetch had
  been failing, uncounted, behind the yfinance fallback.
- Tracker tiers 442 (Unusual Whales) and 443 (parser).
- Chart commands remain on hold until this round is reviewed.

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

## Deployment: 2026-10-14, verified

Deployed from `13ab0dd3` with `scripts/deploy.sh` (one service at a time, `--no-deps`) and
`scripts/deploy_frontend.sh` (under the deployment lock).

**Order.** market-data went first, because it applies the `price_alerts` migration. Then the
other 11 backends, with api-gateway last, then the frontend: 13 of 13.

**Pre-session commits in the pull.** The deploy included 7 earlier commits between EC2's
`2590aa20` and this session. Only one changes running code: `6d1e5d2a` (HK 2027 holidays). Its
operator decision (2026-10-13) says to ship it with "the next coordinated deployment", which this
was. The rest are documentation.

**Verification**, each check run, not inferred:

| Check | Result |
|---|---|
| Health | every container healthy after a 45 s settle; every declared service present; host 41 GB free, iowait about 3% |
| `check_deploy_drift.sh` | 0 of 12 backends drifted against `13ab0dd3` |
| Production DB, read-only | `price_alerts.idempotency_key` / `request_fingerprint` exist; `uq_price_alerts_user_idempotency` is `(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL`; all four copilot tables exist; `idx_uq_wl_item` unchanged |
| `copilot_draft_save` capability, evaluated in the research-engine container | **ready**, all 5 requirements |
| Unauthenticated, through `https://lausing.com` | `/api/copilot/{conversations,chat,screen,drafts/confirm}`, `/api/signal-alerts/preview`, `/api/watchlist/{symbol}/preview`, `/api/rankings/{refresh,tune_kscore_weights}` → **401** (routed, gated; no 404) |
| Served frontend chunks | contain the copilot screen, the new badge text, the unconfirmed-save state and tracker tier 441. The old AI Screen text survives only in the tracker page, which quotes it historically; the screener chunk does not contain it |
| Pages | `/`, `/screener`, `/improvements`, `/stock/AAPL` → 200 |

**Not verified:** an authenticated end-to-end copilot answer in production. That needs a user
login, which was not used.

**Found during verification, pre-existing, not changed.** market-data logs `ingest.adapter_failed`
from the Unusual Whales bar adapter for many US symbols: "Invalid comparison between
dtype=datetime64[ns, UTC] and Timestamp" at `unusual_whales_adapter.py:223`. The provider's
timestamps parse as timezone-aware while the window bounds are naive.

It is not caused by this deploy:
- the adapter and its caller are unchanged in this deploy (the only scheduler change is the
  copilot purge job);
- prices still arrive through the fallback provider (GDX has bars up to 14:00 UTC today).

Because the failure raises after the metered request, the adapter's failure counters do not
record it. **Needs its own decision to fix.**
