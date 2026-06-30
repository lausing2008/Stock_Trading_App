# Signal Engine — Domain Knowledge & Coding Standards

Computes trading signals (TA + ML fusion) and persists them to the `signals` DB table.
The data source for everything the DE, paper trading engine, and alert checker consume.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Signal computation (TA + ML fusion) | `generators/signals.py` (~1,989 lines) |
| Signal storage, query, bulk refresh | `api/routes.py` (~4,481 lines) |
| Research divergence logging | `api/routes.py` `_bulk_persist()` |

---

## Signal Computation Pipeline (`generators/signals.py`)

### Style profiles
Each style has a distinct set of thresholds, TA weights, and ML fusion ramp:

```
_STYLE_PROFILES = {
    "SHORT":  { buy_threshold: higher, ml_weight: lower, ... },
    "SWING":  { buy_threshold: medium, ml_weight: balanced, ... },
    "GROWTH": { buy_threshold: relaxed, fires_more_often: true, ... },
    "LONG":   { buy_threshold: sustained, slow_confirmation: true, ... },
}
```

GROWTH fires BUY more often than SWING by design — its thresholds are intentionally relaxed for
high-volatility momentum stocks. This is not a bug.

### Signal reasons dict
Every signal stores a `reasons` JSON dict in the DB. Key fields used downstream:
- `volume_z`: z-score of today's volume vs 20-day average (used by T200 volume gate)
- `confidence_delta`: change in confidence since last refresh (used by T202 confidence gate)
- `rsi_14`, `macd_signal`, `bb_pct`: TA indicator values
- `ml_prob`: ML model probability for this symbol+style+horizon

---

## Bulk Persist Pipeline (`routes.py` `_bulk_persist()`)

### What it does
1. Computes signals for all stocks in a market
2. Upserts to `signals` table (fixed size — no unbounded growth)
3. Checks research divergence (BUY signal + AVOID research = log warning)
4. Returns count of signals written

### Auth requirement for research divergence check
`_bulk_persist()` calls `GET /research/{symbol}/summary` which requires a JWT.
Must pass `headers={"Authorization": f"Bearer {_service_token()}"}`.
If auth header is missing → silent 401 → research divergence never logged (INT-7 bug pattern).

### Research divergence check
When a BUY signal fires but research reports AVOID/SELL: log `signal.research_divergence`.
This is intentional — the signal and research can disagree; both are data points.

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /signals/{symbol}` | No | Compute or fetch signal; `?persist=true` writes to DB |
| `POST /signals/refresh?market=HK` | Yes (JWT) | Bulk refresh all stocks in a market |
| `GET /signals/accuracy` | Yes | Per-style accuracy metrics from signal_outcomes |
| `GET /signals/{symbol}/history` | No | Historical signal values for a symbol |

**`/signals/{symbol}?persist=true`** is the endpoint that keeps US signals fresh via page visits.
It is intentionally unauthenticated — called from the stock detail page aggregator.

---

## Critical: jose Dependency

`shared/common/jwt_auth.py` does `from jose import JWTError, jwt` at call time.
If `python-jose` is missing from the container, this import fails, the generic `except Exception`
handler raises HTTP 401, and `POST /signals/refresh` silently fails — no signals are written.

**This is the #1 cause of signal staleness.** Check jose before anything else:
```bash
docker exec stockai-signal-engine-1 python3 -c 'from jose import jwt; print("OK")'
# Fix: docker exec stockai-signal-engine-1 pip install 'python-jose[cryptography]==3.3.0'
```

---

## SQLAlchemy CAST Invariant (BUG-6)

Signal writes use `text()` SQL with PostgreSQL enum casts. Always use `CAST()`:
```python
# CORRECT
text("INSERT INTO signals VALUES (:sid, CAST(:sig AS signaltype), CAST(:hor AS signalhorizon), CAST(:rsns AS jsonb))")

# BROKEN — silent bind failure
text("INSERT INTO signals VALUES (:sid, :sig::signaltype, :hor::signalhorizon, :rsns::jsonb)")
```

---

## DISTINCT ON ORDER BY Pattern (BUG-8)

When querying latest signal per stock with DISTINCT ON:
```python
# CORRECT — Stock.id must be FIRST in ORDER BY
.order_by(Stock.id, Signal.ts.desc()).distinct(Stock.id)

# BROKEN — psycopg2 error at runtime
.order_by(Signal.ts.desc()).distinct(Stock.id)
```

---

## Signal Staleness Diagnosis

```bash
# Last signal timestamp per market
docker exec stockai-market-data-1 python3 -c "
from shared.db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text(\"SELECT market, MAX(sig.ts) FROM signals sig JOIN stocks st ON sig.stock_id=st.id GROUP BY market\")).fetchall()
print(rows); s.close()"

# Signal engine refresh errors
docker logs stockai-signal-engine-1 --since 2h | grep -i '401\|error\|syntax\|invalid'

# Trigger manual refresh
docker exec stockai-market-data-1 python3 -c "
import sys, uuid; sys.path.insert(0,'/app/src'); sys.path.insert(0,'/app')
from common.config import get_settings; from datetime import datetime, timezone, timedelta
import httpx; from jose import jwt as _jwt; s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':datetime.now(timezone.utc)+timedelta(days=365)}, s.jwt_secret, algorithm='HS256')
for mkt in ['HK','US']:
    r = httpx.post(f'http://signal-engine:8005/signals/refresh?market={mkt}', headers={'Authorization':f'Bearer {tok}'}, timeout=15)
    print(mkt, r.status_code, r.text[:80])"
```
