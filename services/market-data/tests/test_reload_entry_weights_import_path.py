"""Regression test for the wrong relative import found in the 2026-09-06 deep audit
(priority item #7).

paper_portfolio.py:2238 used `from .paper_trading_engine import reload_entry_weights` — but
paper_trading_engine.py lives in src/services/, not src/api/ (where paper_portfolio.py lives),
so `.paper_trading_engine` resolved to a nonexistent sibling module. The ImportError was
swallowed by the bare `except Exception: pass` immediately below it, so calibrate_entry_weights()
kept returning HTTP 200 with newly-computed weights while reload_entry_weights() silently never
ran — meaning _entry_weights_cache (a process-lifetime, no-TTL global in paper_trading_engine.py)
never got invalidated, so live _should_enter() scoring kept using the OLD weights indefinitely.

The correct sibling import one function away, reload_min_rr_override
(paper_portfolio.py:2339-2340), already uses the right path (`..services.paper_trading_engine`) —
this fix makes reload_entry_weights's import match that established, correct pattern exactly.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def test_reload_entry_weights_import_uses_the_correct_services_path():
    assert "from ..services.paper_trading_engine import reload_entry_weights" in _ROUTES_SOURCE, (
        "reload_entry_weights must be imported via '..services.paper_trading_engine' — "
        "paper_trading_engine.py lives in src/services/, not as a sibling of paper_portfolio.py "
        "in src/api/. A '.paper_trading_engine'-style import silently fails (caught by a bare "
        "except immediately below it), meaning calibrated entry weights never actually reload."
    )


def test_reload_entry_weights_import_no_longer_uses_the_broken_sibling_path():
    assert "from .paper_trading_engine import reload_entry_weights" not in _ROUTES_SOURCE, (
        "found the exact broken import shape this test guards against — "
        "'.paper_trading_engine' resolves one package level too shallow."
    )


def test_reload_entry_weights_import_matches_the_correct_sibling_pattern():
    """reload_min_rr_override, in the exact same file, already uses the correct path — this
    keeps the two imports from silently drifting apart again."""
    assert "from ..services.paper_trading_engine import _default_min_rr_ratio, reload_min_rr_override" in _ROUTES_SOURCE
