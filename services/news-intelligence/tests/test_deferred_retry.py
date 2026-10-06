"""A budget deferral must be recoverable, and must not arm a risk gate with stale news.

WHY THIS EXISTS. Recording `classification_deferred_reason` fixed OBSERVABILITY. It did not
make the work recoverable: ingestion skips URLs it has already stored, so a row saved without a
classification is never offered to the classifier again by the normal path. Without a replay
worker, a deferral is permanent and silent.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_SRC = (Path(__file__).resolve().parents[1] / "src" / "services"
        / "deferred_retry.py").read_text()
_SCHED = (Path(__file__).resolve().parents[1] / "src" / "scheduler.py").read_text()


def test_a_retry_worker_exists_and_is_scheduled():
    """A deferral with no drain is a permanent silent absence."""
    assert "def retry_deferred" in _SRC
    assert "job_retry_deferred" in _SCHED
    assert 'id="news_deferred_retry"' in _SCHED


def test_it_selects_only_budget_deferrals_within_an_age_window():
    assert 'classification_deferred_reason == "budget_exhausted"' in _SRC
    assert "RealtimeNewsItem.ingested_at >= cutoff" in _SRC
    assert "_MAX_AGE_HOURS" in _SRC


def test_out_of_scope_rows_are_never_retried():
    """"Never eligible" is not "deferred" — retrying those would undo the scope saving."""
    assert '"out_of_scope"' not in _SRC.split("def retry_deferred")[1], \
        "the query must not pick up rows that were never eligible"


def test_it_updates_in_place_and_never_re_ingests():
    """A second insert would duplicate the URL and the article."""
    body = _SRC[_SRC.index("def retry_deferred"):]
    assert "session.add(row)" in body
    for forbidden in ("persist_news_items", "pg_insert", "INSERT INTO"):
        assert forbidden not in body, f"{forbidden} would create a second row"


def test_a_successful_retry_clears_the_deferral_marker():
    body = _SRC[_SRC.index("def retry_deferred"):]
    assert "row.classification_deferred_reason = None" in body, \
        "otherwise the row is retried forever"


def test_a_stale_headline_is_labelled_but_does_not_arm_the_gate():
    """The hot-news gate suppresses a BUY on news the market has not absorbed. A day-old story
    is not that, however correct its label."""
    body = _SRC[_SRC.index("def retry_deferred"):]
    i_fresh = body.index("if fresh:")
    seg = body[i_fresh:body.index("session.add(row)")]
    assert "row.is_material = bool(cls[\"is_material\"])" in seg
    stale_branch = seg[seg.index("else:"):]
    assert "is_material" not in stale_branch, \
        "a stale item must not set the materiality that arms the gate"


def test_a_deferral_never_clears_an_existing_risk_flag():
    body = _SRC[_SRC.index("def retry_deferred"):]
    assert "row.is_material = False" not in body
    assert "is_material = None" not in body


def test_an_item_still_over_budget_is_counted_not_lost():
    body = _SRC[_SRC.index("def retry_deferred"):]
    assert "deferred_out=deferred_again" in body
    assert "row.classification_attempts" in body, "so a stuck row is visible"


def test_the_worker_reports_what_it_did():
    for key in ("attempted", "classified_fresh", "classified_stale_no_gate", "still_deferred"):
        assert f'"{key}"' in _SRC, key


def test_the_startup_hook_is_still_a_coroutine():
    """A LIVE OUTAGE OF MINE. Inserting the retry job by text index split `async def
    start_scheduler` — the keyword attached to the new function and the startup hook became a
    plain function. FastAPI awaited its None return and news-intelligence restart-looped:
    "object NoneType can't be used in 'await' expression".

    Asserted against the AST, because the text `async def start_scheduler` existing somewhere
    in the file is exactly what was true while it was broken."""
    import ast
    tree = ast.parse(_SCHED)
    defs = {n.name: type(n).__name__ for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert defs.get("start_scheduler") == "AsyncFunctionDef", \
        "the FastAPI startup hook is awaited and must be a coroutine"
    assert defs.get("job_retry_deferred") == "FunctionDef", \
        "APScheduler runs a plain callable in a worker thread; this does blocking work"
