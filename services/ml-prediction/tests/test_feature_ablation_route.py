"""Regression test for MPE-04's GET /ml/feature_ablation route wiring.

routes.py imports FastAPI/pydantic/db-dependent training functions at module level, none of
which are installed/stubbed for real in this local dev environment — source-text checks,
matching this repo's established pattern for routes.py files in Docker-only-dependency
services (e.g. market-data's test_gamma_exposure_route.py for the identical constraint class).
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_SOURCE = _ROUTES_PATH.read_text()


def test_route_is_registered_as_a_real_literal_path():
    assert '@router.get("/feature_ablation")' in _SOURCE


def test_no_catch_all_get_symbol_route_exists_in_this_file_to_shadow_it():
    """The BUG233-ROUTERORDER class this repo has hit before: a bare GET /{symbol} catch-all
    registered earlier in the same router would silently swallow a later literal-path route."""
    assert '@router.get("/{symbol}")' not in _SOURCE


def test_is_a_synchronous_read_not_a_background_task():
    """Unlike POST /ml/tune (a long-running background training job), this is a real-time
    research read — must call run_feature_ablation() directly and return its result, never
    schedule it via BackgroundTasks the way tune()/tune_all() do."""
    start = _SOURCE.index('def feature_ablation(')
    end = _SOURCE.index("\n\n\n", start) if "\n\n\n" in _SOURCE[start:] else len(_SOURCE)
    body = _SOURCE[start:end]
    assert "tasks.add_task" not in body
    assert "return run_feature_ablation(" in body


def test_requires_authentication():
    start = _SOURCE.index('@router.get("/feature_ablation")')
    end = _SOURCE.index("\n", start + 100)
    header = _SOURCE[start:end]
    assert "get_current_username" in header
