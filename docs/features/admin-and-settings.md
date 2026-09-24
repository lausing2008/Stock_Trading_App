## Feature Reference: Admin AI Assistant Features Page (Built 2026-07-28)

**Direct follow-up request from the user**: "create a page under Admin with all the AI
Assistant API feature turn on and off buttons with explaination with the use of it and how
it can help."

**Scope decision** (no toggle exists for 6 of the 9 real Claude call sites documented in the
Cost Audit section above — most are already tightly cached/rate-limited by design, so a
toggle would add complexity with no real cost-control benefit): the new
`frontend/src/pages/admin-ai-features.tsx` page shows **all 9** call sites for visibility,
split into 3 groups —
1. **Toggleable — global**: `auto_research_enabled` (this session's own new flag).
2. **Toggleable — per paper-trading portfolio**: `llm_scoring_enabled` (T203) and
   `risk_check_enabled` (T258-WHATCOULDGOWRONG-AGENT), one row per real portfolio (fetched via
   `api.paperList()` + `api.paperSummary(id)` for each, updated via the existing
   `api.paperConfigure({...}, portfolioId)`).
3. **Always-on / info-only, no toggle**: the remaining 6 sites (per-symbol sentiment, Market
   Pulse themes, real-time news classification, macro reactions, and the two user-initiated
   chat features) — each shown with its model, real trigger cadence, and cache window, so a
   user understands why these don't need (and wouldn't benefit from) a switch.

**A second, real config-wiring gap found and fixed while building this**: `risk_check_enabled`
had the EXACT same T203-LLMWIRE gap `llm_scoring_enabled` was fixed for — decision-engine's
`routes.py:283` already reads `cfg.get("risk_check_enabled", False)`, but nothing in
`paper_trading_engine.py`'s `_call_decision_engine()` ever threaded it into `config_overrides`
(a built-but-dormant opt-in with no way to turn it on for any real portfolio), AND it was
missing from `paper_portfolio.py`'s `/configure` `allowed_keys` (T232-CONFIGGAP class — any
attempt to set it via the API would have been silently dropped as "unknown"). This meant the
new page's risk-check toggle would have silently done nothing without this fix. Fixed both
sides, mirroring `llm_scoring_enabled`'s own exact conditional-inclusion pattern
(`**( {"risk_check_enabled": True} if cfg.get("risk_check_enabled") else {} )`).

**Tests**: `services/market-data/tests/test_risk_check_config_wiring.py` (4 cases, source-text
extraction matching `test_llm_scoring_config_wiring.py`'s established technique) — confirms
`risk_check_enabled` is in `allowed_keys`, is threaded into `config_overrides`, is conditional
on the portfolio's own flag, and sits alongside the `llm_scoring_enabled` block. Adversarially
verified: sabotaged the `allowed_keys` entry (1 test caught it) and the `config_overrides`
threading (3 tests caught it), both reverted after confirming.

**Frontend**: `frontend/src/lib/api.ts` gained `auto_research_enabled` on both `pushConfig()`'s
param type and `getFeatureFlags()`'s return type, and `risk_check_enabled` on
`PaperPortfolioConfig`. New nav entry under Admin (`AI Assistant Features`, tag `new`) in
`_app.tsx`. The page reuses the exact `Toggle` component markup already established in
`settings.tsx`'s `broker_enabled` toggle, rather than inventing new toggle styling.

**Verification**: `npx tsc --noEmit` clean, full 89-test Vitest suite unaffected, full
`next build` clean (51 routes, `/admin-ai-features` compiles at 4.12 kB) — confirmed via
grepping the actual compiled `_app-*.js` chunk for the new nav label and the compiled
`admin-ai-features-*.js` chunk for `auto_research_enabled`/`risk_check_enabled`/
`What-Could-Go-Wrong`, proving the change reached what would actually ship, not just
looking correct in source. Full 587-test market-data suite (up from 583) green.

**What to check if this looks wrong**:
```bash
# Confirm the compiled page bundle actually contains the new content:
docker exec stockai-frontend-1 sh -c "grep -o 'auto_research_enabled\|risk_check_enabled' /app/.next/static/chunks/pages/admin-ai-features-*.js"

# Confirm risk_check_enabled actually saves for a real portfolio (needs an admin JWT):
docker exec stockai-market-data-1 curl -s -X POST 'http://localhost:8001/paper-portfolio/configure?portfolio_id=<id>' \
  -H "Authorization: Bearer <admin token>" -H "Content-Type: application/json" \
  -d '{"risk_check_enabled": true}'
# The response's config.risk_check_enabled must be true, and ignored_keys must NOT
# contain "risk_check_enabled" — if it does, this fix didn't deploy.
```

---


---

## AUD-ALERTPREFS — per-alert-type preferences + one-click unsubscribe (Built 2026-09-24)

### The defect

Found while auditing why the Short Squeeze Alert had gone quiet. **Every scheduled alert
addressed "any user holding at least one untriggered `PriceAlert` row", and never compared the
alert's own symbol against that row** (`scheduler.py:3324-3332`, `:3680-3691`, `:4147-4155`,
`:4432-4441`). One price alert on one ticker subscribed a user to every candidate on every
symbol across squeeze, pre-breakout, gamma-unwind, options-flow, dark-pool and the rest. There
was no per-type preference and **no unsubscribe path anywhere in `send_email()`**, so the only
way to stop any of it was to delete your price alerts — which also stopped the alerts you wanted.

### What shipped

- `AlertPreference` (`user_id`, `alert_type`, `enabled`, `source`) + migration in `session.py`.
- `shared/common/alert_prefs.py` — 23-entry registry grouped for the UI, plus stateless HMAC
  unsubscribe tokens.
- `_filter_by_alert_pref()` applied at **all 11 alert jobs**. Central rather than folded into
  each job's own `PriceAlert` query: a dozen subtly different WHERE clauses is a dozen chances
  to get the audience rule wrong.
- `GET`/`PUT /alerts/preferences`; `GET /alerts/unsubscribe` (unauthenticated).
- An unsubscribe footer on all 23 manageable email types, and a settings screen.

### Three decisions that decide whether this is safe

**1. Absence means subscribed.** No rows are created up front; a missing row reads as opted IN,
so the deploy changed nobody's mail on day one (verified: 0 rows after deploy). The opposite
default would have silently switched off every alert on the platform at deploy time — a far
worse failure than the one being fixed, and **indistinguishable from the mail system breaking**.

**2. The filter fails open.** A preference lookup that raises returns the full recipient set.
Dropping alerts because a settings query failed looks, from outside, exactly like the alert
never firing.

**3. Essential mail is not representable.** A user's own price alert, an order fill, a broker
re-auth — suppressing these breaks something explicitly asked for or strands an account. They
are absent from the registry **and** listed in `ESSENTIAL`, and the API refuses to store a
preference for them rather than saving a row no sender consults.

### Security of the unauthenticated endpoint

`/alerts/unsubscribe` takes no auth because a mail client follows it with no session, often
months later — a login wall is how *"I unsubscribed and it kept coming"* happens. It is gated by
an HMAC over exactly `(user_id, alert_type)` signed with the shared `jwt_secret`: a
correctly-signed link can **disable one type for one account and nothing else**. It reads
nothing and enables nothing. A bad signature and an unknown type return an identical 400, so the
endpoint cannot be used to probe which alert types exist for which user ids.

The gateway needed a new `_PUBLIC_EXACT_PATHS` set **matched whole** — putting `alerts` in
`_PUBLIC_PREFIXES` would have exposed every price-alert CRUD route with it. Verified live:
`/api/alerts` 401, `/api/alerts/preferences` 401, `/api/alerts/unsubscribe/extra` 401,
`/api/alerts/unsubscribe` reachable.

### Two implementation notes worth keeping

**`send_email()`'s 4-arg signature is deliberately unchanged.** Threading `alert_type` through
it broke **199 tests** whose fakes take exactly four arguments — and those tests patch
`send_email` precisely so they can assert on the rendered body, so a footer applied *downstream*
of the patch would have been invisible to every one of them. `_with_unsub()` appends to the body
*before* the call, which keeps that coverage honest.

**The recipient's user id is resolved from their email address inside the footer**, rather than
threaded through all ~28 `send_*_email` builders and their call sites. One indexed lookup per
email, against a change that would otherwise touch dozens of signatures and invite exactly the
"this one builder forgot to pass it" gap the central footer exists to prevent.

### Testing notes

25 tests, 11 sabotages. The first pass caught only 5, and **four of the misses were
security-relevant — every one a defect in the TEST, not the code**:

| miss | why it passed |
|---|---|
| forged-token test | passed a garbage string, which fails whether or not the empty-secret guard exists |
| `ESSENTIAL` guard | unreachable — essential keys are absent from the registry, so the registry check already rejected them |
| registry check | the sabotage hit `is_known_alert_type()`, which shares the same line of code |
| constant-time compare | a timing property no unit test can observe |

Fixed with, respectively: a token forged *with the empty secret*; a test that patches the
registry to make the guard reachable; a correctly-targeted sabotage; and a structural assertion
that `compare_digest` is used and `== token` is not.

**One defect the suite could not catch at all.** `AlertPreference` was added to `models.py` but
not to `shared/db/__init__.py`, so `from db import AlertPreference` raised ImportError and the
endpoint 500'd in production while all 25 tests stayed green — none of them import from `db`,
because the suite stubs that package wholesale. Found by curling the live endpoint after deploy.
The regression test now asserts on the **source** of `__init__.py` (import block and `__all__`
checked separately, since either alone still breaks it); importing it under the stub would prove
nothing.

**Process note:** do not use `git checkout <file>` to undo a sabotage on a file with uncommitted
work — it restores from HEAD and silently discards the change under test. Copy the file aside
first, as every other sabotage loop here does.
