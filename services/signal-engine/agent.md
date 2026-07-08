# Signal Engine — Engineering Agent Behavior

How to behave when working on `services/signal-engine/`. Signal quality and freshness are the
foundation everything else (paper trading, alerts, research) depends on.

---

## Mindset for This Service

Stale signals cascade into bad paper trades, wrong alert emails, and confused users. The signal
engine must be running and writing to the DB reliably. When investigating any issue in the broader
system, ask first: are signals fresh? Then proceed.

**routes.py is 4,481 lines.** Read the specific function you need, not the whole file.
`_bulk_persist()` is the critical path — changes here affect every signal write.

---

## Before Touching Signal Computation

Read `generators/signals.py` to understand the style profile for the style you're modifying.
GROWTH having a relaxed buy_threshold is intentional — don't "fix" it to match SWING thresholds.
Each style serves a different portfolio configuration.

**Signal reasons dict:** Any new metric added to signal computation should be stored in `reasons`
(the JSON dict persisted with each signal). Downstream consumers (T200 volume gate, T202 confidence
gate, DE scoring) read from `reasons`. If you compute it but don't store it, it's invisible.

---

## Before Touching `_bulk_persist()`

This function writes every signal to the DB. Before changing it:
1. Confirm the SQL uses `CAST(:param AS type)` not `:param::type` (BUG-6)
2. Confirm the research divergence check passes `_service_token()` in the auth header (INT-7)
3. Test with a small batch before triggering a full market refresh

---

## Verifying Changes Work

After deploying a signal engine change, always verify signals are actually being written:

```bash
# 1. Trigger refresh
docker exec stockai-market-data-1 python3 -c "..." # (see skill.md for full command)

# 2. Check refresh returned 200, not 401 or 500
# 3. Check signal timestamps updated
docker exec stockai-market-data-1 python3 -c "
from shared.db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text(\"SELECT market, COUNT(*), MAX(ts) FROM signals sig JOIN stocks st ON sig.stock_id=st.id GROUP BY market\")).fetchall()
print(rows); s.close()"
```

A 200 status but unchanged timestamps means the SQL is executing but not writing rows —
check for BUG-6 (CAST syntax) first.

---

## jose Is Non-Negotiable

After every container rebuild, verify jose is installed before the next market open:
```bash
docker exec stockai-signal-engine-1 python3 -c 'from jose import jwt; print("OK")'
```

Container rebuilds wipe pip-installed packages that weren't in `requirements.txt` at build time.
If a rebuild is needed and jose might not be in the image, reinstall immediately:
```bash
docker exec stockai-signal-engine-1 pip install 'python-jose[cryptography]==3.3.0'
```

---

## Diagnostic Flow

```
Signals stale?
    ↓
1. jose OK?    → docker exec ... python3 -c 'from jose import jwt'
2. 401 on refresh?  → docker logs signal-engine --since 2h | grep 401
3. SQL syntax error? → docker logs signal-engine --since 2h | grep -i 'syntax\|invalid'
4. DISTINCT ON issue? → look for 'InvalidColumnReference' in logs
5. Scheduler not running? → docker logs market-data --since 2h | grep scheduler
```

---

## Deployment

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/signal-engine/src/api/routes.py \
   stockai-signal-engine-1:/app/src/api/routes.py && \
   docker restart stockai-signal-engine-1"

# After restart, verify jose and trigger a test refresh
docker exec stockai-signal-engine-1 python3 -c 'from jose import jwt; print("jose OK")'
```
