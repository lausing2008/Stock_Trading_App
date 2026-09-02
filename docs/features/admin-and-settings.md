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

