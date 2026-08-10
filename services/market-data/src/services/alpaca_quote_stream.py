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

Demand-driven subscription, NOT a static universe subscribe: Alpaca's free market-data tier
caps a single connection at 30 symbols (confirmed live against this app's own real account —
error code 405 "symbol limit exceeded" when this feature's first production deploy tried to
subscribe to the whole ~120-symbol US universe upfront, which silently produced ZERO ticks for
20+ minutes with no visible symptom until the subscribe reply itself was actually read and
logged — see the fix commit's own message for the full incident). 30 is far too few to cover
"every active US stock," so this module instead tracks which symbols connected BROWSER clients
are actually watching right now (api-gateway's quote_ws.py writes a heartbeat per symbol into
the shared "stockai:quotes:demand" Redis sorted set, score = last-seen unix timestamp) and
re-subscribes to just the top _MAX_SYMBOLS_PER_CONNECTION most-recently-seen symbols on a fixed
poll interval — never the static "every active stock" list this module started with.

US-only (Alpaca has no Hong Kong market data at any tier) — HK symbols keep the existing 60s
polling via GET /stocks/latest_prices unconditionally; this module never touches that path.
Fails open at every layer: no credentials configured, an auth failure, a network drop, or the
whole task never starting must never affect any other market-data functionality — this is
purely additive on top of the existing polling-based price infrastructure, never a replacement.
"""
from __future__ import annotations

import asyncio
import json
import time

import structlog
import websockets

from common.ai_keys import get_alpaca_credentials
from common.redis_client import get_redis

log = structlog.get_logger()

_WS_URL = "wss://stream.data.alpaca.markets/v2/iex"
_RECONNECT_BASE_DELAY = 5
_RECONNECT_MAX_DELAY = 300
_CREDENTIAL_RECHECK_INTERVAL = 60  # seconds
# Alpaca's REAL, confirmed-live free-tier per-connection symbol cap — verified directly against
# this app's own account (error 405 "symbol limit exceeded" at anything above this). NOT the
# earlier, wrong assumption of 500 that silently produced zero ticks in this feature's first
# production deploy.
_MAX_SYMBOLS_PER_CONNECTION = 30
_DEMAND_KEY = "stockai:quotes:demand"
_DEMAND_STALE_SECONDS = 30  # matches quote_ws.py's own _DEMAND_TTL_SECONDS
_DEMAND_POLL_INTERVAL = 10  # seconds — how often to re-check demand and adjust the live subscription


def _current_demand(redis_client, limit: int = _MAX_SYMBOLS_PER_CONNECTION) -> list[str]:
    """Reads the top `limit` most-recently-heartbeated symbols from the shared demand set,
    dropping anything older than _DEMAND_STALE_SECONDS (a symbol every watching client has
    since disconnected from). Fails open to an empty list — a Redis hiccup here must never
    crash the connection loop, it just means no NEW subscription changes happen this poll."""
    try:
        cutoff = time.time() - _DEMAND_STALE_SECONDS
        # ZRANGEBYSCORE ... DESC via zrevrangebyscore, capped at `limit`, newest-first so a
        # symbol right at the edge of the cap is the LEAST recently seen one dropped, not an
        # arbitrary one.
        return list(redis_client.zrevrangebyscore(_DEMAND_KEY, "+inf", cutoff, start=0, num=limit))
    except Exception as exc:
        log.warning("alpaca_quote_stream.demand_read_failed", error=str(exc))
        return []


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


async def _read_ack(ws, *, expected_msg: str) -> list[dict]:
    """Reads one control reply and returns it as a list (Alpaca sometimes wraps a single reply
    in a list, sometimes not) — shared by the connect/auth acks below."""
    reply = json.loads(await ws.recv())
    return reply if isinstance(reply, list) else [reply]


async def _apply_subscription_delta(ws, current: set[str], desired: set[str]) -> None:
    """Issues unsubscribe/subscribe messages for exactly the symbols that changed since the
    last poll — never re-sends the full desired set every time, since that would needlessly
    re-trigger Alpaca's own subscription bookkeeping for symbols that haven't changed at all."""
    to_add = sorted(desired - current)
    to_remove = sorted(current - desired)
    if to_remove:
        await ws.send(json.dumps({"action": "unsubscribe", "quotes": to_remove, "trades": to_remove}))
    if to_add:
        await ws.send(json.dumps({"action": "subscribe", "quotes": to_add, "trades": to_add}))
    if to_add or to_remove:
        log.info("alpaca_quote_stream.demand_changed", added=len(to_add), removed=len(to_remove), total=len(desired))


async def _run_once(api_key: str, secret_key: str, stop_event: asyncio.Event) -> None:
    redis_client = get_redis()

    async with websockets.connect(_WS_URL, ping_interval=30, ping_timeout=10) as ws:
        # Same pre-auth "connected" ack quirk as alpaca_source.py's own documented finding —
        # Alpaca sends this the moment the socket opens, before any auth message is sent.
        connect_replies = await _read_ack(ws, expected_msg="connected")
        if not any(r.get("T") == "success" and r.get("msg") == "connected" for r in connect_replies):
            log.warning("alpaca_quote_stream.unexpected_connect_reply", reply=connect_replies)

        await ws.send(json.dumps({"action": "auth", "key": api_key, "secret": secret_key}))
        auth_replies = await _read_ack(ws, expected_msg="authenticated")
        if not any(r.get("T") == "success" and r.get("msg") == "authenticated" for r in auth_replies):
            log.error("alpaca_quote_stream.auth_failed", reply=auth_replies)
            # Must RAISE — a bare return would look like a clean lifecycle to run_quote_stream's
            # own except block, which only resets backoff on the exception path, matching
            # alpaca_source.py's own documented fix for this exact bug class.
            raise RuntimeError(f"Alpaca auth failed: {auth_replies}")

        subscribed: set[str] = set()
        last_demand_poll = 0.0

        while not stop_event.is_set():
            loop_time = asyncio.get_running_loop().time()
            if loop_time - last_demand_poll >= _DEMAND_POLL_INTERVAL:
                last_demand_poll = loop_time
                desired = set(_current_demand(redis_client))
                if desired != subscribed:
                    await _apply_subscription_delta(ws, subscribed, desired)
                    subscribed = desired

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            messages = json.loads(raw)
            for msg in messages if isinstance(messages, list) else [messages]:
                if msg.get("T") in ("subscription", "error"):
                    # A real Alpaca subscribe/unsubscribe confirmation or rejection — log it
                    # explicitly rather than silently discarding it as an "unknown message
                    # type" in _parse_quote_message, matching this module's own established
                    # lesson from its first production deploy (a silently-rejected subscribe
                    # is otherwise indistinguishable from a healthy, quiet connection).
                    level = "warning" if msg.get("T") == "error" else "info"
                    getattr(log, level)("alpaca_quote_stream.control_message", msg=msg)
                    continue
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
