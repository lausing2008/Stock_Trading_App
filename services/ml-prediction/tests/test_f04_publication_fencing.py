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


# ── Promotion: refused until qualification records exist (review PF-02) ──────────────────

def _v2_candidate(md, payload=b"v2-bytes", sym="AAA", style="SWING"):
    pub.set_v2_enabled(md, True, by="t")
    pub.authorize(md, sym, style, by="t")
    res = do(md, protocol="v2", sym=sym, style=style, payload=payload)
    return res["path"], hashlib.sha256(payload).hexdigest(), pub.read_policy(md)["epoch"]


def promote(md, path, sha, epoch, sym="AAA", style="SWING", fam="xgboost"):
    return pub.promote(md, sym, style, fam, path, sha, epoch, by="t", live_path=live(md, sym, style, fam))


@pytest.fixture
def qualified(monkeypatch):
    """Stands in for a qualification record that does not exist yet, so the selection code
    behind the gate can be exercised. Production has no such stub: every promotion refuses."""
    monkeypatch.setattr(pub, "_validate_qualification", lambda q, **kw: {"test_stub": True, **kw})


def test_every_promotion_is_refused_until_qualification_records_exist(md):
    live(md).write_bytes(b"v1")
    path, sha, epoch = _v2_candidate(md)
    with pytest.raises(pub.PublicationRefused, match="promotion_unavailable"):
        promote(md, path, sha, epoch)
    assert pub.read_policy(md)["selections"] == {}
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == live(md)


def test_a_candidate_for_another_cell_or_family_is_refused_even_when_qualified(md, qualified):
    """The review's witness: BBB/LONG/random_forest bytes promoted as AAA/SWING/xgboost."""
    pub.set_v2_enabled(md, True, by="t")
    pub.authorize(md, "AAA", "SWING", by="t")
    other = pub.publish(md, live(md, "BBB", "LONG", "random_forest"), writer(b"bbb"), protocol="v2",
                        symbol="BBB", style="LONG", family="random_forest")["path"]
    same_cell_other_family = pub.publish(md, live(md, fam="random_forest"), writer(b"rf"), protocol="v2",
                                         symbol="AAA", style="SWING", family="random_forest")["path"]
    epoch = pub.read_policy(md)["epoch"]
    for path, payload in ((other, b"bbb"), (same_cell_other_family, b"rf")):
        with pytest.raises(pub.PublicationRefused, match="candidate_not_for_this_cell_family"):
            promote(md, path, hashlib.sha256(payload).hexdigest(), epoch)
    assert pub.read_policy(md)["selections"] == {}


def test_promotion_requires_current_epoch_matching_hash_and_a_v2_candidate(md, qualified):
    path, sha, epoch = _v2_candidate(md)
    with pytest.raises(pub.PublicationRefused, match="candidate_hash_mismatch"):
        promote(md, path, "0" * 64, epoch)
    pub.authorize(md, "ZZZ", "LONG", by="t")                          # any change bumps the epoch
    with pytest.raises(pub.PublicationRefused, match="stale_epoch"):
        promote(md, path, sha, epoch)
    epoch = pub.read_policy(md)["epoch"]
    fenced = do(md, payload=b"v1")["path"]                            # a v1-fenced file
    with pytest.raises(pub.PublicationRefused, match="candidate_not_for_this_cell_family"):
        promote(md, fenced, hashlib.sha256(b"v1").hexdigest(), epoch)
    promote(md, path, sha, epoch)
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == pathlib.Path(path).resolve()


def test_revocation_refuses_a_promotion_approved_before_it(md, qualified):
    path, sha, epoch = _v2_candidate(md)
    pub.revoke(md, "AAA", "SWING", by="t")
    with pytest.raises(pub.PublicationRefused):
        promote(md, path, sha, epoch)


def test_promotion_is_refused_while_v2_is_disabled(md, qualified):
    path, sha, _ = _v2_candidate(md)
    pub.set_v2_enabled(md, False, by="t")
    with pytest.raises(pub.PublicationRefused, match="v2_disabled"):
        promote(md, path, sha, pub.read_policy(md)["epoch"])


def test_no_selection_serves_the_live_path(md):
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == live(md)
    pub.set_v2_enabled(md, True, by="t")
    assert pub.serving_path(md, live(md), "AAA", "SWING", "xgboost") == live(md)


# ── Serving integrity (review PF-04) ───────────────────────────────────────────────────────

def load(md, sym="AAA", style="SWING", fam="xgboost"):
    """load_serving with a loader that returns the bytes it was handed."""
    def loader(src):
        return src.read() if hasattr(src, "read") else pathlib.Path(src).read_bytes()
    return pub.load_serving(md, live(md, sym, style, fam), sym, style, fam, loader)


def _selected(md):
    live(md).write_bytes(b"original-v1")
    path, sha, epoch = _v2_candidate(md, payload=b"selected-v2")
    promote(md, path, sha, epoch)
    return pathlib.Path(path)


def test_a_selection_serves_exactly_its_approved_bytes(md, qualified):
    _selected(md)
    assert load(md) == b"selected-v2"
    pub.set_v2_enabled(md, False, by="t")
    assert load(md) == b"selected-v2", "disabling v2 does not change serving"


def test_a_replaced_selected_file_is_refused_not_served(md, qualified):
    sel = _selected(md)
    assert load(md) == b"selected-v2"                                # now cached by hash
    sel.write_bytes(b"replaced-after-approval")
    with pytest.raises(pub.ServingUnavailable, match="hash_mismatch"):
        load(md)


def test_a_same_size_same_timestamp_replacement_is_still_refused(md, qualified):
    import os
    sel = _selected(md)
    assert load(md) == b"selected-v2"
    st = sel.stat()
    sel.write_bytes(b"selected-XX")                                  # same length
    os.utime(sel, ns=(st.st_atime_ns, st.st_mtime_ns))
    with pytest.raises(pub.ServingUnavailable, match="hash_mismatch"):
        load(md)


def test_a_missing_selected_file_is_unavailable_not_untrained(md, qualified):
    sel = _selected(md)
    sel.unlink()
    with pytest.raises(pub.ServingUnavailable, match="selected_artifact_missing"):
        load(md)
    with pytest.raises(pub.ServingUnavailable, match="selected_artifact_missing"):
        pub.serving_path(md, live(md), "AAA", "SWING", "xgboost")


# ── Policy loss (review PF-03) ─────────────────────────────────────────────────────────────

def restart():
    pub._SERVING_CACHE.clear()
    pub._BUNDLE_CACHE.clear()


def test_initial_legacy_operation_without_any_policy_serves_live(md):
    live(md).write_bytes(b"v1")
    assert load(md) == b"v1"
    assert not (md / pub._MARKER).exists()


def test_the_first_policy_write_establishes_the_marker(md):
    pub.set_v2_enabled(md, False, by="t")
    assert (md / pub._MARKER).exists()
    assert not glob.glob(str(md / "*" / "*.joblib")), "the marker is invisible to live discovery"


@pytest.mark.parametrize("damage", ["corrupt", "missing", "unreadable"])
def test_a_lost_policy_after_a_selection_is_ml_unavailable_across_restart(md, qualified, damage):
    import os
    _selected(md)
    assert load(md) == b"selected-v2"
    pfile = pub.roots(md)[1] / "policy.json"
    if damage == "corrupt":
        pfile.write_text("{broken")
    elif damage == "missing":
        pfile.unlink()
    else:
        if os.geteuid() == 0:
            pytest.skip("root reads a mode-000 file")
        pfile.chmod(0)
    try:
        for _ in range(2):
            with pytest.raises(pub.ServingUnavailable, match="publication_policy_unavailable"):
                load(md)
            restart()
        with pytest.raises(pub.PublicationRefused, match="policy_unreadable"):
            do(md, payload=b"nightly")                                # the writer refuses too
        assert live(md).read_bytes() == b"original-v1"
    finally:
        if pfile.exists():
            pfile.chmod(0o644)


def test_a_lost_policy_is_unavailable_for_an_unselected_cell_too(md):
    """With the policy gone, whether any cell had a selection is unknown, so none is served."""
    pub.authorize(md, "ZZZ", "LONG", by="t")
    live(md).write_bytes(b"v1")
    (pub.roots(md)[1] / "policy.json").unlink()
    restart()
    with pytest.raises(pub.ServingUnavailable):
        load(md)


def test_an_unreadable_policy_refuses_publication_and_serving(md):
    pdir = pub.roots(md)[1]
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "policy.json").write_text("{not json")
    live(md).write_bytes(b"incumbent")
    with pytest.raises(pub.PublicationRefused, match="policy_unreadable"):
        do(md)
    assert live(md).read_bytes() == b"incumbent" and candidates(md) == []
    with pytest.raises(pub.ServingUnavailable):
        pub.serving_path(md, live(md), "AAA", "SWING", "xgboost")


# ── Rollback (review PF-05) ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fence_off", ["disable", "revoke"])
def test_rollback_serves_the_pinned_pre_selection_artifact_after_nightly_writes(md, qualified, fence_off):
    _selected(md)
    if fence_off == "disable":
        pub.set_v2_enabled(md, False, by="t")
    else:
        pub.revoke(md, "AAA", "SWING", by="t")
    assert do(md, payload=b"new-nightly-v1")["target"] == "live"     # nightly v1 resumes
    assert load(md) == b"selected-v2", "neither disabling nor revoking changes serving"
    restart()
    pub.rollback(md, "AAA", "SWING", "xgboost", by="ops")
    restart()
    assert load(md) == b"original-v1", "rollback is the pinned artifact, not the moving live file"
    sel = pub.read_policy(md)["selections"]["AAA|SWING|xgboost"]
    assert sel["kind"] == "rollback" and sel["sha256"] == hashlib.sha256(b"original-v1").hexdigest()
    assert pub.roots(md)[2] in pathlib.Path(sel["path"]).parents, "pinned copy is outside live discovery"


def test_a_tampered_pinned_rollback_is_refused(md, qualified):
    _selected(md)
    pinned = pathlib.Path(pub.read_policy(md)["selections"]["AAA|SWING|xgboost"]["rollback"]["path"])
    pinned.write_bytes(b"tampered")
    with pytest.raises(pub.PublicationRefused, match="pinned_rollback_integrity"):
        pub.rollback(md, "AAA", "SWING", "xgboost", by="ops")


def test_no_live_artifact_at_selection_means_no_pinned_rollback(md, qualified):
    path, sha, epoch = _v2_candidate(md)
    promote(md, path, sha, epoch)
    with pytest.raises(pub.PublicationRefused, match="no_pinned_rollback"):
        pub.rollback(md, "AAA", "SWING", "xgboost", by="ops")


def test_returning_to_the_current_live_file_is_separate_named_and_audited(md, qualified):
    _selected(md)
    pub.set_v2_enabled(md, False, by="t")
    do(md, payload=b"new-nightly-v1")
    with pytest.raises(pub.PublicationRefused, match="reason_required"):
        pub.serve_current_live(md, "AAA", "SWING", "xgboost", live(md), by="ops", reason=" ")
    pub.serve_current_live(md, "AAA", "SWING", "xgboost", live(md), by="ops", reason="v2 pilot ended")
    assert load(md) == b"new-nightly-v1"
    entry = pub.read_policy(md)["log"][-1]
    assert entry["action"] == "serve_current_live" and entry["reason"] == "v2 pilot ended"
    assert entry["live_sha256"] == hashlib.sha256(b"new-nightly-v1").hexdigest()
    assert not hasattr(pub, "deselect"), "the old unpinned deselect is gone"


# ── Locked live rewrite: the suppression resweep (review PF-01) ────────────────────────────

def _rewriter(new: bytes, entered=None, release=None):
    def rewrite(data):
        if entered is not None:
            entered.set()
            assert release.wait(5)
        return writer(new)
    return rewrite


def test_rewrite_live_replaces_atomically_and_reports_the_replaced_identity(md):
    live(md).write_bytes(b"old")
    res = pub.rewrite_live(md, live(md), symbol="AAA", style="SWING", rewrite=_rewriter(b"new"))
    assert res["written"] and res["replaced_sha256"] == hashlib.sha256(b"old").hexdigest()
    assert live(md).read_bytes() == b"new" and not list(live(md).parent.glob("*.tmp"))


def test_rewrite_live_deserialises_the_bytes_it_guards(md):
    live(md).write_bytes(b"exact")
    seen = []
    pub.rewrite_live(md, live(md), symbol="AAA", style="SWING",
                     rewrite=lambda data: seen.append(data))
    assert seen == [b"exact"]


def test_an_authorization_during_a_rewrite_waits_for_it_and_later_rewrites_are_fenced(md):
    """The review's race: authorization used to complete while the resweep was mid-write, which
    then overwrote a now-owned cell. Under the lock the authorization waits; the next rewrite
    sees ownership and writes nothing."""
    live(md).write_bytes(b"v1")
    pub.set_v2_enabled(md, True, by="t")
    entered, release, order = threading.Event(), threading.Event(), []

    def sweep():
        pub.rewrite_live(md, live(md), symbol="AAA", style="SWING", rewrite=_rewriter(b"swept", entered, release))
        order.append("rewrite_done")

    def auth():
        assert entered.wait(5)
        pub.authorize(md, "AAA", "SWING", by="t")
        order.append("authorize_done")

    t1, t2 = threading.Thread(target=sweep), threading.Thread(target=auth)
    t1.start(); t2.start()
    time.sleep(0.3)
    assert order == [], "authorize must be blocked while the rewrite holds the lock"
    release.set(); t1.join(5); t2.join(5)
    assert order == ["rewrite_done", "authorize_done"]
    res = pub.rewrite_live(md, live(md), symbol="AAA", style="SWING", rewrite=_rewriter(b"again"))
    assert res == {"written": False, "reason": "publication_fenced:AAA|SWING"}
    assert live(md).read_bytes() == b"swept"


def test_a_training_publish_during_a_rewrite_lands_after_it_never_under_it(md):
    live(md).write_bytes(b"v1")
    entered, release, order = threading.Event(), threading.Event(), []

    def sweep():
        pub.rewrite_live(md, live(md), symbol="AAA", style="SWING", rewrite=_rewriter(b"swept", entered, release))
        order.append("rewrite")

    def train():
        assert entered.wait(5)
        do(md, payload=b"trained")
        order.append("publish")

    t1, t2 = threading.Thread(target=sweep), threading.Thread(target=train)
    t1.start(); t2.start()
    time.sleep(0.3)
    assert order == []
    release.set(); t1.join(5); t2.join(5)
    assert order == ["rewrite", "publish"] and live(md).read_bytes() == b"trained"


def test_a_rewrite_refuses_when_an_unlocked_writer_replaced_the_file(md):
    live(md).write_bytes(b"read-this")

    def rewrite(data):
        live(md).write_bytes(b"someone-else")                        # outside the lock
        return writer(b"swept")
    with pytest.raises(pub.PublicationRefused, match="artifact_changed_since_read"):
        pub.rewrite_live(md, live(md), symbol="AAA", style="SWING", rewrite=rewrite)
    assert live(md).read_bytes() == b"someone-else" and not list(live(md).parent.glob("*.tmp"))


def test_rewrite_live_refuses_on_an_unreadable_policy(md):
    pdir = pub.roots(md)[1]
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "policy.json").write_text("{")
    live(md).write_bytes(b"v1")
    res = pub.rewrite_live(md, live(md), symbol="AAA", style="SWING", rewrite=_rewriter(b"x"))
    assert not res["written"] and res["reason"].startswith("policy_unreadable")
    assert live(md).read_bytes() == b"v1"


# ── Discovery and audit ────────────────────────────────────────────────────────────────────

def test_candidates_are_outside_every_live_discovery_path(md, qualified):
    """Live discovery is model_dir/*/*.joblib (predictors, resweep, inventory). Candidates and
    pinned rollbacks are a sibling tree, so no live scan can find or serve them."""
    _selected(md)
    do(md, sym="AAA", payload=b"v1")         # fenced v1 candidate too
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


def test_a_change_is_seen_even_when_the_policy_file_keeps_the_same_timestamp(md, qualified):
    """File mtimes are clock-tick coarse; a cache keyed on mtime alone served a deselected
    candidate in the container run. Force identical timestamps to pin the fix."""
    import os
    _selected(md)
    pfile = pub.roots(md)[1] / "policy.json"
    assert load(md) == b"selected-v2"                                # cached
    frozen = pfile.stat().st_mtime_ns
    pub.rollback(md, "AAA", "SWING", "xgboost", by="t")
    os.utime(pfile, ns=(frozen, frozen))
    assert load(md) == b"original-v1"
