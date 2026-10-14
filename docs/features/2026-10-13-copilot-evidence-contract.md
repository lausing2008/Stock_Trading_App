# Evidence-linked copilot — Orders 0–1 (2026-10-13)

Implements Orders 0 and 1 of [the TradingView Copilot / Tenji review](2026-10-12-tradingview-copilot-tenji-review.md):
the evidence/context contract and a read-only stock copilot with citations and report comparison.
**Status: validated locally (see [the validation report](../audits/2026-10-13-copilot-validation.md)),
committed, not deployed. No independent review yet.**

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
conversation or permanently delete one. Retry after an error reuses the same request id, so it is
answered once and billed once. **Stop** prevents further model calls for the question, but a call
already running completes and is counted (the panel says so).

## The contract

| Piece | Where | Rule |
|---|---|---|
| Evidence packet (v2) | `services/research-engine/src/copilot/packet.py` | Built server-side from the report adapters (`price_as_of`, `session_return`, `trend_structure`, `latest_signal`, `next_earnings_event`) plus K-Score, headlines (14 days), and the latest **public** stock report with its `store.diff()` against the predecessor. Every value is a `report_contract.Field` with a state and reason; `validate_evidence()` refuses a packet whose citations do not resolve. `signal_drivers` and `fundamentals` are explicit UNAVAILABLE fields. |
| Quantities | same | Stable keys (`price.close`, `data.age`, `return.5s`, `signal.strength`, `report_change.3`, `headline.1`, ...), each with a **bare display value computed in code** and a separate `meaning` that is sent to the model but never rendered. Staleness propagates to derived values. |
| Output format | `copilot/llm.py`, `grounding.parse_draft` | `<answer>…</answer>` plus `<limitation>…</limitation>` tags, chosen because unescaped quotes broke a JSON protocol in the eval. JSON is still parsed as a fallback. |
| Grounding | `copilot/grounding.py` | Every figure must be a `{{key}}` placeholder; **any other digit is refused**. The exceptions are names, not figures: the symbol, index names (S&P 500), session/bar windows **named by the evidence**, `SMA<n>` for computed averages, and a period label (Q3) **only when a packet headline uses it**. Also refused: figures written in words (hundred/million/percent), unknown keys, an un-negated "probability of profit", and order language. All or nothing: one repair attempt, then the deterministic fallback. |
| Headlines | `packet.py` | Passed as quoted data under `headlines_untrusted`. A headline addressed to AI systems is **withheld** from the model and from rendering, and its source link is kept. This is a heuristic (see the validation report §6). |
| Identity | `api/copilot_routes.py` | User from the JWT only. Every store function takes `user_id`. Another user's conversation id returns 404, and another user's request id is not replayed. |
| Idempotency and claims | `CopilotMessage` unique `(user_id, request_id, role)` plus `status`/`claimed_at` on user turns | A replay returns the stored answer. A retry takes over a question only through one conditional UPDATE, and only if it **failed**, or its claim (pending or cancelled) is older than 300 s. A retry while a call may still be running gets 409 and never a second call. |
| Cancellation | `POST /copilot/cancel` | Marks the question cancelled; the service checks before every model call, so the **repair call** is skipped. An in-flight provider call cannot be recalled. A paid answer is always stored. |
| Bounds | `copilot/service.py` | At most 2 model calls per answer; 1,200 output tokens; 1,000-character questions; 6 prior turns (re-wrapped in the output format). `llm_budget` scope `copilot_chat`: 600k tokens per UTC day, **sized against measured usage** (about 200 answers/day, about $2.50/day on Sonnet 4.6). It reserves before each call and reconciles afterwards; a timeout settles as ambiguous and is not refunded. The user's own API key bypasses the shared budget. Per-user limit: 60 answers per UTC day (`COPILOT_DAILY_ANSWERS_PER_USER`). |
| Model compatibility | `llm.claude_body` | `temperature` is sent only to models that accept it (not Opus 4.7/4.8 or the 5.x generation, which return 400). `stop_reason` is recorded. |
| Connection use | `service.py` | No DB session is held during a model call (read → call → write), to avoid the 2026-09-22 pool-exhaustion shape. |
| Usage logging | `llm_usage.CALL_SITE_COPILOT_CHAT` | One `llm_call_log` row per attempt. |
| Retention | `shared/common/copilot_retention.py` | 180 days after last activity, purged weekly by market-data's `copilot_retention_purge` job (Sunday 15:45 PT). Owner delete is a hard delete of the conversation and its messages. |

Schema: new tables `copilot_conversations` and `copilot_messages` (`shared/db/models.py`).
`create_all()` creates them at startup; verified on a fresh PostgreSQL 16, and idempotent.

API (gateway prefix `copilot` in `_ROUTES`, 240 s POST timeout like `/ai`): `POST /copilot/chat`,
`POST /copilot/cancel`, `GET /copilot/conversations?symbol=`, `GET /copilot/conversations/{id}`
(also the export format), `DELETE /copilot/conversations/{id}`.

## Tests

| File | Engine | What it covers |
|---|---|---|
| `tests/test_copilot.py` over `_copilot_probe.py` | SQLite, or PostgreSQL via `STOCKAI_INTEL_DB` | 14 tests: packet, grounding boundaries in both directions, persistence and replay, repair and fallback, owner isolation, admission limits, claims, cancellation, retention, report changes |
| `tests/test_copilot_pg.py` over `_copilot_pg_concurrency.py` | PostgreSQL only (skipped otherwise; drops the schema of the database it is given) | Startup migration; same-id races; budget ceiling under concurrency |
| `tests/_copilot_eval.py` | Manual only: calls a real model and spends money | 17 representative questions; results in `docs/audits/2026-10-13-copilot-eval/` |

## Not built (later orders or follow-ups)

- **No tool-calling loop.** Evidence is pre-fetched into one packet per turn. Model-driven tools
  (`screen_universe`, `get_existing_alerts`) arrive with Order 2's action drafts.
- **No chart context or chart commands** (Order 2).
- **Fundamentals and valuation are explicitly unavailable** in this phase. Add them through
  `issuer_facts`/`valuation` only after their own readiness gates (see the quality-value readiness inventory).
- **Not run against DeepSeek.**
- **Re-measure the cap** from `llm_call_log WHERE call_site='copilot_chat'` after real use; the
  current sizing comes from a synthetic evaluation.
- Event connections (Order 3) and the DSL builder (Order 4) are not started.

## Deploying

`shared/db/models.py`, `shared/db/__init__.py`, `shared/common/llm_budget.py`,
`shared/common/llm_usage.py` and the new `shared/common/copilot_retention.py` changed, so **all 12
backend containers drift** until rebuilt (`docs/incidents/docker-deploy-staleness.md`). Services
touched directly:

- research-engine: new package and router;
- api-gateway: route map;
- market-data: the purge job.

Frontend: new panel. Use the deploy scripts; they hold the host lock.
