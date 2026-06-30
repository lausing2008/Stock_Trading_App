# Shared Module — Domain Knowledge & Coding Standards

Common utilities, ORM models, auth, config, and DB session management used by all services.
Changes here affect every service simultaneously.

---

## Module Layout

```
shared/
├── db/
│   ├── models.py       — SQLAlchemy ORM models (~891 lines)
│   ├── session.py      — DB session management (~368 lines)
│   └── __init__.py     — SessionLocal + init_db exports (~94 lines)
└── common/
    ├── config.py       — Settings (pydantic BaseSettings, reads from env) (~91 lines)
    ├── service.py      — FastAPI app factory: create_app() (~76 lines)
    ├── jwt_auth.py     — JWT verify + get_current_username dependency (~59 lines)
    ├── redis_client.py — Redis connection helper (~36 lines)
    └── logging.py      — Structured logger (structlog) setup (~31 lines)
```

---

## Key ORM Models (`db/models.py`)

### Core trading models
| Model | Key fields | Notes |
|---|---|---|
| `Stock` | `id`, `symbol`, `name`, `market`, `sector` | All stocks in universe; HK format = `NNNN.HK` |
| `Price` | `stock_id`, `ts`, `open`, `high`, `low`, `close`, `volume`, `timeframe` | Daily + intraday |
| `Signal` | `stock_id`, `style`, `horizon`, `signal`, `confidence`, `reasons`, `ts` | Upsert by (stock_id, style, horizon) |
| `PaperPortfolio` | `id`, `name`, `style`, `config`, `initial_capital`, `is_active` | config = JSON dict |
| `PaperTrade` | `id`, `portfolio_id`, `stock_id`, `entry_price`, `shares`, `pnl`, `exit_reason` | Use `.pnl` not `.realized_pnl` |
| `Strategy` | `id`, `user_id`, `name`, `rules` | User-scoped |
| `SignalAlert` | `stock_id`, `style`, `horizon`, `last_sent_at`, `last_signal` | Alert dedup |
| `signal_outcomes` | Links signals to paper trade outcomes | Planned for T206 ML feedback |

### Auth models
| Model | Key fields |
|---|---|
| `User` | `id`, `username`, `hashed_password`, `role`, `email` |
| `BrokerConnection` | `user_id`, `broker_type`, `credentials_encrypted` |

---

## JWT Auth (`common/jwt_auth.py`)

```python
from jose import JWTError, jwt  # python-jose required — check if missing causes 401

def get_current_username(token: str = Depends(oauth2_scheme)) -> str:
    # Verify signature with jwt_secret
    # Check Redis blacklist: auth:blacklist:{jti}
    # Return username or raise HTTP 401
```

**The import `from jose import JWTError, jwt` happens at call time, not import time.**
If `python-jose` is not installed in the container, the first request hits the `except Exception`
handler and returns HTTP 401. This silently breaks all authenticated endpoints.

---

## Config (`common/config.py`)

All configuration comes from environment variables (`.env` file on EC2 or Docker env).
Key settings:
```python
jwt_secret: str          # shared across all services — NEVER in code
database_url: str        # PostgreSQL connection string
redis_url: str           # Redis connection string
jwt_expire_days: int     # token TTL (default 1)
model_dir: str           # ML model storage path
```

Never hardcode these values. Always access via `get_settings().setting_name`.

---

## Database Migrations

Alembic migrations live in `shared/db/migrations/versions/`.
**All migrations must be run from the market-data service** — it owns DB initialization.
Strategy-engine and others run only `SELECT 1` on startup to avoid lock contention.

When adding a new table or column:
1. Create a new migration file in `shared/db/migrations/versions/`
2. Add the model to `db/models.py`
3. Test the migration with `alembic upgrade head` from market-data
4. Deploy shared models to ALL containers that use the model

---

## Container Deploy Paths

Shared module files must go to `/app/shared/`, not `/app/src/`:
```bash
# CORRECT
docker cp shared/db/models.py stockai-market-data-1:/app/shared/db/models.py

# WRONG — silently deploys to wrong location, old models.py stays active
docker cp shared/db/models.py stockai-market-data-1:/app/src/db/models.py
```

After deploying shared changes, restart ALL services that import the changed module.

---

## Service Factory (`common/service.py`)

```python
app = create_app("service-name", routers=[router], on_startup=startup_fn)
```

All services use this factory. It configures:
- CORS (for frontend requests)
- Structured logging middleware
- Exception handlers (500 → structured log)
- Health check at `/health`
