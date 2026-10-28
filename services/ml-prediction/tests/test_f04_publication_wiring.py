"""F04 publication fencing -- every entry point routes through the control plane.

Local and static (the trainer cannot be imported here: lightgbm). The same paths are EXECUTED
inside the ml-prediction image by test_f04_publication_nightly_path_container.py.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
ROUTES = (ROOT / "src/api/routes.py").read_text()
TRAINER = (ROOT / "src/training/trainer.py").read_text()
TUNER = (ROOT / "src/training/tuner.py").read_text()
SCHEDULER = (REPO / "services/market-data/src/services/scheduler.py").read_text()


def _fn(src, name):
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node, name):
    return [c for c in ast.walk(node) if isinstance(c, ast.Call)
            and (getattr(c.func, "id", None) == name or getattr(c.func, "attr", None) == name)]


def test_every_queued_training_task_carries_the_checked_protocol():
    tree = ast.parse(ROUTES)
    enqueuers = 0
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        adds = [c for c in _calls(fn, "add_task") if c.args and getattr(c.args[0], "id", None) == "train_model"]
        if not adds:
            continue
        enqueuers += 1
        assert _calls(fn, "_require_v1"), f"{fn.name} enqueues training without the protocol check"
        first_check = min(c.lineno for c in _calls(fn, "_require_v1"))
        for c in adds:
            kw = {k.arg: ast.unparse(k.value) for k in c.keywords}
            assert kw.get("protocol") == "protocol", f"{fn.name} line {c.lineno} drops the protocol"
            assert c.lineno > first_check, f"{fn.name}: task queued before the check"
    assert enqueuers == 5      # train, train_all, train_all_ensemble(_three), train_all_horizons


def test_require_v1_refuses_v2_while_disabled_and_v2_is_never_implemented():
    body = ast.unparse(_fn(ROUTES, "_require_v1"))
    assert "409" in body and "501" in body and "read_policy" in body


def test_train_model_refuses_non_v1_and_publishes_only_through_the_control_plane():
    fn = _fn(TRAINER, "train_model")
    body = ast.unparse(fn)
    assert body.index("if protocol != 'v1'") < body.index("from metrics.base_ledger"), "refuse before any work"
    writer_dumps = {id(c) for p in _calls(fn, "_publish") for lam in ast.walk(p)
                    if isinstance(lam, ast.Lambda) for c in _calls(lam, "dump")}
    stray = [c.lineno for c in _calls(fn, "dump") if id(c) not in writer_dumps]
    assert writer_dumps and not stray, f"joblib.dump outside the publish() writer at lines {stray}"
    assert not _calls(fn, "replace") and not _calls(fn, "mkstemp"), "no direct artifact write remains"
    pubs = _calls(fn, "_publish")
    assert len(pubs) == 1
    kw = {k.arg: ast.unparse(k.value) for k in pubs[0].keywords}
    assert kw["protocol"] == "protocol" and kw["family"] == "model_name"


def test_every_prediction_path_resolves_through_the_selection_manifest():
    for name in ("predict_latest", "load_trained", "predict_latest_ensemble", "predict_latest_ensemble_three"):
        body = ast.unparse(_fn(TRAINER, name))
        assert "_serving_artifact_path(" in body, name
        assert "_artifact_path(" not in body.replace("_serving_artifact_path(", ""), name


def test_resweep_never_rewrites_an_owned_cell():
    body = ast.unparse(_fn(TRAINER, "resweep_oos_suppression"))
    assert "owned" in body and "write_skipped" in body
    assert body.index("_fenced") < body.index("joblib.dump(bundle, tmp)")


def test_tuning_publishes_as_v1_only():
    for c in _calls(ast.parse(TUNER), "train_model"):
        kw = {k.arg: ast.unparse(k.value) for k in c.keywords}
        assert kw.get("protocol", "'v1'") == "'v1'"


def test_the_scheduler_nightly_job_requests_v1_and_never_v2():
    nightly = [c for c in _calls(ast.parse(SCHEDULER), "_post") if "train_all_horizons" in ast.unparse(c.args[0])]
    assert len(nightly) == 1
    assert "'protocol': 'v1'" in ast.unparse(nightly[0])
    assert "'v2'" not in SCHEDULER and '"v2"' not in SCHEDULER.split("train_all_horizons")[1][:400]
