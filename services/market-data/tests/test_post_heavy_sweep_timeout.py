"""Tests for BUG-WEEKLYREFRESH-HEAVYSWEEP-TIMEOUT's fix to _post() (scheduler.py).

Root cause: _post()'s original hardcoded timeout=15/3-retry pair is correct for cheap,
idempotent-cost calls, but is actively HARMFUL for a genuinely heavy, synchronous, non-
idempotent-cost route (a multi-minute grid sweep over signal_outcomes) — the target route's
own DB session/thread keeps running to completion even after the client gives up, so a retry
after a timeout doesn't recover anything, it just queues a SECOND overlapping heavy query
against the same bounded DB connection pool. Confirmed live: 3 consecutive Sundays
(2026-08-16/23/30) all show outcomes/calibrate/apply, tune_style_profiles, tune_strategy,
backfill_bearish_pillars, and tune_sell_pillars either timing out on every retry (yet
completing server-side minutes later, confirmed via real TuneHistory rows) or, on 2026-08-30
specifically, tune_strategy never completing at all — silently truncating the rest of that
Sunday's weekly tuning chain.

_post() now accepts keyword-only `timeout`/`retries` overrides, defaulting to the exact
original values (timeout=15, retries=3 => 3 total attempts with delays [3, 8] between them,
matching the original hardcoded behavior byte-for-byte) so every one of the ~25 other _post()
call sites in this file is completely unaffected.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler/db, both stubbed by conftest.py) — _post() itself has no such dependency (it only
uses httpx/time/log/_service_token, none of which need the stubbed modules), so its real
source is extracted and exec()'d against a controllable fake httpx module, matching the
technique already established for _resolve_job_status_check()/score_size_mult elsewhere in
this codebase's test history — this exercises the REAL retry/timeout/backoff logic, not a
hand-copied reimplementation that could silently drift from it.
"""
import pathlib
from unittest.mock import MagicMock

_SCHEDULER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
)
_SOURCE = _SCHEDULER_PATH.read_text()


def _extract_post_source() -> str:
    start = _SOURCE.index("def _post(")
    end = _SOURCE.index("\n\n\n", start)
    return _SOURCE[start:end]


class _FakeResponse:
    pass


def _build_post(*, post_side_effect, sleep_calls, service_token=""):
    """exec()s the real _post() source against a controlled fake httpx.Client and a captured
    time.sleep, so the test can assert on the REAL attempt count / sleep durations / final
    log call without driving a real network request or a real 3-160s wall-clock wait."""
    ns: dict = {}

    fake_httpx = MagicMock()

    class _FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kwargs):
            return post_side_effect()

    fake_httpx.Client = _FakeClient

    fake_log = MagicMock()

    def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    ns.update(
        {
            "httpx": fake_httpx,
            "time": type("T", (), {"sleep": staticmethod(_fake_sleep)})(),
            "log": fake_log,
            "_service_token": lambda: service_token,
        }
    )
    exec(compile(_extract_post_source(), "<extracted _post>", "exec"), ns)
    return ns["_post"], fake_log, ns


# ── Default behavior (retries=3) must exactly reproduce the ORIGINAL hardcoded semantics ────

def test_default_retries_is_3_total_attempts_not_4():
    """The original code iterated `enumerate([3, 8, 20], start=1)` — exactly 3 attempts, never
    using the 20s delay (attempt 3 is the last, so `attempt < len(delays)` is False on it).
    A naive rewrite could easily produce 4 total attempts instead of 3 (e.g. treating `retries`
    as "retries after the first attempt" rather than "total attempts") — this pins down which
    interpretation is correct."""
    call_count = {"n": 0}

    def _always_fail():
        call_count["n"] += 1
        raise ConnectionError("boom")

    sleeps = []
    post_fn, fake_log, _ = _build_post(post_side_effect=_always_fail, sleep_calls=sleeps)
    post_fn("http://x/y")
    assert call_count["n"] == 3
    # sleeps between attempts 1->2 and 2->3 only — never a 3rd sleep after the last attempt
    assert sleeps == [3, 8]
    fake_log.error.assert_called_once()
    _, kwargs = fake_log.error.call_args
    assert kwargs["attempts"] == 3


def test_default_timeout_is_15():
    captured_timeout = {}

    def _capture_and_fail():
        raise ConnectionError("boom")

    sleeps = []
    post_fn, _, ns = _build_post(post_side_effect=_capture_and_fail, sleep_calls=sleeps)

    # Patch the fake Client to record the timeout it was constructed with.
    orig_client_cls = ns["httpx"].Client

    class _RecordingClient(orig_client_cls):
        def __init__(self, timeout=None):
            captured_timeout["value"] = timeout
            super().__init__(timeout=timeout)

    ns["httpx"].Client = _RecordingClient
    post_fn("http://x/y")
    assert captured_timeout["value"] == 15


# ── retries=1 (the heavy-sweep call sites' own new setting) — no retry storm ────────────────

def test_retries_1_makes_exactly_one_attempt_with_no_sleep():
    """This is the exact setting the 7 heavy-sweep call sites now use — a single long-budget
    attempt, never a retry that would pile a second overlapping heavy query onto the same DB
    connection pool while the first is still running server-side."""
    call_count = {"n": 0}

    def _always_fail():
        call_count["n"] += 1
        raise TimeoutError("timed out")

    sleeps = []
    post_fn, fake_log, _ = _build_post(post_side_effect=_always_fail, sleep_calls=sleeps)
    post_fn("http://x/y", timeout=180, retries=1)
    assert call_count["n"] == 1
    assert sleeps == []  # zero backoff sleeps — nothing to back off before, there's no retry
    fake_log.error.assert_called_once()
    _, kwargs = fake_log.error.call_args
    assert kwargs["attempts"] == 1
    # never logs a "retry" warning when there's nothing to retry
    fake_log.warning.assert_not_called()


def test_custom_timeout_is_passed_through_to_the_http_client():
    captured_timeout = {}
    sleeps = []
    post_fn, _, ns = _build_post(post_side_effect=lambda: None, sleep_calls=sleeps)

    orig_client_cls = ns["httpx"].Client

    class _RecordingClient(orig_client_cls):
        def __init__(self, timeout=None):
            captured_timeout["value"] = timeout
            super().__init__(timeout=timeout)

    ns["httpx"].Client = _RecordingClient
    post_fn("http://x/y", timeout=180, retries=1)
    assert captured_timeout["value"] == 180


def test_a_successful_first_attempt_with_retries_1_never_sleeps_or_logs_an_error():
    sleeps = []
    post_fn, fake_log, _ = _build_post(post_side_effect=lambda: None, sleep_calls=sleeps)
    post_fn("http://x/y", timeout=180, retries=1)
    assert sleeps == []
    fake_log.error.assert_not_called()
    fake_log.warning.assert_not_called()


# ── retries=2 (an intermediate value, sanity-checking the general formula) ──────────────────

def test_retries_2_makes_two_attempts_with_one_sleep_of_3s():
    call_count = {"n": 0}

    def _always_fail():
        call_count["n"] += 1
        raise ConnectionError("boom")

    sleeps = []
    post_fn, fake_log, _ = _build_post(post_side_effect=_always_fail, sleep_calls=sleeps)
    post_fn("http://x/y", retries=2)
    assert call_count["n"] == 2
    assert sleeps == [3]
    _, kwargs = fake_log.error.call_args
    assert kwargs["attempts"] == 2
