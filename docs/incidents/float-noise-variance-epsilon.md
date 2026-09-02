## Recurring Issue: AUD292-SHARPE-VAREPS — paper_portfolio.py's Sharpe/Sortino Had the Exact Float-Noise-Explosion Bug strategy-engine's Own T237-SE1 Fix Already Found and Guarded Against, Never Ported Back (Fixed 2026-08-20)

**Found while verifying a background research agent's "next improvement candidates" survey
against real code before acting on any of it** — the agent's ranked list included the
`evaluate_signal_outcomes()` per-signal-rollback savepoint fix and `gate_backtest()`'s
same-day-close lookahead-bias fix as its top 2 picks; both turned out **already fixed** on
direct inspection (`outcomes.py:2455,2517` both show `with session.begin_nested():`;
`gate_backtest()`'s own docstring already carries an `AUD283-GATEBACKTEST-LOOKAHEAD` fix note
dated 2026-08-16, and it's a real, reachable, frontend-tabbed research tool — not the dead code
the agent claimed). The agent's more general "3-4 independently-audited Sharpe/max-drawdown
implementations, never formally cross-checked" concern, however, held up on direct inspection
and surfaced a real, concrete bug once the 3 real implementations were read side by side.

**Root cause**: `strategy-engine/backtest/engine.py`'s own Sharpe/Sortino computation already
carries a detailed, dated comment (`T237-SE1`) documenting a real bug it once had: a bare
`> 0` denominator check lets floating-point NOISE (an all-identical or all-nonnegative return
series produces a variance/downside-deviation of ~1e-16 to 1e-33, not an exact `0.0`) through
as a valid divisor, exploding the resulting ratio toward `+-1e7-1e9`. That file's own fix —
`_VOL_EPS = 1e-9`, a real epsilon threshold instead of a bare `> 0` — was never ported back to
`services/market-data/src/api/paper_portfolio.py`'s own, independent `_portfolio_risk_metrics()`
(the function backing every real paper portfolio's dashboard Sharpe/Sortino reading), which
still used `variance > 0` / `annualised_vol > 0` / `downside_dev > 0` at all 3 of its own
division sites. `portfolio-optimizer/optimizers/methods.py` had independently arrived at the
same real epsilon (`exp_vol > 1e-9`) on its own, unrelated development path — making
`paper_portfolio.py` the one genuinely un-hardened sibling of the three.

**Confirmed the explosion is real, not hypothetical, before fixing**: constructed a fixture
whose recomputed-from-equity variance lands in the genuine `(0, 1e-9)` float-noise band
(`~1e-33`, verified directly — NOT an exact `0.0`, which a naive `[0.001]*24` "identical
returns" list actually recomputes to, since `equity[i]/equity[i-1]-1` round-trips exactly for
that specific construction) and confirmed the pre-fix code produced `sharpe=
194,338,923,966,771.66` / `sortino=987,730,302,862,027.2` — the same order of magnitude
`T237-SE1`'s own comment documents for its sibling bug (`+-50,000,000.0` / `+2,470,000,000.0`).

**Fix applied**: added the identical `_VAR_EPS = 1e-9` threshold (matching `T237-SE1`'s own
`_VOL_EPS` convention exactly) to all 3 of `_portfolio_risk_metrics()`'s own division-by-
near-zero gates: the raw daily-return variance (feeding `std_r`), the annualized volatility
(feeding `sharpe`), and the downside deviation (feeding `sortino`).

**A real, self-caught "still passes after sabotage" test-construction mistake, matching this
repo's own standing discipline of treating that exact outcome as a finding, not a shrug**: the
first test fixture (`[0.001] * 24`, "24 identical daily returns") passed cleanly both before
and after sabotaging the fix back to a bare `> 0` — investigated why rather than accepting it,
and found `equity[i] / equity[i-1] - 1` on an equity curve built by repeatedly multiplying the
SAME rate in is numerically EXACT in IEEE-754 double precision for this specific operation
(no accumulated rounding across iterations, since each step is one multiply and one divide,
not a running sum) — the recomputed variance was an exact `0.0`, which both the buggy and
fixed comparison correctly reject either way. Fixed by perturbing the target daily rate by
`1e-17` per step and confirming DIRECTLY (not assumed) that the round-trip-through-equity
recomputation preserves genuine sub-epsilon noise (`~1e-33`) rather than collapsing to exact
zero. The Sortino-specific test needed the identical correction — an all-nonnegative-returns
fixture recomputes `downside_sq` as exactly `{0.0}` in every case, since `min(r, 0.0)` on a
strictly-positive float involves no subtraction and produces no noise at all — rebuilt with
one deliberately tiny-negative (`-1e-16`) day among otherwise-real-positive returns, confirmed
a genuine sub-epsilon `downside_dev` (`~3.5e-16`) alongside real, large overall variance
(isolating the Sortino gate from the Sharpe gate specifically).

**Tests**: `services/market-data/tests/test_sharpe_variance_epsilon.py` (5 cases) —
`_portfolio_risk_metrics()` is pure math with zero DB/session dependency, but
`paper_portfolio.py` as a whole module can't be imported directly in this test environment
(its module-level `from db.models import ...` fails against the wholesale-`MagicMock` `db`
stub, which has no real `models` submodule) — extracted just this one function's source text
(plus its two module-level constant dependencies, `_MIN_SHARPE_DAYS`/`_MIN_CAGR_DAYS`) via
`exec()` and tested it BEHAVIORALLY with real numeric equity-curve input, not source-text
regex checks alone. Covers: the float-noise-explosion case for both Sharpe and Sortino
(reproducing the real production-scale numbers above), genuine volatility still producing a
real finite ratio (the fix must not break the normal case), max drawdown staying unaffected
(computed independently of the variance/std_r path this fix touches), and the pre-existing
`_MIN_SHARPE_DAYS` sample floor short-circuiting before this epsilon path ever runs.

**Adversarial verification**: reverted all 3 epsilon checks back to bare `> 0` and confirmed
exactly the 2 dedicated float-noise tests failed — with the real production-scale explosion
values reproduced in the assertion output — while the 3 unrelated tests (genuine volatility,
max-drawdown, insufficient-sample-floor) correctly stayed green; restored and confirmed
byte-identical via `diff` before moving on. Full 1929-test market-data suite green; pyflakes
clean (all 4 remaining warnings confirmed pre-existing via `git stash` — only line numbers
shifted).

**Not fixed in this pass, documented not silently dropped**: `strategy-engine` has no
minimum-sample-days floor on Sharpe/Sortino/Calmar at all — `paper_portfolio.py`'s own
`_MIN_SHARPE_DAYS = 20` floor was never ported the OTHER direction. `portfolio-optimizer`
clips per-period returns at `-0.99` before computing max drawdown (`np.clip(port_rets, -0.99,
None)`), a convention neither sibling shares. Both are real, narrower divergences noted here
for a future pass rather than bundled into this one.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_VAR_EPS" /app/src/api/paper_portfolio.py
# Should show 4 matches: the definition, and 3 uses (variance/annualised_vol/downside_dev).

# Spot-check a real portfolio's reported Sharpe/Sortino directly for an implausibly large value:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT id, name FROM paper_portfolios WHERE is_active = true;"
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'<username>','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://localhost:8001/paper-portfolio/1/summary', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.json().get('risk_metrics'))
"
```

---

