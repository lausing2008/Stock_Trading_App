"""AUD-EVENTINTEL-BLOCKEDLOOP — a scheduled sync must not hold the event loop.

`sync_congress_trades` is `async def`, awaits one HTTP fetch, and then ran a few thousand
SYNCHRONOUS per-row upserts inline. For the whole of that loop uvicorn could answer nothing:
/health timed out, Docker marked the container unhealthy, and CPU sat near zero because the
process was not busy so much as unavailable. It fires daily at 07:30, so it happened every day.

Found by py-spy after a deploy guard reported the container unhealthy — not by a test, and not
by anything that looked at the service's own logs, which were clean.
"""
import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_CONGRESS = (_SRC / "services" / "congress.py").read_text()
_SCHED = (_SRC / "scheduler.py").read_text()
_TREE = ast.parse(_CONGRESS)


def _fn(tree, name):
    return next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def test_the_db_write_loop_is_a_separate_synchronous_function():
    """Nothing in it can be awaited — it is CPU and blocking-driver work — so the only correct
    place for it is off the loop entirely."""
    fn = _fn(_TREE, "_upsert_congress_trades")
    assert isinstance(fn, ast.FunctionDef), "must be sync, so it can run in a worker thread"
    body = ast.unparse(fn)
    assert "SessionLocal()" in body and "pg_insert" in body, "the DB work really moved here"


def test_the_async_entry_point_hands_that_loop_to_a_thread():
    body = ast.unparse(_fn(_TREE, "sync_congress_trades"))
    assert "asyncio.to_thread(_upsert_congress_trades" in body


def test_the_async_entry_point_no_longer_writes_rows_itself():
    """The regression this guards: moving the call back inline would restore the outage."""
    body = ast.unparse(_fn(_TREE, "sync_congress_trades"))
    assert "pg_insert(" not in body, "row writes must not run on the event loop"
    assert "s.commit()" not in body


def test_every_scheduled_job_that_does_blocking_work_offloads_it():
    """The sibling `job_sync_insider` already had the right shape; congress did not. Any
    scheduled job whose work is synchronous must say so explicitly."""
    sched = ast.parse(_SCHED)
    offenders = []
    for n in ast.walk(sched):
        if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("job_sync_"):
            src = ast.unparse(n)
            # Either it awaits something that is itself async-safe, or it offloads explicitly.
            if "to_thread" not in src and "_run(" not in src:
                offenders.append(n.name)
    assert not offenders, f"scheduled jobs with no visible offload or wrapper: {offenders}"


def test_the_worker_opens_and_closes_its_own_session():
    """`SessionLocal` is a plain sessionmaker, not a scoped_session, so a session created on the
    event loop and used in the worker would be a connection crossing threads. The session's
    whole lifetime must sit inside the threaded function."""
    fn = _fn(_TREE, "_upsert_congress_trades")
    body = ast.unparse(fn)
    assert "with SessionLocal() as" in body, "opened here, and closed by the with-block"
    # And the async caller must not be holding one open across the handoff.
    caller = ast.unparse(_fn(_TREE, "sync_congress_trades"))
    after = caller[caller.index("asyncio.to_thread"):]
    assert "SessionLocal" not in after, "no session may outlive the handoff into the thread"


def test_the_comment_does_not_claim_a_frequency_nobody_measured():
    """One stall was observed. The job is scheduled daily, but Docker keeps five health entries,
    the container has restarted, and this service has no job-run ledger — so 'it happened every
    day' is plausible and unestablished, and the code must not assert it."""
    seg = _CONGRESS[_CONGRESS.index("AUD-EVENTINTEL-BLOCKEDLOOP"):]
    seg = seg[:seg.index("def _upsert_congress_trades")]
    assert "not established" in seg
    assert "no historical evidence" in seg
    assert "this happened every day" not in seg
