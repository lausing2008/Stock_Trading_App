# Shared Module — Engineering Agent Behavior

How to behave when working on `shared/`. Changes here have the widest blast radius of anything
in the codebase — a bug in `models.py` or `jwt_auth.py` breaks every service simultaneously.

---

## Mindset for This Module

Shared code is the foundation. Think twice before changing it; think three times before
removing anything. Every field on every model is potentially used by multiple services.

**Before changing a model field:**
1. `grep -r "field_name" services/` — find every place the field is used
2. If removing or renaming: update all callers before deploying
3. Deploy shared change to ALL containers, not just the one you're working on

**Before changing `jwt_auth.py`:**
Test the full auth flow: login → get token → use protected endpoint → logout → confirm blacklisted.
A broken jwt_auth breaks every authenticated endpoint in the system.

---

## Adding a New Model

1. Add to `db/models.py` with correct SQLAlchemy column types and constraints
2. Create an Alembic migration in `db/migrations/versions/` with a descriptive name
3. Run `alembic upgrade head` from market-data to apply
4. Copy the updated `models.py` to every container that needs the new model:
   ```bash
   for svc in market-data signal-engine decision-engine ml-prediction; do
     docker cp shared/db/models.py stockai-${svc}-1:/app/shared/db/models.py
   done
   ```
5. Restart the affected containers after copy

---

## Adding a New Config Setting

1. Add to `Settings` class in `common/config.py` with type annotation and default
2. Add to EC2 `.env` file (SSH to EC2, edit `/home/ec2-user/Stock_Trading_App/.env`)
3. Copy updated `config.py` to affected containers
4. If the setting has no sensible default, all services will fail to start until `.env` is updated

---

## Checking jose Across All Services

jose is a dependency of `jwt_auth.py`. It must be installed in every container:
```bash
for svc in signal-engine ml-prediction market-data api-gateway decision-engine; do
  docker exec stockai-${svc}-1 python3 -c "from jose import jwt; print('${svc} OK')" 2>&1
done
```

---

## Deployment (shared module is deployed to ALL services)

```bash
# After changing db/models.py — copy to all service containers
CONTAINERS="market-data signal-engine decision-engine ml-prediction research-engine ranking-engine strategy-engine technical-analysis portfolio-optimizer event-intelligence"
for SVC in $CONTAINERS; do
  docker cp shared/db/models.py stockai-${SVC}-1:/app/shared/db/models.py
done

# Restart all affected containers
for SVC in $CONTAINERS; do
  docker restart stockai-${SVC}-1
done
```

For `common/` files, deploy only to the containers that actually import the changed module.
`jwt_auth.py` → all authenticated services.
`config.py` → all services.
`logging.py` → all services.
