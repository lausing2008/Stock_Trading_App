"""Broker connection CRUD + E*Trade OAuth flow endpoints.

Endpoints:
  GET  /broker/connections             — list my connections
  POST /broker/connections             — create new connection
  PUT  /broker/connections/{id}        — update name / account_id
  DELETE /broker/connections/{id}      — delete
  POST /broker/connections/{id}/oauth/start    — E*Trade OAuth step 1 (returns authorize URL)
  POST /broker/connections/{id}/oauth/complete — E*Trade OAuth step 2 (verifier → tokens)
  POST /broker/connections/{id}/reconnect      — renew E*Trade access token (daily)
  GET  /broker/connections/{id}/account        — live account summary (balance + positions)
  GET  /broker/connections/{id}/orders         — real order history from the broker itself
  GET  /broker/connections/{id}/quote          — real-time quote(s) via the broker's own session

  GET  /broker/paper-portfolios/{portfolio_id}/broker  — get assigned broker
  PUT  /broker/paper-portfolios/{portfolio_id}/broker  — assign / unassign broker

SECURITY: BrokerConnection.config (credentials) is NEVER included in any response body.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.logging import get_logger
from common.config import get_settings
from db import BrokerConnection, PaperPortfolio, get_session
from .auth import get_current_user, User

log = get_logger(__name__)
router = APIRouter(prefix="/broker", tags=["broker"])

_SUPPORTED_TYPES = ("etrade", "etrade_sandbox", "fidelity_manual")


# ── Credential encryption (Fernet with SHA-256 of JWT secret as key) ─────────

def _fernet():
    import base64, hashlib
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(get_settings().jwt_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt_config(config: dict) -> dict:
    import json
    return {"_enc": _fernet().encrypt(json.dumps(config).encode()).decode()}


def _decrypt_config(stored: dict) -> dict:
    """Decrypt config blob. Returns plaintext dict for legacy rows that have no _enc key."""
    if "_enc" not in stored:
        return dict(stored)
    import json
    return json.loads(_fernet().decrypt(stored["_enc"].encode()))


# ── Schemas ──────────────────────────────────────────────────────────────────

class BrokerConnectionOut(BaseModel):
    id: int
    name: str
    broker_type: str
    account_id: str | None
    is_active: bool
    is_authorized: bool


class CreateBrokerRequest(BaseModel):
    name: str
    broker_type: str
    consumer_key: str | None = None
    consumer_secret: str | None = None
    account_number: str | None = None  # Fidelity manual
    notes: str | None = None


class UpdateBrokerRequest(BaseModel):
    name: str | None = None
    account_id: str | None = None


class OAuthCompleteRequest(BaseModel):
    verifier: str


class AssignBrokerRequest(BaseModel):
    broker_connection_id: int | None  # None to unassign


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fetch(conn_id: int, user: User, session: Session) -> BrokerConnection:
    conn = session.execute(
        select(BrokerConnection).where(
            BrokerConnection.id == conn_id,
            BrokerConnection.user_id == user.id,
        )
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Broker connection not found")
    return conn


def _out(conn: BrokerConnection) -> BrokerConnectionOut:
    return BrokerConnectionOut(
        id            = conn.id,
        name          = conn.name,
        broker_type   = conn.broker_type,
        account_id    = conn.account_id,
        is_active     = conn.is_active,
        is_authorized = conn.is_authorized,
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("/connections", response_model=list[BrokerConnectionOut])
def list_connections(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(BrokerConnection).where(BrokerConnection.user_id == current.id)
        .order_by(BrokerConnection.created_at)
    ).scalars().all()
    return [_out(r) for r in rows]


@router.post("/connections", response_model=BrokerConnectionOut)
def create_connection(
    body: CreateBrokerRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if body.broker_type not in _SUPPORTED_TYPES:
        raise HTTPException(400, f"Unsupported broker_type. Supported: {_SUPPORTED_TYPES}")

    config: dict = {}
    if body.broker_type in ("etrade", "etrade_sandbox"):
        if not body.consumer_key or not body.consumer_secret:
            raise HTTPException(400, "consumer_key and consumer_secret are required for E*Trade")
        config = {
            "consumer_key":    body.consumer_key.strip(),
            "consumer_secret": body.consumer_secret.strip(),
        }
    elif body.broker_type == "fidelity_manual":
        config = {
            "account_number": (body.account_number or "").strip(),
            "notes":          (body.notes or "").strip(),
        }

    conn = BrokerConnection(
        user_id      = current.id,
        name         = body.name.strip(),
        broker_type  = body.broker_type,
        account_id   = body.account_number if body.broker_type == "fidelity_manual" else None,
        config       = _encrypt_config(config),
        is_authorized= body.broker_type == "fidelity_manual",  # manual never needs OAuth
    )
    session.add(conn)
    session.commit()
    session.refresh(conn)
    log.info("broker.connection_created", user=current.username, type=body.broker_type, name=body.name)
    return _out(conn)


@router.put("/connections/{conn_id}", response_model=BrokerConnectionOut)
def update_connection(
    conn_id: int,
    body: UpdateBrokerRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    conn = _fetch(conn_id, current, session)
    if body.name is not None:
        conn.name = body.name.strip()
    if body.account_id is not None:
        conn.account_id = body.account_id.strip() or None
    session.commit()
    session.refresh(conn)
    return _out(conn)


@router.delete("/connections/{conn_id}", status_code=204)
def delete_connection(
    conn_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    conn = _fetch(conn_id, current, session)
    # Unlink any portfolios pointing at this connection
    for p in session.execute(
        select(PaperPortfolio).where(PaperPortfolio.broker_connection_id == conn_id)
    ).scalars().all():
        p.broker_connection_id = None
    session.delete(conn)
    session.commit()


# ── E*Trade OAuth flow ────────────────────────────────────────────────────────

@router.post("/connections/{conn_id}/oauth/start")
def oauth_start(
    conn_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Step 1 of E*Trade OAuth: returns the URL the user must visit to authorize."""
    conn = _fetch(conn_id, current, session)
    if conn.broker_type not in ("etrade", "etrade_sandbox"):
        raise HTTPException(400, "OAuth is only available for E*Trade connections")

    from src.services.broker import EtradeBroker
    broker = EtradeBroker(_decrypt_config(conn.config), sandbox=(conn.broker_type == "etrade_sandbox"))
    try:
        authorize_url = broker.start_oauth()
    except Exception as exc:
        raise HTTPException(502, f"E*Trade OAuth start failed: {exc}")

    # Persist request tokens back to DB
    conn.config = _encrypt_config(dict(broker._config))
    conn.is_authorized = False
    session.commit()

    return {"authorize_url": authorize_url, "instructions": (
        "Visit the URL above in your browser. After authorizing, E*Trade will display "
        "a PIN/verifier code. Enter that code via POST /broker/connections/{id}/oauth/complete."
    )}


@router.post("/connections/{conn_id}/oauth/complete")
def oauth_complete(
    conn_id: int,
    body: OAuthCompleteRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Step 2 of E*Trade OAuth: exchange the verifier PIN for access tokens."""
    conn = _fetch(conn_id, current, session)
    if conn.broker_type not in ("etrade", "etrade_sandbox"):
        raise HTTPException(400, "OAuth is only available for E*Trade connections")

    from src.services.broker import EtradeBroker
    broker = EtradeBroker(_decrypt_config(conn.config), sandbox=(conn.broker_type == "etrade_sandbox"))
    try:
        broker.complete_oauth(body.verifier.strip())
    except Exception as exc:
        raise HTTPException(502, f"E*Trade OAuth complete failed: {exc}")

    # Persist access tokens; fetch account list to populate account_id
    _new_config = dict(broker._config)
    conn.is_authorized = True
    try:
        accounts = broker.list_accounts()
        if accounts:
            acct = accounts[0]
            conn.account_id = acct.get("accountId")
            _new_config["account_id_key"] = acct.get("accountIdKey", "")
    except Exception:
        pass
    conn.config = _encrypt_config(_new_config)
    session.commit()
    log.info("broker.oauth_complete", user=current.username, conn_id=conn_id, account=conn.account_id)
    return {"status": "authorized", "account_id": conn.account_id}


@router.post("/connections/{conn_id}/reconnect")
def reconnect(
    conn_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Renew E*Trade access token for today's session (must call once per trading day)."""
    conn = _fetch(conn_id, current, session)
    if conn.broker_type not in ("etrade", "etrade_sandbox"):
        raise HTTPException(400, "Only available for E*Trade connections")
    if not conn.is_authorized:
        raise HTTPException(400, "Not yet authorized — run OAuth flow first")

    from src.services.broker import EtradeBroker
    broker = EtradeBroker(_decrypt_config(conn.config), sandbox=(conn.broker_type == "etrade_sandbox"))
    try:
        broker.renew_access_token()
    except Exception as exc:
        raise HTTPException(502, f"E*Trade renew failed: {exc}")
    return {"status": "reconnected"}


# ── Live account summary ──────────────────────────────────────────────────────

@router.get("/connections/{conn_id}/account")
def get_account_info(
    conn_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Return live balance + positions from the real broker (or placeholder for manual)."""
    conn = _fetch(conn_id, current, session)
    if not conn.is_authorized:
        raise HTTPException(400, "Broker not yet authorized")

    from src.services.broker import get_broker
    broker = get_broker(conn.broker_type, _decrypt_config(conn.config))
    try:
        # BUG-BROKERACCTKEY: conn.account_id is the PLAIN account number (e.g. "823145980"),
        # not E*Trade's opaque accountIdKey — passing it here overrides
        # EtradeBroker._account_id_key()'s own correct fallback to the real
        # config["account_id_key"], causing E*Trade to reject every call with "Please enter
        # valid Account Key". Always pass None so the real key from config is used.
        acct = broker.get_account(None)
    except Exception as exc:
        # BUG-BROKERROUTE-STALEAUTH: an expired/rejected E*Trade token (tokens hard-expire at
        # midnight ET daily, per T257-ETRADE-PROD-SYSTEMATIC) was previously invisible here —
        # this route just returned a generic 502 with no indication the user needed to
        # re-authorize, and conn.is_authorized stayed stuck at True even though the real token
        # was dead, until the next 08:30 ET health check caught it (up to a full day later).
        # Reuses the same in-loop detection already wired into the paper-trading engine's own
        # broker call sites (_place_broker_entry/_place_broker_exit/poll_broker_order_fills) —
        # this route was a genuine gap in that same "in-loop, not just once-daily" coverage.
        from ..services.scheduler import _is_token_rejected_error, _mark_broker_unauthorized_and_notify
        if _is_token_rejected_error(exc):
            _mark_broker_unauthorized_and_notify(session, conn)
            raise HTTPException(401, f"E*Trade session expired — a fresh re-authorize link has been emailed to you. ({exc})")
        raise HTTPException(502, f"Broker account fetch failed: {exc}")

    return {
        "account_id":     acct.account_id,
        "broker_type":    acct.broker_type,
        "cash_available": acct.cash_available,
        "equity":         acct.equity,
        "buying_power":   acct.buying_power,
        "positions": [
            {
                "symbol":             p.symbol,
                "qty":                p.qty,
                "avg_cost":           p.avg_cost,
                "market_value":       p.market_value,
                "unrealized_pnl":     p.unrealized_pnl,
                "unrealized_pnl_pct": round(p.unrealized_pnl_pct * 100, 2),
            }
            for p in acct.open_positions
        ],
    }


@router.get("/connections/{conn_id}/orders")
def get_order_history(
    conn_id: int,
    status: str = "all",
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """T257-BROKER-ORDER-HISTORY: real order history from the broker itself (E*Trade's
    orders.json endpoint, sandbox or prod depending on the connection), not just orders this
    app happens to have a broker_order_id for. status: "all" (default) | "open" | "filled" |
    "cancelled" | "rejected" — matches EtradeBroker.list_orders()'s own vocabulary.

    Returns 501 for broker types that don't implement list_orders (e.g. fidelity_manual,
    which has no real API at all) — matches BrokerInterface's own "raise NotImplementedError
    for unsupported features" convention rather than silently returning an empty list, which
    would look identical to "authorized but genuinely zero orders."
    """
    conn = _fetch(conn_id, current, session)
    if not conn.is_authorized:
        raise HTTPException(400, "Broker not yet authorized")

    from src.services.broker import get_broker
    broker = get_broker(conn.broker_type, _decrypt_config(conn.config))
    try:
        # BUG-BROKERACCTKEY: same fix as get_account_info() above — conn.account_id is the
        # plain account number, not E*Trade's opaque accountIdKey; passing it here overrode
        # EtradeBroker._account_id_key()'s own correct fallback to config["account_id_key"].
        orders = broker.list_orders(None, status=status)
    except NotImplementedError:
        raise HTTPException(501, f"{conn.broker_type} does not support order history")
    except Exception as exc:
        # BUG-BROKERROUTE-STALEAUTH: same fix as get_account_info() above — an expired token
        # must flip is_authorized and notify the user immediately, not silently 502 while the
        # DB keeps claiming the connection is still authorized.
        from ..services.scheduler import _is_token_rejected_error, _mark_broker_unauthorized_and_notify
        if _is_token_rejected_error(exc):
            _mark_broker_unauthorized_and_notify(session, conn)
            raise HTTPException(401, f"E*Trade session expired — a fresh re-authorize link has been emailed to you. ({exc})")
        raise HTTPException(502, f"Broker order history fetch failed: {exc}")

    return {
        "orders": [
            {
                "order_id":         o.order_id,
                "symbol":           o.symbol,
                "side":             o.side.value if hasattr(o.side, "value") else o.side,
                "qty":              o.qty,
                "status":           o.status,
                "filled_qty":       o.filled_qty,
                "filled_avg_price": o.filled_avg_price,
                "placed_at":        o.placed_at,
            }
            for o in orders
        ],
    }


@router.get("/connections/{conn_id}/quote")
def get_broker_quote(
    conn_id: int,
    symbols: str,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """T230-DATA-BROKERQUOTE: real-time quote(s) via the broker's own already-authenticated
    session — e.g. E*Trade's /v1/market/quote — rather than a separate market-data provider.
    symbols is a comma-separated list (e.g. "AAPL,MSFT"). Returns 501 for broker types that
    don't implement get_quote, matching list_orders/get_order_history's own established
    convention rather than silently returning an empty list (which would look identical to
    "authorized but genuinely no quote data").
    """
    conn = _fetch(conn_id, current, session)
    if not conn.is_authorized:
        raise HTTPException(400, "Broker not yet authorized")

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(400, "symbols must be a non-empty comma-separated list")

    from src.services.broker import get_broker
    broker = get_broker(conn.broker_type, _decrypt_config(conn.config))
    try:
        quotes = broker.get_quote(sym_list)
    except NotImplementedError:
        raise HTTPException(501, f"{conn.broker_type} does not support get_quote")
    except Exception as exc:
        # BUG-BROKERROUTE-STALEAUTH: same fix as get_account_info()/get_order_history() above —
        # an expired token must flip is_authorized and notify the user immediately, not
        # silently 502 while the DB keeps claiming the connection is still authorized.
        from ..services.scheduler import _is_token_rejected_error, _mark_broker_unauthorized_and_notify
        if _is_token_rejected_error(exc):
            _mark_broker_unauthorized_and_notify(session, conn)
            raise HTTPException(401, f"E*Trade session expired — a fresh re-authorize link has been emailed to you. ({exc})")
        raise HTTPException(502, f"Broker quote fetch failed: {exc}")

    return {
        "quotes": [
            {
                "symbol":     q.symbol,
                "last_price": q.last_price,
                "bid":        q.bid,
                "ask":        q.ask,
                "prev_close": q.prev_close,
                "volume":     q.volume,
            }
            for q in quotes
        ],
    }


# ── Portfolio broker assignment ───────────────────────────────────────────────

@router.get("/paper-portfolios/{portfolio_id}/broker")
def get_portfolio_broker(
    portfolio_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    port = session.get(PaperPortfolio, portfolio_id)
    if not port:
        raise HTTPException(404, "Portfolio not found")
    if port.broker_connection_id is None:
        return {"broker_connection_id": None, "broker": None}
    conn = session.get(BrokerConnection, port.broker_connection_id)
    if not conn or conn.user_id != current.id:
        return {"broker_connection_id": None, "broker": None}
    return {"broker_connection_id": conn.id, "broker": _out(conn)}


@router.put("/paper-portfolios/{portfolio_id}/broker")
def assign_portfolio_broker(
    portfolio_id: int,
    body: AssignBrokerRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    port = session.get(PaperPortfolio, portfolio_id)
    if not port:
        raise HTTPException(404, "Portfolio not found")

    if body.broker_connection_id is not None:
        conn = _fetch(body.broker_connection_id, current, session)
        port.broker_connection_id = conn.id
    else:
        port.broker_connection_id = None

    session.commit()
    return {"status": "ok", "broker_connection_id": port.broker_connection_id}
