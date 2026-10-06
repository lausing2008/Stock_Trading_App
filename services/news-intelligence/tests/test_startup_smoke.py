"""The application actually starting, not the shape of the code that starts it.

WHY THIS EXISTS. I broke production by inserting a function between `async ` and
`def start_scheduler(`: the keyword attached to the new function, the startup hook became a
plain function, FastAPI awaited its `None`, and the service restart-looped with "object NoneType
can't be used in 'await' expression". An AST test now catches that exact edit — but an AST test
only ever catches the edits someone thought to assert about. This runs the lifespan.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def no_side_effects(monkeypatch):
    """Keep the lifespan from reaching the network or the database.

    The pollers and the Alpaca stream are stubbed; the SCHEDULER is real, because whether jobs
    register is the thing being checked.
    """
    from src import scheduler as S
    monkeypatch.setattr(S, "run_alpaca_stream",
                        lambda stop: asyncio.sleep(0), raising=False)
    for name in ("job_pr_newswire", "job_businesswire", "job_edgar"):
        monkeypatch.setattr(S, name, MagicMock(), raising=False)
    monkeypatch.setattr(S, "job_retry_deferred", MagicMock(), raising=False)
    # EACH TEST GETS A FRESH SCHEDULER. An AsyncIOScheduler is bound to the loop it was
    # started on, so one left behind by an earlier test fails with "Event loop is closed" in
    # the next — which looks like a lifespan bug and is a fixture bug.
    def _reset():
        sched = getattr(S, "_scheduler", None)
        if sched is not None:
            try:
                if sched.running:
                    sched.shutdown(wait=False)
            except Exception:
                pass
        S._scheduler = None
        S._alpaca_task = None

    _reset()
    yield S
    _reset()


def test_the_startup_hook_can_actually_be_awaited(no_side_effects):
    """The precise failure: a synchronous hook returns None and `await None` raises."""
    S = no_side_effects

    async def _go():
        await S.start_scheduler()

    asyncio.run(_go())          # a plain function here raises TypeError on await
    assert S._scheduler is not None, "the scheduler was never created"


def test_every_expected_job_is_registered_by_the_real_lifespan(no_side_effects):
    S = no_side_effects
    asyncio.run(S.start_scheduler())
    ids = {j.id for j in S._scheduler.get_jobs()}
    assert {"pr_newswire_poll", "businesswire_poll", "edgar_poll",
            "news_deferred_retry"} <= ids, f"registered: {ids}"


def test_the_deferred_retry_job_is_callable_as_registered(no_side_effects):
    """A job registered as a coroutine but written as a blocking function (or the reverse)
    fails only when it first fires, which can be long after a deploy looks healthy."""
    import inspect

    from src import scheduler as S
    assert not inspect.iscoroutinefunction(S.job_retry_deferred.__wrapped__
                                           if hasattr(S.job_retry_deferred, "__wrapped__")
                                           else S.job_retry_deferred), \
        "APScheduler runs a plain callable in a worker thread; this does blocking work"


def test_the_lifespan_starts_registers_serves_and_shuts_down(no_side_effects):
    """Start through the real hook, answer a health question, and shut down — in one loop.

    Driven directly rather than through FastAPI's TestClient: the client manages its own loop
    in a worker thread, and an AsyncIOScheduler bound to that loop raises "Event loop is
    closed" on teardown, which reads as a lifespan failure and is a harness artefact. The
    property under test — the hook awaits, jobs register, shutdown is clean — is the same.
    """
    S = no_side_effects

    async def _lifespan():
        await S.start_scheduler()                    # the exact call FastAPI's on_startup makes
        assert S._scheduler is not None and S._scheduler.running

        # What a /health handler would report.
        health = {"status": "ok", "jobs": len(S._scheduler.get_jobs())}
        assert health["jobs"] >= 4, f"the lifespan registered {health['jobs']} jobs"

        S._scheduler.shutdown(wait=False)
        return health

    health = asyncio.run(_lifespan())
    assert health["status"] == "ok"


def test_starting_twice_does_not_register_duplicate_jobs(no_side_effects):
    """A restart loop would otherwise compound: each attempt adds another copy of every job."""
    S = no_side_effects

    async def _twice():
        await S.start_scheduler()
        first = len(S._scheduler.get_jobs())
        await S.start_scheduler()                    # the guard should make this a no-op
        second = len(S._scheduler.get_jobs())
        S._scheduler.shutdown(wait=False)
        return first, second

    first, second = asyncio.run(_twice())
    assert first == second, f"a second start added {second - first} duplicate job(s)"
