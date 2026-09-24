"""AUD-ALERTPREFS (2026-09-24): per-alert-type subscription preferences and one-click unsubscribe.

THE PROBLEM THIS FIXES. Every scheduled alert on this platform addressed "any user holding at
least one untriggered PriceAlert row", and never compared the alert's own symbol against that
row. A single price alert on a single ticker therefore subscribed a user to every candidate on
every symbol, across squeeze, pre-breakout, gamma-unwind, options-flow, dark-pool and the rest.
There was no per-type preference, and `send_email()` had no unsubscribe path at all — so the
only way to stop any of it was to delete your price alerts, which also stopped the alerts you
wanted. Found while auditing why the Short Squeeze Alert had gone quiet.

TWO DESIGN DECISIONS WORTH KNOWING:

**Absence means subscribed.** No preference rows are created up front and a missing row reads as
opted IN, so shipping this changes nobody's mail on day one. The opposite default would have
silently switched off every alert on the platform at deploy time — a much worse failure than the
one being fixed, and one that would have looked like the mail system breaking.

**Unsubscribe tokens are stateless HMACs, not stored secrets.** A mail client must be able to
act on a link months later without a session, so the token has to survive with no server-side
row. It is signed with the same `jwt_secret` every service already shares, and it is scoped to
(user_id, alert_type) so a leaked link can silence exactly one alert type for one account and
nothing else. There is deliberately NO expiry: an unsubscribe link that stops working is a link
that traps someone in mail they have asked to leave.
"""
from __future__ import annotations

import hashlib
import hmac

# ── The registry ──────────────────────────────────────────────────────────────
#
# `key` is what the sender passes and what is stored in alert_preferences.alert_type. Adding a
# type here makes it manageable; it needs no migration, and an unknown key simply matches no
# sender. `group` exists so the settings UI can offer "turn off all of these" without this
# module knowing anything about the UI.

ALERT_TYPES: list[dict] = [
    # Trading signals
    {"key": "signal",              "group": "Signals",   "label": "AI Signal changes",
     "desc": "A watchlist stock's AI signal flips (e.g. HOLD to BUY)."},
    {"key": "top3_conviction",     "group": "Signals",   "label": "Top conviction picks",
     "desc": "The day's highest-conviction names."},
    {"key": "sector_rotation",     "group": "Signals",   "label": "Sector rotation",
     "desc": "Money moving between sectors."},

    # Squeeze family
    {"key": "short_squeeze",       "group": "Squeeze",   "label": "Short Squeeze Alert",
     "desc": "Heavily-shorted stock moving up hard right now. Measured win rate has been poor — "
             "see the Squeeze Alert Performance page before acting on it."},
    {"key": "squeeze_ignition",    "group": "Squeeze",   "label": "Squeeze Watch (early stage)",
     "desc": "The same setup one stage earlier, below the confirmation threshold."},
    {"key": "prebreakout",         "group": "Squeeze",   "label": "Pre-Breakout Watch",
     "desc": "Heavily-shorted stocks compressing toward a 6-month low in volatility."},

    # Options / flow
    {"key": "gamma_unwind",        "group": "Options",   "label": "Options Expiry Watch",
     "desc": "Concentrated open interest near the money, close to expiry."},
    {"key": "options_flow",        "group": "Options",   "label": "Options flow alerts",
     "desc": "Unusual options activity."},
    {"key": "dark_pool",           "group": "Options",   "label": "Dark pool prints",
     "desc": "Large off-exchange block trades."},

    # Market events
    {"key": "volume_anomaly",      "group": "Market",    "label": "Volume anomalies",
     "desc": "Unusual volume against a stock's own baseline."},
    {"key": "earnings_screener",   "group": "Market",    "label": "Earnings beat screener",
     "desc": "Fresh earnings surprises."},
    {"key": "earnings_reminder",   "group": "Market",    "label": "Earnings reminders",
     "desc": "Upcoming reports for stocks you follow."},
    {"key": "sr_watch",            "group": "Market",    "label": "Support/resistance watch",
     "desc": "Price approaching a level you are watching."},
    {"key": "value_area",          "group": "Market",    "label": "Value area breakdown",
     "desc": "Price leaving its established value area."},
    {"key": "theme_forecast",      "group": "Market",    "label": "Theme forecast",
     "desc": "Emerging cross-stock themes."},

    # Digests
    {"key": "morning_digest",      "group": "Digests",   "label": "Morning digest"},
    {"key": "premarket_brief",     "group": "Digests",   "label": "Pre-market brief"},
    {"key": "post_open_digest",    "group": "Digests",   "label": "Post-open digest"},
    {"key": "portfolio_digest",    "group": "Digests",   "label": "Paper portfolio digest"},
    {"key": "trade_coach",         "group": "Digests",   "label": "Trade pattern coach"},

    # Portfolio
    {"key": "portfolio_drawdown",  "group": "Portfolio", "label": "Drawdown alerts",
     "desc": "Your paper portfolio breaching a drawdown limit."},
    {"key": "trade_exit",          "group": "Portfolio", "label": "Trade exits",
     "desc": "A paper position closing."},
    {"key": "squeeze_watch_revert", "group": "Portfolio", "label": "Squeeze watch reverts",
     "desc": "A stock you added to a squeeze watch losing the setup."},
]

_BY_KEY = {a["key"]: a for a in ALERT_TYPES}

# ESSENTIAL MAIL IS NOT REPRESENTABLE AS A PREFERENCE AND NEVER CHECKS ONE.
#
# These are not "important" alerts — that is a judgement the user gets to make for everything in
# ALERT_TYPES. They are messages where suppressing delivery would break something the user
# explicitly asked for or leave an account silently broken:
#
#   price_alert    — the user created this exact alert, on this exact symbol, themselves. It is
#                    unsubscribed by deleting the alert, which is where the control already is.
#   conditional_order — confirms an order the user placed actually fired. Silence here is
#                    indistinguishable from the order never running.
#   broker_reauth  — the broker link is dead until they act; not telling them strands the account.
#   data_quality / llm_usage_spike — operator alerts to admins, not user mail.
ESSENTIAL: frozenset = frozenset({
    "price_alert", "conditional_order", "broker_reauth", "data_quality", "llm_usage_spike",
})


def is_known_alert_type(alert_type: str) -> bool:
    return alert_type in _BY_KEY


def label_for(alert_type: str) -> str:
    entry = _BY_KEY.get(alert_type)
    return entry["label"] if entry else alert_type


def is_manageable(alert_type: str | None) -> bool:
    """True when this type can be turned off. Essential and unknown types cannot."""
    if not alert_type or alert_type in ESSENTIAL:
        return False
    return alert_type in _BY_KEY


# ── Stateless unsubscribe tokens ──────────────────────────────────────────────

def _sign(user_id: int, alert_type: str, secret: str) -> str:
    msg = f"{user_id}:{alert_type}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def make_unsubscribe_token(user_id: int, alert_type: str, secret: str) -> str:
    return _sign(user_id, alert_type, secret)


def verify_unsubscribe_token(user_id: int, alert_type: str, token: str, secret: str) -> bool:
    """Constant-time comparison — a token check that leaks timing is a token check that can be
    brute-forced one character at a time."""
    if not token or not secret:
        return False
    return hmac.compare_digest(_sign(user_id, alert_type, secret), token)
