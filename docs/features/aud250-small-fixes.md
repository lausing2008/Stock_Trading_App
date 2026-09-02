## Feature Reference: AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG — Fallback Reason Now Visible in Response (Built 2026-07-19)

**The gap**: `T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT` (2026-07-07) added a `log.warning()` for
every silent equal-weight fallback in `services/portfolio-optimizer/src/optimizers/methods.py`
— SLSQP non-convergence, or `constraints.max_weight` making `sum(w)=1.0` mathematically
infeasible (`n * max_weight < 1.0`) — but `PortfolioWeights` (the response dataclass) never
gained a field for it. A caller got a flat, indistinguishable-from-genuine equal-weight result
with `HTTP 200` and zero indication their constraint was ignored, visible only by grepping
production logs.

**Fix**: added `fallback_reason: str | None = None` to `PortfolioWeights`, set at all 4
fallback call sites:
- `mean_variance()` / `risk_parity()` / `ai_allocation()` — each has two fallback branches
  (the `n * max_weight < 1.0` bypass that skips SLSQP entirely, and the `res.success == False`
  branch after a real SLSQP attempt), each now sets a distinct descriptive message.
- `hierarchical_risk_parity()` — its cap-and-redistribute helper, `_cap_and_redistribute()`,
  changed its return type from a bare `np.ndarray` to `tuple[np.ndarray, str | None]` so the
  infeasibility-bypass case (the only fallback HRP has, since it never calls SLSQP) can
  propagate a reason up to `_pack()`.

`_pack()` (the shared response-builder used by 3 of the 4 methods) gained a `fallback_reason`
parameter; `ai_allocation()` builds `PortfolioWeights` directly (doesn't go through `_pack()`)
and was updated separately. `routes.py`'s `asdict(out)` already serializes any dataclass field
automatically — no route change was needed for the field to reach the actual HTTP response.

**Frontend**: `frontend/src/lib/api.ts`'s `PortfolioWeights` type gained `fallback_reason?:
string | null`; `frontend/src/pages/portfolio.tsx` shows a warning banner
("Optimization fell back to equal weight: ...") whenever it's set, using the same `setWarning()`
state the existing `dropped_symbols` warning already uses (mutually exclusive — `dropped_symbols`
wins if both would otherwise fire, so the single banner never has to show two unrelated
warnings at once).

**A real gap surfaced while wiring this up**: `portfolio.tsx` has no `max_weight` UI control at
all today — only the SLSQP-non-convergence fallback is reachable from this page currently. The
infeasibility-bypass fallback_reason is still fully live and tested via the API directly (and
protects any future UI work that adds a `max_weight` slider/input), just not yet exercisable
end-to-end through this specific page.

**Tests**: 8 new/extended cases across `services/portfolio-optimizer/tests/test_optimizers.py`
and `test_optimize_endpoint.py` — genuine (non-fallback) results assert `fallback_reason is
None` for all 4 methods; every fallback path (SLSQP non-convergence for the 3 SLSQP-based
methods, the infeasible-`max_weight` bypass for all 4 methods — `ai_allocation`'s infeasibility
path had zero prior test coverage, added here) asserts a real, descriptive `fallback_reason`
string; a new end-to-end test calls the real `optimize()` endpoint function with an infeasible
`max_weight` and confirms `fallback_reason` survives all the way into the actual HTTP response
dict, not just on the internal dataclass.

Adversarially verified 3 times, each caught and reverted: sabotaged `mean_variance()`'s
SLSQP-failure `fallback_reason` assignment (1 test caught it); sabotaged
`_cap_and_redistribute()`'s infeasibility-branch return value to `None` (3 tests caught it,
including the HRP-level integration test — proving the signal genuinely propagates end-to-end,
not just at the helper's own unit level); sabotaged `_pack()`'s own `fallback_reason`
pass-through to a hardcoded `None` (5 tests caught it, including the new end-to-end endpoint
test — confirming the flag would still be visible even if a regression were introduced at the
shared packing layer, not just at an individual method's own call site).

**What to check if this looks wrong**:
```bash
docker exec stockai-portfolio-optimizer-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.optimizers.methods import risk_parity
import pandas as pd, numpy as np
rng = np.random.default_rng(0)
returns = pd.DataFrame({'A': rng.normal(0.0005, 0.01, 60), 'B': rng.normal(0.0003, 0.015, 60)})
# 2 symbols * 0.2 max_weight = 0.4 < 1.0 -> infeasible, must set fallback_reason
r = risk_parity(returns, max_weight=0.2)
print(r.fallback_reason)
"
```

---

