"""Action DRAFTS: the copilot proposes, the user confirms, the existing APIs save.

THE COPILOT GAINS NO WRITE AUTHORITY. A draft is a validated description of a change. Nothing is
saved until the user presses Save, and the save goes through the same market-data endpoint the
ordinary UI uses, with the user's own credentials and that endpoint's own ownership checks. There
is no copilot write endpoint into alerts or watchlists, and no model output can reach one.

WHAT A DRAFT MAY CONTAIN, decided by code, not by the model:
  - only the conversation's own symbol;
  - only when the user's question asks for an action ("alert me", "add to my watchlist") — a
    draft attached to "what is the trend?" is dropped;
  - a price threshold must be a packet PRICE quantity ({{trend.sma50}}, resolved to its stored
    value here) or a number the user typed; anything else is dropped with the reason shown.

DISCLOSED BEFORE SAVING (operator decision D1). Each draft carries its effects as the platform
will apply them: a watchlist add's signal-email enrolment comes from market-data's own preview
(the same helper the add uses), including whether mail can actually be sent.

CONFIRMED SAFELY. A draft expires, is bound to the state it was drafted against (a hash of the
user's relevant alerts or list membership), and saves idempotently: price alerts send a draft-
scoped idempotency key (D2), watchlist adds and signal-alert subscriptions are already
idempotent on market-data's side.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta

DRAFT_TTL = timedelta(minutes=15)
MAX_DRAFTS = 2
KINDS = ("price_alert", "watchlist_add", "signal_alert")
#: Packet quantities that are PRICES, and so may be a price-alert threshold.
PRICE_KEYS = ("price.close", "trend.sma20", "trend.sma50", "kscore.fair_price")
HORIZONS = ("SHORT", "SWING", "LONG", "GROWTH")

_DRAFT_TAG = re.compile(r"<draft\b([^>]*)/?>(?:\s*</draft>)?", re.I | re.S)
_ATTR = re.compile(r'([a-z_]+)\s*=\s*"([^"]*)"', re.I)
_PLACEHOLDER = re.compile(r"^\{\{\s*([A-Za-z0-9_.:-]{1,64})\s*\}\}$")
_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")
#: The user asked for an ACTION. A draft attached to an informational question is dropped.
_INTENT = {
    "price_alert": re.compile(r"\b(?:alert|notify|tell me|let me know|ping me|warn me|remind)",
                              re.I),
    "signal_alert": re.compile(r"\b(?:alert|notify|tell me|let me know|subscribe|signal change)",
                               re.I),
    "watchlist_add": re.compile(r"\b(?:watch\s*list|watchlist|watch it|track it|keep an eye|"
                                r"follow it)", re.I),
}


def strip_tags(text: str) -> tuple[str, list[dict]]:
    """Remove <draft> tags from model text; return the text and each tag's attributes."""
    found = [dict((k.lower(), v.strip()) for k, v in _ATTR.findall(m.group(1)))
             for m in _DRAFT_TAG.finditer(text or "")]
    return _DRAFT_TAG.sub("", text or "").strip(), found


@dataclass
class DraftResult:
    drafts: list[dict] = dc_field(default_factory=list)
    dropped: list[dict] = dc_field(default_factory=list)


def _money(v: float, ccy: str | None) -> str:
    return f"{'HK$' if (ccy or '').upper() == 'HKD' else '$'}{v:,.2f}"


def validate(raw: list[dict], *, packet, question: str) -> DraftResult:
    """Proposed drafts → validated arguments, or dropped with the reason. No I/O here."""
    out = DraftResult()
    nums = {float(x) for x in _NUM.findall(question)}
    for d in raw:
        kind = (d.get("kind") or "").lower()

        def drop(reason):
            out.dropped.append({"kind": kind or "?", "reason": reason})
        if len(out.drafts) >= MAX_DRAFTS:      # the cap counts ACCEPTED drafts
            drop(f"at most {MAX_DRAFTS} drafts per answer"); continue
        if kind not in KINDS:
            drop("not an action the copilot can draft"); continue
        if d.get("symbol") and d["symbol"].upper() != packet.symbol:
            drop("drafts are limited to this conversation's symbol"); continue
        if not _INTENT[kind].search(question):
            drop("you did not ask for this action"); continue
        if kind == "price_alert":
            cond = (d.get("condition") or "").lower()
            if cond not in ("above", "below"):
                drop("condition must be above or below"); continue
            t = d.get("threshold") or ""
            m = _PLACEHOLDER.match(t)
            if m:
                key = m.group(1)
                q = packet.quantities.get(key)
                if key not in PRICE_KEYS or q is None:
                    drop(f"{{{{{key}}}}} is not a price on file for {packet.symbol}"); continue
                fkey = q.field
                f = packet.fields.get(fkey)
                value = _price_value(key, f)
                if value is None:
                    drop(f"{{{{{key}}}}} has no stored value"); continue
                source = {"kind": "packet", "key": key, "display": q.display, "state": q.state}
            else:
                try:
                    value = float(t)
                except ValueError:
                    drop("threshold must be a packet price or a number you typed"); continue
                if value not in nums:
                    drop("this threshold does not appear in your question"); continue
                source = {"kind": "typed", "key": None, "display": _money(value, packet.currency)}
            if value <= 0:
                drop("threshold must be positive"); continue
            out.drafts.append({
                "kind": kind,
                "arguments": {"symbol": packet.symbol, "condition": cond,
                              "threshold": round(value, 4)},
                "threshold_source": source,
                "summary": (f"Price alert: email me when {packet.symbol} closes "
                            f"{cond} {_money(value, packet.currency)}"
                            + (f" ({q.meaning or key})" if source["kind"] == "packet" else "")),
            })
        elif kind == "signal_alert":
            horizon = (d.get("horizon") or "SWING").upper()
            mode = (d.get("mode") or "all").lower()
            if horizon not in HORIZONS:
                drop("not a signal horizon"); continue
            if mode not in ("all", "buy_only"):
                drop("mode must be all or buy_only"); continue
            out.drafts.append({
                "kind": kind,
                "arguments": {"symbol": packet.symbol, "horizon": horizon, "alert_mode": mode},
                "summary": (f"Signal alert: email me when the {horizon} signal for "
                            f"{packet.symbol} changes" + (" to BUY" if mode == "buy_only" else "")),
            })
        else:
            out.drafts.append({"kind": kind, "arguments": {"symbol": packet.symbol},
                               "summary": f"Add {packet.symbol} to your default watchlist"})
    return out


def _price_value(key: str, f) -> float | None:
    v = getattr(f, "value", None)
    if not isinstance(v, dict):
        return None
    return {"price.close": v.get("close"), "trend.sma20": v.get("sma20"),
            "trend.sma50": v.get("sma50"), "kscore.fair_price": v.get("fair_price")}.get(key)


# ── State binding and execution through market-data, as the user ──────────────

class MarketClient:
    """market-data's existing endpoints, called with the USER'S Authorization header."""
    def __init__(self, base_url: str, authorization: str):
        self.base, self.auth = base_url.rstrip("/"), authorization

    async def _req(self, method: str, path: str, **kw):
        import httpx
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.request(method, f"{self.base}{path}",
                                headers={"Authorization": self.auth}, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text[:200]
            raise MarketError(r.status_code, str(detail))
        return r.json() if r.content else {}

    async def alerts(self):
        return await self._req("GET", "/alerts")

    async def signal_alerts(self):
        return await self._req("GET", "/signal-alerts")

    async def watchlist_preview(self, symbol):
        return await self._req("GET", f"/watchlist/{symbol}/preview")

    async def create_alert(self, body):
        return await self._req("POST", "/alerts", json=body)

    async def create_signal_alert(self, body):
        return await self._req("POST", "/signal-alerts", json=body)

    async def add_to_watchlist(self, symbol):
        return await self._req("POST", f"/watchlist/{symbol}")


class MarketError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:24]


async def state_and_effects(market: MarketClient, draft: dict) -> tuple[str, list[str], dict]:
    """The state a draft is bound to, the effects to disclose, and any preview payload."""
    a = draft["arguments"]
    sym = a["symbol"]
    if draft["kind"] == "price_alert":
        mine = [x for x in await market.alerts() if x.get("symbol") == sym and not x.get("triggered")]
        same = [x for x in mine if x.get("condition") == a["condition"]
                and abs(float(x.get("threshold", 0)) - a["threshold"]) < 1e-9]
        effects = ["Sends an email to your account address when the condition is met.",
                   "Price alerts are essential notifications: they cannot be turned off in "
                   "alert preferences, only deleted."]
        if same:
            effects.append("You already have an identical active alert; saving creates another.")
        state = _hash(sorted((x.get("id"), x.get("condition"), x.get("threshold")) for x in mine))
        return state, effects, {"existing_active_alerts_for_symbol": len(mine)}
    if draft["kind"] == "signal_alert":
        subs = [x for x in await market.signal_alerts()
                if x.get("symbol") == sym and x.get("horizon") == a["horizon"]]
        effects = ([f"You are already subscribed to {a['horizon']} signal alerts for {sym}; "
                    f"saving keeps the existing subscription."] if subs else
                   [f"Subscribes you to {a['horizon']} signal-change emails for {sym}."])
        return _hash([(x.get("id"), x.get("alert_mode")) for x in subs]), effects, {}
    p = await market.watchlist_preview(sym)
    return (_hash([p.get("already_in_list"), p.get("signal_subscription"), p.get("list")]),
            [p.get("effect", "")], p)


async def finalise(market: MarketClient, validated: DraftResult, now: datetime) -> DraftResult:
    """Attach id, expiry, idempotency key, bound state and disclosed effects to each draft."""
    out = DraftResult(dropped=list(validated.dropped))
    for d in validated.drafts:
        try:
            state, effects, preview = await state_and_effects(market, d)
        except MarketError as exc:
            out.dropped.append({"kind": d["kind"], "reason": f"could not check your current "
                                                            f"state ({exc.status}): {exc.detail}"})
            continue
        out.drafts.append({**d, "id": uuid.uuid4().hex[:12], "status": "proposed",
                           "idempotency_key": f"cop-{uuid.uuid4().hex}",
                           "state_version": state, "side_effects": effects, "preview": preview,
                           "expires_at": (now + DRAFT_TTL).isoformat()})
    return out


async def execute(market: MarketClient, draft: dict) -> dict:
    a = draft["arguments"]
    if draft["kind"] == "price_alert":
        return await market.create_alert({
            "symbol": a["symbol"], "condition": a["condition"], "threshold": a["threshold"],
            "note": "Created from a copilot draft", "idempotency_key": draft["idempotency_key"]})
    if draft["kind"] == "signal_alert":
        return await market.create_signal_alert({
            "symbol": a["symbol"], "horizon": a["horizon"], "alert_mode": a["alert_mode"]})
    return await market.add_to_watchlist(a["symbol"])
