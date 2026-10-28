"""F04 publication fencing -- the ACTUAL nightly path, executed in the ml-prediction image.

POST /ml/train_all_horizons (the scheduler's nightly call) -> FastAPI BackgroundTasks -> the REAL
train_model (XGBoost + RandomForest fitted on synthetic prices) -> training/publication.py -> the
filesystem. Only the data loaders are replaced (synthetic prices, no macro/sector/outcome/
fundamental rows) and the symbol query returns two fixture symbols; everything from the route to
the artifact on disk is production code. Also: a job queued before authorization and run after
it, a v2 request, serving selection, and the suppression resweep on an owned cell.

Skipped wherever lightgbm/xgboost are not importable (the local suite). Run inside the image:
  docker run --rm --network none -v <this file>:/t/test_nightly.py:ro -v <src>:/app/src:ro \
      --entrypoint python <ml-prediction image> -m pytest -q -p no:cacheprovider /t/test_nightly.py
"""
import hashlib
import sys

import pytest

pytest.importorskip("lightgbm")
pytest.importorskip("xgboost")
sys.path[:0] = ["/app/shared", "/app"]

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi import BackgroundTasks, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import db  # noqa: E402
from src.api import routes  # noqa: E402
from src.training import publication as pub  # noqa: E402
from src.training import trainer  # noqa: E402

SYMS = ["AAA", "BBB"]


def _prices(symbol, lookback_days=0):
    rng = np.random.default_rng(abs(hash(symbol)) % 2**32)
    ts = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=3), periods=900)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, len(ts))))
    return pd.DataFrame({"ts": ts, "open": close * (1 + rng.normal(0, .003, len(ts))),
                         "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": rng.integers(1_000_000, 5_000_000, len(ts)).astype(float)})


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        class _R:
            def scalars(self_inner):
                return iter(SYMS)
        return _R()


@pytest.fixture
def env(tmp_path, monkeypatch):
    md = tmp_path / "data" / "models"
    md.mkdir(parents=True)
    monkeypatch.setattr(trainer._settings, "model_dir", str(md))
    monkeypatch.setattr(db, "SessionLocal", _Session)
    monkeypatch.setattr(trainer, "_load_prices", _prices)
    empty = lambda *a, **k: pd.DataFrame()  # noqa: E731
    for name in ("fetch_macro_features", "fetch_sector_features", "fetch_signal_outcome_features"):
        monkeypatch.setattr(trainer, name, empty)
    monkeypatch.setattr(trainer, "_load_fundamentals", lambda *a, **k: None)
    monkeypatch.setattr(trainer, "_load_fund_snapshots", lambda *a, **k: [])
    monkeypatch.setattr(trainer, "_load_options_snapshots", lambda *a, **k: [])
    monkeypatch.setattr(trainer, "_load_sector_and_market_cap", lambda *a, **k: (None, None))
    # One style keeps the run short; the route loops styles exactly as in production.
    monkeypatch.setattr(routes, "_HORIZON_BY_STYLE", {"SWING": 10})
    pub._SERVING_CACHE.clear()
    app = FastAPI()
    app.include_router(routes.router)          # the router carries its own /ml prefix
    app.dependency_overrides[routes.require_model_admin] = lambda: "test"
    return md, TestClient(app)


def _live(md, sym, fam):
    return md / fam / f"{sym}_swing.joblib"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_nightly_v1_publishes_live_exactly_as_before_and_v2_is_refused(env):
    md, client = env
    r = client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    assert r.status_code == 200 and r.json()["total_tasks"] == 4
    for sym in SYMS:
        for fam in ("xgboost", "random_forest"):
            bundle = joblib.load(_live(md, sym, fam))
            assert bundle["metrics"]["published"] is True and bundle["style"] == "SWING"
    assert not (md.parent / "ml_candidates").exists()
    before = sorted(p.name for p in md.rglob("*.joblib"))
    r = client.post("/ml/train_all_horizons", params={"protocol": "v2"})
    assert r.status_code == 409
    assert sorted(p.name for p in md.rglob("*.joblib")) == before


def test_an_owned_cell_is_fenced_and_other_cells_keep_v1(env):
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    aaa = {f: _sha(_live(md, "AAA", f)) for f in ("xgboost", "random_forest")}
    bbb = {f: _sha(_live(md, "BBB", f)) for f in ("xgboost", "random_forest")}
    pub.set_v2_enabled(md, True, by="test")
    pub.authorize(md, "AAA", "SWING", by="test")
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    assert {f: _sha(_live(md, "AAA", f)) for f in aaa} == aaa, "owned live artifacts untouched"
    assert {f: _sha(_live(md, "BBB", f)) for f in bbb} != bbb, "non-owned cells retrained live"
    fenced = list((md.parent / "ml_candidates" / "v1-fenced").rglob("AAA_swing.*.joblib"))
    assert len(fenced) == 2


def test_a_job_queued_before_authorization_is_fenced_when_it_runs(env):
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    aaa = _sha(_live(md, "AAA", "xgboost"))
    tasks = BackgroundTasks()
    routes.train_all_horizons(tasks, protocol="v1", _="test")          # queued, not yet run
    pub.set_v2_enabled(md, True, by="test")
    pub.authorize(md, "AAA", "SWING", by="test")
    for t in tasks.tasks:
        t.func(*t.args, **t.kwargs)
    assert _sha(_live(md, "AAA", "xgboost")) == aaa
    pub.revoke(md, "AAA", "SWING", by="test")                          # retry after revocation
    retry = [t for t in tasks.tasks if t.args[:2] == ("AAA", "xgboost")]   # order is day-rotated
    assert len(retry) == 1
    retry[0].func(*retry[0].args, **retry[0].kwargs)
    assert _sha(_live(md, "AAA", "xgboost")) != aaa


def test_serving_follows_only_an_explicit_selection(env):
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    live = _live(md, "BBB", "xgboost")
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") == live
    pub.set_v2_enabled(md, True, by="test")
    pub.authorize(md, "BBB", "SWING", by="test")
    data = live.read_bytes()
    res = pub.publish(md, live, lambda tmp: open(tmp, "wb").write(data), protocol="v2",
                      symbol="BBB", style="SWING", family="xgboost")
    pub.promote(md, "BBB", "SWING", "xgboost", res["path"], hashlib.sha256(data).hexdigest(),
                pub.read_policy(md)["epoch"], by="test")
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") != live
    pub.set_v2_enabled(md, False, by="test")
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") != live, "disable is not a rollback"
    pub.deselect(md, "BBB", "SWING", "xgboost", by="test")
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") == live


def test_resweep_skips_an_owned_cell_and_still_corrects_others(env):
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    for sym in SYMS:                          # make the stored flag disagree with the rule
        p = _live(md, sym, "xgboost")
        b = joblib.load(p)
        b["oos_suppressed"] = not b["oos_suppressed"]
        joblib.dump(b, p)
    pub.set_v2_enabled(md, True, by="test")
    pub.authorize(md, "AAA", "SWING", by="test")
    aaa = _sha(_live(md, "AAA", "xgboost"))
    out = trainer.resweep_oos_suppression(dry_run=False)
    assert _sha(_live(md, "AAA", "xgboost")) == aaa
    recs = {c["artifact"]: c for c in out["newly_suppressed"] + out["newly_unsuppressed"]}
    assert recs["xgboost/AAA_swing.joblib"]["write_skipped"].startswith("publication_fenced")
    assert "write_skipped" not in recs["xgboost/BBB_swing.joblib"]
