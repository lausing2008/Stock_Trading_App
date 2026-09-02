## Recurring Issue: Signal Refresh 401 — jose Library Missing from signal-engine

**Symptom:** HK (and potentially US) stock signals go stale — DB signals table has entries that are
days old even though the scheduler appears to be running. Users may receive BUY email alerts for a
stock that shows SELL in Signal Filter, or the top "AI Signal" badge on the stock detail page
disagrees with the 4-horizon tab signals. `POST /signals/refresh?market=HK` logs show 401.

**Root cause:** `python-jose` was missing from the `stockai-signal-engine-1` container despite
being listed in `requirements.txt`. The `shared/common/jwt_auth.py` `get_current_username()`
dependency does `from jose import JWTError, jwt` at call time — if the import fails, the generic
`except Exception` handler raises HTTP 401. This silently broke every authenticated endpoint on
the signal engine, including `POST /signals/refresh`. The scheduled `_bulk_persist` background task
was never registered so no signals were ever written.

HK stocks appeared most affected because individual US stock page visits trigger auto-persist via
the unauthenticated `GET /signals/{symbol}` endpoint, keeping US signals fresher. HK stocks with
fewer page views sat stale.

**Why the badge and tabs disagreed:** The top "AI Signal" badge comes from the aggregate overview
endpoint (`/aggregate/overview/{symbol}`) which calls `GET /signals/{symbol}?persist=true` —
unauthenticated, forces live computation. The 4 horizon tabs call `api.signal(symbol, style, false)`
which reads stored DB signals (`live=false`). When DB signals are stale, these diverge.

**Fix applied (2026-06-17):**
1. Installed `python-jose[cryptography]==3.3.0` directly in running container (immediate).
2. Rebuilt `stockai-signal-engine-1` image so it persists through future restarts.
3. Triggered manual HK + US refresh to backfill stale signals.

**What to check if signals go stale again:**
```bash
# Check if signal engine refresh is being rejected
docker logs stockai-signal-engine-1 --since 2h | grep 'refresh.*401\|401.*refresh'

# Verify jose is installed in the container
docker exec stockai-signal-engine-1 python3 -c 'from jose import jwt; print("jose OK")'

# If jose is missing, install it and rebuild:
docker exec stockai-signal-engine-1 pip install 'python-jose[cryptography]==3.3.0'
docker compose -f docker/docker-compose.yml build signal-engine && docker compose -f docker/docker-compose.yml up -d signal-engine

# Check last signal timestamp across markets
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text(\"SELECT market, MAX(sig.ts) FROM signals sig JOIN stocks st ON sig.stock_id=st.id GROUP BY market\")).fetchall()
print(rows); s.close()"
```

**After fix — trigger manual refresh:**
```bash
# Run from market-data container to trigger bulk signal refresh
docker exec stockai-market-data-1 python3 -c "
import sys, uuid; sys.path.insert(0,'/app/src'); sys.path.insert(0,'/app')
from common.config import get_settings; from datetime import datetime, timezone, timedelta
import httpx; from jose import jwt as _jwt; settings = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':datetime.now(timezone.utc)+timedelta(days=365)}, settings.jwt_secret, algorithm='HS256')
for mkt in ['HK','US']:
    r = httpx.post(f'http://signal-engine:8005/signals/refresh?market={mkt}', headers={'Authorization':f'Bearer {tok}'}, timeout=15)
    print(mkt, r.status_code, r.text[:60])
"
```

**Deployment note:** After any rebuild of `stockai-signal-engine-1`, verify `jose` is installed
before the next market open. The image build step must run `pip install -r requirements.txt`
including `python-jose[cryptography]`.

---


## Recurring Issue: tune_all 401 — jose Library Missing from ml-prediction

**Symptom:** `POST /ml/tune_all` returns 401 even with a valid JWT token. Optuna re-tune fails
silently. Models remain trained with stale hyperparameters.

**Root cause (found 2026-06-19):** `python-jose` was missing from the running `stockai-ml-prediction-1`
container even though it's in `requirements.txt`. The image was built before `python-jose` was added
to requirements.txt, so the installed package is absent. `shared/common/jwt_auth.py` does
`from jose import JWTError, jwt` — if the import fails, the generic `except Exception` block
raises HTTP 401, same as signal-engine's bug.

**Fix:**
```bash
docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'
# Verify:
docker exec stockai-ml-prediction-1 python3 -c 'from jose import jwt; print("jose OK")'
```

**Trigger tune_all after fix** (run from market-data container, ml-prediction is on port 8003):
```bash
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from common.config import get_settings
from jose import jwt as _jwt
import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400*365}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://ml-prediction:8003/ml/tune_all?n_trials=60', headers={'Authorization': f'Bearer {tok}'}, timeout=20)
print(r.status_code, r.text[:300])
"
```

**Permanent fix:** Rebuild the ml-prediction image (after tune_all completes — restarting kills it):
```bash
docker compose -f docker/docker-compose.yml build ml-prediction && docker compose -f docker/docker-compose.yml up -d ml-prediction
# Then re-install jose and re-trigger tune_all (rebuild wipes the pip-install)
docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'
```

---


## Recurring Issue: Stale Rankings — jose Missing from ranking-engine (BUG-10)

**Symptom:** Rankings are 7+ days old even though scheduler appears to be running. `POST /rankings/refresh?market=US` returns 401. Paper trading engine uses stale K-scores for `min_kscore` gate.

**Root cause (found 2026-07-01):** ranking-engine image was built before `python-jose` was added to `requirements.txt`. The same jose-missing-from-container pattern as signal-engine (Jun-17) and ml-prediction (Jun-19). `shared/common/jwt_auth.py` does `from jose import JWTError, jwt` — if that fails, all auth-protected endpoints return 401.

**Fix:**
```bash
docker exec stockai-ranking-engine-1 pip install 'python-jose[cryptography]==3.3.0'
# Verify:
docker exec stockai-ranking-engine-1 python3 -c 'from jose import jwt; print("jose OK")'
# Rebuild image so it persists:
docker compose -f docker/docker-compose.yml build ranking-engine && docker compose -f docker/docker-compose.yml up -d ranking-engine
# Trigger manual refresh:
docker exec stockai-market-data-1 python3 /tmp/rank_refresh.py  # or use inline token script
```

**Trigger manual ranking refresh:**
```bash
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
for mkt in ['US','HK']:
    r = httpx.post(f'http://ranking-engine:8004/rankings/refresh?market={mkt}', headers={'Authorization':f'Bearer {tok}'}, timeout=10)
    print(mkt, r.status_code, r.text[:80])
"
```

**Also found (same audit):** portfolio-optimizer missing jose → `/optimize` returning 401 for all users. Same fix: `pip install jose` + rebuild portfolio-optimizer image.

**What to check if rankings go stale:**
```bash
docker logs stockai-market-data-1 --since 2h | grep 'rankings.*401\|401.*rankings'
docker exec stockai-ranking-engine-1 python3 -c 'from jose import jwt; print("OK")'
# Check last ranking update:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
r = s.execute(text('SELECT COUNT(*), MAX(as_of)::date FROM rankings')).fetchone()
print('rankings:', r); s.close()"
```

---


## Recurring Issue: api-gateway Crash-Loops on `ModuleNotFoundError: No module named 'numpy'`

**Symptom (found during the reboot-recovery above):** `stockai-api-gateway-1` crash-looped
immediately on every fresh start with `ModuleNotFoundError: No module named 'numpy'`, traced
through `shared/common/__init__.py` → `from .indicators import ...` → `shared/common/indicators.py`'s
`import numpy as np`.

**Root cause:** `T233-ARCH-INDICATOR-DEDUP` (2026-07-09, commit `6a6de85`) added
`shared/common/indicators.py` and wired it into `shared/common/__init__.py`'s unconditional
top-level imports — meaning EVERY service that does `from common import ...` (or transitively
triggers `common/__init__.py`, which is essentially all of them) now requires `numpy` and
`pandas`, whether that service actually uses indicators or not. Every other service's
`requirements.txt` already had `numpy` (it's a common transitive need for a data-heavy trading
app), but `api-gateway` — originally a thin auth/routing proxy with no data-science
dependencies by design — never did. This was a DORMANT bug for over a week: the running
`api-gateway` process had already successfully imported everything before the fix landed (or
survived on a build predating it), so nothing crashed until this session's unrelated instance
reboot forced a genuinely fresh container start and the import ran for real for the first time.

**Fix applied (2026-07-17):** added `numpy==1.26.4` and `pandas>=2.0.0` to
`services/api-gateway/requirements.txt` (matching every sibling service's exact numpy pin) and
rebuilt the image via `docker compose -f docker/docker-compose.yml build api-gateway` (a real
rebuild was required here — this is a dependency addition, not a code hotfix, so `docker cp`
cannot fix it). Verified clean startup and live traffic post-recreate.

**Systemic risk not fixed here (documented, not silently dropped):** `shared/common/__init__.py`
importing `indicators.py` unconditionally means ANY future service added to this repo, or any
existing thin service, could hit this same class of bug the moment it touches `shared/common/`
at all — the dependency is invisible until a cold start actually exercises the import chain.
The more robust fix would be making the `indicators` import lazy (deferred until a caller
actually requests `sma`/`ema`/etc.) so services that never touch indicators never pay the
numpy/pandas cost — not done here under incident-recovery time pressure, since it touches a
file imported by all 11 services and deserves its own careful, non-incident-driven change.

**What to check if a similar crash-loop appears in a different service:**
```bash
docker logs stockai-<service>-1 --tail 40 | grep -A3 "ModuleNotFoundError"
# If the traceback bottoms out in shared/common/__init__.py -> indicators.py -> numpy/pandas,
# check that services/<service>/requirements.txt actually has numpy + pandas pinned — compare
# against a sibling service's requirements.txt (they should all match on these two).
```

---


## Recurring Issue: ml-prediction Missing `redis` — jose-Missing Bug Class, New Service (Found + Fixed 2026-07-21)

**Symptom:** while deploying an unrelated fix, `stockai-ml-prediction-1`'s logs showed
`macro_features.redis_save_failed` / `ModuleNotFoundError: No module named 'redis'` firing
continuously (1787 times in 2 hours before being caught) — a real, pre-existing, unrelated
bug surfaced only because this container happened to be restarted for a different reason.

**Root cause:** identical to the jose-missing-from-container pattern already documented
multiple times in this file for signal-engine/ml-prediction/ranking-engine — `redis==5.0.8`
is correctly pinned in `services/ml-prediction/requirements.txt`, but the currently-running
image predates that line being added, so the package was never actually installed in the
running container. Confirmed fail-open (this specific call site is a best-effort Redis write
of meta-model promotion status, wrapped in its own try/except per its own docstring — never
blocks the actual retrain), so this was silent/low-severity, not a live-trading risk — but
still a real, continuously-firing gap worth fixing.

**Fix applied:**
```bash
docker exec stockai-ml-prediction-1 pip install 'redis==5.0.8'  # immediate
docker compose -f docker/docker-compose.yml build ml-prediction  # durable — bakes it into the image
docker compose -f docker/docker-compose.yml up -d ml-prediction
```
Verified both the immediate `pip install` and the subsequent real rebuild — `from redis import
Redis` succeeds and zero `redis_save_failed` log lines appeared in the minutes following
either step.

**What to check if this recurs (or a similar dependency gap is suspected in any service)**:
```bash
docker exec stockai-<service>-1 python3 -c 'import <package>; print("OK")'
# If it fails despite the package being in requirements.txt, the image predates that line —
# pip install for an immediate fix, then a real `docker compose build <service>` to persist it.
```

---


## Recurring Issue: strategy-engine Crash-Loop on Deploy — `common.indicators` Never Baked Into Its Image

**Symptom (2026-07-22):** deploying the strategy-engine ATR-consolidation fix (importing
`from common.indicators import atr` for the first time in this service) caused an immediate
crash-loop on restart: `ModuleNotFoundError: No module named 'common.indicators'`.

**Root cause:** `shared/common/indicators.py` was created for `T233-ARCH-INDICATOR-DEDUP`
(2026-07-09) and rolled out via `docker cp` to the services that needed it AT THE TIME
(research-engine, ranking-engine, ml-prediction, market-data, signal-engine) — strategy-engine
wasn't one of them, since nothing in that service imported it yet. `docker cp` only patches a
container's writable layer; strategy-engine's actual image was never rebuilt with the file
baked in. This is the exact "docker cp is a session-scoped hotfix, not a deployment" invariant
already documented elsewhere in this file (see the EC2-reboot incident and the "docker compose
up -d --force-recreate can revert docker-cp'd files" entries) — except surfaced from the
opposite direction: instead of a REVERT losing a fix, this was a service that had simply never
received the original `docker cp` rollout in the first place, because it had no prior reason to.

**Fix applied:** `docker cp shared/common/indicators.py stockai-strategy-engine-1:/app/shared/
common/indicators.py` immediately (matching every other service's own copy), then restarted —
recovered cleanly within seconds. Confirmed via `docker run --rm --entrypoint /bin/sh
stockai-strategy-engine:latest -c 'ls -la /app/shared/common/'` that the image's baked-in
`shared/common/` genuinely lacked `indicators.py` (and, by extension, `ai_keys.py` and any
other shared file added since this service's image was last built) before concluding this was
the same root cause rather than guessing.

**Design invariant, generalized from this incident:** whenever a NEW shared/common/ module
gets its first consumer in a service that previously had no reason to import it, check whether
that service's image actually has the file baked in — do NOT assume "shared/common/ is shared,
so every service already has every file in it." A service can go a long time without ever
needing a given shared module and silently never receive it via any of this repo's historical
`docker cp` rollouts. Before deploying a NEW import of an existing shared/common/ file to a
service, either (a) confirm via `docker run --rm --entrypoint /bin/sh <image>:latest -c 'ls
/app/shared/common/'` that the file is already there, or (b) proactively `docker cp` it as part
of the same deploy, before restarting — don't wait for a crash-loop to discover the gap.

**What to check if a similar crash-loop appears in a different service:**
```bash
docker logs stockai-<service>-1 --tail 20 | grep "ModuleNotFoundError"
# If it names a shared/common/ module, confirm the image's baked-in copy is missing:
docker run --rm --entrypoint /bin/sh stockai-<service>:latest -c "ls -la /app/shared/common/"
# If missing, docker cp the specific file(s) in immediately, then restart:
docker cp shared/common/<file>.py stockai-<service>-1:/app/shared/common/<file>.py
docker restart stockai-<service>-1
```
This is a hotfix, not a durable deploy — the service's image is still owed a real rebuild that
bakes the shared file in, per the standing "docker cp is session-scoped" invariant.

---


## Recurring Issue: `feedparser` Missing From event-intelligence — FOMC/CPI/PPI/GDP/NFP Reaction Polls Silently Never Fired (Fixed 2026-07-29)

**Symptom:** on a real FOMC decision day, the "Latest Macro Reaction" card on both `/reports`
and `/intelligence` still showed "No macro reaction generated yet." User directly reported
this — a real FOMC statement had already been released, the card should have populated.

**Root cause:** `check_fomc_statement_poll()` (`services/event-intelligence/src/services/
macro_reaction.py`) crashed with `ModuleNotFoundError: No module named 'feedparser'` on
`import feedparser` — the VERY FIRST line of the function, before the RSS fetch, before the
DB row check, before `generate_reaction()` ever ran. This is the exact same jose-missing-
from-container bug class documented multiple times elsewhere in this file (signal-engine,
ml-prediction, ranking-engine, portfolio-optimizer) — except here the gap was in the
`requirements.txt` itself, not just the running container: `feedparser==6.0.11` is correctly
pinned in `market-data/requirements.txt` and `news-intelligence/requirements.txt`, but was
NEVER added to `event-intelligence/requirements.txt`, even though `macro_reaction.py` has
imported it since T249-MARKETMOVER-P2 first shipped (2026-07-15). Confirmed live: the job
fired correctly every minute across its full armed window (14:50–14:59 EDT,
`_FOMC_DATES` correctly contained today's real date), and logged the identical import error
on every single run — the poll was never actually broken by the Fed's RSS feed, the FOMC
date list, or the `macro_llm_reaction_enabled` flag (confirmed on, and confirmed irrelevant
regardless — it only gates delivery, not generation). Every other piece of the pipeline
(today's pending `economic_events` row with `actual_value IS NULL`, the flag, the date list)
was correctly in place and ready; the crash on the very first line was the sole cause.

**This is also, separately, the CPI/PPI/GDP/NFP poll's bug** — `check_release_day_fast_poll()`
imports the same `macro_reaction.py` module and would hit the identical crash on its own
8:30-9:59am ET armed window. Both polls were silently non-functional the entire time this
dependency was missing, not just the FOMC one — the FOMC report is what happened to surface
it, since a real FOMC date + the exact 2:00-2:59pm ET window overlapping a live user
Report-page visit is what made the gap visible.

**Fix applied:** added `feedparser==6.0.11` to `services/event-intelligence/requirements.txt`
(matching the exact version already pinned in the two sibling services), `pip install`'d it
into the live container for immediate recovery, then manually re-triggered
`check_fomc_statement_poll()` directly to backfill TODAY'S already-past reaction (its armed
window had already closed for the day by the time this was fixed) — confirmed live: the poll
found and processed the real Fed statement, wrote a genuine `reaction_text` to today's
`economic_events` row, and `check_macro_reaction_alerts()`'s own next 1-minute cycle correctly
picked it up and set `reaction_sent_at`, and `GET /events/overview` confirmed serving the real
reaction end-to-end. The image itself is still owed a real rebuild to persist the dependency
through the next full container recreate (`docker cp`/`pip install` are both session-scoped
hotfixes per this file's own standing invariant) — tracked via the `requirements.txt` commit,
which forces a real image rebuild on next deploy rather than staying a silent runtime patch.

**What to check if this recurs (or a similar poll goes silently dead again):**
```bash
docker exec stockai-event-intelligence-1 python3 -c 'import feedparser; print("OK")'
# If this fails despite feedparser being in requirements.txt, the image predates that line —
# pip install for an immediate fix, then confirm a real image rebuild actually ran (not just
# a docker cp) before trusting it survives the next container recreate.

docker logs stockai-event-intelligence-1 --since 24h | grep -i 'fomc\|release_day_fast_poll' | grep -i error
```

---

