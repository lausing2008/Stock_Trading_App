"""F04 publication fencing -- the ACTUAL nightly path, executed in the ml-prediction image.

POST /ml/train_all_horizons (the scheduler's nightly call) -> FastAPI BackgroundTasks -> the REAL
train_model (XGBoost + RandomForest fitted on synthetic prices) -> training/publication.py -> the
filesystem. Only the data loaders are replaced (synthetic prices, no macro/sector/outcome/
fundamental rows) and the symbol query returns two fixture symbols; everything from the route to
the artifact on disk is production code. Also: a job queued before authorization and run after
it, a v2 request, serving selection and rollback, the suppression resweep on an owned cell, and
the independent review's races and serving refusals (docs/audits/2026-10-28-f04-fencing-s03-
independent-review.md, PF-01..PF-05) driven through the real trainer and routes.

Skipped wherever lightgbm/xgboost are not importable (the local suite). Run inside the image:
  docker run --rm --network none -v <this file>:/t/test_nightly.py:ro -v <src>:/app/src:ro \
      --entrypoint python <ml-prediction image> -m pytest -q -p no:cacheprovider /t/test_nightly.py
"""
import hashlib
import sys
import threading
import time

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
    pub._BUNDLE_CACHE.clear()
    app = FastAPI()
    app.include_router(routes.router)          # the router carries its own /ml prefix
    app.dependency_overrides[routes.require_model_admin] = lambda: "test"
    app.dependency_overrides[routes.get_current_username] = lambda: "test"
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


@pytest.fixture
def qualified(monkeypatch):
    """Stands in for a v2 qualification record, which does not exist yet; production refuses."""
    monkeypatch.setattr(pub, "_validate_qualification", lambda q, **kw: {"test_stub": True})


def _select(md, sym, fam, payload=None):
    live = _live(md, sym, fam)
    data = live.read_bytes() if payload is None else payload
    pub.set_v2_enabled(md, True, by="test")
    pub.authorize(md, sym, "SWING", by="test")
    res = pub.publish(md, live, lambda tmp: open(tmp, "wb").write(data), protocol="v2",
                      symbol=sym, style="SWING", family=fam)
    pub.promote(md, sym, "SWING", fam, res["path"], hashlib.sha256(data).hexdigest(),
                pub.read_policy(md)["epoch"], by="test", live_path=live)
    return res["path"]


def test_promotion_is_refused_without_a_qualification_record(env):
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    live = _live(md, "BBB", "xgboost")
    pub.set_v2_enabled(md, True, by="test")
    pub.authorize(md, "BBB", "SWING", by="test")
    data = live.read_bytes()
    res = pub.publish(md, live, lambda tmp: open(tmp, "wb").write(data), protocol="v2",
                      symbol="BBB", style="SWING", family="xgboost")
    with pytest.raises(pub.PublicationRefused, match="promotion_unavailable"):
        pub.promote(md, "BBB", "SWING", "xgboost", res["path"], hashlib.sha256(data).hexdigest(),
                    pub.read_policy(md)["epoch"], by="test", live_path=live)
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") == live


def test_serving_follows_only_an_explicit_selection_and_rollback_is_pinned(env, qualified):
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    live = _live(md, "BBB", "xgboost")
    original = live.read_bytes()
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") == live
    _select(md, "BBB", "xgboost")
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") != live
    pub.set_v2_enabled(md, False, by="test")
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") != live, "disable is not a rollback"
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})        # nightly v1 resumes
    assert live.read_bytes() != original
    pub._SERVING_CACHE.clear(); pub._BUNDLE_CACHE.clear()                    # restart
    pub.rollback(md, "BBB", "SWING", "xgboost", by="test")
    served = trainer._serving_artifact_path("BBB", "xgboost", "SWING")
    assert served != live and served.read_bytes() == original, "rollback serves the pinned bytes"
    pub.serve_current_live(md, "BBB", "SWING", "xgboost", live, by="test", reason="container test")
    assert trainer._serving_artifact_path("BBB", "xgboost", "SWING") == live


def _predict(client, path, sym="BBB"):
    r = client.post(f"/ml/{path}", json={"symbol": sym, "style": "SWING", "model": "xgboost"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_a_lost_policy_is_ml_unavailable_through_every_served_route_across_restart(env, qualified, damage):
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    _select(md, "BBB", "xgboost")
    assert _predict(client, "predict_ensemble_three").get("ml_unavailable_reason") != "serving_unavailable"
    pfile = pub.roots(md)[1] / "policy.json"
    pfile.write_text("{broken") if damage == "corrupt" else pfile.unlink()
    for _ in range(2):
        for route in ("predict", "predict_ensemble", "predict_ensemble_three"):
            out = _predict(client, route)
            assert out["ml_available"] is False and out["ml_unavailable_reason"] == "serving_unavailable", route
            assert out["serving_unavailable_reason"].startswith("publication_policy_unavailable")
        pub._SERVING_CACHE.clear(); pub._BUNDLE_CACHE.clear()               # restart


@pytest.mark.parametrize("fam", ["xgboost", "random_forest"])
def test_a_tampered_selected_member_fails_the_whole_cell_closed(env, qualified, fam):
    """random_forest is inside a member try-block that used to drop a failing member silently."""
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    path = _select(md, "BBB", fam)
    assert _predict(client, "predict_ensemble_three").get("ml_unavailable_reason") != "serving_unavailable"
    other = joblib.load(_live(md, "AAA", fam))                  # a valid bundle, wrong bytes
    joblib.dump(other, path)
    out = _predict(client, "predict_ensemble_three")
    assert out["ml_unavailable_reason"] == "serving_unavailable"
    assert "hash_mismatch" in out["serving_unavailable_reason"]
    assert out["model_probabilities"] == {}


def _flip_stored_flags(md):
    for sym in SYMS:
        p = _live(md, sym, "xgboost")
        b = joblib.load(p)
        b["oos_suppressed"] = not b["oos_suppressed"]
        joblib.dump(b, p)


def test_an_authorization_cannot_complete_while_the_resweep_writes(env, monkeypatch):
    """PF-01, the review's witness made an acceptance test: pause the REAL resweep inside its
    dump. Authorization used to complete first and the resweep then overwrote an owned cell."""
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    _flip_stored_flags(md)
    pub.set_v2_enabled(md, True, by="test")
    reached, resume, order = threading.Event(), threading.Event(), []
    real_dump = joblib.dump

    def paused(obj, target, *a, **k):
        if str(target).endswith(".tmp") and not reached.is_set():
            reached.set()
            assert resume.wait(10)
        return real_dump(obj, target, *a, **k)
    monkeypatch.setattr(joblib, "dump", paused)

    def sweep():
        order.append(("sweep", trainer.resweep_oos_suppression(dry_run=False)))

    def auth():
        assert reached.wait(10)
        pub.authorize(md, "AAA", "SWING", by="test")
        order.append(("authorize", None))

    t1, t2 = threading.Thread(target=sweep), threading.Thread(target=auth)
    t1.start(); t2.start()
    assert reached.wait(10)
    time.sleep(0.5)
    assert order == [], "authorization must wait for the in-progress locked rewrite"
    resume.set(); t1.join(30); t2.join(30)
    monkeypatch.setattr(joblib, "dump", real_dump)
    # The lock is per artifact: authorization waited for AAA's in-progress rewrite, which
    # therefore landed while the cell was not yet owned. It may complete before the sweep
    # moves on to BBB; that is correct, and BBB is not part of the cohort.
    assert {o[0] for o in order} == {"sweep", "authorize"}
    swept = dict(order)["sweep"]
    recs = {c["artifact"]: c for c in swept["newly_suppressed"] + swept["newly_unsuppressed"]}
    aaa_rec = recs["xgboost/AAA_swing.joblib"]
    assert "write_skipped" not in aaa_rec and "write_error" not in aaa_rec
    assert bool(joblib.load(_live(md, "AAA", "xgboost"))["oos_suppressed"]) == aaa_rec["now"]
    # Now owned: a further rewrite of AAA is fenced, BBB is still corrected.
    _flip_stored_flags(md)
    aaa = _sha(_live(md, "AAA", "xgboost"))
    out = trainer.resweep_oos_suppression(dry_run=False)
    assert _sha(_live(md, "AAA", "xgboost")) == aaa
    recs = {c["artifact"]: c for c in out["newly_suppressed"] + out["newly_unsuppressed"]}
    assert recs["xgboost/AAA_swing.joblib"]["write_skipped"].startswith("publication_fenced")


def test_a_model_trained_after_the_resweep_scan_is_never_overwritten_with_the_old_bundle(env, monkeypatch):
    """The resweep scans unlocked, then rewrites under the lock from FRESH bytes. A training run
    that publishes in between is re-judged on its own metrics, not replaced by the scanned bundle."""
    md, client = env
    client.post("/ml/train_all_horizons", params={"protocol": "v1"})
    _flip_stored_flags(md)
    real = pub.rewrite_live
    trained = {}

    def train_then_rewrite(model_dir, artifact, **kw):
        if "AAA_swing" in str(artifact) and not trained:
            trainer.train_model("AAA", "xgboost", style="SWING", horizon=10)
            trained["sha"] = _sha(_live(md, "AAA", "xgboost"))
        return real(model_dir, artifact, **kw)
    monkeypatch.setattr(pub, "rewrite_live", train_then_rewrite)
    out = trainer.resweep_oos_suppression(dry_run=False)
    assert trained, "training ran between the scan and the rewrite"
    assert _sha(_live(md, "AAA", "xgboost")) == trained["sha"], "the newer model was kept"
    recs = {c["artifact"]: c for c in out["newly_suppressed"] + out["newly_unsuppressed"]}
    assert recs["xgboost/AAA_swing.joblib"]["write_skipped"] == "unchanged"


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
