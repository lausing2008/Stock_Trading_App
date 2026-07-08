# The `shared/` Layer — Reference

Everything under `shared/` is copied into every service's Docker image and imported as top-level
`common`/`db` packages — it is the single source of truth for the database schema and the handful
of cross-cutting utilities (auth, logging, Redis, the FastAPI app factory) every one of the 11
backend services relies on. This doc catalogs what's there, how it's consumed, and one real
operational gotcha (§5) worth knowing before touching migrations.

```
shared/
├── agent.md            engineering-agent guidance for touching shared code
├── skill.md             domain-knowledge/coding-standards reference (see §5 for a caveat)
├── pyproject.toml       packaging metadata (stockai-shared v0.1.0)
├── common/
│   ├── config.py        Settings (env vars) — get_settings()
│   ├── jwt_auth.py       get_current_username() — JWT verification dependency
│   ├── logging.py        configure_logging() / get_logger() — structlog setup
│   ├── redis_client.py   get_redis() — pooled Redis client factory
│   └── service.py        create_app() — shared FastAPI app factory
└── db/
    ├── models.py         39 SQLAlchemy table models (+ 7 enums)
    ├── session.py        engine/session setup + the REAL migration mechanism (see §5)
    ├── __init__.py        public export list — see §3 gap
    └── migrations/        Alembic scaffold — NOT the live migration path, see §5
```

---

## 1. How every service actually gets `shared/`

Confirmed identical across all 11 service Dockerfiles (api-gateway, market-data, signal-engine,
ranking-engine, decision-engine, ml-prediction, portfolio-optimizer, research-engine,
strategy-engine, technical-analysis, event-intelligence) — no exceptions:

```dockerfile
COPY shared /app/shared
ENV PYTHONPATH=/app/shared:/app
COPY services/<service-name> /app
```

`PYTHONPATH=/app/shared:/app` makes `shared/common/` and `shared/db/` importable as bare top-level
packages — `from common.config import get_settings`, `from db import Signal, SessionLocal` — not
`from shared.db import ...`. This is also why editing a shared file requires `docker cp` to
`/app/shared/db/...` inside a running container, NOT `/app/src/db/...` — a distinction CLAUDE.md
calls out repeatedly because getting it wrong silently no-ops the fix.

---

## 2. `shared/db/models.py` — the 39 tables, grouped by domain

| Domain | Tables |
|---|---|
| Auth | `User` |
| Market data | `Stock`, `Price`, `Indicator`, `Fundamental`, `FundamentalsSnapshot` |
| Signals / ML / rankings | `Signal`, `SignalOutcome`, `Ranking` |
| Strategy / backtest / portfolio construction | `Strategy`, `Backtest`, `Portfolio`, `PortfolioHolding` |
| Watchlists | `Watchlist`, `WatchlistItem` |
| Alerts / notifications | `PriceAlert`, `SignalAlert`, `AppNotification` |
| User trading records | `UserPosition`, `PositionTrade`, `UserCash`, `TradeJournal`, `TradePlan` |
| Broker integration | `BrokerConnection` |
| Paper trading engine | `PaperPortfolio`, `PaperTrade`, `PaperEquityCurve` |
| Event Intelligence Platform | `EconomicEvent`, `EarningsEvent`, `InsiderTransaction`, `CongressTrade`, `InstitutionalHolding`, `InstitutionalTransaction`, `PoliticalEvent`, `StockConnectFlow`, `CatalystScore`, `SecFiling`, `HkConnectFlow` |

Plus 7 `str, enum.Enum` classes used as column types: `Market` (US/HK), `Exchange`, `TimeFrame`,
`UserRole`, `SignalType` (BUY/SELL/HOLD/WAIT), `SignalHorizon` (SHORT/SWING/LONG/GROWTH),
`AlertCondition`.

For the two tables the self-improvement loop is built on (`Signal`, `SignalOutcome`, `PaperTrade`)
and their exact columns, see `docs/SELF_IMPROVEMENT_LOOP.md` §1 — not repeated here.

**Known irregularities worth knowing before touching these tables:**

- **`HkConnectFlow` vs. `StockConnectFlow`** cover overlapping HK southbound-flow data, but
  `HkConnectFlow` is deliberately keyed by raw `symbol` string with no FK to `stocks` — its own
  docstring explains this avoids a per-symbol stock lookup during ingest. Not a bug, but a
  surprising inconsistency if you're looking for "the" HK flow table.
- **`WatchlistItem.user_id` and `.watchlist_id` are both nullable** — a legacy shape from before
  per-user/per-list watchlists existed. `session.py`'s inline migrations backfill orphaned rows into
  a default "My Watchlist" per user; new code should never rely on either being null.
- **`Ranking.value`/`.growth` are nullable** — a deliberate 2026-07-02 fix (`T232-RANKSTALE`) after a
  real 10-day production incident: `compute_kscore` legitimately returns `None` for stocks lacking
  fundamentals, but the columns were `NOT NULL`, so every batch INSERT touching such a stock failed
  atomically with zero logging at the time. See §5 for why this fix's Alembic migration doesn't
  match what's actually live.
- **Cash-ledger columns migrated float→`NUMERIC(20,6)`** (`user_cash.amount`, `paper_trades.entry_price`
  etc., ~15 columns) via a conditional inline migration — exact-arithmetic storage for money, not
  the floats the original `Float` column type implies if you're reading `models.py` in isolation
  (check the current column type in the DB, not just the ORM declaration, if precision matters).

---

## 3. `shared/db/__init__.py` — the public export list has a gap

`from db import X` only works for 39 of the names people actually need. **`StockConnectFlow`,
`SecFiling`, `HkConnectFlow`, and `FundamentalsSnapshot` are defined in `models.py` but never
re-exported from `__init__.py`.** Any code needing them must import from the submodule directly:

```python
from db.models import HkConnectFlow, SecFiling, StockConnectFlow, FundamentalsSnapshot
```

This isn't broken — just an inconsistency between "the documented import pattern" and reality. If
you add a new model and want the clean `from db import X` form to work, add it to `__init__.py`'s
`__all__` explicitly; it does not happen automatically.

---

## 4. `shared/common/` — one file each

| File | Provides |
|---|---|
| `config.py` | `Settings` (pydantic `BaseSettings`) + `@lru_cache`'d `get_settings()`. Reads `.env`/env vars: `database_url`, `redis_url`, `jwt_secret`, all 10 internal service base URLs, provider API keys, email config. **Hard safety gate**: raises `RuntimeError` at startup if `env != "development"` and `jwt_secret` is still the placeholder default. |
| `jwt_auth.py` | `get_current_username()` — the FastAPI auth dependency used by every protected endpoint across every service. Decodes HS256, checks a Redis blacklist (`auth:blacklist:{jti}`) with an in-memory fallback cache, fails open on Redis-unreachable for *unknown* JTIs but fails closed for already-cached-revoked ones. **`jose` is imported at module level on purpose** — a comment in the file explains this is specifically so a missing `python-jose` package crashes the service at startup instead of silently turning every authenticated endpoint into a 401 (the exact recurring bug documented multiple times in the root `CLAUDE.md`). |
| `logging.py` | `configure_logging()` / `get_logger()` — structlog setup with `cache_logger_on_first_use=False` (deliberate — see `CLAUDE.md`'s BUG-9 entry on the hk_connect_flows logging crash this setting fixes). |
| `redis_client.py` | `get_redis()` — one pooled `redis.Redis` client per process (`max_connections=20`, 2s connect timeout). Use this instead of instantiating your own client. |
| `service.py` | `create_app(name, routers, on_startup, version)` — the shared FastAPI factory every service's `main.py` calls. Wires correlation-ID middleware, CORS, `GET /health`, structlog lifecycle logging. |

`common/__init__.py` re-exports `Settings`/`get_settings`, `configure_logging`/`get_logger`,
`get_redis` — but **not** anything from `jwt_auth.py` or `service.py`. Those two are always imported
by full path (`from common.jwt_auth import get_current_username`, `from common.service import
create_app`), matching how every service's code actually does it.

`python-jose` is notably **absent** from `shared/pyproject.toml`'s dependency list even though
`jwt_auth.py` imports it — each service declares it independently in its own `requirements.txt`.
This split-declaration is the direct cause of the recurring "jose missing from container" class of
bug (signal-engine, ml-prediction, ranking-engine all hit this independently, per `CLAUDE.md`) — a
service can build successfully and start cleanly while missing the one package its auth layer
needs, with the failure only surfacing as a wave of 401s once real traffic hits it.

---

## 5. Migrations — Alembic exists but is not the live path

`shared/db/migrations/` is a fully-wired Alembic setup (`env.py`, `script.py.mako`, 4 version files:
`001_baseline`, `002_signals_dedup_index`, `003_event_intelligence_tables`,
`004_rankings_nullable_value_growth`). Both `shared/agent.md` and `shared/skill.md` describe adding
a new Alembic migration and running `alembic upgrade head` as the standard workflow.

**Verified against production directly — this is not what actually happens:**

```
$ psql -c "SELECT * FROM alembic_version;"
 version_num
-------------------------------
 003_event_intelligence_tables
```

Production's Alembic version is stuck at `003`. The REAL, currently-active migration mechanism is
`_run_migrations()` in `shared/db/session.py` — a long, ordered list of idempotent inline
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` statements, run every time
any service calls `init_db()` at startup (in practice, only `market-data`'s `main.py` and
`seed_universe.py` call it — market-data owns schema initialization for the whole system). No
docker-compose service, startup script, or CI step anywhere invokes `alembic upgrade head`.

**Confirmed via git history exactly how migration `004` diverged**: commit `a577a70` (2026-07-02)
fixed the real production incident behind `Ranking.value`/`.growth` being nullable — but per its own
commit message, the fix was "Applied directly to production" (a manual `ALTER TABLE`, not
`alembic upgrade head`) with the Alembic version file `004_rankings_nullable_value_growth.py` added
alongside purely as a record of the change, never actually executed by Alembic. Production's schema
has therefore already moved past what `alembic_version` claims — running `alembic upgrade head`
today would attempt to re-apply changes (`002`, `003`, `004`) some of which may already be present
via the inline path, with unpredictable results depending on whether each statement is idempotent.

**Practical implications:**
- **To add a new table/column today**: add it to `models.py` AND add the matching idempotent SQL to
  `_run_migrations()` in `session.py` — this is the path that will actually run. Adding only an
  Alembic version file (as `004` did) documents the intent but does not apply the change; someone
  still has to run the SQL by hand against production, as happened here.
- **Do not run `alembic upgrade head` against production** without first reconciling
  `alembic_version` against what's actually live — it is currently NOT a safe, idempotent
  representation of production's real schema history.
- This mismatch between documented practice (`agent.md`/`skill.md`) and actual practice
  (`_run_migrations()`) is itself worth fixing at some point — either by adopting Alembic for real
  (backfilling `alembic_version` to match reality first) or by updating `agent.md`/`skill.md` to
  stop describing a workflow nobody follows. Not fixed as part of writing this doc — flagged here so
  it isn't lost, and can be picked up as a tracker item if worth the effort.

---

## 6. Also documented, not repeated here

`shared/agent.md` and `shared/skill.md` cover engineering practices when touching shared code (grep
all of `services/` before renaming a model field; test the full auth flow before changing
`jwt_auth.py`; the `docker cp` deployment loop across all 10 model-consuming services). Read those
directly for the "how to change this safely" guidance — this document is a "what exists and how it
fits together" reference, not a replacement for them.
