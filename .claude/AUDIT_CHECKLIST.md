# AUDIT_CHECKLIST.md — Full System Audit

Step-by-step verification for all platform subsystems. Run from the EC2 host
(`ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71`).

Last audited: 2026-07-01. Findings documented in § Audit Findings section at bottom.

---

## 1. Container Health

```bash
# All containers should show "healthy"
docker ps --format 'table {{.Names}}\t{{.Status}}'

# Any crash loops? (restart count > 0 = problem)
docker inspect $(docker ps -q) --format '{{.Name}} restarts={{.RestartCount}}' | grep -v 'restarts=0'

# Memory usage
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'
```

**Expected:** All 10 service containers + postgres + redis = 12 total, all `healthy`.
**Fix:** `docker logs stockai-<name>-1 --tail 50` — look for Python tracebacks on startup.

---

## 2. Signal Freshness

```bash
# Last signal per market — should be within 24h on trading days
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text('SELECT market, MAX(sig.ts)::date, COUNT(*) FROM signals sig JOIN stocks st ON sig.stock_id=st.id GROUP BY market')).fetchall()
for r in rows: print(r)
r2 = s.execute(text('SELECT COUNT(*), MAX(ts_evaluated)::date FROM signal_outcomes')).fetchone()
print('signal_outcomes:', r2)
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
# Services with auth-protected routes (all need jose): signal-engine, ml-prediction,
# market-data, api-gateway, research-engine, ranking-engine, portfolio-optimizer,
# decision-engine, strategy-engine, event-intelligence.
# technical-analysis has NO auth routes — jose missing has no functional impact there.
for svc in signal-engine ml-prediction market-data api-gateway research-engine ranking-engine portfolio-optimizer decision-engine strategy-engine event-intelligence; do
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
```

**Expected:** All containers print "OK"; token length ~200 chars.
**Fix:** `docker exec stockai-<svc>-1 pip install 'python-jose[cryptography]==3.3.0'`

---

## 4. Scheduler Job Status

```bash
# Critical job statuses via Redis (scheduler_jobs DB table does NOT exist — Redis only)
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
import redis, json, os
r = redis.Redis(host=os.environ.get('REDIS_HOST','redis'), port=6379, db=0)
critical = ['us_refresh','hk_refresh','hk_connect_flows','paper_trading','edgar_8k_ingest']
for name in critical:
    val = r.get('scheduler:job:' + name)
    if val:
        d = json.loads(val)
        lr = str(d.get('last_run','?'))[:19]
        print(name + ': status=' + str(d.get('status')) + ' last=' + lr + ' err=' + str(d.get('error','')))
    else:
        print(name + ': no data')"

# Scheduler heartbeat — active APScheduler jobs
docker logs stockai-market-data-1 --since 5m | grep 'Added job\|Running job' | tail -5

# Alert system — confirm market:refresh_failed flag is NOT set
docker exec stockai-redis-1 redis-cli exists market:refresh_failed
```

**Expected:** `us_refresh`/`hk_refresh` show `ok` with recent timestamps (within last market day). `market:refresh_failed` = 0.
**Fix if flag set:** `docker exec stockai-redis-1 redis-cli del market:refresh_failed`
**Note:** The `market:refresh_failed` flag suppresses ALL email alerts for 6 hours when set. It was incorrectly being set by any failed `_post()` call — fixed in BUG-8 to only be set by explicit callers.

---

## 5. DB Integrity

```bash
# Key table sizes
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
for t in ['signals', 'signal_outcomes', 'paper_trades', 'hk_connect_flows', 'sec_filings', 'prices']:
    r = s.execute(text(f'SELECT COUNT(*) FROM {t}')).fetchone()
    print(f'{t}: {r[0]:,}')
s.close()"

# NOTE: prices_5m table does NOT exist. The intraday price table is 'prices' (daily bars).
# NOTE: prices_5m was planned but never implemented.

# Any duplicate signals (upsert should prevent, but verify)
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
r = s.execute(text('SELECT stock_id, horizon, COUNT(*) as cnt FROM signals GROUP BY stock_id, horizon HAVING COUNT(*) > 1 LIMIT 5')).fetchall()
print('signal duplicates:', r)
s.close()"
```

**Expected:** signal_outcomes growing; signals ~8-9k US + ~2k HK; no duplicates.
**Fix for duplicates:** Check CLAUDE.md § BUG-6 — SQLAlchemy CAST syntax.

---

## 6. Paper Trading Engine Health

```bash
# Open positions per portfolio
# NOTE: paper_trades uses 'stage' NOT 'status'; paper_portfolios has no 'trading_style' column
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text(\"\"\"
    SELECT p.name, COUNT(t.id) as open_pos
    FROM paper_portfolios p
    LEFT JOIN paper_trades t ON p.id=t.portfolio_id AND t.stage='open'
    GROUP BY p.id, p.name ORDER BY p.id
\"\"\")).fetchall()
for r in rows: print(r)
r2 = s.execute(text(\"SELECT stage, COUNT(*) FROM paper_trades GROUP BY stage\")).fetchall()
print('by stage:', r2)
s.close()"

# Last entry + exit events (confirm engine is processing)
docker logs stockai-market-data-1 --since 2h | grep 'paper\.open_position\|paper\.exit_position\|paper\.skip_' | tail -10

# Regime state — what is the current market classification?
docker logs stockai-market-data-1 --since 1h | grep 'paper.regime_classified' | tail -3

# HMM second-opinion (T211)
docker exec stockai-market-data-1 python3 -c "
import httpx
r = httpx.get('http://ml-prediction:8003/ml/regime-state', timeout=10)
print(r.status_code, r.json())"
```

**Expected:** Portfolios have <= max_positions open; recent entry/exit events; regime logged; HMM returns state.
**Schema note:** `paper_trades.stage` values are `'open'`/`'closed'`. `paper_trades.trading_style` stores the style (not on `paper_portfolios`).

---

## 7. ML Model Health

```bash
# Models trained for each symbol?
docker exec stockai-ml-prediction-1 python3 -c "
import httpx
r = httpx.get('http://localhost:8003/ml/models', timeout=5)
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
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
import redis, json, os
r = redis.Redis(host=os.environ.get('REDIS_HOST','redis'), port=6379, db=0)
val = r.get('scheduler:job:tune_all_sent')
if val: print(json.loads(val))
else: print('no tune_all record')"
```

**Expected:** Models list is non-empty; jose OK; hmmlearn version shown; regime-state returns JSON with hmm_state.
**Fix if hmmlearn missing:** `docker exec stockai-ml-prediction-1 pip install 'hmmlearn>=0.3.0'`

---

## 8. Email Alert System

```bash
# Confirm no alert suppression flag
docker exec stockai-redis-1 redis-cli exists market:refresh_failed

# Last alert check run
docker logs stockai-market-data-1 --since 2h | grep 'signal_alert\|alert_sent\|alert_skipped' | tail -10

# Confirm live=False is being passed (oscillation prevention)
docker exec stockai-market-data-1 grep -n '"live".*"false"\|live.*false' /app/src/services/scheduler.py | head -3

# Alert cooldown state — any same-symbol alerts in the last 2h?
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
import datetime
s = SessionLocal()
two_h_ago = datetime.datetime.utcnow() - datetime.timedelta(hours=2)
rows = s.execute(text(\"SELECT symbol, horizon, direction, last_sent_at FROM signal_alerts WHERE last_sent_at > :t ORDER BY last_sent_at DESC LIMIT 10\"), {'t': two_h_ago}).fetchall()
print('recent alerts:', rows)
s.close()"
```

**Expected:** `market:refresh_failed` = 0; alert checker running; `live=false` present; no duplicate symbol+direction within 2h.
**Fix if suppressed:** `docker exec stockai-redis-1 redis-cli del market:refresh_failed`

---

## 9. Frontend Build Verification

```bash
# Is it serving content?
curl -s -o /dev/null -w '%{http_code}' http://localhost:3000/

# .env.production exists? (required for API_GATEWAY_URL)
ls -la /home/ec2-user/Stock_Trading_App/frontend/.env.production

# Check frontend logs for errors
docker logs stockai-frontend-1 --tail 5
```

**Expected:** HTTP 200; .env.production exists; frontend started cleanly.
**Rebuild command (ALWAYS use DOCKER_BUILDKIT=0 — never docker compose build):**
```bash
DOCKER_BUILDKIT=0 docker build --no-cache -f frontend/Dockerfile -t stockai-frontend:latest . && \
docker compose -f docker/docker-compose.yml up -d --force-recreate frontend
```

**Why DOCKER_BUILDKIT=0?**
Docker's newer BuildKit build engine has a caching bug: even with `--no-cache`, BuildKit can silently serve cached layers from its content-addressable store. This means `docker compose build --no-cache frontend` can succeed and look correct, but the image contains OLD code. The legacy builder (DOCKER_BUILDKIT=0) has a simpler cache model — `--no-cache` truly starts fresh. Since Next.js bakes the compiled `.next/` output into the image at build time, a stale image means users see old code even after you've changed files. Always use the legacy builder for frontend builds.

---

## 10. HMM Regime Classifier (T211)

```bash
# Is hmmlearn installed?
docker exec stockai-ml-prediction-1 python3 -c 'from hmmlearn.hmm import GaussianHMM; print("GaussianHMM OK")'

# Regime-state endpoint — full response
docker exec stockai-market-data-1 python3 -c "
import httpx
r = httpx.get('http://ml-prediction:8003/ml/regime-state', timeout=10)
print(r.json())"
```

**Expected:** GaussianHMM imports OK; regime-state returns `{hmm_state, hmm_prob, vix_now, spy_5d_return, iwm_vs_ema200}`.
**Fix if hmmlearn missing:** `docker exec stockai-ml-prediction-1 pip install 'hmmlearn>=0.3.0'`

---

## 11. HK Connect Flows

```bash
# Was last run successful? Check Redis scheduler status
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
import redis, json, os
r = redis.Redis(host=os.environ.get('REDIS_HOST','redis'), port=6379, db=0)
val = r.get('scheduler:job:hk_connect_flows')
if val: print(json.loads(val))"

# How many rows in hk_connect_flows table?
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
r = s.execute(text('SELECT COUNT(*), MAX(trade_date) FROM hk_connect_flows')).fetchone()
print('hk_connect_flows:', r)
s.close()"

# Check jose is configured in APScheduler thread context (T225 fix)
docker exec stockai-market-data-1 grep -n 'configure_logging' /app/src/services/hk_connect.py
```

**Expected:** `hk_connect_flows` status=ok; rows > 0 after first successful run; configure_logging() present in ingest function.
**Schedule:** Runs Mon-Fri at 17:00 HKT (09:00 UTC).
**Fix if logging bug recurs:** See CLAUDE.md § hk_connect_flows logging bug.

---

## Common Findings & Fixes

| Finding | Root Cause | Fix |
|---|---|---|
| Signal engine returning 401 | jose missing from signal-engine | `docker exec stockai-signal-engine-1 pip install 'python-jose[cryptography]==3.3.0'` |
| ML tune_all returning 401 | jose missing from ml-prediction | `docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'` |
| Signals stale (days old) | SQLAlchemy CAST bug OR jose 401 | Check logs for syntax error; verify CAST(:p AS type) used (not :p::type) |
| Alert emails oscillating | check_signal_alerts using live=True | Confirm `live=false` in scheduler.py signal fetch |
| Login redirect loop | 401 deleting valid JWT | Check api.ts 401 handler — must check token expiry before removing |
| Frontend showing old code | BuildKit cache | Rebuild with DOCKER_BUILDKIT=0 |
| HMM returns error | hmmlearn not installed | `pip install hmmlearn>=0.3.0` in ml-prediction container |
| Paper trades not opening | Regime suspend or gate blocking | Check paper.regime_classified log + paper.skip_* log entries |
| Research divergence never fires | Signal→research call missing auth header | Check _service_token() in signal-engine routes.py |
| All alerts suppressed for 6h | market:refresh_failed flag set by EDGAR 8-K timeout | `docker exec stockai-redis-1 redis-cli del market:refresh_failed` |
| hk_connect_flows job fails | Logger._log() kwargs error in APScheduler thread | Fixed in T225: configure_logging() at function entry + explicit logger_factory in common/logging.py |

---

## Schema Notes (Common Query Mistakes)

These columns/tables do NOT exist — correct versions below:

| Wrong | Correct | Table |
|---|---|---|
| `paper_trades.status` | `paper_trades.stage` | Values: `'open'` / `'closed'` |
| `paper_portfolios.trading_style` | `paper_trades.trading_style` | Style is on trades, not portfolios |
| `prices_5m` | `prices` | No intraday 5m table exists |
| `scheduler_jobs` (DB table) | `scheduler:job:*` (Redis keys) | Scheduler status is Redis-only |
| `signal_outcomes.checked_at` | `signal_outcomes.ts_evaluated` | Timestamp field name |
| `signal_alerts.direction` | `signal_alerts.last_signal` | Holds last signal value (BUY/SELL/HOLD/etc) |
| `signal_alerts.last_checked_at` | does not exist | Only `last_sent_at` tracks timing |
| `research_reports` | does not exist | Research engine uses in-memory dict (24h TTL, lost on restart) |

**Correct service ports (from Dockerfiles — use these in service-to-service calls):**
| Service | Port |
|---|---|
| api-gateway | 8000 |
| market-data | 8001 |
| technical-analysis | 8002 |
| ml-prediction | 8003 |
| ranking-engine | 8004 |
| signal-engine | 8005 |
| strategy-engine | 8006 |
| portfolio-optimizer | 8007 |
| research-engine | 8008 |
| decision-engine | 8009 |
| event-intelligence | 8010 |

---

## Audit Findings Log

### 2026-07-01 (Round 2 — Deep Audit) Findings

**BUG-10: ranking-engine jose missing → rankings 11 days stale**
- `POST /rankings/refresh?market=US` was returning 401 for at least 11 days.
- All 143 uniquely-ranked stocks had stale rankings (last refresh: 2026-06-19 per stock).
- Root cause: ranking-engine image was built before jose was added to requirements.txt.
- Fix: `pip install jose` in container, triggered manual US+HK refresh, rebuilt ranking-engine image.

**BUG-11: portfolio-optimizer jose missing → /optimize returning 401**
- `/optimize` endpoint requires auth. jose missing → every optimization request from frontend failed.
- Fix: `pip install jose` in container, rebuilt portfolio-optimizer image.

**BUG-12: Port documentation wrong in agent.md and CLAUDE.md**
- Documented ports were incorrect for 5 services. Correct ports verified from Dockerfiles.
- technical-analysis: **8002** (was documented as 8009)
- ranking-engine: **8004** (was documented as 8007)
- strategy-engine: **8006** (was documented as 8010)
- portfolio-optimizer: **8007** (was documented as 8011)
- decision-engine: **8009** (was documented as 8006)
- event-intelligence: **8010** (correct)
- Fix: Updated agent.md port list.

**FINDING: Research engine stores reports in-memory only**
- No `research_reports` DB table exists. Reports live in a Python dict in research-engine process.
- Container restart = all cached reports lost. Signal-engine divergence check always gets 404 after restart until cache warms (24h TTL).
- Not a bug per se, but means research context is unavailable for ~24h after any restart.

**FINDING: technical-analysis and strategy-engine also had jose missing**
- technical-analysis has NO auth-protected routes — jose missing had no operational impact.
- strategy-engine already had jose OK.
- portfolio-optimizer and ranking-engine are the ones that matter (both fixed + rebuilt).

**FINDING: HK portfolios in risk_off regime**
- HK portfolio configs show `regime_state: risk_off`, HSI -11.2% below SMA200.
- T226-A (regime_risk_off_gate=True default) will block all HK entries. Correct behavior.

**FINDING: GROWTH strategy win rate 8.3%**
- 12 closed GROWTH trades: 1 win (8.3%). All in bull OR risk_off at entry.
- 4 risk_off GROWTH trades all stopped out (covered by T226-A going forward).
- 8 bull regime GROWTH trades: 1 win, 4 at breakeven (-0.1%), 3 at full stop.
- Open GROWTH trades look good: avg +8.24% unrealized, all bull regime.

**FINDING: signal_alerts schema correction**
- `signal_alerts` does NOT have `direction` or `last_checked_at` columns.
- Correct columns: `id, user_id, symbol, email, last_signal, created_at, last_sent_at, alert_mode, horizon, require_consensus`

### 2026-07-01 (Round 1) Audit Results

| Check | Result | Notes |
|---|---|---|
| Container health | ✅ PASS | All 14 containers healthy |
| Signal freshness | ✅ PASS | US+HK signals current (Jun 30) |
| Jose — signal-engine | ✅ PASS | OK |
| Jose — ml-prediction | ✅ PASS | OK |
| Jose — market-data | ✅ PASS | OK |
| Jose — api-gateway | ✅ PASS | OK |
| Jose — research-engine | ✅ PASS | OK |
| Jose — ranking-engine | ❌ FAIL → FIXED | Missing → 401 on /rankings/refresh; 11 days stale; pip-installed + image rebuilt |
| Jose — portfolio-optimizer | ❌ FAIL → FIXED | Missing → /optimize returning 401; pip-installed + image rebuilt |
| Jose — technical-analysis | ⚠️ NOTE | Missing (jose installed), but TA has no auth routes — no functional impact |
| us_refresh | ✅ PASS | ok, 2026-06-30T20:16 |
| hk_refresh | ✅ PASS | ok, 2026-06-30T08:15 (Jul 1 = HK holiday) |
| paper_trading | ✅ PASS | ok, 2026-06-30T20:31; 16 open (GROWTH=9, US_SWING=7) |
| edgar_8k_ingest | ✅ PASS | ok, 2026-06-30T21:30; 21 filings |
| ML models | ✅ PASS | 5 trained models; jose OK; hmmlearn 0.3.3 |
| HMM regime | ✅ PASS | bull (prob=0.9999), VIX=16.45, SPY+1.8% |
| Frontend | ✅ PASS | HTTP 200, Next.js serving |
| Rankings freshness | ❌ FAIL → FIXED | All 143 stocks stale (Jun 19). jose was missing in ranking-engine image. Rebuilt. |
| Decision engine | ✅ PASS | Active: /decide/* returning 200; DE gate working (DELL HOLD suppressed alert) |
| Signal alerts | ✅ PASS | Active: 15 alerts sent in last 7 days across HK+US |
| hk_connect_flows | ⚠️ NOTE | Still 0 rows — BUG-9 fix deployed, waiting for next 17:00 HKT run |
| Signal outcomes | ✅ PASS | 1,232 rows; last evaluated Jun 30 |
| Service connectivity | ✅ PASS | All 11 services reachable (correct ports now documented) |
