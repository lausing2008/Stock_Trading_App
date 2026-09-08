# Deep Platform Audit — 2026-08-16

Comprehensive audit of all 12 microservices in the StockAI platform.

---

## Executive Summary

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Known Bugs (BUG- comments) | 3 | 12 | 18 | 8 |
| Silent Exception Handling | 0 | 8 | 22 | 5 |
| Code Quality Issues | 2 | 6 | 15 | 10 |
| Performance Concerns | 1 | 4 | 8 | 5 |
| Security Considerations | 0 | 2 | 4 | 3 |
| Architecture Debt | 2 | 5 | 8 | 4 |

**Services Audited**: api-gateway, decision-engine, event-intelligence, market-data, ml-prediction, news-intelligence, portfolio-optimizer, ranking-engine, research-engine, signal-engine, strategy-engine, technical-analysis

---

## Part 1: Known Bugs (from BUG- comments)

### Critical (3)

| ID | Bug | Location | Status |
|----|-----|----------|--------|
| BUG-DELISTED-GENERATION-BLIND | Signals generated for delisted stocks | signal-engine/routes.py:187, technical-analysis/routes.py:115 | Documented, needs fix |
| BUG-REASONSJSON-NAN | NaN/Inf in JSON causes parse errors | signal-engine/routes.py:459 | Fixed via `_json_safe()` |
| BUG-SIGNALS-UNBOUNDED-GROWTH | Signals table grows unbounded | scheduler.py:7779 | Documented, needs cleanup job |

### High (12)

| ID | Bug | Location |
|----|-----|----------|
| BUG-PAPERPOS-DELISTED-FROZEN | Paper positions frozen on delisted stocks | scheduler.py:4995 |
| BUG-ALERTS-DELISTED-SILENT | Alerts silently fail for delisted stocks | scheduler.py:5692 |
| BUG-VOLANOM-STALEMARKET | Volume anomaly uses stale market data | scheduler.py:2470 |
| BUG-EARNINGS-IMPACT-UNSCOPED | Earnings alerts sent to wrong users | scheduler.py:1577 (fixed) |
| BUG-EARNINGS-REMINDER-SKIPS-DAY-OF | Earnings reminder skips day-of | scheduler.py:5452 |
| BUG-LOCALDEV-ALERTS-UNGATED | Local dev sends real emails | scheduler.py:133 |
| BUG-MORNINGDIGEST-SENDLOOP | Morning digest send loop issue | scheduler.py:8426 |
| BUG-TRADEPERF-ALLINSEQUENTIAL | Trade perf assumes sequential | outcomes.py:680 (fixed) |
| BUG-NEWSCLASSIFY-REPEATCOST | News re-classified repeatedly | news-intelligence/storage.py:97 |
| BUG233-RETROEV-SIGNMIX | Return sign mix in retro eval | outcomes.py:68 (fixed) |
| BUG233-TAWEIGHTS-NOVALIDATION | TA weights no validation split | calibration.py:553 (fixed) |
| BUG-PROXYGAP-CONDITIONALORDERS | Conditional orders not proxied | api-gateway/proxy.py:83 |

### Medium (18)

| ID | Bug | Location |
|----|-----|----------|
| BUG-6 | SQLAlchemy text() param binding | signal-engine/routes.py:436 |
| BUG-8 | Stale market refresh handling | scheduler.py:417 |
| EI-BUG | Period date wrong in sync | scheduler.py:8921 |
| Various AUD- prefixed issues | Multiple locations | See codebase |

---

## Part 2: Silent Exception Handling

**Found 35 instances of `except Exception:` without logging**

### High Risk (ml-prediction service)

| File | Line | Context |
|------|------|---------|
| trainer.py | 213, 408, 456, 475, 484, 813, 862, 873, 886, 897, 962, 994, 1054, 1061, 1132, 1247, 1255, 1284, 1304 | Training failures silently swallowed |
| tuner.py | 158, 166, 174, 186, 200 | Tuning failures silently swallowed |
| meta_trainer.py | 204, 235, 384, 535 | Meta training failures |
| builder.py | 194 | Feature building failures |

**Recommendation**: Add structured logging to all exception handlers:
```python
except Exception as exc:
    log.error("operation.failed", error=str(exc), symbol=symbol)
    # Then continue or re-raise as appropriate
```

---

## Part 3: Code Quality Issues

### God Files (>2000 lines)

| File | Lines | Recommendation |
|------|-------|----------------|
| scheduler.py | 10,131 | Split into: alerts.py, jobs.py, data_sync.py, email_jobs.py |
| paper_trading_engine.py | 5,939 | Split into: entry.py, exit.py, monitoring.py, sizing.py |
| signals.py | 2,921 | Split into: ta_signals.py, ml_signals.py, fusion.py |

### Hardcoded Values

Found multiple hardcoded values that should be configurable:

```python
# scheduler.py
_SQUEEZE_MIN_SHORT_FLOAT = 15.0  # Should be in config
_SQUEEZE_MIN_INTRADAY_MOVE_PCT = 3.0  # Should be in config

# paper_trading_engine.py
"max_portfolio_drawdown_pct": 0.20  # Already configurable ✓
"trail_atr_mult": 2.0  # Already configurable ✓
```

### Missing Type Hints

Several functions lack complete type hints, especially in:
- scheduler.py (many internal functions)
- paper_trading_engine.py (helper functions)

---

## Part 4: Performance Concerns

### Unbounded Queries

Found 341 instances of `.all()` or `.first()` queries. Most are properly bounded, but review:

| Location | Query | Risk |
|----------|-------|------|
| scheduler.py | Various watchlist queries | Medium - could grow large |
| paper_trading_engine.py | Open trades query | Low - naturally bounded |

### Sleep Calls (Rate Limiting)

Found 20 sleep calls for rate limiting - all appear intentional:

| Location | Sleep | Purpose |
|----------|-------|---------|
| scheduler.py:440 | Exponential backoff | HTTP retry |
| scheduler.py:2063 | 2.0s | Options chain rate limit |
| scheduler.py:3978 | 1.0s | Options flow rate limit |
| scheduler.py:6222 | 0.33s | yfinance rate limit |
| hk_connect.py:202 | 0.2s | Eastmoney rate limit |
| edgar_8k.py:205 | 0.15s | SEC rate limit |

### In-Memory Caches Without Bounds

| Location | Cache | Risk |
|----------|-------|------|
| research-engine/routes.py:44 | `_cache` dict | Medium - no eviction |
| api-gateway/proxy.py:102 | `_BLACKLIST_MEM` | Low - TTL cleanup exists |
| paper_trading_engine.py:480 | `_entry_weights_cache` | Low - single value |

**Recommendation**: Add LRU cache or size limits:
```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_cached_report(symbol: str) -> dict:
    ...
```

---

## Part 5: Security Considerations

### API Key Handling

| Location | Finding | Status |
|----------|---------|--------|
| research-engine/ai_proxy.py | Keys stored in Redis | OK - not in code |
| research-engine/routes.py | Keys fetched from Redis | OK |
| ml-prediction/routes.py:203 | JWT secret from settings | OK |

### No Hardcoded Secrets Found ✓

All secrets properly loaded from environment/Redis.

### Potential SSRF Vectors

| Location | Risk | Mitigation |
|----------|------|------------|
| api-gateway proxy | Low | Whitelist of internal services |
| webhook URLs | Medium | `_validate_webhook_url` SSRF guard exists |

---

## Part 6: Architecture Debt

### Service Dependencies

```
┌─────────────────┐
│   api-gateway   │ ← Single point of entry
└────────┬────────┘
         │
    ┌────┴────┬────────────┬─────────────┐
    ▼         ▼            ▼             ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
│market- │ │signal- │ │decision- │ │research- │
│data    │ │engine  │ │engine    │ │engine    │
└────┬───┘ └────┬───┘ └────┬─────┘ └──────────┘
     │          │          │
     └────┬─────┴──────────┘
          ▼
    ┌──────────┐
    │ Postgres │
    └──────────┘
```

### Circular Dependencies

| From | To | Via |
|------|----|-----|
| signal-engine | market-data | HTTP for regime |
| market-data | signal-engine | HTTP for signals |
| decision-engine | market-data | HTTP for style params |
| decision-engine | signal-engine | HTTP for signals |
| decision-engine | research-engine | HTTP for research |

**Recommendation**: Consider event-driven architecture for decoupling.

### Missing Health Checks

All services have `/health` endpoints ✓

### Missing Circuit Breakers

No circuit breaker pattern found for inter-service calls.

**Recommendation**: Add circuit breaker for external calls:
```python
from circuitbreaker import circuit

@circuit(failure_threshold=5, recovery_timeout=30)
def call_external_service():
    ...
```

---

## Part 7: Service-by-Service Findings

### 1. api-gateway

| Finding | Severity | Location |
|---------|----------|----------|
| BUG-PROXYGAP-CONDITIONALORDERS | Medium | proxy.py:83 |
| In-memory blacklist fallback | Low | proxy.py:102 |

### 2. decision-engine

| Finding | Severity | Location |
|---------|----------|----------|
| Blocking httpx.get in async context | Medium | regime.py:27 (documented, has async version) |
| Hardcoded fallback params | Low | aggregator.py:68 |
| Short timeout (3s) for signal fetch | Low | aggregator.py:124 |

### 3. event-intelligence

| Finding | Severity | Location |
|---------|----------|----------|
| Rate limiting via sleep | OK | economic.py, edgar_8k.py |
| No retry on FRED failures | Low | economic.py |

### 4. market-data (scheduler.py)

| Finding | Severity | Location |
|---------|----------|----------|
| God file (10,131 lines) | High | scheduler.py |
| 40+ BUG comments | Various | Throughout |
| Complex job dependencies | Medium | Job registration |

### 5. ml-prediction

| Finding | Severity | Location |
|---------|----------|----------|
| 25+ silent exception handlers | High | trainer.py, tuner.py |
| No model versioning cleanup | Medium | meta_trainer.py |

### 6. news-intelligence

| Finding | Severity | Location |
|---------|----------|----------|
| BUG-NEWSCLASSIFY-REPEATCOST | Medium | storage.py:97 |
| Alpaca reconnect backoff | OK | alpaca_source.py |

### 7. portfolio-optimizer

| Finding | Severity | Location |
|---------|----------|----------|
| No significant issues found | - | - |

### 8. ranking-engine

| Finding | Severity | Location |
|---------|----------|----------|
| No significant issues found | - | - |

### 9. research-engine

| Finding | Severity | Location |
|---------|----------|----------|
| Unbounded in-memory cache | Medium | routes.py:44 |
| Long AI timeout (120s) | Low | ai_proxy.py:116 |
| Concurrent request dedup | OK | routes.py:790 |

### 10. signal-engine

| Finding | Severity | Location |
|---------|----------|----------|
| BUG-DELISTED-GENERATION-BLIND | Critical | routes.py:187 |
| BUG-REASONSJSON-NAN | Fixed | routes.py:459 |
| Multiple calibration bugs fixed | OK | calibration.py |

### 11. strategy-engine

| Finding | Severity | Location |
|---------|----------|----------|
| No significant issues found | - | - |

### 12. technical-analysis

| Finding | Severity | Location |
|---------|----------|----------|
| BUG-DELISTED-GENERATION-BLIND sibling | Critical | routes.py:115 |

---

## Part 8: Recommendations

### Immediate (P0)

1. **Fix BUG-DELISTED-GENERATION-BLIND** across all services
   - Add `Stock.delisted.is_(False)` filter to all bulk queries
   - Estimated effort: 2 hours

2. **Add logging to silent exception handlers** in ml-prediction
   - Replace `except Exception:` with proper logging
   - Estimated effort: 4 hours

3. **Split scheduler.py** into smaller modules
   - alerts.py, jobs.py, data_sync.py, email_jobs.py
   - Estimated effort: 8 hours

### Short-term (P1)

4. **Add circuit breakers** for inter-service HTTP calls
   - Use `circuitbreaker` library
   - Estimated effort: 4 hours

5. **Bound in-memory caches** with LRU or TTL
   - research-engine `_cache`
   - Estimated effort: 2 hours

6. **Add signals table cleanup job**
   - Delete signals older than 90 days
   - Estimated effort: 2 hours

### Medium-term (P2)

7. **Refactor paper_trading_engine.py** (5,939 lines)
   - Split into focused modules
   - Estimated effort: 16 hours

8. **Add comprehensive type hints**
   - Focus on public APIs first
   - Estimated effort: 8 hours

9. **Consider event-driven architecture**
   - Reduce circular HTTP dependencies
   - Estimated effort: 40+ hours

---

## Part 9: Test Coverage Gaps

### Services with Tests ✓
- ml-prediction (11 test files)
- signal-engine (12 test files)
- decision-engine (6 test files)
- news-intelligence (1 test file)
- technical-analysis (1 test file)
- strategy-engine (4 test files)

### Services Missing Tests
- api-gateway (0 test files found)
- event-intelligence (0 test files found)
- portfolio-optimizer (0 test files found)
- ranking-engine (0 test files found)
- research-engine (1 test file - conftest only)

**Recommendation**: Add integration tests for untested services.

---

## Part 10: Monitoring Gaps

### Existing Monitoring ✓
- `/health` endpoints on all services
- structlog JSON logging
- Job status recording (`_record_job_status`)

### Missing Monitoring
- No distributed tracing (OpenTelemetry)
- No metrics export (Prometheus)
- No alerting on job failures
- No SLO/SLA tracking

**Recommendation**: Add OpenTelemetry for distributed tracing.

---

## Appendix: File Sizes

| File | Lines | Status |
|------|-------|--------|
| scheduler.py | 10,131 | ⚠️ God file |
| paper_trading_engine.py | 5,939 | ⚠️ God file |
| signals.py | 2,921 | ⚠️ Large |
| routes.py (signal-engine) | 1,261 | OK |
| outcomes.py | ~2,500 | ⚠️ Large |
| calibration.py | ~2,200 | ⚠️ Large |

---

*Generated: 2026-08-16*
*Auditor: Amazon Q*
