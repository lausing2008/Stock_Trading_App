"""Capture each model family's own prediction (the ensemble "members") for the replay.

Runs INSIDE the running signal-engine container via `docker exec -i ... python - < this`, so the
service's own ml-prediction token is used in-process and never written anywhere. Read-only:
POST /ml/predict on the deployed ml-prediction, which returns predict_latest()'s result -- a
suppressed model's 0.5 plus its stored metrics, or 404 when no artifact exists. Prints one JSON
object {"symbol|style|family": {"status", "body"}} to stdout.

Symbols are read from the JSON list on the first line of stdin after this script (see usage in
the replay README). Style/horizon mapping is ml-prediction's own (derived server-side).
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path[:0] = ["/app", "/app/shared"]
import httpx  # noqa: E402

from src.generators.signals import _ml_service_token, _settings  # noqa: E402

symbols = json.loads(os.environ["REPLAY_SYMBOLS"])
url = f"{_settings.ml_prediction_url}/ml/predict"
headers = {"Authorization": f"Bearer {_ml_service_token()}"}
jobs = [(s, st, f) for s in symbols for st in ("SHORT", "SWING", "LONG", "GROWTH")
        for f in ("xgboost", "lightgbm", "random_forest")]


def one(job):
    s, st, f = job
    with httpx.Client(timeout=60) as c:
        r = c.post(url, json={"symbol": s, "style": st, "model": f}, headers=headers)
    try:
        body = r.json()
    except Exception:
        body = {}
    return f"{s}|{st}|{f}", {"status": r.status_code, "body": body}


with ThreadPoolExecutor(max_workers=int(os.environ.get("REPLAY_WORKERS", "3"))) as ex:
    out = dict(ex.map(one, jobs))
print(json.dumps(out, default=str))
