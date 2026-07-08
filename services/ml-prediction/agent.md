# ML Prediction Service — Engineering Agent Behavior

How to behave when working on `services/ml-prediction/`. ML models underpin signal confidence —
changes here affect every signal the system generates.

---

## Mindset for This Service

ML changes are high-leverage and high-risk. A bad feature (e.g., one that leaks future data) can
produce a model that looks great in training but fails in production. A missing feature can leave
real alpha on the table.

**The cardinal rule:** Never use future data as a feature. Every feature must be computable using
only data available at the time of the signal, with no look-ahead. Walk-forward CV is the test —
if the feature can't be computed at the CV fold boundary, it's a leak.

---

## Before Adding a Feature

1. Read `features/builder.py` to understand how existing features are computed and timestamped
2. Ask: is this value known at signal computation time? (Price from tomorrow = NO. Earnings from last quarter = YES.)
3. Check whether an equivalent feature already exists — 22 features is dense; duplication degrades model
4. Add the feature to `features/builder.py` AND update the feature list in `skill.md`

---

## Before Modifying Training Logic

1. Read `training/trainer.py` — understand the walk-forward fold structure
2. Confirm the change doesn't alter fold boundaries in a way that creates look-ahead
3. Test with a single symbol+style+horizon before running `train_all`

---

## Tuning Workflow

```bash
# 1. Verify jose (required for tune_all auth)
docker exec stockai-ml-prediction-1 python3 -c 'from jose import jwt; print("OK")'

# 2. Trigger tune_all (20-40 min — do NOT restart container mid-run)
# (full command in skill.md)

# 3. After tuning completes, retrain with best params
# POST /ml/train_all uses the tuned params automatically

# 4. Verify models were written
docker exec stockai-ml-prediction-1 ls {model_dir}/xboost/ | wc -l
```

**Do not rebuild the container while tune_all is running** — it kills the job and the Optuna
study is lost. Only rebuild after the job completes.

---

## After a Container Rebuild

1. Reinstall jose immediately: `docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'`
2. Check if models survived the rebuild (they should if mounted, but verify)
3. Re-run `POST /ml/train_all` if models are missing

---

## Verifying Model Quality

```bash
# Check accuracy metrics
curl -s -H "Authorization: Bearer <token>" http://localhost:8003/ml/accuracy | python3 -m json.tool

# Check feature importance for a specific model
curl -s -H "Authorization: Bearer <token>" "http://localhost:8003/ml/features?symbol=AAPL&style=SWING&horizon=5d"
```

Red flags in accuracy metrics:
- Training accuracy >> validation accuracy = overfitting
- Validation accuracy < 52% = not better than coin flip
- Feature importance dominated by one feature = likely a data leak

---

## Deployment

```bash
# Deploy a specific file (trainer, features, or routes)
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/ml-prediction/src/<file> \
   stockai-ml-prediction-1:/app/src/<file> && \
   docker restart stockai-ml-prediction-1"

# After restart: reinstall jose
docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'
```
