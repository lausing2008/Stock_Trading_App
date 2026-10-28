"""F04 nightly-publication fencing (training/publication.py) -- the control plane on its own.

Real files, real fcntl lock, real threads, a temporary model_dir. The module is stdlib-only, so
it is imported directly. The trainer/route wiring is pinned in
test_f04_publication_wiring.py, and the route -> queued task -> writer path is executed for real
inside the ml-prediction image by test_f04_publication_nightly_path_container.py.
"""
import glob
import hashlib
import importlib.util
import json
import pathlib
import sys
import threading
import time

import pytest

_p = pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "publication.py"
_spec = importlib.util.spec_from_file_location("publication_under_test", _p)
pub = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pub
_spec.loader.exec_module(pub)


@pytest.fixture
def md(tmp_path):
    d = tmp_path / "data" / "models"
    (d / "xgboost").mkdir(parents=True)
    pub._SERVING_CACHE.clear()
    return d


def live(md, sym="AAA", style="SWING", fam="xgboost"):
    return md / fam / f"{sym}_{style.lower()}.joblib"


def writer(payload: bytes, delay=0.0, started=None):
    def w(tmp):
        if started is not None:
            started.set()
        time.sleep(delay)
        pathlib.Path(tmp).write_bytes(payload)
    return w


def do(md, protocol="v1", sym="AAA", style="SWING", payload=b"new", **kw):
    return pub.publish(md, live(md, sym, style), writer(payload, **kw), protocol=protocol,
                       symbol=sym, style=style, family="xgboost")


def candidates(md):
    return sorted(glob.glob(str(pub.roots(md)[2] / "**" / "*.joblib"), recursive=True))


# ── Default off; v1 unchanged for non-enabled cells ────────────────────────────────────────

def test_no_policy_file_is_the_default_v2_off():
    assert pub.read_policy("/nonexistent/models") == pub.DEFAULT_POLICY


def test_v1_publishes_to_the_live_path_exactly_as_before(md):
    live(md).write_bytes(b"old")
    res = do(md, payload=b"new")
    assert res["target"] == "live" and res["path"] == str(live(md))
    assert live(md).read_bytes() == b"new"
    assert not list(live(md).parent.glob("*.tmp")), "atomic write leaves no temp file"
    assert candidates(md) == []


def test_authorized_cell_with_v2_disabled_keeps_v1_live(md):
    pub.authorize(md, "AAA", "SWING", by="t")
    assert do(md)["target"] == "live"


# ── v2 is candidate-only, always ───────────────────────────────────────────────────────────

def test_v2_never_reaches_the_live_path_even_when_enabled_and_authorized(md):
    pub.set_v2_enabled(md, True, by="t")
    pub.authorize(md, "AAA", "SWING", by="t")
    live(md).write_bytes(b"incumbent")
    res = do(md, protocol="v2", payload=b"v2")
    assert res["target"] == "candidate" and "/ml_candidates/v2/" in res["path"]
    assert live(md).read_bytes() == b"incumbent"


def test_unknown_protocol_is_refused_and_nothing_is_written(md):
    with pytest.raises(pub.PublicationRefused):
        do(md, protocol="v3")
    assert not live(md).exists() and candidates(md) == []


# ── Owned cells: the nightly v1 job cannot overwrite them ──────────────────────────────────

def test_v1_write_for_an_owned_cell_is_fenced_into_the_candidate_root(md):
    pub.set_v2_enabled(md, True, by="t")
    pub.authorize(md, "AAA", "SWING", by="t")
    live(md).write_bytes(b"incumbent")
    res = do(md, payload=b"nightly")
    assert res["target"] == "candidate" and res["reason"] == "v1_fenced_cell_owned_by_v2"
    assert "/ml_candidates/v1-fenced/" in res["path"]
    assert live(md).read_bytes() == b"incumbent"
    assert do(md, sym="BBB")["target"] == "live", "other cells keep v1"


def test_a_job_queued_before_authorization_is_fenced_when_it_runs_after(md):
    queued = lambda: do(md, payload=b"queued")       # args captured at enqueue time  # noqa: E731
    pub.set_v2_enabled(md, True, by="t")
    pub.authorize(md, "AAA", "SWING", by="t")
    assert queued()["target"] == "candidate"
    pub.revoke(md, "AAA", "SWING", by="t")
    assert queued()["target"] == "live", "a retry after revocation is judged by the policy now"


def test_in_flight_publication_and_a_policy_change_are_serialised(md):
    """A write already in progress finishes under the policy it started with; the change waits
    for it, and every later write sees the change. No interleaving is possible."""
    pub.set_v2_enabled(md, True, by="t")
    started, order, results = threading.Event(), [], {}

    def slow_publish():
        results["first"] = do(md, payload=b"slow", delay=0.4, started=started)
        order.append("publish_done")

    def change():
        started.wait(2)
        pub.authorize(md, "AAA", "SWING", by="t")
        order.append("authorize_done")

    t1, t2 = threading.Thread(target=slow_publish), threading.Thread(target=change)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert order == ["publish_done", "authorize_done"]
    assert results["first"]["target"] == "live"
    assert do(md)["target"] == "candidate"


# ── Promotion and serving ──────────────────────────────────────────────────────────────────

def _v2_candidate(md):
    pub.set_v2_enabled(md, True, by="t")
    pub.authorize(md, "AAA", "SWING", by="t")
    res = do(md, protocol="v2", payload=b"v2-bytes")
    return res["path"], hashlib.sha256(b"v2-bytes").hexdigest(), pub.read_policy(md)["epoch"]


def test_promotion_requires_current_epoch_matching_hash_and_a_v2_candidate(md):
    path, sha, epoch = _v2_candidate(md)
    with pytest.raises(pub.PublicationRefused, match="candidate_hash_mismatch"):
        pub.promote(md, "AAA", "SWING", "xgboost", path, "0" * 64, epoch, by="t")
    pub.authorize(md, "ZZZ", "LONG", by="t")                          # any change bumps the epoch
    with pytest.raises(pub.PublicationRefused, match="stale_epoch"):
        pub.promote(md, "AAA", "SWING", "xgboost", path, sha, epoch, by="t")
    epoch = pub.read_policy(md)["epoch"]
    fenced = do(md, payload=b"v1")["path"]                            # a v1-fenced file
    with pytest.raises(pub.PublicationRefused, match="not_a_v2_candidate"):
        pub.promote(md, "AAA", "SWING", "xgboost", fenced, hashlib.sha256(b"v1").hexdigest(), epoch, by="t")
    pub.promote(md, "AAA", "SWING", "xgboost", path, sha, epoch, by="t")
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == pathlib.Path(path).resolve()


def test_revocation_refuses_a_promotion_approved_before_it(md):
    path, sha, epoch = _v2_candidate(md)
    pub.revoke(md, "AAA", "SWING", by="t")
    with pytest.raises(pub.PublicationRefused):
        pub.promote(md, "AAA", "SWING", "xgboost", path, sha, epoch, by="t")


def test_promotion_is_refused_while_v2_is_disabled(md):
    path, sha, _ = _v2_candidate(md)
    pub.set_v2_enabled(md, False, by="t")
    with pytest.raises(pub.PublicationRefused, match="v2_disabled"):
        pub.promote(md, "AAA", "SWING", "xgboost", path, sha, pub.read_policy(md)["epoch"], by="t")


def test_disabling_v2_does_not_silently_roll_back_serving_only_deselect_does(md):
    path, sha, epoch = _v2_candidate(md)
    pub.promote(md, "AAA", "SWING", "xgboost", path, sha, epoch, by="t")
    pub.set_v2_enabled(md, False, by="t")
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") != live(md)
    pub.deselect(md, "AAA", "SWING", "xgboost", by="t")
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == live(md)


def test_no_selection_serves_the_live_path(md):
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == live(md)
    pub.set_v2_enabled(md, True, by="t")
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == live(md)


# ── Failure behaviour and discovery ────────────────────────────────────────────────────────

def test_an_unreadable_policy_refuses_publication_and_serves_live(md):
    pdir = pub.roots(md)[1]
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "policy.json").write_text("{not json")
    live(md).write_bytes(b"incumbent")
    with pytest.raises(pub.PublicationRefused, match="policy_unreadable"):
        do(md)
    assert live(md).read_bytes() == b"incumbent" and candidates(md) == []
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == live(md)


def test_candidates_are_outside_every_live_discovery_path(md):
    """Live discovery is model_dir/*/*.joblib (predictors, resweep, inventory). Candidates are a
    sibling tree, so no live scan can find or serve them."""
    _v2_candidate(md)
    do(md, payload=b"v1")                    # fenced v1 candidate too
    assert candidates(md)
    discovered = glob.glob(str(md / "*" / "*.joblib")) + glob.glob(str(md / "**" / "*.joblib"), recursive=True)
    assert not set(discovered) & set(candidates(md))
    assert pub.roots(md)[2] not in pathlib.Path(md).resolve().parents


def test_every_change_is_logged_with_its_epoch(md):
    pub.set_v2_enabled(md, True, by="alice")
    pub.authorize(md, "AAA", "SWING", by="alice")
    log = pub.read_policy(md)["log"]
    assert [(e["action"], e["epoch"], e["by"]) for e in log] == [("enable_v2", 1, "alice"), ("authorize", 2, "alice")]
    assert json.loads((pub.roots(md)[1] / "policy.json").read_text())["epoch"] == 2


def test_a_change_is_seen_even_when_the_policy_file_keeps_the_same_timestamp(md):
    """File mtimes are clock-tick coarse; a cache keyed on mtime alone served a deselected
    candidate in the container run. Force identical timestamps to pin the fix."""
    import os
    path, sha, epoch = _v2_candidate(md)
    pub.promote(md, "AAA", "SWING", "xgboost", path, sha, epoch, by="t")
    pfile = pub.roots(md)[1] / "policy.json"
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") != live(md)     # cached
    frozen = pfile.stat().st_mtime_ns
    pub.deselect(md, "AAA", "SWING", "xgboost", by="t")
    os.utime(pfile, ns=(frozen, frozen))
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == live(md)
