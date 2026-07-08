# Python Version Upgrade: 3.11 → 3.12 (2026-07-04)

**Status: DONE.** All 11 backend services upgraded from `python:3.11-slim` to `python:3.12-slim`,
tested, and deployed to production with zero restarts across the board. This document records
the research that justified the decision, the compatibility findings, and the verification
results — kept for reference the next time a Python version decision comes up (3.13, numpy 2.x,
or whatever the landscape looks like by then).

---

## 1. Why now, and why 3.12 (not 3.13, not "wait for 3.14/3.15")

| Question | Answer |
|---|---|
| Is 3.11 EOL? | No — still receiving security patches, EOL is October 2027 (~15 months of runway as of this writing) |
| Is there a performance emergency forcing an upgrade? | No — see the performance table below, the real gains are modest |
| Does waiting for 3.14/3.15 buy anything? | No — Python ships one major version per year every October; waiting only delays getting off the security-only clock with no offsetting benefit, since the dependency-compatibility risk (numpy 2.x) doesn't go away by waiting, it just moves further from your current 3.11-era pins |
| Why 3.12 over 3.13? | 3.12 is a clean drop-in (every pinned dependency already has a `cp312` wheel at its exact current version); 3.13 forces a numpy 1.26→2.x major-version bump with real breaking changes, turning a routine base-image bump into a dependency-migration project |

---

## 2. Dependency compatibility matrix (researched before upgrading)

Every dependency pinned across all 11 `requirements.txt` files was checked against the PyPI wheel
listings for the *exact currently-pinned version* — not "does a compatible version exist
somewhere," but "does this specific pin already work."

| Package (pinned version) | Python 3.12 | Python 3.13 | Notes |
|---|---|---|---|
| `numpy==1.26.4` | ✅ works as-is | ❌ needs bump to ≥2.1.0 | numpy 2.x has breaking API/ABI changes (dtype promotion rules, removed APIs) — the single biggest 3.13 risk item |
| `pandas==2.2.2` | ✅ works as-is | ⚠️ needs bump to ≥2.2.3 | Patch bump, low risk on its own |
| `scikit-learn==1.5.1` / `1.5.2` | ✅ works as-is | ⚠️/✅ 1.5.1 needs 1.5.2 | Already within the accepted pin range |
| `xgboost==2.1.1` | ✅ works as-is | ✅ works as-is | Ships ABI-agnostic wheel tags |
| `lightgbm==4.5.0` | ✅ works as-is | ✅ works as-is | Same |
| `torch==2.4.1` | ✅ works as-is (confirmed via direct wheel check) | not evaluated | `cp312` linux wheels confirmed present |
| `optuna==3.6.1` | ✅ works as-is | ✅ works as-is | Pure Python |
| `psycopg2-binary==2.9.9` | ✅ works as-is (confirmed via direct wheel check) | ⚠️ needs ≥2.9.10 | `cp312` wheel confirmed present at the exact pinned version |
| `sqlalchemy==2.0.32` | ✅ works as-is | ⚠️ needs bump to ≥2.0.36 | Patch-level, low risk |
| `pydantic==2.8.2` (+ pydantic-core) | ✅ works as-is | ✅ works as-is | pydantic-core already ships `cp313` wheels at this version |
| `fastapi==0.115.0` | ✅ works as-is | ✅ works as-is | Pure Python |
| `python-jose[cryptography]==3.3.0` | ✅ works as-is | ✅ works as-is | `cryptography` uses a stable ABI, forward-compatible |
| `uvicorn[standard]==0.30.6` (incl. uvloop) | ✅ works as-is | ⚠️ uvloop needs ≥0.21.0 if pinned at 0.20.0 | Only matters if uvloop is actually the active event loop |
| APScheduler, httpx, structlog | ✅ works as-is | ✅ works as-is | Pure Python |

**Verdict at research time:** 3.12 required zero forced dependency bumps across all 11 services.
3.13 would have required at minimum a numpy 2.x migration (major breaking changes) plus several
minor patch bumps (pandas, sqlalchemy, psycopg2, uvloop) — a real project, not a drop-in.

---

## 3. Breaking-change risk (Python language/stdlib itself, not libraries)

| Python version | Risk found | Relevance to this codebase |
|---|---|---|
| 3.12 | `distutils` removed (PEP 632) | **Checked — zero usage found** (`grep -rln "import distutils" services/ shared/` returned nothing) |
| 3.12 | `asynchat`, `asyncore`, `imp`, `smtpd` removed | Not used anywhere in this codebase (FastAPI/SQLAlchemy/APScheduler/psycopg2/jose don't depend on them) |
| 3.12 | `datetime.utcnow()` deprecated (not removed) | Still works; worth cleaning up opportunistically later, not a blocker |
| 3.13 | 19 more "dead battery" stdlib modules removed (`cgi`, `telnetlib`, `crypt`, etc.) | Not evaluated in depth since 3.13 wasn't the target, but none of these are used by this stack's core dependencies |
| 3.13 | Free-threaded build (`3.13t`) | Opt-in only, not default — irrelevant unless deliberately adopted |

**Result:** zero breaking-change risk found for the 3.12 target. The only stdlib item worth
checking (`distutils`) was checked and confirmed absent from the codebase before upgrading.

---

## 4. Performance research (what upgrading actually buys — measured, not marketing)

| Claim | Reality | Relevance to this app |
|---|---|---|
| PEP 709 comprehension inlining (3.12) | PEP's own microbenchmark shows "up to 2x" on a trivial case; representative pyperformance benchmark shows **~11%** | Applies to Python-level glue code (gate-checking logic, scheduler loops), not compiled ML code |
| Specializing adaptive interpreter increment (3.12) | No official headline percentage (unlike 3.11's "~25% faster than 3.10" claim); third-party pyperformance runs show roughly **+5% over 3.11**, hardware-dependent | Same — glue code only |
| PEP 703 free-threading / no-GIL (3.13) | Experimental opt-in only, not default; scikit-learn/XGBoost/LightGBM support partial or unconfirmed | Not usable for this stack without a deliberate rewrite — irrelevant to a routine upgrade |
| PEP 744 JIT compiler (3.13) | PEP 744 itself states the JIT is "about as fast as the existing specializing interpreter" — i.e. **no speedup yet**. Off by default, requires an experimental build flag. A core CPython developer publicly said it's "often slower than the interpreter" in current form. Roadmap targets only ~5% by 3.15, ~10% by 3.16 | Foundational work, not a shipped win — explicitly not a reason to chase newer versions yet |
| ML training speed (XGBoost/LightGBM/Optuna) | Training loops run in compiled C/C++/Cython, not Python bytecode — interpreter version differences are noise. A direct scikit-learn cross-version benchmark found training-time differences within noise (3.12 was sometimes the *slowest* in some runs) | **No expected benefit** for this app's core ML workload |
| FastAPI async request handling | No controlled version-isolated benchmark found; framework overhead dominates over interpreter-level differences | Negligible expected benefit |
| Glue/orchestration code (scheduler, gates) | pyperformance sub-benchmarks: `generators` 1.55x faster, `asyncio_tcp` 1.49x faster in 3.12 vs 3.11 — alongside some regressions (`unpack_sequence` 1.52x *slower*) | **The one place real gains concentrate** — modest, inconsistent, real but easy to overstate |

**Bottom line on performance:** this upgrade was justified by the security-support timeline, not
by a performance win. Any speed improvement in the scheduler/gate-checking code is a welcome side
effect, not the reason this was done.

---

## 5. EOL / security support timeline

| Version | Released | Status (as of this writing) | EOL date |
|---|---|---|---|
| 3.11 | 2022-10-24 | Security-only maintenance | 2027-10 |
| 3.12 | 2023-10-02 | Security-only maintenance | 2028-10 |
| 3.13 | 2024-10-07 | Bugfix (full support) | 2029-10 |
| 3.14 | 2025-10-07 | Bugfix (full support) | 2030-10 |

Moving to 3.12 buys one additional year of runway before hitting security-only mode again
(2028 vs 2027), and sets up a cleaner future path to 3.13 once numpy 2.x has been evaluated
separately.

---

## 6. Pre-deploy verification performed (before touching production)

1. **Static check:** grepped the entire codebase for `distutils` and other stdlib modules removed
   in 3.12 — zero matches.
2. **Dependency wheel check:** confirmed every pinned package version has a `cp312` wheel via
   direct PyPI JSON API queries (not just "PyPI lists a compatible version somewhere").
3. **Local build test — all 11 services:** built every service's Docker image locally against
   `python:3.12-slim`, confirmed clean `pip install` with zero version-resolution conflicts.
4. **Local runtime smoke tests:**
   - `technical-analysis`: real RSI computation on synthetic data — correct output.
   - `ml-prediction`: real XGBoost fit/predict cycle, plus `TimeSeriesSplit(gap=...)` — the exact
     API used in this session's T232-ML4 embargo fix — confirmed working.
   - All 11 services: `python3 --version` confirms 3.12.13; each service's actual
     `src.main:app` FastAPI app object imports cleanly (matching the real `uvicorn src.main:app`
     production invocation, not a naive bare-script import).
5. **Local image cleanup:** all local test images removed after verification, no leftover local
   state.

---

## 7. Production deployment results

Deployed sequentially, lowest-blast-radius services first, `market-data` (scheduler + paper
trading + auth) last:

| Service | Build result | Restarts after deploy | Post-deploy check |
|---|---|---|---|
| technical-analysis | OK | 0 | `/health` → 200 (verified inside Docker network) |
| strategy-engine | OK | 0 | Clean logs, no errors |
| portfolio-optimizer | OK | 0 | Clean logs, no errors |
| ranking-engine | OK | 0 | Clean logs, no errors |
| research-engine | OK | 0 | Clean logs, no errors |
| event-intelligence | OK | 0 | Clean logs, no errors |
| api-gateway | OK | 0 | `https://lausing.com/` → 200; `/health/deep` → 9/9 services ok |
| signal-engine | OK | 0 | Clean logs, no errors |
| decision-engine | OK | 0 | Confirmed the T232-DE1 fix (committed earlier this session) survived the rebuild |
| ml-prediction | OK | 0 | Loaded all 269 existing trained models correctly (cross-version joblib compatibility confirmed); triggered a live end-to-end training run (MSFT/SWING) — completed successfully including this session's ML2/ML3/ML4 fixes |
| market-data | OK | 0 | All 20 scheduler jobs registered; triggered a live `paper-portfolio/run-step` — all portfolios processed successfully |

**Final verification:** `GET /health/deep` from the gateway reports all 9 checked services
healthy; `python3 --version` confirms `3.12.13` on all 11 containers; **zero restarts across the
entire fleet**; frontend pages (`/`, `/paper-portfolio`, `/improvements`, `/rankings`) all return
200 against the fully-upgraded backend.

---

## 8. What's deliberately deferred

- **Python 3.13** — blocked on a separate, deliberate numpy 1.26→2.x migration and regression
  test across every numpy/pandas-touching service (ML training, technical-analysis,
  ranking-engine, market-data, and more). Not scheduled; revisit once numpy 2.x has been
  evaluated on its own.
- **`datetime.utcnow()` cleanup** — deprecated (not removed) in 3.12, still functions correctly.
  Opportunistic cleanup, not a blocker for anything.
- **Free-threading / JIT adoption** — both are immature/opt-in even in 3.13, irrelevant to
  today's decision. Revisit only once these are stable, default, and actually measured to help
  a workload like this one (unlikely for the ML training path regardless, given it's
  compiled-code-bound).
