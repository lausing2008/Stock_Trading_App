"""Tests for T230-DATA-STREAMING-QUOTES's quote_ws.py — the WebSocket relay route for
real-time quotes. Imports the real module directly (matches test_proxy.py's established
convention — jose.jwt is real, only common.config/redis are stubbed by conftest.py), so
_validate_token exercises real JWT encode/decode + the real jti-blacklist-check path, not a
re-implementation of proxy.py's own auth logic.
"""
import asyncio
import threading

from jose import jwt as _jwt

from src.api import quote_ws as m

_JWT_SECRET = "test-secret-not-a-real-key"


def _make_token(*, jti: str = "test-jti-1") -> str:
    return _jwt.encode({"sub": "testuser", "jti": jti}, _JWT_SECRET, algorithm="HS256")


class TestValidateToken:
    def test_accepts_a_valid_non_blacklisted_token(self, monkeypatch):
        monkeypatch.setattr(m, "_is_blacklisted", lambda jti: False)
        assert m._validate_token(_make_token()) is True

    def test_rejects_an_empty_token(self):
        assert m._validate_token("") is False

    def test_rejects_a_malformed_token(self):
        assert m._validate_token("not.a.real.jwt") is False

    def test_rejects_a_token_signed_with_the_wrong_secret(self):
        bad_token = _jwt.encode({"sub": "testuser", "jti": "x"}, "wrong-secret", algorithm="HS256")
        assert m._validate_token(bad_token) is False

    def test_rejects_a_token_with_no_jti(self):
        # A token missing a jti can never be blacklist-checked — must be rejected outright,
        # not silently treated as "not blacklisted, so valid".
        token = _jwt.encode({"sub": "testuser"}, _JWT_SECRET, algorithm="HS256")
        assert m._validate_token(token) is False

    def test_rejects_a_blacklisted_token(self, monkeypatch):
        monkeypatch.setattr(m, "_is_blacklisted", lambda jti: True)
        assert m._validate_token(_make_token()) is False

    def test_calls_is_blacklisted_with_the_tokens_own_jti(self, monkeypatch):
        seen = []
        monkeypatch.setattr(m, "_is_blacklisted", lambda jti: (seen.append(jti), False)[1])
        m._validate_token(_make_token(jti="specific-jti-42"))
        assert seen == ["specific-jti-42"]


class TestSymbolCap:
    def test_max_symbols_per_client_is_a_generous_but_bounded_cap(self):
        assert 0 < m._MAX_SYMBOLS_PER_CLIENT <= 500


class _FakeMessage(dict):
    pass


class _FakePubsub:
    """Stands in for a real redis-py PubSub object: .listen() yields a fixed sequence of
    messages, then blocks forever (simulating a live connection) unless stop is set — mirrors
    the real listen() generator's blocking behavior closely enough to exercise
    _pubsub_listen_blocking's own filtering/threading logic."""

    def __init__(self, messages, stop_event: threading.Event):
        self._messages = list(messages)
        self._stop_event = stop_event
        self.closed = False

    def listen(self):
        for msg in self._messages:
            if self._stop_event.is_set():
                return
            yield msg
        # After exhausting the fixture messages, block until stop is set (matches a real
        # long-lived connection with no more traffic) instead of returning immediately, so a
        # test can assert on "nothing more was queued" without a race against generator exit.
        self._stop_event.wait(timeout=2.0)

    def close(self):
        self.closed = True


class TestPubsubListenBlocking:
    def _run(self, messages, *, stop_immediately=False):
        stop = threading.Event()
        pubsub = _FakePubsub(messages, stop)
        loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        if stop_immediately:
            stop.set()

        thread = threading.Thread(
            target=m._pubsub_listen_blocking, args=(pubsub, loop, queue, stop), daemon=True,
        )
        thread.start()

        async def _drain():
            # Give the listener thread a moment to enqueue everything it's going to enqueue.
            await asyncio.sleep(0.2)
            items = []
            while not queue.empty():
                items.append(queue.get_nowait())
            return items

        try:
            items = loop.run_until_complete(_drain())
        finally:
            stop.set()
            thread.join(timeout=2.0)
            loop.close()
        return items, pubsub

    def test_forwards_real_message_type_data_to_the_queue(self):
        items, _ = self._run([{"type": "message", "data": '{"symbol":"AAPL","price":1}'}])
        assert items == ['{"symbol":"AAPL","price":1}']

    def test_ignores_subscribe_confirmation_messages(self):
        # redis-py's pubsub.listen() also yields a "subscribe" ack message right after
        # subscribing — this must never be forwarded to the browser as if it were a real tick.
        items, _ = self._run([
            {"type": "subscribe", "channel": "stockai:quotes:AAPL", "data": 1},
            {"type": "message", "data": '{"symbol":"AAPL","price":1}'},
        ])
        assert items == ['{"symbol":"AAPL","price":1}']

    def test_forwards_multiple_real_messages_in_order(self):
        items, _ = self._run([
            {"type": "message", "data": "tick-1"},
            {"type": "message", "data": "tick-2"},
        ])
        assert items == ["tick-1", "tick-2"]

    def test_closes_the_pubsub_on_exit(self):
        _, pubsub = self._run([{"type": "message", "data": "tick-1"}])
        assert pubsub.closed is True

    def test_stops_promptly_when_the_stop_event_is_already_set(self):
        items, pubsub = self._run([{"type": "message", "data": "tick-1"}], stop_immediately=True)
        assert items == []
        assert pubsub.closed is True
