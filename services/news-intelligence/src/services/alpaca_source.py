"""Alpaca real-time news WebSocket client — the only PUSH-based source of the four (RSS/EDGAR
are all polled). Genuinely new infrastructure for this codebase: confirmed via a direct repo-
wide grep that zero WebSocket client code exists anywhere else (T230-DATA-STREAMING-QUOTES
documents this as a known, deferred gap for price data specifically) — this is the first.

Protocol (Alpaca's documented v1beta1 news stream): connect to
wss://stream.data.alpaca.markets/v1beta1/news, send an `{"action":"auth","key":...,"secret":...}`
message, wait for a `{"T":"success","msg":"authenticated"}` reply, then send
`{"action":"subscribe","news":["*"]}` to receive every news item across all symbols (Alpaca's
news stream has no per-symbol subscription cost — items already arrive natively ticker-tagged
via each message's own `symbols` field, so subscribing to "*" and filtering downstream is
simpler and no more expensive than subscribing to a fixed symbol list that would need constant
maintenance as the app's own tracked universe changes).

Runs as a long-lived background task (started once at service startup, not a per-cycle poll
like the RSS/EDGAR sources) with automatic reconnect-with-backoff on any disconnect — a real
production WebSocket client needs this; Alpaca's own docs note connections can drop and must
be re-established by the client.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog
import websockets

from common.ai_keys import get_alpaca_credentials

from .storage import persist_news_items

log = structlog.get_logger()

_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
_RECONNECT_BASE_DELAY = 5
_RECONNECT_MAX_DELAY = 300
_CREDENTIAL_RECHECK_INTERVAL = 60  # seconds — how often to re-check Redis when no key is set


def _parse_news_message(msg: dict) -> dict | None:
    """Alpaca's own news message shape: {"T":"n","id":...,"headline":...,"summary":...,
    "author":...,"created_at":"2026-...Z","updated_at":...,"url":...,"symbols":["AAPL",...]}."""
    headline = msg.get("headline")
    if not headline:
        return None
    created = msg.get("created_at")
    try:
        published_at = datetime.fromisoformat(created.replace("Z", "+00:00")) if created else datetime.now(timezone.utc)
    except Exception:
        published_at = datetime.now(timezone.utc)
    # Alpaca's own symbols are already this app's convention for US tickers (bare, no suffix);
    # HK symbols (".HK" suffix) are never returned by Alpaca (US-market-only data source), so
    # no suffix translation is needed here.
    symbols = [s for s in (msg.get("symbols") or []) if isinstance(s, str)]
    return {"headline": headline, "url": msg.get("url"), "published_at": published_at, "symbols": symbols}


async def _run_once(api_key: str, secret_key: str, stop_event: asyncio.Event) -> None:
    async with websockets.connect(_WS_URL, ping_interval=30, ping_timeout=10) as ws:
        # Real bug found live in production: Alpaca sends {"T":"success","msg":"connected"} the
        # MOMENT the socket opens — before any auth message is even sent. The original code
        # skipped consuming this, so its one post-auth recv() actually read this stale
        # already-queued "connected" ack instead of the real auth reply, misreported as an auth
        # failure on every single connection attempt (confirmed via production logs: "reply":
        # [{"T": "success", "msg": "connected"}], logged as alpaca_source.auth_failed, in a
        # tight ~5s reconnect loop with the exponential backoff never actually growing).
        connect_ack = json.loads(await ws.recv())
        connect_replies = connect_ack if isinstance(connect_ack, list) else [connect_ack]
        if not any(r.get("T") == "success" and r.get("msg") == "connected" for r in connect_replies):
            log.warning("alpaca_source.unexpected_connect_reply", reply=connect_replies)

        await ws.send(json.dumps({"action": "auth", "key": api_key, "secret": secret_key}))
        auth_reply = json.loads(await ws.recv())
        # Alpaca replies with a LIST of one or more control messages, not a bare dict.
        replies = auth_reply if isinstance(auth_reply, list) else [auth_reply]
        if not any(r.get("T") == "success" and r.get("msg") == "authenticated" for r in replies):
            log.error("alpaca_source.auth_failed", reply=replies)
            # Must RAISE, not silently return — a bare return looks identical to a clean
            # connection lifecycle to run_alpaca_stream()'s own try/except, which only resets
            # the reconnect backoff to its base delay on the NON-exception path. A real auth
            # failure (bad/revoked key) should escalate the backoff like any other failure, not
            # retry every 5s forever — this was a second bug in the same live incident (the
            # logged backoff never grew past its base delay across 50+ consecutive attempts).
            raise RuntimeError(f"Alpaca auth failed: {replies}")

        await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
        log.info("alpaca_source.subscribed")

        buffer: list[dict] = []
        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                if buffer:
                    persist_news_items(buffer, source="alpaca", symbol_mode="tagged")
                    buffer.clear()
                continue
            messages = json.loads(raw)
            for msg in messages if isinstance(messages, list) else [messages]:
                if msg.get("T") != "n":
                    continue  # control/error message, not a news item
                item = _parse_news_message(msg)
                if item:
                    buffer.append(item)
            if len(buffer) >= 5:
                persist_news_items(buffer, source="alpaca", symbol_mode="tagged")
                buffer.clear()


async def run_alpaca_stream(stop_event: asyncio.Event) -> None:
    """Long-lived task: reconnects with exponential backoff on any failure/disconnect, and
    re-checks Redis for admin-configured credentials periodically so setting a key via the
    Settings page (no restart needed) picks up within _CREDENTIAL_RECHECK_INTERVAL seconds."""
    delay = _RECONNECT_BASE_DELAY
    while not stop_event.is_set():
        api_key, secret_key = get_alpaca_credentials()
        if not api_key or not secret_key:
            log.info("alpaca_source.no_credentials_configured")
            await asyncio.sleep(_CREDENTIAL_RECHECK_INTERVAL)
            continue
        try:
            log.info("alpaca_source.connecting")
            await _run_once(api_key, secret_key, stop_event)
            delay = _RECONNECT_BASE_DELAY  # reset backoff after a clean run
        except Exception as exc:
            log.warning("alpaca_source.disconnected", error=str(exc), retry_in=delay)
        if not stop_event.is_set():
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY)
