"""Regression guard for AUD-EI-MACRO-REACTION-BLOCKING.

event-intelligence is a single-process FastAPI app whose AsyncIOScheduler jobs run on the SAME
event loop that serves real-time HTTP requests (main.py: start_scheduler runs inside the
FastAPI app, not a separate process). check_release_day_fast_poll(), check_fomc_statement_poll(),
and generate_reaction() are all `async def` but originally called httpx.get()/feedparser.parse()
(both blocking, synchronous I/O) directly — every one of these calls would stall the entire
service's event loop for up to its timeout, hanging any concurrent request to event-intelligence
during that window. conftest.py stubs httpx as MagicMock() for local tests, which returns
instantly regardless of sync/async context — a dynamic test would not have caught this, so this
checks the actual source text for the fix (a dedicated ThreadPoolExecutor +
loop.run_in_executor(), the same pattern already used by decision-engine's regime.py).
"""
import pathlib

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "macro_reaction.py"
)
_SOURCE = _PATH.read_text()


def test_dedicated_executor_exists():
    assert "_macro_reaction_executor = ThreadPoolExecutor(" in _SOURCE


def test_market_regime_fetch_runs_in_executor():
    start = _SOURCE.index("async def generate_reaction(")
    end = _SOURCE.index("\nasync def ", start + 1)
    body = _SOURCE[start:end]
    assert "run_in_executor(_macro_reaction_executor, _get_market_regime)" in body
    assert "regime = _get_market_regime()" not in body, "blocking direct call reintroduced"


def test_fred_poll_runs_in_executor():
    start = _SOURCE.index("async def check_release_day_fast_poll(")
    end = _SOURCE.index("\ndef ", start + 1)
    body = _SOURCE[start:end]
    assert "run_in_executor(" in body
    assert "httpx.get(" in body  # still calls it — just not directly on the event loop


def test_fomc_feed_parse_runs_in_executor():
    start = _SOURCE.index("async def check_fomc_statement_poll(")
    end = len(_SOURCE)
    body = _SOURCE[start:end]
    assert "run_in_executor(\n            _macro_reaction_executor,\n            feedparser.parse" in body \
        or "run_in_executor(_macro_reaction_executor, feedparser.parse" in body
