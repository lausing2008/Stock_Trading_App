"""AUD-T409-RANKINGENGINE (2026-09-21) — routes.py's own share of the UTC-vs-ET date-boundary
bug class (docs/incidents/utc-vs-et-date-boundary.md, market-data's AUD-T409), with an extra
wrinkle: this file ranks stocks from BOTH US and HK markets, sometimes in the SAME batch (a
manual/admin POST /rankings/refresh with no `market` filter processes every active stock across
every market in one call — the scheduler itself always passes a market, per this file's own
T247-RANKINGENGINE-CROSSMARKET comment, so the real scheduled path was never actually at risk).

_persist_rankings()'s Ranking.as_of upsert key used ONE shared `date.today()` (naive UTC) for
the whole batch, regardless of each stock's own market — during the evening US bug window (8pm
EDT/7pm EST to midnight UTC), a manually-triggered mixed-market refresh would mis-date every US
row under tomorrow's ET date. Fixed with a new `_today_for_market()` helper that resolves the
correct trading day PER STOCK: America/New_York for US, Asia/Hong_Kong for HK.
"""
import pathlib
from datetime import datetime, timezone
from unittest.mock import patch

from src.api.routes import _today_for_market

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
).read_text()


class _FrozenDatetime(datetime):
    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


def _frozen_at(utc_iso: str):
    frozen = _FrozenDatetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc)
    _FrozenDatetime._frozen = frozen
    return patch("src.api.routes.datetime", _FrozenDatetime)


# ── _today_for_market() itself ──────────────────────────────────────────────────────────────

def test_us_market_resolves_via_america_new_york():
    """2026-09-18 20:40 ET == 2026-09-19 00:40 UTC — a naive UTC read would say Sept 19; the
    correct US trading-day answer is Sept 18."""
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_for_market("US").isoformat() == "2026-09-18"


def test_hk_market_resolves_via_asia_hong_kong():
    """At the SAME instant (2026-09-19 00:40 UTC), Hong Kong is 8 hours ahead: 08:40 HKT on
    Sept 19 — a genuinely different calendar day from the US answer above, confirming this
    isn't just reusing the ET helper under a new name."""
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_for_market("HK").isoformat() == "2026-09-19"


def test_accepts_an_enum_like_object_with_a_value_attribute():
    """Stock.market is a real enum elsewhere in this file (`stock.market.value`) — the helper
    must accept that shape directly, not just a plain string, since callers pass `stock.market`."""
    class _MarketEnum:
        value = "HK"
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_for_market(_MarketEnum()).isoformat() == "2026-09-19"


def test_is_case_insensitive():
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_for_market("hk").isoformat() == "2026-09-19"


def test_defaults_to_us_for_any_non_hk_value():
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_for_market("US").isoformat() == _today_for_market("CN").isoformat()


# ── _persist_rankings() resolves per-stock, not once for the whole batch ───────────────────

def _persist_rankings_body() -> str:
    """The last function in the file — no trailing `\\ndef ` to bound against."""
    start = _SOURCE.index("def _persist_rankings(")
    return _SOURCE[start:]


def test_persist_rankings_no_longer_computes_today_once_for_the_whole_batch():
    body = _persist_rankings_body()
    assert "today = date.today()" not in body


def test_persist_rankings_resolves_today_per_stock_inside_the_loop():
    body = _persist_rankings_body()
    stock_check_idx = body.index("if not stock:\n                        continue")
    today_idx = body.index("today = _today_for_market(stock.market)", stock_check_idx)
    as_of_idx = body.index('"as_of": today', today_idx)
    assert stock_check_idx < today_idx < as_of_idx


def test_persist_rankings_uses_today_for_both_the_upsert_key_and_the_skip_diagnostic():
    body = _persist_rankings_body()
    assert '"as_of": today.isoformat(),' in body
    assert '"as_of": today,' in body


# ── _leaderboard_live()'s display label ─────────────────────────────────────────────────────

def test_leaderboard_live_as_of_label_uses_today_for_market():
    start = _SOURCE.index("def _leaderboard_live(")
    end = _SOURCE.index("\n\ndef ", start + 10)
    body = _SOURCE[start:end]
    assert 'str(_today_for_market(market or "US"))' in body
    assert "str(date.today())" not in body


# ── Regression guard: wide lookback windows and the empty-result fallback were NOT swept ────

def test_lookback_window_cutoffs_are_deliberately_left_on_naive_date_today():
    """These were classified LIKELY FINE by the triage — a blanket find-and-replace would
    have been scope creep here."""
    assert "since = date.today() - timedelta(days=lookback * 2)" in _SOURCE
    assert "_cutoff = date.today() - timedelta(days=60)" in _SOURCE
    assert "_screen_cutoff = date.today() - timedelta(days=60)" in _SOURCE


def test_empty_rows_fallback_default_is_deliberately_left_on_naive_date_today():
    """`max((row[1].as_of for row in rows), default=date.today())` only ever fires when
    `rows` is empty — it can never override real persisted data, so it was classified LIKELY
    FINE rather than a mis-dating bug."""
    assert "default=date.today())" in _SOURCE
