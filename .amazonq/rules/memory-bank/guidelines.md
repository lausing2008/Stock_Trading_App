# StockAI — Development Guidelines

## Code Quality Standards

### Python Backend

**Naming Conventions**
- Functions: `snake_case` (e.g., `_fetch_live_bulk`, `check_hard_rejects`)
- Private functions: prefix with `_` (e.g., `_atr`, `_score_technical`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `_LIVE_TTL`, `_SECTOR_BENCHMARKS`)
- Classes: `PascalCase` (e.g., `StockOut`, `FundamentalsOut`)

**Type Hints**
- Always use type hints for function signatures
- Use `| None` for optional types (Python 3.10+ syntax)
- Use Pydantic models for API request/response schemas

```python
def _fetch_live_one(symbol: str, currency: str) -> dict | None:
    ...

class StockOut(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
```

**Docstrings**
- Use triple-quoted docstrings for public functions
- Include context about why the function exists, not just what it does
- Reference ticket IDs for traceability (e.g., `T232-DL-DUALSCORER-DEBT`)

```python
def _atr(prices: list[dict], period: int = 14) -> float | None:
    """Wilder's ATR — seeded with SMA of first `period` bars, then EWM (alpha=1/period).

    Matches every standard charting platform (TradingView, Bloomberg, ThinkOrSwim).
    """
```

### TypeScript Frontend

**Component Structure**
- Use functional components with hooks
- Prefer `'use client'` directive for client-side pages
- Use SWR for data fetching with caching

```typescript
'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
```

**Type Definitions**
- Define explicit types for component props and state
- Use `type` for simple types, `interface` for object shapes

```typescript
type Severity = 'critical' | 'high' | 'medium' | 'low' | 'feature';
type Status = 'todo' | 'in-progress' | 'done';

interface Item {
  id: string;
  tier: number;
  severity: Severity;
  title: string;
}
```

## Architectural Patterns

### API Route Structure (FastAPI)

```python
router = APIRouter(prefix="/stocks", tags=["stocks"])

@router.get("/{symbol}/fundamentals", response_model=FundamentalsOut)
def get_fundamentals(symbol: str, refresh: bool = False, db: Session = Depends(get_session)):
    ...
```

### Redis Caching Pattern

```python
_CACHE_KEY = "stockai:feature_name"
_CACHE_TTL = 3600  # 1 hour

def get_data():
    try:
        cached = _get_redis().get(_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    
    # Compute fresh data
    result = compute_expensive_operation()
    
    try:
        _get_redis().setex(_CACHE_KEY, _CACHE_TTL, json.dumps(result))
    except Exception:
        pass
    
    return result
```

### Database Query Pattern (SQLAlchemy)

```python
from sqlalchemy import select
from db import Stock, get_session

def list_stocks(session: Session = Depends(get_session)):
    stmt = select(Stock).where(Stock.active.is_(True))
    return list(session.execute(stmt).scalars())
```

### Upsert Pattern (PostgreSQL)

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = (
    pg_insert(Model)
    .values(field1=value1, field2=value2)
    .on_conflict_do_update(
        constraint="unique_constraint_name",
        set_=dict(field2=value2),
    )
)
session.execute(stmt)
session.commit()
```

## Testing Patterns

### Test File Structure

```python
"""Tests for check_hard_rejects()'s gate ordering and boundary math."""
import pytest
from unittest.mock import MagicMock

# Stub external dependencies
sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())

from src.api.core import hard_rejects as hr

@pytest.fixture(autouse=True)
def _frozen_market_hours(monkeypatch):
    """Freeze time for consistent test results."""
    monkeypatch.setattr(hr, "datetime", _FrozenDateTime)

def _base_kwargs(**overrides):
    """Base test data — individual tests override specific fields."""
    kwargs = dict(signal_direction="BUY", confidence=70.0, ...)
    kwargs.update(overrides)
    return kwargs

def test_all_gates_clear_returns_none():
    assert hr.check_hard_rejects(**_base_kwargs()) is None
```

### Test Naming Convention
- `test_<feature>_<scenario>_<expected_outcome>`
- Example: `test_bear_regime_blocks_all_entries`

### Boundary Testing
- Test exact boundaries (at threshold, just above, just below)
- Document floating-point precision considerations

```python
def test_confidence_just_above_hard_floor_passes():
    """hard_floor = min_confidence * 0.90 = 62 * 0.90 = 55.8"""
    result = hr.check_hard_rejects(**_base_kwargs(confidence=55.81, cfg={"min_confidence": 62.0}))
    assert result is None
```

## Error Handling

### Fail-Open Pattern
External service failures should not block core functionality:

```python
def get_data_with_fallback():
    try:
        return fetch_from_external_api()
    except Exception as exc:
        log.warning("external_api.failed", error=str(exc))
        return fallback_value_or_cached_data()
```

### Structured Logging

```python
from common.logging import get_logger
log = get_logger("module_name")

log.info("operation.success", symbol=symbol, count=len(results))
log.warning("operation.partial_failure", symbol=symbol, error=str(exc))
```

## Common Idioms

### Safe Value Extraction

```python
def _safe(info: dict, key: str):
    v = info.get(key)
    if v in (None, "N/A", "None", "", "Infinity", float("inf"), float("-inf")):
        return None
    return v
```

### Percentage Conversion

```python
def pct(v):
    if v is None:
        return None
    return round(v * 100, 1) if abs(v) <= 1 else round(v, 1)
```

### Last Non-None Value

```python
def _last(arr: list, default=None):
    for v in reversed(arr):
        if v is not None:
            return v
    return default
```

## Bug Fix Documentation

Reference ticket IDs in comments when fixing bugs:

```python
# BUG-DELISTED-GENERATION-BLIND: filter out delisted stocks
stmt = stmt.where(Stock.delisted.is_(False))

# T237-INST-TXN-NEVER-WRITTEN: capture previous period's holdings
previous_holdings = session.execute(...)
```

## Performance Considerations

- Use bulk operations over N+1 queries
- Cache expensive computations in Redis with appropriate TTLs
- Use `ThreadPoolExecutor` for parallel I/O operations
- Limit yfinance API calls to avoid rate limiting

```python
with ThreadPoolExecutor(max_workers=4) as pool:
    futures = {pool.submit(fetch_one, s): s for s in symbols}
    for fut in as_completed(futures):
        result = fut.result()
```
