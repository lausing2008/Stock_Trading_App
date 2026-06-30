# System & Deep Audit Guide

How to verify the platform is healthy. Three levels of depth depending on the situation.

---

## When to Run an Audit

| Trigger | Depth |
|---|---|
| After any deployment | Quick (5 min) |
| After a container rebuild | Quick + signal freshness |
| Weekly, Monday pre-market | Full (30 min) |
| Anomaly: signals stale, trades not opening, alerts missing | Full + targeted section |
| Before market open on a trade day | Quick |

---

## Quick Health Check (5 minutes)

```bash
# 1. All containers running?
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep stockai

# 2. Any container in restart loop?
docker ps --format '{{.Names}} {{.Status}}' | grep -v "healthy\|Up"

# 3. Signal freshness — any stale markets?
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text(\"SELECT market, MAX(sig.ts) FROM signals sig JOIN stocks st ON sig.stock_id=st.id GROUP BY market\")).fetchall()
[print(r) for r in rows]; s.close()"

# 4. jose installed in critical containers?
for svc in signal-engine ml-prediction market-data; do
  docker exec stockai-${svc}-1 python3 -c "from jose import jwt; print('${svc} OK')" 2>&1
done
```

---

## Full Audit

See **[[AUDIT_CHECKLIST]]** for the complete procedure with expected output, diagnostic commands,
and fix recipes for every check.

Sections in AUDIT_CHECKLIST.md:
1. Container health — running, healthy, no crash loops
2. Signal freshness — last signal per market, staleness diagnosis
3. Auth/jose connectivity — jose in all containers, service tokens valid
4. Scheduler job status — last_run_at per job, no stuck jobs
5. DB integrity — signal_outcomes, signal_alerts, prices_5m growth
6. Paper trading engine — open positions, exit logic, entry gates
7. ML model health — last retrain, Optuna params, jose, HMM regime (T211)
8. Email alert system — last send, dedup cooldown, oscillation check
9. Frontend build verification — image date, serving latest content
10. HMM regime state — T211 probabilistic second-opinion

---

## Post-Deployment Quick Checks

After any backend deploy (`docker cp` + `docker restart`):
```bash
# Confirm new code is live (check a known function/line that changed)
docker exec stockai-market-data-1 grep -n 'momentum_fade\|regime_suspension' /app/src/services/paper_trading_engine.py | head -5

# Check logs for startup errors (first 30 lines)
docker logs stockai-market-data-1 2>&1 | head -30

# Confirm no auth 401 loops after restart
docker logs stockai-market-data-1 --since 3m | grep '401'
```

After a frontend rebuild:
```bash
# Confirm container is using the new image (Started should be recent)
docker ps --format '{{.Names}} {{.Status}}' | grep frontend

# Hard refresh browser: Ctrl+Shift+R (Win/Linux) or Cmd+Shift+R (Mac)
# Check /improvements page shows latest tier (T216 and beyond)
```

---

## Reference: CLAUDE.md for Recurring Issues

The following recurring failure patterns are documented in CLAUDE.md and the root skill.md:
- Login redirect loop after deployment — see CLAUDE.md § Recurring Issue: Login Redirect Loop
- Signal 401 from signal-engine — jose missing; see CLAUDE.md § Signal Refresh 401
- Signal write failures — SQLAlchemy ::type cast bug; see CLAUDE.md § BUG-6
- Alert email oscillation — check `live=False` in scheduler.py check_signal_alerts()
- Improvements page not showing new tiers — TIER_LABEL/TIER_COLOR missing entry
