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

T270-BROKER-ADMIN-GATE: every route here requires get_admin_user, not get_current_user.
Previously all 12 routes were get_current_user-only — matching the frontend's own UI, which
already hides broker-linking behind an isAdmin check, but that check was purely cosmetic:
nothing server-side actually enforced it. This mattered most for
PUT /paper-portfolios/{id}/broker — PaperPortfolio has no user_id column (portfolios are
shared/global), so any authenticated non-admin user could call that endpoint directly and
assign/unassign a broker connection on any shared portfolio, disrupting live trading for
everyone, with no admin privilege required. _fetch()'s own per-connection
user_id == current.id check still limits which CONNECTIONS a user can reference (unchanged),
but that was never the gap — the gap was that non-admins could reach these endpoints at all.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.logging import get_logger
from common.config import get_settings
from db import BrokerConnection, PaperPortfolio, get_session
from .auth import get_admin_user, User

log = get_logger(__name__)
router = APIRouter(prefix="/broker", tags=["broker"])

# TIER84-BROKER-PORTABILITY: derived from the broker registry (services/market-data/src/
# services/broker/__init__.py) rather than a hand-maintained duplicate list — a new broker
# adapter's BROKER_TYPES automatically become "supported" here the moment it's registered.
from src.services.broker import SUPPORTED_BROKER_TYPES as _SUPPORTED_TYPES


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
    # TIER84-BROKER-ALPACA: plain key/secret pair, no OAuth — set once at creation and never
    # rotated through a separate flow the way E*Trade's request/access tokens are.
    key_id: str | None = None
    secret_key: str | None = None


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


# ── Broker type metadata ───────────────────────────────────────────────────────

@router.get("/types")
def list_broker_types(current: User = Depends(get_admin_user)):
    """TIER84-BROKER-PORTABILITY: metadata for every supported broker_type (auth style +
    required credential fields), driven entirely by the registry in services/market-data/src/
    services/broker/__init__.py — lets the frontend render the "Add Broker Connection" form's
    dynamic credential fields for ANY registered broker (including a future Schwab/real-
    Fidelity-API adapter) without a hardcoded per-broker-type JSX branch."""
    from src.services.broker import SUPPORTED_BROKER_TYPES, broker_metadata
    return {"broker_types": [broker_metadata(t) for t in SUPPORTED_BROKER_TYPES]}


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("/connections", response_model=list[BrokerConnectionOut])
def list_connections(
    current: User = Depends(get_admin_user),
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
    current: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """TIER84-BROKER-PORTABILITY: config-building is now entirely metadata-driven off each
    adapter class's own CONFIG_FIELDS/AUTH_STYLE declaration (services/market-data/src/
    services/broker/__init__.py's registry) rather than a hardcoded if/elif per broker_type —
    adding a new broker (Schwab, a real Fidelity API, etc.) never requires touching this
    function, only registering the new adapter class in that one registry.
    """
    from src.services.broker import AuthStyle, broker_class_for_type, broker_metadata, get_broker

    if body.broker_type not in _SUPPORTED_TYPES:
        raise HTTPException(400, f"Unsupported broker_type. Supported: {_SUPPORTED_TYPES}")

    meta = broker_metadata(body.broker_type)
    config: dict = {}
    for field_meta in meta["config_fields"]:
        value = getattr(body, field_meta["key"], None)
        if not value:
            raise HTTPException(
                400, f"{field_meta['key']} is required for {body.broker_type}"
            )
        config[field_meta["key"]] = value.strip()

    # fidelity_manual's account_number/notes are display-only metadata, not real credentials —
    # kept as a targeted special case rather than folded into CONFIG_FIELDS (which drives
    # required-field VALIDATION; these two are always optional regardless of broker_type).
    if body.broker_type == "fidelity_manual":
        config.setdefault("account_number", (body.account_number or "").strip())
        config["notes"] = (body.notes or "").strip()

    # AUTH_STYLE.KEY_SECRET brokers (Alpaca, and any future key/secret-only broker) have no
    # separate OAuth authorize step to validate credentials against — a typo'd key/secret
    # would otherwise sit silently "authorized" until the daily 08:30 ET health check (or the
    # first real order attempt) eventually surfaces it. A cheap upfront check here catches
    # that same-session instead, generically for ANY broker declaring this auth style.
    cls = broker_class_for_type(body.broker_type)
    if cls.AUTH_STYLE == AuthStyle.KEY_SECRET:
        try:
            get_broker(body.broker_type, config).get_account()
        except Exception as exc:
            raise HTTPException(400, f"{body.broker_type} credential check failed: {exc}")

    conn = BrokerConnection(
        user_id      = current.id,
        name         = body.name.strip(),
        broker_type  = body.broker_type,
        account_id   = body.account_number if body.broker_type == "fidelity_manual" else None,
        config       = _encrypt_config(config),
        # MANUAL and KEY_SECRET brokers need no separate authorize step — only OAUTH1 brokers
        # (E*Trade) start out unauthorized pending the oauth/start + oauth/complete flow.
        is_authorized= cls.AUTH_STYLE != AuthStyle.OAUTH1,
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
    current: User = Depends(get_admin_user),
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
    current: User = Depends(get_admin_user),
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
    current: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Step 1 of the OAuth flow: returns the URL the user must visit to authorize.

    TIER84-BROKER-PORTABILITY: the guard checks AUTH_STYLE generically (any future OAuth1
    broker's connections would pass this check too), but the actual start_oauth()/
    complete_oauth()/renew_access_token() calls below remain EtradeBroker-specific — those
    3 methods live on EtradeBroker itself, not BrokerInterface, since no second OAuth1-style
    broker exists yet to generalize the call signature against.
    """
    from src.services.broker import AuthStyle, broker_class_for_type
    conn = _fetch(conn_id, current, session)
    if broker_class_for_type(conn.broker_type).AUTH_STYLE != AuthStyle.OAUTH1:
        raise HTTPException(400, "OAuth is only available for OAuth-based broker connections")

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
    current: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Step 2 of the OAuth flow: exchange the verifier PIN for access tokens."""
    from src.services.broker import AuthStyle, broker_class_for_type
    conn = _fetch(conn_id, current, session)
    if broker_class_for_type(conn.broker_type).AUTH_STYLE != AuthStyle.OAUTH1:
        raise HTTPException(400, "OAuth is only available for OAuth-based broker connections")

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
    current: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Renew the OAuth access token for today's session (must call once per trading day —
    an OAuth1 concept; key/secret-only brokers like Alpaca never need this)."""
    from src.services.broker import AuthStyle, broker_class_for_type
    conn = _fetch(conn_id, current, session)
    if broker_class_for_type(conn.broker_type).AUTH_STYLE != AuthStyle.OAUTH1:
        raise HTTPException(400, "Only available for OAuth-based broker connections")
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
    current: User = Depends(get_admin_user),
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
    current: User = Depends(get_admin_user),
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
    current: User = Depends(get_admin_user),
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
    current: User = Depends(get_admin_user),
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
    current: User = Depends(get_admin_user),
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
