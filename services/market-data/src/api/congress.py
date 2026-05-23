"""Congressional trading data — proxies Quiver Quantitative API."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from common.logging import get_logger
from .auth import get_current_user

log = get_logger("congress")
router = APIRouter(prefix="/congress", tags=["congress"])

_quiver_api_key: str | None = None


def set_quiver_key(key: str) -> None:
    global _quiver_api_key
    _quiver_api_key = key


def get_quiver_key() -> str | None:
    return _quiver_api_key


@router.get("/trades")
async def congress_trades(
    days: int = Query(90, le=365),
    politician: str | None = None,
    ticker: str | None = None,
    transaction: str | None = None,
    _user=Depends(get_current_user),
):
    """Fetch recent congressional trades from Quiver Quantitative."""
    key = _quiver_api_key
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Quiver Quantitative API key not configured. Add it in Settings → Data Sources.",
        )

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.quiverquant.com/beta/live/congresstrading",
                headers={
                    "Authorization": f"Token {key}",
                    "Accept": "application/json",
                    "X-CSRFToken": "null",
                },
            )
            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail="Invalid Quiver API key.")
            if not resp.is_success:
                raise HTTPException(
                    status_code=502,
                    detail=f"Quiver API returned {resp.status_code}.",
                )
            data: list[dict] = resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach Quiver API: {exc}")

    # Optional filters
    if politician:
        name_lower = politician.lower()
        data = [t for t in data if name_lower in (t.get("Politician") or "").lower()]
    if ticker:
        tk = ticker.upper()
        data = [t for t in data if (t.get("Ticker") or "").upper() == tk]
    if transaction:
        tx = transaction.lower()
        data = [t for t in data if tx in (t.get("Transaction") or "").lower()]

    return data
