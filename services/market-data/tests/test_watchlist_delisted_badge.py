"""Tests for T260-DELISTED-BADGE — WatchlistItemOut/_item_out() surface Stock.delisted to the
watchlist UI. Deliberately informational only (no auto-removal) — see the design discussion
in CLAUDE.md's aud14-survivorship entry for why silent auto-removal was rejected for a
terminal/irreversible condition.

watchlist.py imports `from db import ...` (stubbed wholesale by conftest.py as a MagicMock),
but _item_out() itself is a pure function taking real WatchlistItem/Stock-shaped objects —
plain SimpleNamespace stand-ins work fine here without needing the real ORM models.
"""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parents[1].as_posix())

from src.api.watchlist import _item_out  # noqa: E402


def _stock(**overrides):
    defaults = dict(
        symbol="AAPL", name="Apple Inc", name_zh=None, market="US", exchange="NASDAQ",
        sector="Technology", currency="USD", delisted=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _item(**overrides):
    defaults = dict(added_at=datetime(2026, 1, 1, tzinfo=timezone.utc), note=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestItemOutDelistedField:
    def test_active_stock_reports_delisted_false(self):
        out = _item_out(_item(), _stock(delisted=False))
        assert out.delisted is False

    def test_delisted_stock_reports_delisted_true(self):
        out = _item_out(_item(), _stock(delisted=True))
        assert out.delisted is True

    def test_other_fields_are_unaffected(self):
        out = _item_out(_item(note="watching for a breakout"), _stock(symbol="MSFT", delisted=True))
        assert out.symbol == "MSFT"
        assert out.note == "watching for a breakout"
        assert out.delisted is True
