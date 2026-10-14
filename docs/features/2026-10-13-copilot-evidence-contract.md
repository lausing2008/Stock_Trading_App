# Evidence-linked copilot — Orders 0–1 (2026-10-13)

Implements Orders 0 and 1 of [the TradingView Copilot / Tenji review](2026-10-12-tradingview-copilot-tenji-review.md):
the evidence/context contract and a read-only stock copilot with citations and report comparison.
**Status: built and tested locally. Not committed, not deployed.**

## What changed for the user

The stock page's "Ask AI" panel is replaced by `CopilotPanel`. The browser now sends only the
question. The server builds the evidence, checks the answer against it, and returns:

- the answer, where every figure was rendered by the server from stored data;
- a status: **grounded**, or **evidence check failed** (the stored data is shown instead, with the
  rejection reasons);
- `data_as_of`: the time of the newest observation used, not the time the answer was generated;
- the citations: label, displayed value, state (OK/STALE), and evidence id, source and observation
  or publication time; headlines link to their URL;
- the limitations: what is missing or stale, each with its reason.

Conversations persist per user and resume when the panel is reopened. A user can start a new
conversation or permanently delete one. Retry reuses the same request id, so a retried request is
answered once and billed once. Cancel aborts the browser request; see the limits below.

## The contract

| Piece | Where | Rule |
|---|---|---|
| Evidence packet | `services/research-engine/src/copilot/packet.py` | Built server-side from the report adapters (`price_as_of`, `session_return`, `trend_structure`, `latest_signal`, `next_earnings_event`) plus K-Score, headlines (14 days), and the latest **public** stock report with its `store.diff()` against the predecessor. Every value is a `report_contract.Field` with a state and reason; `validate_evidence()` refuses a packet whose citations do not resolve. |
| Quantities | same | Each citable value has a stable key (`price.close`, `return.5s`, `signal.strength`, `report_change.3`, `headline.1`, ...) and a **display string computed in code**. Staleness propagates: returns computed from stale bars are STALE too. |
| Grounding | `copilot/grounding.py` | The model returns `{"answer", "limitations"}` JSON. Every figure must be a `{{key}}` placeholder. **Any digit outside a placeholder is refused** (`quantities.literal_numbers`, the N1 rule); the symbol's own digits (0700.HK) are allowed. Unknown keys, wording that presents a score as a probability of profit, and order language are also refused. All or nothing: a refused draft gets one repair attempt, then the deterministic fallback. |
| Headlines | `packet.model_view()` | Passed as quoted data under `headlines_untrusted`, never inside the system prompt. Test p1 asserts this. |
| Identity | `api/copilot_routes.py` | User from the JWT only. Every store function takes `user_id`. Another user's conversation id returns 404, and another user's request id is not replayed. |
| Idempotency | `CopilotMessage` unique `(user_id, request_id, role)` | A replay returns the stored turn. A retry after a failed first turn reuses that conversation (bug found by the probe and fixed). |
| Bounds | `copilot/service.py` | At most 2 model calls per answer; 1,200 output tokens; 1,000-character questions; 6 prior turns. `llm_budget` scope `copilot_chat` (600k tokens per UTC day, **sized, not measured**) reserves before each call and reconciles afterwards; a timeout settles as ambiguous and is not refunded. Calls using the user's own API key are not charged to the shared budget. Per-user limit: 60 answers per UTC day (`COPILOT_DAILY_ANSWERS_PER_USER`). |
| Connection use | `service.py` | No DB session is held during the model call. The turn runs as read → call → write, to avoid the 2026-09-22 pool-exhaustion shape. |
| Usage logging | `llm_usage.CALL_SITE_COPILOT_CHAT` | One `llm_call_log` row per attempt. |
| Retention | `copilot/store.py` | 180 days after last activity (`purge_expired()`). Owner delete is a hard delete of the conversation and its messages. |

Schema: new tables `copilot_conversations` and `copilot_messages` (`shared/db/models.py`). They are
new tables, so `create_all()` creates them at research-engine startup; no column migration is needed.

API (gateway prefix `copilot` added to `_ROUTES`, 240 s POST timeout like `/ai`):
`POST /copilot/chat`, `GET /copilot/conversations?symbol=`, `GET /copilot/conversations/{id}`
(also the export format), `DELETE /copilot/conversations/{id}`.

## Verification

- `services/research-engine/tests/test_copilot.py`: 10 tests over `_copilot_probe.py`, 9 scenarios on a
  real database with only the model scripted. Covered: packet citations resolve, staleness
  propagates, the grounding rules, persistence and replay, the repair and fallback paths, owner
  isolation, quota/budget/timeout accounting, retry after a timeout, retention purge, report
  changes citing both versions, and private reports being ignored.
- **Ran on SQLite only.** Setting `STOCKAI_INTEL_DB` would run the same probe on PostgreSQL; that
  has not been done.
- Sabotage check: disabling the digit refusal turns the suite red.
- `make test`: all 12 backend services pass. Frontend: `tsc --noEmit` is clean and vitest passes
  576/576, including the `CopilotPanel.test.tsx` render tests.
- **Not exercised against a real model.** The rejection rate is the first thing to measure: the
  digit rule refuses phrases like "S&P 500" or "Q3", and the repair turn exists for that.

## Deliberately not built (later orders or follow-ups)

- **No `purge_expired()` schedule yet.** The retention rule is defined and tested, but nothing calls
  it. Wire it into the market-data scheduler's purge jobs.
- **No tool-calling loop.** Evidence is pre-fetched into one packet per turn. Model-driven tools
  (`screen_universe`, `get_existing_alerts`) arrive with Order 2's action drafts.
- **No chart context or chart commands** (Order 2). The request does not carry timeframe or visible
  range yet.
- **No server-side cancellation.** Cancel aborts the browser request, but the server may finish and
  bill the call. A retry with the same request id then serves the stored answer.
- **Fundamentals and valuation are not in the packet.** The model is told to report them as
  unavailable. Add them through `issuer_facts`/`valuation` only after their own readiness gates
  (see the quality-value readiness inventory).
- **The 600k budget is unmeasured.** Re-size it from `llm_call_log WHERE call_site='copilot_chat'`
  after a week of use.
- Event connections (Order 3) and the DSL builder (Order 4) are not started.

## Deploying

`shared/db/models.py`, `shared/db/__init__.py`, `shared/common/llm_budget.py` and
`shared/common/llm_usage.py` changed, so **all 12 backend containers drift** until rebuilt
(`docs/incidents/docker-deploy-staleness.md`). Services touched directly: research-engine (new
package and router) and api-gateway (route map). Frontend: new panel. Use the deploy scripts; they
hold the host lock.
