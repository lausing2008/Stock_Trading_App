"""E*Trade broker adapter — OAuth 1.0a REST API.

OAuth flow (must run once per calendar day — E*Trade tokens expire at midnight ET):
  1. start_oauth()          → returns the E*Trade authorize URL
  2. User visits URL, authorizes the app, E*Trade shows a PIN/verifier
  3. complete_oauth(verifier) → exchanges verifier for access_token + token_secret
     Both are stored back into the config dict (caller must persist to DB)
  4. All subsequent calls use the stored access_token + token_secret

E*Trade API reference:
  https://developer.etrade.com/getting-started/developer-guides
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import requests
from requests_oauthlib import OAuth1

from .interface import (
    BrokerAccount, BrokerInterface, BrokerOrder, BrokerPosition, OrderSide, OrderType,
)

_PROD_BASE   = "https://api.etrade.com"
_SAND_BASE   = "https://apisb.etrade.com"
_AUTH_BASE   = "https://us.etrade.com/e/t/etws/authorize"


class EtradeBroker(BrokerInterface):
    """E*Trade REST API adapter (production or sandbox).

    config keys (stored encrypted in BrokerConnection.config):
      consumer_key        — from E*Trade developer portal
      consumer_secret     — from E*Trade developer portal
      oauth_token         — access token (after OAuth complete)
      oauth_token_secret  — access token secret (after OAuth complete)
      request_token       — temp token (only valid during OAuth flow)
      request_token_secret— temp secret (only valid during OAuth flow)
    """

    def __init__(self, config: dict, sandbox: bool = False):
        self._config  = config
        self._sandbox = sandbox
        self._base    = _SAND_BASE if sandbox else _PROD_BASE

    # ── OAuth helpers ─────────────────────────────────────────────────────────

    def _oauth1_base(self) -> OAuth1:
        """OAuth1 using only consumer key/secret — for request-token step."""
        return OAuth1(
            self._config["consumer_key"],
            self._config["consumer_secret"],
        )

    def _oauth1(self) -> OAuth1:
        """OAuth1 using all four credentials — for authenticated API calls."""
        return OAuth1(
            self._config["consumer_key"],
            self._config["consumer_secret"],
            self._config.get("oauth_token"),
            self._config.get("oauth_token_secret"),
        )

    def start_oauth(self) -> str:
        """Step 1: Request a temp token from E*Trade and return the authorize URL.
        Stores request_token + request_token_secret in self._config (caller must save to DB).
        """
        resp = requests.get(
            f"{_PROD_BASE}/oauth/request_token",  # always prod endpoint for OAuth
            params={"format": "json", "oauth_callback": "oob"},
            auth=self._oauth1_base(),
            timeout=10,
        )
        if not resp.ok:
            raise RuntimeError(f"E*Trade request_token failed: {resp.status_code} {resp.text}")
        # Response is URL-encoded: oauth_token=...&oauth_token_secret=...
        from urllib.parse import parse_qs
        parsed = parse_qs(resp.text)
        self._config["request_token"]        = parsed["oauth_token"][0]
        self._config["request_token_secret"] = parsed["oauth_token_secret"][0]
        return (
            f"{_AUTH_BASE}?key={self._config['consumer_key']}"
            f"&token={self._config['request_token']}"
        )

    def complete_oauth(self, verifier: str) -> None:
        """Step 3: Exchange the verifier for access tokens.
        Updates self._config with oauth_token + oauth_token_secret (caller must save to DB).
        """
        auth = OAuth1(
            self._config["consumer_key"],
            self._config["consumer_secret"],
            self._config.get("request_token"),
            self._config.get("request_token_secret"),
            verifier=verifier,
        )
        resp = requests.get(
            f"{_PROD_BASE}/oauth/access_token",
            params={"format": "json"},
            auth=auth,
            timeout=10,
        )
        if not resp.ok:
            raise RuntimeError(f"E*Trade access_token failed: {resp.status_code} {resp.text}")
        from urllib.parse import parse_qs
        parsed = parse_qs(resp.text)
        self._config["oauth_token"]        = parsed["oauth_token"][0]
        self._config["oauth_token_secret"] = parsed["oauth_token_secret"][0]
        # Clean up temp tokens
        self._config.pop("request_token",        None)
        self._config.pop("request_token_secret", None)

    def renew_access_token(self) -> None:
        """Renew the access token (must be called once per trading day at session start)."""
        resp = requests.get(
            f"{_PROD_BASE}/oauth/renew_access_token",
            auth=self._oauth1(),
            timeout=10,
        )
        if not resp.ok:
            raise RuntimeError(f"E*Trade renew failed: {resp.status_code} {resp.text}")

    # ── Account info ──────────────────────────────────────────────────────────

    def _account_id_key(self, account_id: str | None) -> str:
        key = account_id or self._config.get("account_id_key", "")
        if not key:
            raise RuntimeError("No E*Trade account_id_key configured. Run list_accounts() first.")
        return key

    def list_accounts(self) -> list[dict]:
        """Return list of E*Trade accounts (each has accountIdKey, accountId, accountType)."""
        resp = requests.get(
            f"{self._base}/v1/accounts/list.json",
            auth=self._oauth1(),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"E*Trade list_accounts failed: {resp.status_code} {resp.text}")
        data = resp.json()
        return data.get("AccountListResponse", {}).get("Accounts", {}).get("Account", [])

    def get_account(self, account_id: str | None = None) -> BrokerAccount:
        key = self._account_id_key(account_id)
        resp = requests.get(
            f"{self._base}/v1/accounts/{key}/balance.json",
            params={"instType": "BROKERAGE", "realTimeNAV": "true"},
            auth=self._oauth1(),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"E*Trade balance failed: {resp.status_code} {resp.text}")
        bal = resp.json().get("BalanceResponse", {})
        computed = bal.get("Computed", {})

        positions = self._get_positions_raw(key)
        return BrokerAccount(
            account_id        = bal.get("accountId", key),
            broker_type       = "etrade_sandbox" if self._sandbox else "etrade",
            cash_available    = float(computed.get("cashAvailableForInvestment", 0)),
            equity            = float(computed.get("RealTimeValues", {}).get("totalAccountValue", 0)),
            buying_power      = float(computed.get("marginBuyingPower",
                                       computed.get("cashAvailableForInvestment", 0))),
            open_positions    = positions,
        )

    def _get_positions_raw(self, key: str) -> list[BrokerPosition]:
        resp = requests.get(
            f"{self._base}/v1/accounts/{key}/portfolio.json",
            auth=self._oauth1(),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if not resp.ok:
            return []
        portfolio = resp.json().get("PortfolioResponse", {})
        positions = []
        for acct in portfolio.get("AccountPortfolio", []):
            for pos in acct.get("Position", []):
                qty  = float(pos.get("quantity", 0))
                cost = float(pos.get("costPerShare", 0))
                mval = float(pos.get("marketValue", 0))
                pnl  = float(pos.get("totalGain", 0))
                pnl_pct = (pnl / (cost * qty)) if cost * qty else 0.0
                positions.append(BrokerPosition(
                    symbol             = pos.get("symbolDescription", ""),
                    qty                = qty,
                    avg_cost           = cost,
                    market_value       = mval,
                    unrealized_pnl     = pnl,
                    unrealized_pnl_pct = pnl_pct,
                ))
        return positions

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self, symbol, qty, side, order_type=OrderType.MARKET,
        limit_price=None, stop_price=None, time_in_force="day", account_id=None,
    ) -> BrokerOrder:
        key = self._account_id_key(account_id)
        order_action = "BUY" if side == OrderSide.BUY else "SELL"
        price_type = {
            OrderType.MARKET:     "MARKET",
            OrderType.LIMIT:      "LIMIT",
            OrderType.STOP:       "STOP",
            OrderType.STOP_LIMIT: "STOP_LIMIT",
        }[order_type]

        payload = {
            "PlaceOrderRequest": {
                "orderType": "EQ",
                "clientOrderId": str(uuid.uuid4())[:20],
                "Order": [{
                    "allOrNone": "false",
                    "priceType": price_type,
                    "orderTerm": "GOOD_FOR_DAY",
                    "marketSession": "REGULAR",
                    "stopPrice": str(stop_price) if stop_price else "",
                    "limitPrice": str(limit_price) if limit_price else "",
                    "Instrument": [{
                        "Product": {"securityType": "EQ", "symbol": symbol},
                        "orderAction": order_action,
                        "quantityType": "QUANTITY",
                        "quantity": str(int(qty)),
                    }],
                }],
            }
        }
        resp = requests.post(
            f"{self._base}/v1/accounts/{key}/orders/place.json",
            json=payload,
            auth=self._oauth1(),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"E*Trade place_order failed: {resp.status_code} {resp.text}")

        data = resp.json().get("PlaceOrderResponse", {})
        order_id = str(data.get("OrderIds", [{}])[0].get("orderId", ""))
        return BrokerOrder(
            order_id   = order_id,
            symbol     = symbol,
            side       = side,
            qty        = qty,
            order_type = order_type,
            status     = "pending",
            limit_price = limit_price,
            stop_price  = stop_price,
        )

    def cancel_order(self, order_id: str, account_id: str | None = None) -> bool:
        key = self._account_id_key(account_id)
        payload = {"CancelOrderRequest": {"orderId": int(order_id)}}
        resp = requests.put(
            f"{self._base}/v1/accounts/{key}/orders/cancel.json",
            json=payload,
            auth=self._oauth1(),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        return resp.ok

    def get_order(self, order_id: str, account_id: str | None = None) -> BrokerOrder:
        key = self._account_id_key(account_id)
        resp = requests.get(
            f"{self._base}/v1/accounts/{key}/orders.json",
            params={"orderId": order_id},
            auth=self._oauth1(),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if not resp.ok:
            raise RuntimeError(f"E*Trade get_order failed: {resp.status_code} {resp.text}")
        orders = resp.json().get("OrdersResponse", {}).get("Order", [])
        if not orders:
            raise RuntimeError(f"Order {order_id} not found")
        o = orders[0]
        detail = o.get("OrderDetail", [{}])[0]
        instr  = detail.get("Instrument", [{}])[0]
        status_map = {
            "OPEN": "pending", "EXECUTED": "filled",
            "CANCELLED": "cancelled", "REJECTED": "rejected",
            "PARTIAL": "partially_filled",
        }
        raw_status = o.get("orderStatus", "OPEN")
        return BrokerOrder(
            order_id          = order_id,
            symbol            = instr.get("Product", {}).get("symbol", ""),
            side              = OrderSide.BUY if instr.get("orderAction") == "BUY" else OrderSide.SELL,
            qty               = float(instr.get("quantity", 0)),
            order_type        = OrderType.MARKET,
            status            = status_map.get(raw_status, raw_status.lower()),
            filled_qty        = float(instr.get("filledQuantity", 0)),
            filled_avg_price  = float(detail.get("averageExecutionPrice", 0)) or None,
        )

    def is_market_open(self) -> bool:
        now = datetime.now(timezone.utc)
        # US market hours: Mon–Fri 14:30–21:00 UTC (9:30am–4pm ET, ignoring DST for simplicity)
        if now.weekday() >= 5:
            return False
        return 1430 <= now.hour * 100 + now.minute <= 2100
