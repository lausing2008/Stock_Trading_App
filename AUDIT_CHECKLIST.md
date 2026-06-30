# AUDIT_CHECKLIST.md — Full System Audit

Step-by-step verification for all platform subsystems. Run from the EC2 host
(`ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71`).

---

## 1. Container Health

```bash
# All containers should show "healthy" or "Up X hours/days"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# Any crash loops? (restart count > 0 = problem)
docker inspect $(docker ps -q) --format '{{.Name}} restarts={{.RestartCount}}' | grep -v 'restarts=0'

# Check for OOM kills in last 24h
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'
```

**Expected:** All 10 service containers + postgres + redis = 12 total, all `healthy`.
**Fix:** `docker logs stockai-<name>-1 --tail 50` — look for Python tracebacks on startup.

---

## 2. Signal Freshness

```bash
# Last signal per market — should be within 24h on trading days
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text('SELECT market, MAX(sig.ts), COUNT(*) FROM signals sig JOIN stocks st ON sig.stock_id=st.id GROUP BY market')).fetchall()
for r in rows: print(r)
s.close()"

# Signal engine health — last 2h
docker logs stockai-signal-engine-1 --since 2h | grep -E 'refresh|error|401' | tail -20

# jose installed?
docker exec stockai-signal-engine-1 python3 -c 'from jose import jwt; print("jose OK")'
```

**Expected:** HK and US signals < 24h old on market days; signal-engine logs show successful refreshes.
**Fix if stale:** See CLAUDE.md § Recurring Issue: Signal Refresh 401. Check jose, then trigger manual refresh.

---

## 3. Auth / Jose Connectivity

```bash
# jose must be installed in all auth-dependent containers
for svc in signal-engine ml-prediction market-data api-gateway decision-engine research-engine; do
  echo -n "${svc}: "
  docker exec stockai-${svc}-1 python3 -c "from jose import jwt; print('OK')" 2>&1
done

# Service token generation works?
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time
sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings
from jose import jwt as _jwt
s = get_settings()
tok = _jwt.encode({'sub':'audit','jti':str(uuid.uuid4()),'exp':int(time.time())+300}, s.jwt_secret, algorithm='HS256')
print('token ok, length:', len(tok))"

# api-gateway can validate the token?
# (Run from market-data which is on the same Docker network)
docker exec stockai-market-data-1 python3 -c "
import httpx, sys, uuid, time
sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings
from jose import jwt as _jwt
s = get_settings()
tok = _jwt.encode({'sub':'audit','jti':str(uuid.uuid4()),'exp':int(time.time())+300}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/stocks/latest_prices', headers={'Authorization': f'Bearer {tok}'}, timeout=5)
print(r.status_code, r.text[:80])"
```

**Expected:** All containers print "OK"; token length ~200 chars; api-gateway returns 200.
**Fix:** `docker exec stockai-<svc>-1 pip install 'python-jose[cryptography]==3.3.0'`

---

## 4. Scheduler Job Status

```bash
# Last run time for each scheduled job
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text('SELECT job_name, last_run_at, status FROM scheduler_jobs ORDER BY last_run_at DESC NULLS LAST')).fetchall()
for r in rows: print(r)
s.close()"

# Scheduler heartbeat — is it running?
docker logs stockai-market-data-1 --since 10m | grep 'scheduler\|heartbeat\|refresh' | tail -10
```

**Expected:** `refresh_market` ran within last market-day period (5 runs/day). `check_signal_alerts` < 2 min ago.
**Fix if stuck:** `docker restart stockai-market-data-1` — the scheduler auto-restarts on container start.

---

## 5. DB Integrity

```bash
# Signal outcomes — should be accumulating
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
r = s.execute(text('SELECT COUNT(*), MAX(created_at) FROM signal_outcomes')).fetchone()
print('signal_outcomes:', r)
r2 = s.execute(text('SELECT COUNT(*), MAX(updated_at) FROM signal_alerts')).fetchone()
print('signal_alerts:', r2)
s.close()"

# prices_5m size (grows ~3.5M rows/year — check not bloated)
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
r = s.execute(text('SELECT COUNT(*) FROM prices_5m')).fetchone()
print('prices_5m rows:', r[0])
s.close()"

# Any duplicate signals (upsert should prevent, but verify)
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
r = s.execute(text('SELECT stock_id, horizon, COUNT(*) as cnt FROM signals GROUP BY stock_id, horizon HAVING COUNT(*) > 1 LIMIT 5')).fetchall()
print('duplicates:', r)
s.close()"
```

**Expected:** signal_outcomes growing; signal_alerts count stable; no duplicate signals.
**Fix for duplicates:** The signals table has a UNIQUE constraint — duplicates indicate an upsert bug (check SQLAlchemy CAST rule in CLAUDE.md § BUG-6).

---

## 6. Paper Trading Engine Health

```bash
# Open positions per portfolio
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text(\"SELECT p.name, p.style, COUNT(t.id) as open FROM paper_portfolios p LEFT JOIN paper_trades t ON p.id=t.portfolio_id AND t.status='open' GROUP BY p.id ORDER BY p.id\")).fetchall()
for r in rows: print(r)
s.close()"

# Last entry + exit events (confirm engine is processing)
docker logs stockai-market-data-1 --since 2h | grep 'paper\.open_position\|paper\.exit_position\|paper\.momentum_fade\|paper\.regime_suspension' | tail -10

# Regime state — what is the current market classification?
docker logs stockai-market-data-1 --since 1h | grep 'paper.regime_classified' | tail -3

# HMM second-opinion (T211) — does it agree with rule-based?
docker exec stockai-market-data-1 python3 -c "
import httpx
r = httpx.get('http://ml-prediction:8003/ml/regime-state', timeout=5)
print(r.status_code, r.json())"
```

**Expected:** Portfolios have <= max_positions open; recent entry/exit events; regime logged; HMM returns state.

---

## 7. ML Model Health

```bash
# Models trained for each symbol?
docker exec stockai-ml-prediction-1 python3 -c "
import httpx
r = httpx.get('http://localhost:8003/ml/models')
import json; data = r.json()
print('models:', len(data.get('models', [])))
print('first 3:', data.get('models', [])[:3])"

# jose installed?
docker exec stockai-ml-prediction-1 python3 -c 'from jose import jwt; print("jose OK")'

# hmmlearn installed? (T211)
docker exec stockai-ml-prediction-1 python3 -c 'import hmmlearn; print("hmmlearn", hmmlearn.__version__)'

# HMM regime endpoint responding?
docker exec stockai-ml-prediction-1 python3 -c "
import httpx
r = httpx.get('http://localhost:8003/ml/regime-state', timeout=10)
print(r.status_code, r.text[:200])"

# When was the last tune_all run?
docker logs stockai-ml-prediction-1 --since 7d | grep 'tune_all\|tuning_complete' | tail -5
```

**Expected:** Models list is non-empty; jose OK; hmmlearn version shown; regime-state returns JSON with hmm_state.
**Fix if hmmlearn missing:** `docker exec stockai-ml-prediction-1 pip install 'hmmlearn>=0.3.0'`

---

## 8. Email Alert System

```bash
# Last alert check run
docker logs stockai-market-data-1 --since 2h | grep 'signal_alert\|alert_sent\|alert_skipped' | tail -10

# Confirm live=False is being passed (oscillation prevention)
docker exec stockai-market-data-1 grep -n "live.*false\|live=.false" /app/src/services/scheduler.py | head -5

# Alert cooldown state — any same-symbol alerts in the last 2h?
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
import datetime
s = SessionLocal()
two_h_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
rows = s.execute(text(\"SELECT symbol, horizon, direction, sent_at FROM signal_alerts WHERE last_sent_at > :t ORDER BY last_sent_at DESC LIMIT 10\"), {'t': two_h_ago}).fetchall()
for r in rows: print(r)
s.close()"
```

**Expected:** `live=false` present in scheduler; no duplicate symbol+direction within 2h (2h cooldown).
**Fix if oscillating:** See CLAUDE.md § Signal Alert Email Spam.

---

## 9. Frontend Build Verification

```bash
# When was the frontend container last started? (should be after last rebuild)
docker ps --format '{{.Names}}\t{{.Status}}' | grep frontend

# Is it serving the right content? (check improvements page for latest tier number)
curl -s http://localhost:3000/improvements | grep -o 'Tier [0-9]*' | sort -t' ' -k2 -n | tail -3

# .env.production exists? (required for API_GATEWAY_URL)
ls -la /home/ec2-user/Stock_Trading_App/frontend/.env.production
```

**Expected:** Frontend started recently; improvements page includes T216 (highest current tier); .env.production exists.
**Fix if stale:** `DOCKER_BUILDKIT=0 docker build --no-cache -f frontend/Dockerfile -t stockai-frontend:latest . && docker compose -f docker/docker-compose.yml up -d --force-recreate frontend`

---

## 10. HMM Regime Classifier (T211)

```bash
# Is hmmlearn installed?
docker exec stockai-ml-prediction-1 python3 -c 'from hmmlearn.hmm import GaussianHMM; print("GaussianHMM OK")'

# Model file exists and is fresh?
docker exec stockai-ml-prediction-1 ls -lh /tmp/hmm_regime.pkl 2>/dev/null || echo "No model file yet — will fit on first /ml/regime-state call"

# Regime-state endpoint — full response
curl -s http://localhost:8003/ml/regime-state | python3 -m json.tool

# Force refit (requires auth token)
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time, httpx
sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt
s = get_settings()
tok = _jwt.encode({'sub':'audit','jti':str(uuid.uuid4()),'exp':int(time.time())+300}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://ml-prediction:8003/ml/regime-refit', headers={'Authorization': f'Bearer {tok}'}, timeout=120)
print(r.status_code, r.text[:300])"
```

**Expected:** GaussianHMM imports OK; regime-state returns `{hmm_state, hmm_prob, vix_now, spy_5d_return, iwm_vs_ema200}`.
**Fix if hmmlearn missing:** `docker exec stockai-ml-prediction-1 pip install 'hmmlearn>=0.3.0'`
**Note:** First call to /ml/regime-state triggers model fitting (~60s) — subsequent calls use cached model.

---

## Common Findings & Fixes

| Finding | Root Cause | Fix |
|---|---|---|
| Signal engine returning 401 | jose missing from signal-engine | `docker exec stockai-signal-engine-1 pip install 'python-jose[cryptography]==3.3.0'` |
| ML tune_all returning 401 | jose missing from ml-prediction | `docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'` |
| Signals stale (days old) | SQLAlchemy CAST bug OR jose 401 | Check logs for syntax error; verify CAST(:p AS type) used (not :p::type) |
| Alert emails oscillating | check_signal_alerts using live=True | Confirm `live=false` in scheduler.py signal fetch |
| Login redirect loop | 401 deleting valid JWT | Check api.ts 401 handler — must check token expiry before removing |
| Frontend showing old content | BuildKit cache | Rebuild with DOCKER_BUILDKIT=0 |
| HMM returns error | hmmlearn not installed | pip install hmmlearn>=0.3.0 in ml-prediction container |
| Paper trades not opening | Regime suspend or gate blocking | Check paper.regime_classified log + paper.skip_* log entries |
| Research divergence never fires | Signal→research call missing auth header | Check _service_token() in signal-engine routes.py |
