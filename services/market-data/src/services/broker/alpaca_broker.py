"""Alpaca broker adapter — Trading API v2, plain key/secret header auth.

TIER84-BROKER-ALPACA: the structural fix to E*Trade's own daily-midnight-ET OAuth 1.0a token
expiry (see T257-ETRADE-PROD-SYSTEMATIC in .claude/CLAUDE.md, which explicitly names Alpaca as
"the structural answer" — a key-only broker with no PIN, no daily re-auth). Unlike EtradeBroker,
there is no OAuth flow at all here: `key_id`/`secret_key` are sent as static HTTP headers on
every request and never expire on their own (only if the user revokes/rotates them at Alpaca's
own dashboard) — so a BrokerConnection using this adapter is immediately is_authorized=True at
creation time, mirroring ManualBroker's own "manual never needs OAuth" convention exactly.

Alpaca API reference: https://docs.alpaca.markets/reference (Trading API v2)
  Auth headers: APCA-API-KEY-ID / APCA-API-SECRET-KEY
  Paper (sandbox): https://paper-api.alpaca.markets
  Live:            https://api.alpaca.markets
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from .interface import (
    AuthStyle, BrokerAccount, BrokerInterface, BrokerOrder, BrokerPosition, BrokerQuote,
    ConfigField, OrderSide, OrderType,
)

_PAPER_BASE = "https://paper-api.alpaca.markets"
_LIVE_BASE = "https://api.alpaca.markets"
_DATA_BASE = "https://data.alpaca.markets"

# Alpaca's own order-status vocabulary is much finer-grained than this app's 5-state one
# (new/accepted/pending_new/partially_filled/filled/done_for_day/canceled/expired/replaced/
# pending_cancel/pending_replace/rejected/suspended/calculated) — collapsed to the same 5
# states EtradeBroker's own status_map already uses, so callers never have to special-case a
# per-broker vocabulary.
_STATUS_MAP = {
    "new": "pending", "accepted": "pending", "pending_new": "pending",
    "accepted_for_bidding": "pending", "calculated": "pending",
    "partially_filled": "partially_filled",
    "filled": "filled", "done_for_day": "filled",
    "canceled": "cancelled", "expired": "cancelled", "replaced": "cancelled",
    "pending_cancel": "pending", "pending_replace": "pending",
    "rejected": "rejected", "suspended": "rejected", "stopped": "rejected",
}

# This app's internal order-status vocabulary (used by list_orders' own status param) mapped
# to Alpaca's own query-param values — matches EtradeBroker.list_orders()'s established
# "map our vocabulary to the broker's literal param, don't push that mapping onto callers"
# convention exactly.
_QUERY_STATUS_MAP = {
    "open": "open", "pending": "open",
    "filled": "closed", "executed": "closed",
    "cancelled": "closed", "canceled": "closed",
    "rejected": "closed",
    "all": "all",
}


class AlpacaBroker(BrokerInterface):
    """Alpaca Trading API v2 adapter (paper or live).

    config keys (stored encrypted in BrokerConnection.config):
      key_id      — Alpaca API key ID
      secret_key  — Alpaca API secret key
    """

    BROKER_TYPES = ("alpaca", "alpaca_paper")
    AUTH_STYLE = AuthStyle.KEY_SECRET
    CONFIG_FIELDS = (
        ConfigField("key_id", "API Key ID", placeholder="From alpaca.markets dashboard"),
        ConfigField("secret_key", "Secret Key", secret=True),
    )

    def __init__(self, config: dict, paper: bool = True):
        self._config = config
        self._paper = paper
        self._base = _PAPER_BASE if paper else _LIVE_BASE

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": self._config.get("key_id", ""),
            "APCA-API-SECRET-KEY": self._config.get("secret_key", ""),
        }

    # ── Account info ──────────────────────────────────────────────────────────

    def _get_positions_raw(self) -> list[BrokerPosition]:
        resp = requests.get(
            f"{self._base}/v2/positions", headers=self._headers(), timeout=15,
        )
        if not resp.ok:
            return []
        positions = []
        for pos in resp.json():
            qty = float(pos.get("qty", 0))
            cost = float(pos.get("avg_entry_price", 0))
            mval = float(pos.get("market_value", 0))
            pnl = float(pos.get("unrealized_pl", 0))
            pnl_pct = float(pos.get("unrealized_plpc", 0))
            positions.append(BrokerPosition(
                symbol=pos.get("symbol", ""),
                qty=qty,
                avg_cost=cost,
                market_value=mval,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=pnl_pct,
            ))
        return positions

    def get_account(self, account_id: str | None = None) -> BrokerAccount:
        resp = requests.get(
            f"{self._base}/v2/account", headers=self._headers(), timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"Alpaca account fetch failed: {resp.status_code} {resp.text}")
        acct = resp.json()
        return BrokerAccount(
            account_id=acct.get("account_number", acct.get("id", "")),
            broker_type="alpaca_paper" if self._paper else "alpaca",
            cash_available=float(acct.get("cash", 0)),
            equity=float(acct.get("equity", 0)),
            buying_power=float(acct.get("buying_power", 0)),
            day_trading_buying_power=float(acct.get("daytrading_buying_power", 0)),
            open_positions=self._get_positions_raw(),
        )

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self, symbol, qty, side, order_type=OrderType.MARKET,
        limit_price=None, stop_price=None, time_in_force="day", account_id=None,
    ) -> BrokerOrder:
        order_type_map = {
            OrderType.MARKET: "market", OrderType.LIMIT: "limit",
            OrderType.STOP: "stop", OrderType.STOP_LIMIT: "stop_limit",
        }
        payload: dict = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": "buy" if side == OrderSide.BUY else "sell",
            "type": order_type_map[order_type],
            "time_in_force": time_in_force if time_in_force in ("day", "gtc", "ioc", "fok") else "day",
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)
        if stop_price is not None:
            payload["stop_price"] = str(stop_price)

        resp = requests.post(
            f"{self._base}/v2/orders", json=payload, headers=self._headers(), timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"Alpaca place_order failed: {resp.status_code} {resp.text}")
        return self._parse_order(resp.json())

    def cancel_order(self, order_id: str, account_id: str | None = None) -> bool:
        resp = requests.delete(
            f"{self._base}/v2/orders/{order_id}", headers=self._headers(), timeout=15,
        )
        # Alpaca returns 204 on success — matches this app's "return True on success" contract.
        return resp.status_code == 204

    def _parse_order(self, o: dict) -> BrokerOrder:
        raw_status = o.get("status", "new")
        side = OrderSide.BUY if o.get("side") == "buy" else OrderSide.SELL
        order_type_reverse = {
            "market": OrderType.MARKET, "limit": OrderType.LIMIT,
            "stop": OrderType.STOP, "stop_limit": OrderType.STOP_LIMIT,
        }
        placed_at = None
        _submitted = o.get("submitted_at")
        if _submitted:
            try:
                # Alpaca's own timestamps are already ISO8601 (RFC3339) — no conversion needed,
                # unlike E*Trade's epoch-millisecond placedTime.
                placed_at = _submitted
            except (ValueError, TypeError):
                placed_at = None
        return BrokerOrder(
            order_id=str(o.get("id", "")),
            symbol=o.get("symbol", ""),
            side=side,
            qty=float(o.get("qty", 0)),
            order_type=order_type_reverse.get(o.get("type", "market"), OrderType.MARKET),
            status=_STATUS_MAP.get(raw_status, raw_status),
            filled_qty=float(o.get("filled_qty", 0) or 0),
            filled_avg_price=float(o["filled_avg_price"]) if o.get("filled_avg_price") else None,
            limit_price=float(o["limit_price"]) if o.get("limit_price") else None,
            stop_price=float(o["stop_price"]) if o.get("stop_price") else None,
            placed_at=placed_at,
        )

    def get_order(self, order_id: str, account_id: str | None = None) -> BrokerOrder:
        resp = requests.get(
            f"{self._base}/v2/orders/{order_id}", headers=self._headers(), timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"Alpaca get_order failed: {resp.status_code} {resp.text}")
        return self._parse_order(resp.json())

    def list_orders(self, account_id: str | None = None, status: str = "open") -> list[BrokerOrder]:
        """TIER84-BROKER-ALPACA: full order history via Alpaca's own /v2/orders endpoint,
        matching EtradeBroker.list_orders()'s own established status-vocabulary-mapping
        pattern (this app's internal open/filled/cancelled/rejected/all terms mapped to
        Alpaca's own open/closed/all query param, never pushed onto callers)."""
        params: dict = {"limit": 50}
        alpaca_status = _QUERY_STATUS_MAP.get(status.lower())
        if alpaca_status:
            params["status"] = alpaca_status
        resp = requests.get(
            f"{self._base}/v2/orders", params=params, headers=self._headers(), timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"Alpaca list_orders failed: {resp.status_code} {resp.text}")
        return [self._parse_order(o) for o in resp.json()]

    def get_quote(self, symbols: list[str]) -> list[BrokerQuote]:
        """TIER84-BROKER-ALPACA: real-time quotes via Alpaca's own market-data API (a
        SEPARATE base URL, data.alpaca.markets, from the trading API — Alpaca splits
        trading and market-data onto different hosts, unlike E*Trade's single-host design).
        Free-tier Alpaca market data (IEX feed) is US-equities-only, matching
        EtradeBroker.get_quote()'s own "no HK market access" caveat exactly — this can only
        ever supplement, never replace, this app's yfinance-based HK coverage.
        """
        if not symbols:
            return []
        resp = requests.get(
            f"{_DATA_BASE}/v2/stocks/quotes/latest",
            params={"symbols": ",".join(symbols)},
            headers=self._headers(), timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"Alpaca get_quote failed: {resp.status_code} {resp.text}")
        quotes_by_symbol = resp.json().get("quotes", {})
        results: list[BrokerQuote] = []
        for sym in symbols:
            q = quotes_by_symbol.get(sym)
            if not q:
                # A symbol Alpaca can't quote (bad ticker, no IEX coverage) degrades to an
                # all-None quote rather than raising — matches EtradeBroker.get_quote()'s own
                # per-item fail-soft convention (a bad symbol never aborts the whole batch).
                results.append(BrokerQuote(symbol=sym, last_price=None))
                continue
            bid = float(q["bp"]) if q.get("bp") else None
            ask = float(q["ap"]) if q.get("ap") else None
            # Alpaca's quotes-latest endpoint returns bid/ask only, no last-trade/prev-close/
            # volume fields — last_price is approximated as the midpoint of bid/ask when both
            # are present, matching how a real quote screen would read it; None when either
            # side is missing rather than fabricating a value from a single one-sided quote.
            last = (bid + ask) / 2 if (bid is not None and ask is not None) else None
            results.append(BrokerQuote(
                symbol=sym, last_price=last, bid=bid, ask=ask,
            ))
        return results

    def is_market_open(self) -> bool:
        try:
            resp = requests.get(
                f"{self._base}/v2/clock", headers=self._headers(), timeout=10,
            )
            if resp.ok:
                return bool(resp.json().get("is_open", False))
        except Exception:
            pass
        # Fail open to the same fixed-hours approximation every other broker adapter in this
        # file uses, rather than raising — is_market_open() has no documented error contract
        # for a caller to handle, unlike every other method here.
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return False
        return 1430 <= now.hour * 100 + now.minute <= 2100
