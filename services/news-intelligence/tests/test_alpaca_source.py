"""Tests for alpaca_source.py — _parse_news_message()'s pure message-parsing logic, plus a real
integration test of _run_once()'s connection handshake using a fake WebSocket that mimics
Alpaca's actual protocol sequence exactly.

The handshake test exists because a real bug shipped and ran in production for several minutes
before being caught: Alpaca sends {"T":"success","msg":"connected"} the MOMENT the socket opens
— before any auth message is even sent. The original code sent auth then did a single recv(),
which actually read this stale, already-queued "connected" ack instead of the real auth reply,
misreporting a genuine, successful connection as an auth failure on literally every attempt
(confirmed live: production logs showed a tight ~5s reconnect loop, "reply": [{"T": "success",
"msg": "connected"}], logged as alpaca_source.auth_failed, for over a minute straight after the
user configured real, valid credentials). A prior version of this file's own docstring called
_run_once "integration-level WebSocket I/O not covered by unit tests" — that was wrong: the
handshake SEQUENCING is exactly the kind of pure, mockable logic a fake transport object can
test directly, and doing so here would have caught this bug before it ever reached production.
"""
import asyncio
import json
import sys
from datetime import timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services import alpaca_source  # noqa: E402


class _FakeWebSocket:
    """A fake WebSocket transport whose .recv() replays a fixed script of server messages, in
    order, and whose .send() just records what was sent — enough to exercise _run_once()'s real
    handshake/message-loop logic without a real network connection."""

    def __init__(self, script):
        self._script = list(script)
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        if not self._script:
            raise asyncio.TimeoutError()  # end of script — let the caller's own timeout handle it
        return json.dumps(self._script.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_connect(monkeypatch, script):
    fake_ws = _FakeWebSocket(script)
    monkeypatch.setattr(alpaca_source.websockets, "connect", lambda *a, **kw: fake_ws)
    return fake_ws


class TestParseNewsMessage:
    def test_parses_a_real_shaped_alpaca_news_message(self):
        msg = {
            "T": "n", "id": 12345,
            "headline": "Acme Corp Reports Record Quarterly Revenue",
            "summary": "...", "author": "Alpaca",
            "created_at": "2026-07-27T22:24:00Z",
            "updated_at": "2026-07-27T22:24:00Z",
            "url": "https://example.com/acme-earnings",
            "symbols": ["ACME", "MSFT"],
        }
        item = alpaca_source._parse_news_message(msg)
        assert item is not None
        assert item["headline"] == "Acme Corp Reports Record Quarterly Revenue"
        assert item["url"] == "https://example.com/acme-earnings"
        assert item["symbols"] == ["ACME", "MSFT"]
        assert item["published_at"].tzinfo == timezone.utc

    def test_missing_headline_returns_none(self):
        assert alpaca_source._parse_news_message({"T": "n", "symbols": ["AAPL"]}) is None

    def test_malformed_created_at_falls_back_to_now_not_crash(self):
        msg = {"headline": "X", "created_at": "not-a-real-timestamp", "symbols": []}
        item = alpaca_source._parse_news_message(msg)
        assert item is not None
        assert item["headline"] == "X"

    def test_missing_symbols_defaults_to_empty_list(self):
        item = alpaca_source._parse_news_message({"headline": "X", "created_at": "2026-07-27T22:24:00Z"})
        assert item["symbols"] == []

    def test_non_string_entries_in_symbols_are_filtered(self):
        msg = {"headline": "X", "created_at": "2026-07-27T22:24:00Z", "symbols": ["AAPL", None, 123, "MSFT"]}
        item = alpaca_source._parse_news_message(msg)
        assert item["symbols"] == ["AAPL", "MSFT"]


class TestRunOnceHandshake:
    def test_real_alpaca_handshake_sequence_authenticates_successfully(self, monkeypatch):
        """The exact real-world sequence: connect-ack, THEN auth reply, THEN subscribe — must
        not be misread as an auth failure (the bug that shipped to production)."""
        script = [
            {"T": "success", "msg": "connected"},
            [{"T": "success", "msg": "authenticated"}],
        ]
        fake_ws = _patch_connect(monkeypatch, script)
        stop_event = asyncio.Event()
        stop_event.set()  # stop immediately after the handshake — we only care about the handshake here
        asyncio.run(alpaca_source._run_once("key", "secret", stop_event))
        assert {"action": "auth", "key": "key", "secret": "secret"} in fake_ws.sent
        assert {"action": "subscribe", "news": ["*"]} in fake_ws.sent

    def test_genuine_auth_failure_raises_instead_of_silently_returning(self, monkeypatch):
        """The second bug in the same incident: a silent `return` on auth failure looked
        identical to a clean disconnect to run_alpaca_stream()'s own reconnect-backoff logic,
        so the backoff never actually grew across repeated real auth failures. _run_once() must
        raise so the caller's exception path (which grows the backoff) is exercised."""
        script = [
            {"T": "success", "msg": "connected"},
            [{"T": "error", "msg": "invalid credentials"}],
        ]
        _patch_connect(monkeypatch, script)
        stop_event = asyncio.Event()
        try:
            asyncio.run(alpaca_source._run_once("bad-key", "bad-secret", stop_event))
            assert False, "expected _run_once to raise on auth failure"
        except RuntimeError as exc:
            assert "auth failed" in str(exc).lower()

    def test_missing_connect_ack_still_proceeds_to_auth(self, monkeypatch):
        """If Alpaca's protocol ever changes and the connect-ack is absent/different, the code
        must still attempt auth rather than getting stuck — a warning is logged, not a crash."""
        script = [
            {"T": "error", "msg": "something unexpected"},
            [{"T": "success", "msg": "authenticated"}],
        ]
        fake_ws = _patch_connect(monkeypatch, script)
        stop_event = asyncio.Event()
        stop_event.set()
        asyncio.run(alpaca_source._run_once("key", "secret", stop_event))
        assert {"action": "auth", "key": "key", "secret": "secret"} in fake_ws.sent
