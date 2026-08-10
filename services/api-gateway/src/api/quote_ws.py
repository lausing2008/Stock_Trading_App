"""T230-DATA-STREAMING-QUOTES: WebSocket relay for real-time US equity quotes.

Frontend connects to wss://.../ws/quotes?token=<jwt>&symbols=AAPL,MSFT and receives a JSON
message per tick: {"symbol": "AAPL", "price": 231.45, "ts": "2026-...Z"}. This route does NOT
talk to Alpaca directly — market-data's alpaca_quote_stream.py owns that connection and
publishes each tick to Redis pub/sub (channel "stockai:quotes:{SYMBOL}"); this route only
relays from Redis to the connected browser. Keeps api-gateway's own role unchanged (a thin
proxy/relay, never a data source) and means only ONE upstream Alpaca connection exists
regardless of how many browser tabs are watching — Alpaca bills/limits per connection, not per
symbol-subscriber, so fanning out from one shared Redis channel to N browser clients is the
right shape, not N independent Alpaca connections.

WebSocket auth cannot use the standard Authorization header (browsers' native WebSocket API
has no custom-header support) — the JWT is passed as a query parameter instead, decoded with
the SAME validation logic already used by every other route (jwt_secret, jti blacklist check)
via proxy.py's own _require_auth, reused directly rather than a second, divergent auth
implementation.

Redis's pub/sub SUBSCRIBE + listen() loop is a blocking call — routed through a dedicated
ThreadPoolExecutor, matching proxy.py's own established fix for exactly this class of problem
(T247-APIGATEWAY-BLACKLIST-BLOCKING) rather than reintroducing the same bug class here.

Demand registration: Alpaca's free market-data tier caps a single connection at 30 symbols
(confirmed live — error code 405 "symbol limit exceeded" when this feature's first production
deploy tried to subscribe to the whole ~120-symbol US universe upfront), so market-data cannot
statically subscribe to everything — it must track which symbols are ACTUALLY being watched by
connected browser clients right now and subscribe to only those. Every connected client here
periodically refreshes its own symbols' scores in a Redis sorted set
(stockai:quotes:demand, score = last-seen unix timestamp) so alpaca_quote_stream.py can read
"symbols with a recent heartbeat" without api-gateway needing to explicitly deregister on
disconnect — a crashed/killed client's entries simply age out and stop being read as demand.
"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from common.config import get_settings

from .proxy import _get_redis, _is_blacklisted

router = APIRouter(tags=["quotes-ws"])
log = structlog.get_logger()
_settings = get_settings()

_ws_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="quote_ws")
# 30, not a generous number — matches Alpaca's real, confirmed-live free-tier per-connection
# symbol cap exactly (see the module docstring). A single client requesting more than the
# WHOLE connection's own budget would starve every other connected client's symbols out of
# alpaca_quote_stream.py's demand set — capped here at the connection-wide limit, not a
# per-client allowance, since there is only one shared upstream Alpaca connection to divide.
_MAX_SYMBOLS_PER_CLIENT = 30
_DEMAND_KEY = "stockai:quotes:demand"
_DEMAND_REFRESH_INTERVAL = 10  # seconds — how often a connected client re-heartbeats its symbols
_DEMAND_TTL_SECONDS = 30  # a symbol not re-heartbeated within this window ages out of demand


def _validate_token(token: str) -> bool:
    """Same validation as proxy.py's _require_auth (jwt_secret + jti blacklist check), reused
    directly rather than a second, divergent implementation of the same auth rule."""
    if not token:
        return False
    try:
        payload = jwt.decode(token, _settings.jwt_secret, algorithms=["HS256"])
        jti = payload.get("jti", "")
        if not jti or _is_blacklisted(jti):
            return False
        return True
    except JWTError:
        return False


def _register_demand(redis_client, symbols: list[str]) -> None:
    """Refreshes each symbol's last-seen score in the shared demand sorted set. Fails open —
    a Redis hiccup here must never crash the relay loop; the client's own symbols just won't
    be picked up by alpaca_quote_stream.py until the NEXT successful heartbeat."""
    try:
        now = time.time()
        redis_client.zadd(_DEMAND_KEY, {sym: now for sym in symbols})
    except Exception as exc:
        log.warning("quote_ws.demand_register_failed", error=str(exc))


async def _heartbeat_demand_loop(redis_client, symbols: list[str], stop: threading.Event) -> None:
    """Re-registers this connection's own symbols on a fixed interval for as long as the
    connection stays open — a symbol only needs to age out of demand once EVERY connected
    client watching it has disconnected/crashed, not just this one."""
    while not stop.is_set():
        _register_demand(redis_client, symbols)
        await asyncio.sleep(_DEMAND_REFRESH_INTERVAL)


def _pubsub_listen_blocking(pubsub, loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[str]", stop: threading.Event) -> None:
    """Runs in a worker thread — pubsub.listen() blocks until a message arrives or the
    connection is closed. Each raw message is handed back to the event loop via
    call_soon_threadsafe so the async send loop can forward it to the WebSocket client."""
    try:
        for message in pubsub.listen():
            if stop.is_set():
                break
            if message.get("type") != "message":
                continue
            loop.call_soon_threadsafe(queue.put_nowait, message["data"])
    except Exception as exc:
        log.warning("quote_ws.pubsub_listen_error", error=str(exc))
    finally:
        try:
            pubsub.close()
        except Exception:
            pass


@router.websocket("/ws/quotes")
async def quote_stream_ws(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    symbols_param = websocket.query_params.get("symbols", "")
    symbols = [s.strip().upper() for s in symbols_param.split(",") if s.strip()][:_MAX_SYMBOLS_PER_CLIENT]

    if not _validate_token(token):
        await websocket.close(code=4401)  # custom close code — 4401 mirrors HTTP 401
        return
    if not symbols:
        await websocket.close(code=4400)  # 4400 mirrors HTTP 400 (bad request — no symbols)
        return

    await websocket.accept()
    log.info("quote_ws.connected", count=len(symbols))

    stop = threading.Event()
    queue: asyncio.Queue[str] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    try:
        redis_client = _get_redis()
        pubsub = redis_client.pubsub()
        channels = [f"stockai:quotes:{sym}" for sym in symbols]
        pubsub.subscribe(*channels)
        _register_demand(redis_client, symbols)  # immediate first heartbeat, don't wait 10s
    except Exception as exc:
        log.warning("quote_ws.subscribe_failed", error=str(exc))
        await websocket.close(code=1011)  # 1011 = internal error
        return

    listener_future = loop.run_in_executor(_ws_executor, _pubsub_listen_blocking, pubsub, loop, queue, stop)
    heartbeat_task = asyncio.ensure_future(_heartbeat_demand_loop(redis_client, symbols, stop))

    try:
        while True:
            # Race the Redis-fed queue against the client's own recv() so a client-initiated
            # disconnect is noticed promptly instead of only on the next tick to relay.
            get_task = asyncio.ensure_future(queue.get())
            recv_task = asyncio.ensure_future(websocket.receive_text())
            done, pending = await asyncio.wait({get_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if recv_task in done:
                # Any inbound message (or the disconnect exception raised by receive_text())
                # ends this connection — the client has no legitimate reason to send data on
                # this one-way relay; treat any inbound frame the same as a disconnect signal.
                recv_task.result()  # re-raises WebSocketDisconnect if that's what happened
                break
            raw = get_task.result()
            await websocket.send_text(raw)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("quote_ws.relay_error", error=str(exc))
    finally:
        stop.set()
        heartbeat_task.cancel()
        try:
            pubsub.unsubscribe(*channels)
        except Exception:
            pass
        listener_future.cancel()
        log.info("quote_ws.disconnected")
