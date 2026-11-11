"""AUD-OPT-IVRANK-DESK (2026-11-10): the stock-page options desk receives a dated IV rank."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from src.api import routes
from src.services import unusual_whales as UW

TODAY = date(2026, 11, 10)


def _rank(value, as_of):
    with patch.object(UW, "is_available", lambda: True), \
         patch.object(UW, "get_iv_rank", lambda s: SimpleNamespace(iv_rank_1y=value, as_of_date=as_of, volatility=0.3, close=1.0)):
        return routes._desk_iv_rank("XYZ", TODAY)


def test_a_recent_reading_is_used():
    assert _rank(72.5, "2026-11-07") == (72.5, "2026-11-07", "ok")


def test_a_stale_future_or_out_of_range_reading_is_not_used():
    assert _rank(72.5, "2026-10-30")[0] is None and _rank(72.5, "2026-10-30")[2] == "stale"
    assert _rank(72.5, "2026-11-12")[2] == "stale"
    assert _rank(140.0, "2026-11-09")[2] == "out_of_range"
    assert _rank(float("nan"), "2026-11-09")[2] == "out_of_range"


def test_missing_or_undated_or_failed_is_unavailable():
    assert _rank(None, "2026-11-09") == (None, None, "unavailable")
    assert _rank(50.0, None)[2] == "undated"
    with patch.object(UW, "is_available", lambda: True), patch.object(UW, "get_iv_rank", lambda s: (_ for _ in ()).throw(RuntimeError())):
        assert routes._desk_iv_rank("XYZ", TODAY) == (None, None, "unavailable")
    with patch.object(UW, "is_available", lambda: False):
        assert routes._desk_iv_rank("XYZ", TODAY)[2] == "provider_disabled"


def test_the_route_passes_it_to_the_builder():
    import inspect
    body = inspect.getsource(routes.get_options_game_plan)
    assert "iv_rank, iv_rank_as_of, iv_rank_status = _desk_iv_rank(sym, today)" in body
    assert "iv_rank=iv_rank," in body
