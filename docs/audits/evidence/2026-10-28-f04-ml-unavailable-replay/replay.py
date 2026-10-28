"""F04 Stage 0 fix -- frozen-input replay of signal generation, old path vs corrected path.

Pass 1 (record, OLD code): runs the DEPLOYED signal-engine generator (`generate_all_signals`)
for every active symbol. Every outbound httpx response and every Redis read is recorded. ML
calls are answered by a simulator from each model family's own prediction (the "members"),
captured beforehand inside the running service by capture_members.py (MEMBERS_FILE), building
the endpoint's response with the PRE-PATCH ensemble functions.

Pass 2 (replay, NEW code): runs the PATCHED generator against the recording only (any request
not recorded is a hard miss, counted), and answers ML calls from the same recorded members with
the PATCHED ensemble functions. Inputs are therefore identical; only code differs.

Writes <out>/<pass>.jsonl (one row per symbol x style) and, for record, <out>/recording.json.
Read-only against production: GET/POST to read endpoints and Redis GET only. Never persists a
signal. The clock is not frozen; each row keeps its input_contract so the comparison can flag
any session-state difference between the passes.

Usage (inside a throwaway container from the signal-engine image):
  APP_ROOT=/app      ENSEMBLE_SRC=/replay/pre_stage0_ensembles.py python replay.py record /out
  APP_ROOT=/newapp   ENSEMBLE_SRC=/replay/trainer_new.py          python replay.py replay /out
"""
import ast
import json
import os
import pathlib
import sys
import threading
import time
import types
from datetime import datetime, timezone

MODE, OUT = sys.argv[1], pathlib.Path(sys.argv[2])
PASS = os.environ.get("PASS_NAME", MODE)     # e.g. "control": replay with the OLD code
APP = os.environ["APP_ROOT"]
sys.path[:0] = [APP, "/app/shared"]
LOCK = threading.Lock()
REC_PATH = OUT / "recording.json"
REC = json.loads(REC_PATH.read_text()) if MODE == "replay" else {"http": {}, "redis": {}, "members": {}}
if MODE == "record":
    # Members are captured beforehand inside the running service (capture_members.py), so this
    # container never needs ml-prediction credentials.
    REC["members"] = json.loads(pathlib.Path(os.environ["MEMBERS_FILE"]).read_text())
MISSES = []


def key(method, url, params=None, body=None):
    return json.dumps([method, str(url), params or {}, body or {}], sort_keys=True, default=str)


# ── Redis: record every GET, replay the recorded value ─────────────────────────────────────
import common.redis_client as _rc_mod  # noqa: E402

_real_get_redis = _rc_mod.get_redis


class _Redis:
    def __init__(self):
        self._real = _real_get_redis() if MODE == "record" else None

    def get(self, k):
        with LOCK:
            if k in REC["redis"]:
                return REC["redis"][k]     # stored decoded; readers use float() / json.loads
            if MODE == "replay":
                MISSES.append(["redis", k])
                return None
        v = self._real.get(k)
        v = v.decode() if isinstance(v, bytes) else v
        with LOCK:
            REC["redis"].setdefault(k, v)
            return REC["redis"][k]

    def __getattr__(self, name):
        raise RuntimeError(f"replay: unexpected redis call {name}")


_rc_mod.get_redis = lambda: _Redis()

# ── HTTP: record / replay; ML answered by the simulator ────────────────────────────────────
import httpx  # noqa: E402

_RealClient = httpx.Client
_ENS = {}


def _load_ensembles():
    src = pathlib.Path(os.environ["ENSEMBLE_SRC"]).read_text()
    names = {"_no_eligible_model", "predict_single_served", "predict_latest_ensemble", "predict_latest_ensemble_three"}
    return [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name in names]


_ENS_NODES = _load_ensembles()


class _MetaDisabled(Exception):
    pass


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return json.loads(json.dumps(self._payload))

    @property
    def text(self):
        return json.dumps(self._payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code}", request=None, response=None)


def _member(symbol, style, family, real_client):
    mk = f"{symbol}|{style}|{family}"
    with LOCK:
        if mk in REC["members"]:
            return REC["members"][mk]
    with LOCK:
        MISSES.append(["member", mk])
    return {"status": 404, "body": {}}


def _simulate_ml(endpoint, body, real_client):
    symbol, style = body["symbol"], body.get("style", "SWING")
    members = {f: _member(symbol, style, f, real_client) for f in ("xgboost", "lightgbm", "random_forest")}
    have = {f: v["body"] for f, v in members.items() if v["status"] == 200}
    exists = {f: f in have for f in members}

    def predict_latest(sym, model, horizon, style="SWING"):
        if model not in have:
            raise FileNotFoundError(model)
        return dict(have[model])

    env = {"predict_latest": predict_latest,
           "_artifact_path": lambda s, model, style="SWING": types.SimpleNamespace(exists=lambda m=model: exists[m]),
           "log": types.SimpleNamespace(warning=lambda *a, **k: None),
           "_meta_blend_enabled": lambda: False, "_meta_shadow_enabled": lambda: False,
           "_MetaDisabled": _MetaDisabled, "_load_sector_and_market_cap": lambda s: (None, None)}
    exec(compile(ast.Module(body=_ENS_NODES, type_ignores=[]), "ensemble", "exec"), env)
    horizon = {"SHORT": 5, "SWING": 10, "LONG": 20, "GROWTH": 15}[style.upper()]
    try:
        if endpoint.endswith("/ml/predict_ensemble_three"):
            return _Resp(200, env["predict_latest_ensemble_three"](symbol, horizon, style=style))
        if endpoint.endswith("/ml/predict_ensemble"):
            return _Resp(200, env["predict_latest_ensemble"](symbol, horizon, style=style))
        fam = body.get("model", "xgboost")
        if "predict_single_served" in env:
            return _Resp(200, env["predict_single_served"](symbol, fam, horizon, style=style))
        return _Resp(200, predict_latest(symbol, fam, horizon, style=style))
    except FileNotFoundError:
        return _Resp(404, {"detail": "No trained model"})


class _Client:
    def __init__(self, *a, **kw):
        self._real = _RealClient(*a, **kw) if MODE == "record" else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if self._real is not None:
            self._real.close()
        return False

    def close(self):
        pass

    def _call(self, method, url, **kw):
        url = str(url)
        if method == "POST" and "/ml/predict" in url:
            return _simulate_ml(url, kw.get("json") or {}, self._real)
        k = key(method, url, kw.get("params"), kw.get("json"))
        with LOCK:
            if k in REC["http"]:
                s, p = REC["http"][k]
                return _Resp(s, p)
        if MODE == "replay":
            MISSES.append(["http", k])
            return _Resp(599, {})
        r = getattr(self._real, method.lower())(url, **kw)
        try:
            payload = r.json()
        except Exception:
            payload = None
        with LOCK:
            REC["http"].setdefault(k, [r.status_code, payload])
            s, p = REC["http"][k]
        return _Resp(s, p)

    def get(self, url, **kw):
        return self._call("GET", url, **kw)

    def post(self, url, **kw):
        return self._call("POST", url, **kw)


httpx.Client = _Client
httpx.get = lambda url, **kw: _Client().get(url, **kw)      # the HK-flow fetch uses httpx.get

# ── Database: the generator's four direct reads (8-K, 13F, EPS revisions) ──────────────────
import db as _db_mod  # noqa: E402

_RealSessionLocal = _db_mod.SessionLocal
REC.setdefault("sql", {})


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    first = fetchone

    def fetchall(self):
        return self._rows

    all = fetchall


class _Session:
    """Rows are JSON round-tripped (dates/Decimals -> str) in BOTH passes, so each pass sees
    byte-identical values; the generator only str()/float()s them."""

    def __init__(self, *a, **kw):
        self._s = _RealSessionLocal(*a, **kw) if MODE == "record" else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if self._s is not None:
            self._s.close()
        return False

    def execute(self, stmt, params=None):
        k = key("SQL", " ".join(str(stmt).split()), params)
        with LOCK:
            if k in REC["sql"]:
                return _Result(REC["sql"][k])
        if MODE == "replay":
            MISSES.append(["sql", k])
            raise RuntimeError("replay: unrecorded SQL")
        rows = json.loads(json.dumps([list(r) for r in self._s.execute(stmt, params).fetchall()], default=str))
        with LOCK:
            REC["sql"].setdefault(k, rows)
            return _Result(REC["sql"][k])


_db_mod.SessionLocal = _Session

# ── Run the generator ──────────────────────────────────────────────────────────────────────
from src.generators import signals  # noqa: E402

symbols = json.loads(pathlib.Path(os.environ.get("SYMBOLS", "/replay/active_symbols.json")).read_text())
OUT.mkdir(parents=True, exist_ok=True)
started = datetime.now(timezone.utc).isoformat()
rows, errors = [], []
for sym in symbols:
    t0 = time.time()
    try:
        out = signals.generate_all_signals(sym)
    except Exception as exc:
        errors.append({"symbol": sym, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        continue
    for style, ai in out.items():
        rows.append({"symbol": sym, "style": style, "signal": ai.signal, "confidence": ai.confidence,
                     "bullish_probability": ai.bullish_probability,
                     "reasons": json.loads(json.dumps(ai.reasons, default=str)),
                     "seconds": round(time.time() - t0, 2)})

with open(OUT / f"{PASS}.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps(r, default=str) + "\n")
meta = {"mode": MODE, "pass": PASS, "app_root": APP, "ensemble_src": os.environ["ENSEMBLE_SRC"], "started": started,
        "finished": datetime.now(timezone.utc).isoformat(), "symbols": len(symbols), "rows": len(rows),
        "errors": errors, "misses": len(MISSES), "miss_sample": MISSES[:20],
        "recorded_http": len(REC["http"]), "recorded_redis": len(REC["redis"]), "recorded_sql": len(REC.get("sql", {})), "recorded_members": len(REC["members"])}
(OUT / f"{PASS}-meta.json").write_text(json.dumps(meta, indent=1, default=str))
if MODE == "record":
    REC_PATH.write_text(json.dumps(REC, default=str))
print(json.dumps({k: meta[k] for k in ("pass", "rows", "misses", "recorded_http", "recorded_members")}
                 | {"errors": len(errors)}))
