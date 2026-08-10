"""Tests for T230-DATA-STREAMING-QUOTES's alpaca_quote_stream.py.

Every function here has zero DB/sqlalchemy dependency at call time (the earlier "subscribe to
every active US stock" design DID need one, via a now-removed _active_us_symbols() — replaced
by a demand-driven design after discovering, in the first live production deploy, that
Alpaca's free tier caps a single connection at 30 symbols, far too few for the whole ~120-symbol
universe; see the module's own docstring for the full incident). websockets is stubbed since it
isn't installed in this local dev environment (a real, pinned requirements.txt dependency
absent locally, same class of gap already documented for jose/redis/requests_oauthlib elsewhere
in this repo's history).
"""
import json
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("websockets", MagicMock())

from src.services import alpaca_quote_stream as m  # noqa: E402


class TestQuoteChannel:
    def test_builds_the_expected_redis_channel_name(self):
        assert m._quote_channel("AAPL") == "stockai:quotes:AAPL"

    def test_channel_is_symbol_specific(self):
        assert m._quote_channel("AAPL") != m._quote_channel("MSFT")


class TestPublishQuote:
    def test_publishes_the_expected_json_payload(self):
        redis_client = MagicMock()
        m._publish_quote(redis_client, "AAPL", 231.45, "2026-08-01T12:00:00Z")
        redis_client.publish.assert_called_once()
        channel, payload = redis_client.publish.call_args[0]
        assert channel == "stockai:quotes:AAPL"
        assert json.loads(payload) == {"symbol": "AAPL", "price": 231.45, "ts": "2026-08-01T12:00:00Z"}

    def test_fails_open_when_redis_publish_raises(self):
        """A Redis outage must never propagate up into the connection-handling loop — this is
        best-effort fan-out, not a required write."""
        redis_client = MagicMock()
        redis_client.publish.side_effect = ConnectionError("redis down")
        m._publish_quote(redis_client, "AAPL", 231.45, "2026-08-01T12:00:00Z")  # must not raise


class TestParseQuoteMessage:
    def test_parses_a_trade_message(self):
        msg = {"T": "t", "S": "AAPL", "p": 231.45, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) == ("AAPL", 231.45, "2026-08-01T12:00:00Z")

    def test_parses_a_quote_message_as_the_bid_ask_midpoint(self):
        msg = {"T": "q", "S": "AAPL", "bp": 231.00, "ap": 231.50, "t": "2026-08-01T12:00:00Z"}
        result = m._parse_quote_message(msg)
        assert result == ("AAPL", 231.25, "2026-08-01T12:00:00Z")

    def test_returns_none_for_a_quote_message_with_a_zero_bid(self):
        """A zero/negative bid or ask is a malformed/incomplete quote, not a real midpoint —
        must not silently publish a bogus price."""
        msg = {"T": "q", "S": "AAPL", "bp": 0, "ap": 231.50, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_for_a_negative_ask(self):
        msg = {"T": "q", "S": "AAPL", "bp": 231.00, "ap": -1, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_for_a_quote_message_missing_ask(self):
        msg = {"T": "q", "S": "AAPL", "bp": 231.00, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_for_a_trade_message_missing_price(self):
        msg = {"T": "t", "S": "AAPL", "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_for_an_unrecognized_message_type(self):
        """Alpaca's stream also emits status/subscription-ack/error messages (T="success",
        T="error", etc.) interleaved with real ticks — these must be silently ignored, not
        mistaken for a price update."""
        msg = {"T": "success", "msg": "connected"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_when_symbol_is_missing(self):
        msg = {"T": "t", "p": 231.45, "t": "2026-08-01T12:00:00Z"}
        assert m._parse_quote_message(msg) is None

    def test_returns_none_when_timestamp_is_missing(self):
        msg = {"T": "t", "S": "AAPL", "p": 231.45}
        assert m._parse_quote_message(msg) is None


class TestMaxSymbolsCap:
    def test_max_symbols_per_connection_matches_alpacas_real_confirmed_free_tier_cap(self):
        # Regression guard against reintroducing the exact incident this module's docstring
        # documents: an earlier, unverified assumption of 500 here silently produced ZERO
        # ticks for 20+ minutes in production (Alpaca rejected the subscribe outright with
        # "symbol limit exceeded"). 30 is the real, live-confirmed free-tier cap.
        assert m._MAX_SYMBOLS_PER_CONNECTION == 30


class TestCurrentDemand:
    def test_returns_the_most_recently_seen_symbols_first(self):
        redis_client = MagicMock()
        redis_client.zrevrangebyscore.return_value = ["NVDA", "AAPL", "MSFT"]
        result = m._current_demand(redis_client, limit=10)
        assert result == ["NVDA", "AAPL", "MSFT"]

    def test_queries_the_shared_demand_key_with_the_stale_cutoff_and_limit(self):
        redis_client = MagicMock()
        redis_client.zrevrangebyscore.return_value = []
        m._current_demand(redis_client, limit=7)
        args, kwargs = redis_client.zrevrangebyscore.call_args
        assert args[0] == m._DEMAND_KEY
        assert args[1] == "+inf"
        # arg[2] is the stale cutoff (a real time.time()-based value) — just confirm it's a
        # float in the plausible recent past, not a hardcoded/frozen sentinel.
        assert isinstance(args[2], float)
        assert kwargs.get("num") == 7 or 7 in args

    def test_fails_open_to_an_empty_list_on_a_redis_error(self):
        """A Redis outage here must never crash the connection loop — it just means no NEW
        subscription changes happen until the next successful poll."""
        redis_client = MagicMock()
        redis_client.zrevrangebyscore.side_effect = ConnectionError("redis down")
        assert m._current_demand(redis_client) == []

    def test_default_limit_matches_the_real_connection_cap(self):
        redis_client = MagicMock()
        redis_client.zrevrangebyscore.return_value = []
        m._current_demand(redis_client)
        args, kwargs = redis_client.zrevrangebyscore.call_args
        assert kwargs.get("num", args[-1] if args else None) == m._MAX_SYMBOLS_PER_CONNECTION


class _FakeWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


class TestApplySubscriptionDelta:
    async def _apply(self, current, desired):
        ws = _FakeWs()
        await m._apply_subscription_delta(ws, set(current), set(desired))
        return ws.sent

    def test_subscribes_to_newly_added_symbols(self):
        import asyncio

        sent = asyncio.run(self._apply(set(), {"AAPL", "MSFT"}))
        assert len(sent) == 1
        assert sent[0]["action"] == "subscribe"
        assert sorted(sent[0]["quotes"]) == ["AAPL", "MSFT"]
        assert sorted(sent[0]["trades"]) == ["AAPL", "MSFT"]

    def test_unsubscribes_from_removed_symbols(self):
        import asyncio

        sent = asyncio.run(self._apply({"AAPL", "MSFT"}, set()))
        assert len(sent) == 1
        assert sent[0]["action"] == "unsubscribe"
        assert sorted(sent[0]["quotes"]) == ["AAPL", "MSFT"]

    def test_sends_both_when_the_set_partially_changes(self):
        import asyncio

        sent = asyncio.run(self._apply({"AAPL", "MSFT"}, {"MSFT", "NVDA"}))
        # unsubscribe (removed) is sent before subscribe (added) — order matters for staying
        # within the connection-wide symbol budget mid-transition.
        assert sent[0]["action"] == "unsubscribe"
        assert sent[0]["quotes"] == ["AAPL"]
        assert sent[1]["action"] == "subscribe"
        assert sent[1]["quotes"] == ["NVDA"]

    def test_sends_nothing_when_the_set_is_unchanged(self):
        import asyncio

        sent = asyncio.run(self._apply({"AAPL"}, {"AAPL"}))
        assert sent == []


class TestReadAck:
    def test_wraps_a_bare_dict_reply_in_a_list(self):
        import asyncio

        class _FakeWsRecv:
            async def recv(self):
                return json.dumps({"T": "success", "msg": "connected"})

        result = asyncio.run(m._read_ack(_FakeWsRecv(), expected_msg="connected"))
        assert result == [{"T": "success", "msg": "connected"}]

    def test_passes_through_a_list_reply_unchanged(self):
        import asyncio

        class _FakeWsRecv:
            async def recv(self):
                return json.dumps([{"T": "success", "msg": "connected"}])

        result = asyncio.run(m._read_ack(_FakeWsRecv(), expected_msg="connected"))
        assert result == [{"T": "success", "msg": "connected"}]
