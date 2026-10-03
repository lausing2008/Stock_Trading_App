"""AUD-ADDSTOCK-MISATTRIBUTED — the retry was already there; the MESSAGE was the bug.

WHAT HAPPENED (2026-10-02, reported as "not able to add stock from dashboard").
`POST /admin/add_stock` returned 502 at 15:38, 15:39, 15:45 and 17:23 UTC, then 200 at 23:44.
market-data had been up since 07:15, so this was not a deploy, and the handler logged
`add_stock.start` then nothing — no `add_stock.done`.

AN ERROR OF MINE, RECORDED BECAUSE THE SHAPE REPEATS. I first concluded `_fetch_yf_info` had
no retry, having grepped the function BODY and never looked at the line above the `def`.
BUG-ADDSTOCK-NORETRY (2026-08-07) had already given it a 3-attempt tenacity policy with 1-8s
exponential backoff. I then added a second retry layer on top of it — 3 x 3 = up to nine calls
into a live rate-limit storm, which is exactly the amplification BUG-YFCALLVOL2 warns about,
introduced while citing that incident as the reason for caution. The duplicate layer was
removed; a test below pins that only one retry policy exists.

WHAT WAS GENUINELY STILL BROKEN, after the existing retry had made its three attempts:

  1. Every failure became 502. A throttle is not a broken upstream; 503 + Retry-After says
     "come back shortly", which is what is actually true.
  2. The exception was never LOGGED, only returned, so the four production 502s cannot now be
     proved to have been throttles — only shown overwhelmingly likely by 6,566 provider
     rate-limit errors in the same window.
  3. The modal's fallback said "Failed to add — check the ticker symbol" for EVERY failure,
     sending the user to hunt for a typo in a symbol that was correct. That is the half of
     this incident that wasted someone's time.
"""
import ast
import pathlib

import pytest

_SRC = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py").read_text()


def _load():
    """Exec just the pure helpers — admin.py as a whole pulls DB/auth/yfinance."""
    tree = ast.parse(_SRC)
    wanted = {"_is_rate_limited", "RateLimited"}
    keep = [n for n in tree.body
            if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in wanted)
            or (isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in wanted for t in n.targets))]
    mod = ast.Module(body=keep, type_ignores=[])
    ns: dict = {"_fetch_yf_info": None}
    exec(compile(ast.fix_missing_locations(mod), "<admin-helpers>", "exec"), ns)
    return ns


NS = _load()


# ── telling a throttle from a bad symbol ────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "429 Client Error: Too Many Requests",
    "YFRateLimitError: rate limit exceeded",
    "Too Many Requests. Rate limited. Try after a while.",
    "HTTPError: 429",
])
def test_a_throttle_is_recognised(msg):
    assert NS["_is_rate_limited"](RuntimeError(msg)) is True


@pytest.mark.parametrize("msg", [
    "404 Not Found",
    "No data found for this symbol",
    "KeyError: 'longName'",
    "connection reset by peer",
])
def test_a_non_throttle_is_not_mistaken_for_one(msg):
    """Retrying a malformed symbol spends more of the rate budget that is already the
    constraint — and would make a permanent failure take three times as long to report."""
    assert NS["_is_rate_limited"](RuntimeError(msg)) is False
def _add_stock_body() -> str:
    i = _SRC.index('@router.post("/add_stock")')
    return _SRC[i:_SRC.index("# ── Multi-symbol add", i)]


def test_a_throttle_is_reported_as_retryable_not_as_a_broken_upstream():
    body = _add_stock_body()
    assert "_is_rate_limited(exc)" in body
    arm = body[body.index("if _is_rate_limited(exc):"):body.index("add_stock.provider_error")]
    assert "503" in arm, "503 means come back shortly; 502 means the upstream is broken"
    assert "Retry-After" in arm


def test_the_throttle_message_does_not_blame_the_symbol():
    """THE DEFECT THAT WASTED THE USER'S TIME. The old UI fallback told them to check a
    ticker that was already correct."""
    body = _add_stock_body()
    arm = body[body.index("if _is_rate_limited(exc):"):body.index("add_stock.provider_error")]
    assert "not a problem with the symbol" in arm
    assert "rate-limiting" in arm



# ── the multi-symbol endpoint ───────────────────────────────────────────────────────────
#
# Requested as "make the add Stock on the dashboard multi select". The shape matters more than
# the convenience: under rate limiting a batch of ten routinely splits — some added, some
# throttled, one genuine typo — and a single pass/fail verdict for the batch would be wrong
# for most of them AND would hide which ones are worth retrying.

def _add_stocks_body() -> str:
    i = _SRC.index('@router.post("/add_stocks")')
    return _SRC[i:_SRC.index("# ── SL-1: Admin signal log", i)]


def test_every_symbol_carries_its_own_outcome():
    body = _add_stocks_body()
    for status in ('"added"', '"exists"', '"not_found"', '"rate_limited"', '"error"'):
        assert status in body, f"{status} is not a reportable outcome"
    assert '"results": results' in body


def test_a_throttled_symbol_is_marked_retryable_and_a_typo_is_not():
    body = _add_stocks_body()
    assert 'status, retryable = "rate_limited", True' in body
    assert 'status, retryable = "not_found", False' in body


def test_the_retryable_subset_is_computed_for_the_caller():
    """So the UI offers 'retry the three that were throttled' instead of making the user
    re-type a list and work out which ones to keep."""
    assert '"retryable_symbols"' in _add_stocks_body()


def test_one_bad_symbol_cannot_abort_the_batch():
    body = _add_stocks_body()
    assert "except Exception as exc:" in body
    assert "never let one symbol abort" in body


def test_the_batch_is_sequential_not_concurrent():
    """BUG-YFCALLVOL2: a per-symbol fan-out amplified a live Yahoo rate-limit event. A bulk
    endpoint that parallelises would make the condition it most often meets worse."""
    body = _add_stocks_body()
    for forbidden in ("ThreadPool", "asyncio.gather", "concurrent.futures", "executor"):
        assert forbidden not in body, f"{forbidden} would amplify the rate limiting"
    assert "_ADD_STOCKS_SPACING_SECONDS" in body


def test_the_batch_is_bounded_with_a_stated_reason():
    tree = ast.parse(_SRC)
    cap = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "_ADD_STOCKS_MAX" for t in n.targets))
    assert 0 < ast.literal_eval(cap.value) <= 50
    body = _add_stocks_body()
    assert "the limit is" in body and "rate-limit" in body, \
        "a rejection must say WHY and what to do"


def test_duplicates_collapse_and_order_is_preserved():
    """A repeated ticker must not be counted twice, and the report should read back in the
    order the user typed."""
    body = _add_stocks_body()
    assert "seen: set[str] = set()" in body
    assert "not (s in seen or seen.add(s))" in body


def test_an_empty_request_is_rejected_rather_than_silently_succeeding():
    assert 'raise HTTPException(400, "No symbols supplied.")' in _add_stocks_body()


def test_the_summary_counts_every_status():
    body = _add_stocks_body()
    i = body.index("summary = {")
    block = body[i:body.index("log.info", i)]
    for status in ("added", "exists", "not_found", "rate_limited", "error"):
        assert f'"{status}"' in block


# ── exactly ONE retry policy ────────────────────────────────────────────────────────────

def test_there_is_exactly_one_retry_policy_on_the_metadata_fetch():
    """THE MISTAKE THIS FILE RECORDS. A second retry layer over an already-retrying function
    multiplies attempts (3 x 3 = 9) into the very rate-limit storm it is reacting to."""
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_fetch_yf_info")
    decorators = [ast.unparse(d) for d in fn.decorator_list]
    assert sum("retry" in d for d in decorators) == 1, \
        f"expected one retry policy, found: {decorators}"
    assert "stop_after_attempt(3)" in " ".join(decorators)


def test_no_second_retry_wrapper_was_reintroduced():
    for banned in ("_fetch_yf_info_resilient", "_YF_RETRY_ATTEMPTS", "_YF_RETRY_BASE_SECONDS"):
        assert banned not in _SRC, f"{banned} duplicates the tenacity policy above _fetch_yf_info"


def test_the_handler_calls_the_decorated_fetch_directly():
    body = _add_stock_body()
    assert "info = _fetch_yf_info(symbol)" in body


def test_the_failure_is_logged_not_only_returned():
    """The four production 502s were undiagnosable because the exception only ever reached the
    HTTP response body."""
    body = _add_stock_body()
    assert "add_stock.rate_limited" in body
    assert "add_stock.provider_error" in body
