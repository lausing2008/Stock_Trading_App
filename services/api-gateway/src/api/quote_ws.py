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
"""
from __future__ import annotations

import asyncio
import threading
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
_MAX_SYMBOLS_PER_CLIENT = 50  # a generous cap — a dashboard/watchlist page, not the whole universe


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
    except Exception as exc:
        log.warning("quote_ws.subscribe_failed", error=str(exc))
        await websocket.close(code=1011)  # 1011 = internal error
        return

    listener_future = loop.run_in_executor(_ws_executor, _pubsub_listen_blocking, pubsub, loop, queue, stop)

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
        try:
            pubsub.unsubscribe(*channels)
        except Exception:
            pass
        listener_future.cancel()
        log.info("quote_ws.disconnected")
