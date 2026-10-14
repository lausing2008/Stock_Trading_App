"""Watchlist endpoints — per-user, multi-list support."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import SignalAlert, Stock, User, Watchlist, WatchlistItem, get_session
from .auth import get_current_user

# ── Schemas ───────────────────────────────────────────────────────────────────

class WatchlistItemOut(BaseModel):
    symbol: str
    name: str
    name_zh: str | None = None
    market: str
    exchange: str
    sector: str | None = None
    currency: str
    added_at: str
    note: str | None = None
    # T260-DELISTED-BADGE: surfaces aud14-survivorship's real delisting detection to the
    # watchlist UI. Deliberately informational only — no auto-removal (see the design note in
    # CLAUDE.md's aud14-survivorship entry for why silent auto-removal was rejected for a
    # terminal/irreversible condition, unlike the existing win-rate-based auto-rotation).
    delisted: bool = False

    class Config:
        from_attributes = True


class UpdateNoteRequest(BaseModel):
    note: str | None = None


class WatchlistOut(BaseModel):
    id: int
    name: str
    item_count: int
    trading_style: str | None = None
    created_at: str


class CreateWatchlistRequest(BaseModel):
    name: str
    trading_style: str | None = None


class RenameWatchlistRequest(BaseModel):
    name: str
    trading_style: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_default(session: Session, user: User) -> Watchlist:
    wl = session.execute(
        select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.created_at).limit(1)
    ).scalar_one_or_none()
    if not wl:
        wl = Watchlist(user_id=user.id, name="My Watchlist")
        session.add(wl)
        session.commit()
        session.refresh(wl)
    return wl


def _resolve(session: Session, list_id: int | None, user: User) -> Watchlist:
    if list_id is None:
        return _get_or_create_default(session, user)
    wl = session.execute(
        select(Watchlist).where(Watchlist.id == list_id, Watchlist.user_id == user.id)
    ).scalar_one_or_none()
    if not wl:
        raise HTTPException(404, "Watchlist not found")
    return wl


def _item_out(item: WatchlistItem, stock: Stock) -> WatchlistItemOut:
    return WatchlistItemOut(
        symbol=stock.symbol, name=stock.name, name_zh=stock.name_zh,
        market=stock.market, exchange=stock.exchange, sector=stock.sector,
        currency=stock.currency, added_at=item.added_at.isoformat(),
        note=item.note, delisted=stock.delisted,
    )


# ── /watchlists — CRUD ────────────────────────────────────────────────────────

lists_router = APIRouter(prefix="/watchlists", tags=["watchlist"])


@lists_router.get("", response_model=list[WatchlistOut])
def list_watchlists(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(Watchlist, func.count(WatchlistItem.id).label("cnt"))
        .outerjoin(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
        .where(Watchlist.user_id == current.id)
        .group_by(Watchlist.id)
        .order_by(Watchlist.created_at)
    ).all()
    if not rows:
        # Auto-create default on first access
        wl = _get_or_create_default(session, current)
        return [WatchlistOut(id=wl.id, name=wl.name, item_count=0, trading_style=wl.trading_style, created_at=wl.created_at.isoformat())]
    return [WatchlistOut(id=wl.id, name=wl.name, item_count=cnt or 0, trading_style=wl.trading_style, created_at=wl.created_at.isoformat())
            for wl, cnt in rows]


@lists_router.post("", response_model=WatchlistOut)
def create_watchlist(
    body: CreateWatchlistRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    if session.execute(
        select(Watchlist).where(Watchlist.user_id == current.id, Watchlist.name == name)
    ).scalar_one_or_none():
        raise HTTPException(409, f"'{name}' already exists")
    wl = Watchlist(user_id=current.id, name=name, trading_style=body.trading_style)
    session.add(wl)
    session.commit()
    session.refresh(wl)
    return WatchlistOut(id=wl.id, name=wl.name, item_count=0, trading_style=wl.trading_style, created_at=wl.created_at.isoformat())


@lists_router.put("/{list_id}", response_model=WatchlistOut)
def rename_watchlist(
    list_id: int,
    body: RenameWatchlistRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    wl = _resolve(session, list_id, current)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    wl.name = name
    if body.trading_style is not None:
        wl.trading_style = body.trading_style if body.trading_style != '' else None
    session.commit()
    cnt = session.execute(select(func.count()).where(WatchlistItem.watchlist_id == wl.id)).scalar() or 0
    return WatchlistOut(id=wl.id, name=wl.name, item_count=cnt, trading_style=wl.trading_style, created_at=wl.created_at.isoformat())


@lists_router.delete("/{list_id}")
def delete_watchlist(
    list_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    wl = _resolve(session, list_id, current)
    total = session.execute(select(func.count()).where(Watchlist.user_id == current.id)).scalar() or 0
    if total <= 1:
        raise HTTPException(400, "Cannot delete the last watchlist")
    session.delete(wl)
    session.commit()
    return {"status": "deleted", "id": list_id}


# ── /watchlist — items ────────────────────────────────────────────────────────

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItemOut])
def list_watchlist(
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if list_id is not None:
        wl = _resolve(session, list_id, current)
        rows = session.execute(
            select(WatchlistItem, Stock)
            .join(Stock, WatchlistItem.stock_id == Stock.id)
            .where(WatchlistItem.watchlist_id == wl.id)
            .order_by(WatchlistItem.added_at.desc())
        ).all()
    else:
        # No list_id: return unique stocks across ALL user's watchlists (used by dashboard)
        rows = session.execute(
            select(WatchlistItem, Stock)
            .join(Stock, WatchlistItem.stock_id == Stock.id)
            .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
            .where(Watchlist.user_id == current.id)
            .order_by(WatchlistItem.added_at.desc())
        ).all()
        seen: set[str] = set()
        deduped = []
        for item, stock in rows:
            if stock.symbol not in seen:
                seen.add(stock.symbol)
                deduped.append((item, stock))
        rows = deduped
    return [_item_out(item, stock) for item, stock in rows]


def _add_effects(session: Session, stock: Stock, wl: Watchlist | None, user: User) -> dict:
    """What adding `stock` to `wl` WILL do — computed once, used by the add AND its preview.

    D1 (2026-10-13): a watchlist add also enrols the user in signal-change emails for the
    list's horizon. That side effect is preserved and disclosed BEFORE saving with its
    EFFECTIVE result, from the same subscription helper the signal-alert endpoints use
    (`signal_alerts.subscription_effects`). Review S5: the disclosed fields are versioned
    (`effects_version`), and the add can refuse a stale disclosure inside its transaction.
    """
    from .signal_alerts import describe_subscription, effects_version, subscription_effects
    horizon = (wl.trading_style if wl is not None else None) or "SWING"
    in_list = wl is not None and session.execute(
        select(WatchlistItem.id).where(WatchlistItem.stock_id == stock.id,
                                       WatchlistItem.watchlist_id == wl.id)
    ).first() is not None
    sub = subscription_effects(session, user, stock.symbol, horizon)
    creates = not in_list and sub["will_create"]
    effect = (f"{stock.symbol} is already in this list; nothing changes." if in_list
              else f"Adds {stock.symbol}. " + describe_subscription(sub))
    out = {
        "symbol": stock.symbol,
        "list": ({"id": wl.id, "name": wl.name, "trading_style": wl.trading_style}
                 if wl is not None else {"id": None, "name": "My Watchlist", "created": True}),
        "already_in_list": in_list,
        "signal_subscription": {"horizon": horizon, "already_subscribed": sub["already_subscribed"],
                                "will_create": creates},
        "email_on_file": sub["email_on_file"],
        "signal_emails_enabled": sub["signal_emails_enabled"],
        "effect": effect,
    }
    out["effects_version"] = effects_version(out)
    return out


@router.get("/{symbol}/preview")
def preview_add(
    symbol: str,
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """What POST /watchlist/{symbol} would do, WITHOUT doing it. Read-only: unlike the add, it
    never creates a default list."""
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    if list_id is None:
        # The same default _get_or_create_default() picks (oldest by created_at) — minus the
        # create.
        wl = session.execute(select(Watchlist).where(Watchlist.user_id == current.id)
                             .order_by(Watchlist.created_at).limit(1)).scalar_one_or_none()
    else:
        wl = session.execute(select(Watchlist).where(
            Watchlist.id == list_id, Watchlist.user_id == current.id)).scalar_one_or_none()
        if wl is None:
            raise HTTPException(404, "Watchlist not found")
    return _add_effects(session, stock, wl, current)


@router.post("/{symbol}", response_model=WatchlistItemOut)
def add_to_watchlist(
    symbol: str,
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    expected_effects: str | None = Query(None, max_length=64),
):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    wl = _resolve(session, list_id, current)
    existing = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.watchlist_id == wl.id,
        )
    ).scalar_one_or_none()
    if existing:
        return _item_out(existing, stock)
    # Decided by the SAME helper the preview shows the user (D1), so what was disclosed and
    # what is done cannot drift apart.
    effects = _add_effects(session, stock, wl, current)
    # isinstance: a direct (non-HTTP) caller receives the Query() default object, not None.
    if isinstance(expected_effects, str) and expected_effects \
            and effects["effects_version"] != expected_effects:
        # Review S5: the user confirmed a DIFFERENT disclosure (email address, preference or
        # subscription changed since). Refused inside this transaction, before any write.
        raise HTTPException(409, "what this add will do has changed since you reviewed it; "
                                 "review it again")
    item = WatchlistItem(stock_id=stock.id, watchlist_id=wl.id)
    session.add(item)

    # Auto-subscribe to signal alerts for this watchlist's horizon
    horizon = effects["signal_subscription"]["horizon"]
    if effects["signal_subscription"]["will_create"]:
        session.add(SignalAlert(
            user_id=current.id, symbol=stock.symbol,
            email=current.email, horizon=horizon,
        ))

    try:
        session.commit()
    except IntegrityError:
        # A concurrent add won the unique (watchlist, stock) index — or the (user, symbol,
        # horizon) subscription. The symbol is in the list either way: serve it, not a 500.
        session.rollback()
        existing = session.execute(select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.watchlist_id == wl.id)).scalar_one_or_none()
        if existing is None:
            raise
        return _item_out(existing, stock)
    session.refresh(item)
    return _item_out(item, stock)


@router.delete("/{symbol}")
def remove_from_watchlist(
    symbol: str,
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    wl = _resolve(session, list_id, current)
    item = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.watchlist_id == wl.id,
        )
    ).scalar_one_or_none()
    if item:
        session.delete(item)
        session.commit()
    return {"status": "removed", "symbol": symbol}


@router.patch("/{symbol}/note")
def update_note(
    symbol: str,
    body: UpdateNoteRequest,
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """UI-2: Persist per-item note to the DB (was localStorage-only)."""
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    wl = _resolve(session, list_id, current)
    item = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.watchlist_id == wl.id,
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Stock not in watchlist")
    item.note = body.note
    session.commit()
    return {"status": "ok", "symbol": symbol, "note": item.note}
