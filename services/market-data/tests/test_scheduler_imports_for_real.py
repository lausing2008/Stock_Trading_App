"""Regression test for the apscheduler conftest stub gap (2026-09-06 deep audit, R2-1 / R2-27).

Before this fix, `apscheduler` was never stubbed in conftest.py, so `scheduler.py` could not be
imported by any test at all — 87 other test files in this directory fell back to source-text
regex/substring scraping instead of real execution (see test_scheduler_minute_job_misfire_grace.py's
own docstring: "scheduler.py can't be imported directly in this test environment"). That gap is why
send_paper_portfolio_digest()'s `from ..db import SessionLocal` (resolves to the nonexistent
`src.db`, since `db` is a top-level package from `shared/`, not `src/`) went undetected: the function
has never executed successfully in production, and no test caught it.

This file exists to (a) prove scheduler.py now imports cleanly under pytest, and (b) pin the
send_paper_portfolio_digest() crash as a real, executed regression test rather than a documented-only
finding — so the fix at scheduler.py:11251-11252 has something concrete to turn green.
"""
import pytest


def test_scheduler_module_imports():
    """scheduler.py must be importable — this alone would have caught the digest bug on first run."""
    import src.services.scheduler as sch

    assert hasattr(sch, "send_paper_portfolio_digest")
    assert hasattr(sch, "start_scheduler")


def test_send_paper_portfolio_digest_does_not_crash_on_import_resolution():
    """R2-1: `from ..db import SessionLocal` inside send_paper_portfolio_digest() must resolve to
    the real `db` package (from shared/), not `src.db` (which does not exist). Before the fix this
    raised ModuleNotFoundError on the function's first executable statement, before its own `try:`
    block — i.e. unconditionally, every single invocation. This test intentionally does NOT mock
    SessionLocal/User/PaperPortfolio/PaperTrade: the goal is specifically to prove the import
    resolves, not to test the digest's business logic (covered elsewhere once the import is fixed).
    """
    import src.services.scheduler as sch

    try:
        sch.send_paper_portfolio_digest()
    except ModuleNotFoundError as exc:
        if "src.db" in str(exc):
            pytest.fail(
                f"send_paper_portfolio_digest() raised ModuleNotFoundError resolving to "
                f"src.db (the exact R2-1 bug — 'from ..db import ...' resolves one package "
                f"level too high inside src/services/, landing on the nonexistent src.db "
                f"instead of the real top-level db package from shared/): {exc}"
            )
        # Any other ModuleNotFoundError (e.g. a test-stub artifact unrelated to the src.db
        # resolution bug) is not what this test guards against.
    except Exception:
        # Any other exception (e.g. a DB/session-level failure from the stubbed SessionLocal)
        # is fine here — this test only guards the import-resolution class of bug.
        pass
