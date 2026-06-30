# Strategy Engine — Domain Knowledge & Coding Standards

User-defined strategy rules via DSL, with backtesting against historical price data.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Strategy CRUD + backtest endpoints | `api/routes.py` (~282 lines) |
| Backtest execution engine | `backtest/engine.py` (~129 lines) |
| DSL rule evaluation | `dsl/evaluator.py` (~122 lines) |

---

## DSL (Domain-Specific Language)

Users define strategies as rule sets using a DSL. Example rule:
```json
{
  "conditions": [
    {"indicator": "rsi_14", "operator": "<", "value": 35},
    {"indicator": "macd_signal", "operator": ">", "value": 0},
    {"indicator": "volume_z", "operator": ">", "value": 0.5}
  ],
  "entry": "BUY",
  "stop_pct": 0.05,
  "target_pct": 0.15
}
```

`dsl/evaluator.py` compiles these rules into Python predicates and applies them over
historical price + indicator data.

---

## Backtest Engine (`backtest/engine.py`)

The backtest runs rules over historical data and simulates trades:
- Entry: when all conditions are met
- Exit: when stop or target is hit, or conditions reverse
- Returns: trade list with P&L, win rate, Sharpe ratio, max drawdown

**Limitations (current):**
- No slippage modeling (fills at close price)
- No survivorship bias correction (delisted stocks not in universe)
- Walk-forward backtest not yet implemented (deferred — 2+ weeks of work per improvement tracker)
- Commission defaults to 0.0 (user's broker is commission-free)

---

## Database Migration Note

From `main.py`:
```python
# Connection health check only — migrations are owned by market-data to avoid
# concurrent ACCESS EXCLUSIVE lock contention on startup (AUD19-DB2).
```

**Do not add schema migrations here.** All Alembic migrations run from market-data service.
The strategy-engine only runs a SELECT 1 health check on startup.

---

## Endpoint Reference

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /strategies` | Yes | Create a new strategy (user-scoped) |
| `GET /strategies` | Yes | List user's strategies |
| `GET /strategies/{id}` | Yes | Get strategy details |
| `DELETE /strategies/{id}` | Yes | Delete a strategy |
| `POST /backtest` | Yes | Run backtest for a strategy |
| `GET /backtest/{id}/results` | Yes | Fetch backtest results |

---

## Strategy Scoping

Strategies are user-scoped — each user can only see and backtest their own strategies.
The `user_id` is extracted from the JWT and used as a filter on all queries.
Do not expose other users' strategies regardless of admin status.
