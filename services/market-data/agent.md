# Market Data Service — Engineering Agent Behavior

How to behave when working on `services/market-data/`. This is the most complex service — the
scheduler, paper trading engine, auth, and email all live here.

---

## Mindset for This Service

Market-data is the system's orchestrator. A bug here can: stop all price ingestion, freeze paper
trading for all portfolios, silently stop signal refresh, or break auth for every user. Changes
here have the highest blast radius of any service.

**Before touching `paper_trading_engine.py`:** Read the entire `_scan_for_entries` and
`_monitor_positions` functions, not just the section you're changing. The pre-fetched state
variables (`_prefetched_open`, `_sig_ref_prices`, `_recently_stopped`) are shared across the
entire candidate loop — a change to how they're built affects every gate downstream.

**Before touching `scheduler.py`:** Understand which jobs call which services and in what order.
The scheduler is stateful — a change to job timing or the service token affects all downstream calls.

---

## Working on the Paper Trading Engine

### Gate implementation checklist
Every new gate must have all of these:
- [ ] Config key with default: `_threshold = float(cfg.get("key_name", default))`
- [ ] Fail-open on None: `_value = float(...) if data else 0.0`
- [ ] Structured log on skip: `log.info("paper.skip_<reason>", symbol=..., threshold=..., actual=...)`
- [ ] Position in pipeline: hard rejects run before DE call; post-DE checks before `_open_position`

### Adding a new exit reason
1. Add the exit_reason string to the exit block
2. Determine cooldown bucket: real loss → stop_hit (24h), break-even → breakeven_stop (2h)
3. Ensure `_recently_stopped` query includes the new reason with the right time window
4. Add to the exit taxonomy table in `skill.md`

### Model field invariants
- `PaperTrade.pnl` — use this for P&L. `realized_pnl` does not exist.
- `PaperPortfolio.initial_capital` — reference baseline, never changes
- `PaperPortfolio.is_active` — paper_trading_step already filters on this; don't bypass it
- `PaperPortfolio.config` — JSON dict; always access via `cfg.get("key", default)` never direct key access

---

## Working on the Scheduler

### Alert checker invariant — never break this
`check_signal_alerts()` MUST pass `params={"live": "false"}` when fetching signals.
If changed to live=True, every stock at the threshold boundary will email BUY↔HOLD every minute.
Check this before every scheduler deploy: `grep -n 'live.*false' /app/src/services/scheduler.py`

### Service token maintenance
Every scheduler → external-service call must include `Authorization: Bearer {_service_token()}`.
If a new endpoint is added to any service and the scheduler needs to call it, add the token.
The pattern is in `_service_token()` at module level.

---

## Working on Auth

After any change to `api/auth.py` or `shared/common/jwt_auth.py`:
1. Deploy to container
2. Test login: confirm `POST /auth/login` returns 200 and a valid JWT
3. Test a protected endpoint with that JWT
4. Test logout: confirm the JWT is blacklisted
5. Test that a stale JWT (expired) gets properly rejected

Do not deploy auth changes without completing this checklist. A broken login loop affects every user.

---

## Diagnostic Commands

```bash
# Paper trading logs (last 30 min)
docker logs stockai-market-data-1 --since 30m | grep 'paper\.'

# Scheduler heartbeat
docker logs stockai-market-data-1 --since 1h | grep 'scheduler\.'

# Alert checker — confirm live=False
docker exec stockai-market-data-1 grep -n 'live.*false\|live.*False' /app/src/services/scheduler.py

# Signal alert send history
docker logs stockai-market-data-1 --since 24h | grep 'signal_alert'

# Check jose in market-data (needed for _service_token())
docker exec stockai-market-data-1 python3 -c 'from jose import jwt; print("OK")'

# Check which portfolios are active
docker exec stockai-market-data-1 python3 -c "
from shared.db import SessionLocal; from shared.db.models import PaperPortfolio
s = SessionLocal()
for p in s.query(PaperPortfolio).all():
    print(p.id, p.name, p.is_active, p.style)
s.close()"
```

---

## Deployment

```bash
# Single service file
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/market-data/src/<file> \
   stockai-market-data-1:/app/src/<path> && docker restart stockai-market-data-1"

# Shared model file (note path: /app/shared/db/ not /app/src/db/)
docker cp shared/db/models.py stockai-market-data-1:/app/shared/db/models.py
docker restart stockai-market-data-1
```
