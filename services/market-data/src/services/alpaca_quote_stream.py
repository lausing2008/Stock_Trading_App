"""T230-DATA-STREAMING-QUOTES: real-time US equity quote streaming via Alpaca's free IEX
market-data WebSocket (wss://stream.data.alpaca.markets/v2/iex) — distinct from
news-intelligence's own Alpaca WebSocket client (which streams NEWS on a different URL path,
/v1beta1/news). Same admin-configured credential pair (get_alpaca_credentials(), shared across
Alpaca's news/market-data/trading APIs) — reused directly, not a new credential slot.

Mirrors services/news-intelligence/src/services/alpaca_source.py's exact connection/auth/
reconnect pattern (that file's own module docstring already documents this codebase's real,
hard-won lessons about Alpaca's protocol quirks — the pre-auth "connected" ack that must be
consumed before the real auth reply, the list-vs-dict reply shape, and the need to RAISE on
auth failure rather than silently return so the reconnect backoff actually escalates). Ticks
are published to Redis pub/sub (channel "stockai:quotes:{SYMBOL}") for api-gateway's WebSocket
route to relay to subscribed frontend clients — this is a genuinely NEW mechanism for this
codebase (grepped: zero existing pub/sub usage anywhere before this).

US-only (Alpaca has no Hong Kong market data at any tier) — HK symbols keep the existing 60s
polling via GET /stocks/latest_prices unconditionally; this module never touches that path.
Fails open at every layer: no credentials configured, an auth failure, a network drop, or the
whole task never starting must never affect any other market-data functionality — this is
purely additive on top of the existing polling-based price infrastructure, never a replacement.
"""
from __future__ import annotations

import asyncio
import json

import structlog
import websockets

from common.ai_keys import get_alpaca_credentials
from common.redis_client import get_redis

from db import SessionLocal, Stock
from sqlalchemy import select

log = structlog.get_logger()

_WS_URL = "wss://stream.data.alpaca.markets/v2/iex"
_RECONNECT_BASE_DELAY = 5
_RECONNECT_MAX_DELAY = 300
_CREDENTIAL_RECHECK_INTERVAL = 60  # seconds
_MAX_SYMBOLS_PER_CONNECTION = 500  # Alpaca's own documented per-connection subscription cap


def _active_us_symbols() -> list[str]:
    """Every active, non-delisted US stock — HK is deliberately excluded (Alpaca has no HK
    coverage at any tier). Stock.active.is_(True) does NOT by itself exclude a confirmed
    delisting (a delisted stock stays active=True forever — see BUG-DELISTED-GENERATION-BLIND);
    Stock.delisted.is_(False) is required alongside it, matching every other universe/generation
    query in this codebase fixed under that same bug class."""
    with SessionLocal() as session:
        return list(
            session.execute(
                select(Stock.symbol).where(
                    Stock.active.is_(True),
                    Stock.delisted.is_(False),
                    Stock.market == "US",
                )
            ).scalars()
        )


def _quote_channel(symbol: str) -> str:
    return f"stockai:quotes:{symbol}"


def _publish_quote(redis_client, symbol: str, price: float, ts: str) -> None:
    """Fails open — a Redis publish failure must never crash the WebSocket read loop; the
    tick is simply lost (the next one arrives within seconds regardless)."""
    try:
        redis_client.publish(_quote_channel(symbol), json.dumps({"symbol": symbol, "price": price, "ts": ts}))
    except Exception as exc:
        log.warning("alpaca_quote_stream.publish_failed", symbol=symbol, error=str(exc))


def _parse_quote_message(msg: dict) -> tuple[str, float, str] | None:
    """Alpaca's real-time quote message shape (T="q"): {"T":"q","S":"AAPL","bp":..,"ap":..,
    "bs":..,"as":..,"t":"2026-...Z", ...}. Uses the midpoint of bid/ask as the displayed price
    (matches this app's own established convention elsewhere for a quote-only feed with no
    trade price — see the BrokerQuote dataclass's own last_price field, which for a broker
    quote endpoint is the actual last trade; here Alpaca's IEX quote stream gives bid/ask only,
    not last-trade, on the free tier). Falls back to the trade message shape (T="t", "p" field)
    for symbols where a real trade print arrives instead of/alongside a quote update."""
    t = msg.get("T")
    symbol = msg.get("S")
    ts = msg.get("t")
    if not symbol or not ts:
        return None
    if t == "t":  # real trade print — most direct, use its own price
        price = msg.get("p")
        if price is None:
            return None
        return symbol, float(price), ts
    if t == "q":  # quote update — use bid/ask midpoint
        bid, ask = msg.get("bp"), msg.get("ap")
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return None
        return symbol, (float(bid) + float(ask)) / 2, ts
    return None  # control/other message type


async def _run_once(api_key: str, secret_key: str, stop_event: asyncio.Event) -> None:
    redis_client = get_redis()
    symbols = _active_us_symbols()[:_MAX_SYMBOLS_PER_CONNECTION]
    if not symbols:
        log.info("alpaca_quote_stream.no_symbols")
        return

    async with websockets.connect(_WS_URL, ping_interval=30, ping_timeout=10) as ws:
        # Same pre-auth "connected" ack quirk as alpaca_source.py's own documented finding —
        # Alpaca sends this the moment the socket opens, before any auth message is sent.
        connect_ack = json.loads(await ws.recv())
        connect_replies = connect_ack if isinstance(connect_ack, list) else [connect_ack]
        if not any(r.get("T") == "success" and r.get("msg") == "connected" for r in connect_replies):
            log.warning("alpaca_quote_stream.unexpected_connect_reply", reply=connect_replies)

        await ws.send(json.dumps({"action": "auth", "key": api_key, "secret": secret_key}))
        auth_reply = json.loads(await ws.recv())
        replies = auth_reply if isinstance(auth_reply, list) else [auth_reply]
        if not any(r.get("T") == "success" and r.get("msg") == "authenticated" for r in replies):
            log.error("alpaca_quote_stream.auth_failed", reply=replies)
            # Must RAISE — a bare return would look like a clean lifecycle to run_quote_stream's
            # own except block, which only resets backoff on the exception path, matching
            # alpaca_source.py's own documented fix for this exact bug class.
            raise RuntimeError(f"Alpaca auth failed: {replies}")

        await ws.send(json.dumps({"action": "subscribe", "quotes": symbols, "trades": symbols}))
        log.info("alpaca_quote_stream.subscribed", count=len(symbols))

        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            messages = json.loads(raw)
            for msg in messages if isinstance(messages, list) else [messages]:
                parsed = _parse_quote_message(msg)
                if parsed:
                    symbol, price, ts = parsed
                    _publish_quote(redis_client, symbol, price, ts)


async def run_quote_stream(stop_event: asyncio.Event) -> None:
    """Long-lived task: reconnects with exponential backoff on any failure/disconnect, and
    re-checks Redis for admin-configured credentials periodically (same convention as
    alpaca_source.py's run_alpaca_stream — a key set via the Settings page picks up within
    _CREDENTIAL_RECHECK_INTERVAL seconds, no restart needed)."""
    delay = _RECONNECT_BASE_DELAY
    while not stop_event.is_set():
        api_key, secret_key = get_alpaca_credentials()
        if not api_key or not secret_key:
            log.info("alpaca_quote_stream.no_credentials_configured")
            await asyncio.sleep(_CREDENTIAL_RECHECK_INTERVAL)
            continue
        try:
            log.info("alpaca_quote_stream.connecting")
            await _run_once(api_key, secret_key, stop_event)
            delay = _RECONNECT_BASE_DELAY  # reset backoff after a clean run
        except Exception as exc:
            log.warning("alpaca_quote_stream.disconnected", error=str(exc), retry_in=delay)
        if not stop_event.is_set():
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX_DELAY)
