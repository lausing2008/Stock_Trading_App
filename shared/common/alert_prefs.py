"""AUD-ALERTPREFS (2026-09-24): per-alert-type subscription preferences and one-click unsubscribe.

THE PROBLEM THIS FIXES. Every scheduled alert on this platform addressed "any user holding at
least one untriggered PriceAlert row", and never compared the alert's own symbol against that
row. A single price alert on a single ticker therefore subscribed a user to every candidate on
every symbol, across squeeze, pre-breakout, gamma-unwind, options-flow, dark-pool and the rest.
There was no per-type preference, and `send_email()` had no unsubscribe path at all — so the
only way to stop any of it was to delete your price alerts, which also stopped the alerts you
wanted. Found while auditing why the Short Squeeze Alert had gone quiet.

TWO DESIGN DECISIONS WORTH KNOWING:

**Each type declares its default.** Existing operational alerts default on to preserve their
established subscriptions. A new trade-opportunity channel defaults off: absence of a row is
not consent to receive a new class of trade prompt.

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
    # Separate, explicit opt-in channels; neither inherits AI-signal enrolment.
    {"key": "little_rzy", "group": "Signals", "label": "Little RZY-inspired pattern alerts",
     "desc": "Completed-candle continuation signals and separate reversal watches for your US watchlist.",
     "default_enabled": False},
    {"key": "short_term_digest", "group": "Digests", "label": "Short-term watchlist morning digest",
     "desc": "Conditional day-trading preparation 20 minutes before US regular open, separate from AI signals.",
     "default_enabled": False},
    # Trading signals
    {"key": "signal",              "group": "Signals",   "label": "AI Signal changes",
     "desc": "A watchlist stock's AI signal flips (e.g. HOLD to BUY).",
     "default_enabled": True},
    {"key": "top3_conviction",     "group": "Signals",   "label": "Top conviction picks",
     "desc": "The day's highest-conviction names.",
     "default_enabled": True},
    {"key": "sector_rotation",     "group": "Signals",   "label": "Sector rotation",
     "desc": "Money moving between sectors.",
     "default_enabled": True},

    # Squeeze family
    {"key": "short_squeeze",       "group": "Squeeze",   "label": "Short Squeeze Alert",
     "desc": "Heavily-shorted stock moving up hard right now. Measured win rate has been poor — "
             "see the Squeeze Alert Performance page before acting on it.",
     "default_enabled": True},
    {"key": "squeeze_ignition",    "group": "Squeeze",   "label": "Squeeze Watch (early stage)",
     "desc": "The same setup one stage earlier, below the confirmation threshold.",
     "default_enabled": True},
    {"key": "prebreakout",         "group": "Squeeze",   "label": "Pre-Breakout Watch",
     "desc": "Heavily-shorted stocks compressing toward a 6-month low in volatility.",
     "default_enabled": True},

    # Options / flow
    {"key": "gamma_unwind",        "group": "Options",   "label": "Options Expiry Watch",
     "desc": "Concentrated open interest near the money, close to expiry.",
     "default_enabled": True},
    {"key": "options_flow",        "group": "Options",   "label": "Options flow alerts",
     "desc": "Unusual options activity.",
     "default_enabled": True},
    {"key": "option_opportunity", "group": "Options",   "label": "Actionable option opportunities",
     "desc": "Only fully gated option opportunities with frozen risk and contract details.",
     "default_enabled": False},
    {"key": "dark_pool",           "group": "Options",   "label": "Dark pool prints",
     "desc": "Large off-exchange block trades.",
     "default_enabled": True},

    # Market events
    # PRICE RISK (2026-10-12). Two keys, not one, because the design calls for independent
    # in-app and email control and this table stores one boolean per type — so "independent"
    # has to be two rows, not a second column nobody would see. Both DEFAULT OFF: neither has
    # an established subscription to preserve, and the channel is in shadow mode with
    # deliberately unvalidated thresholds. Turning them on is a decision, not an omission.
    {"key": "price_risk", "group": "Market", "label": "Price-risk email alerts",
     "desc": "Email when a followed stock, theme or benchmark makes a material measured move "
             "— independent of the AI signal's direction label, which does not change merely "
             "because a price falls.",
     "default_enabled": False},
    {"key": "price_risk_in_app", "group": "Market", "label": "Price-risk in-app notices",
     "desc": "The same detections as a notice inside the app, controlled separately from "
             "email so one can be on without the other.",
     "default_enabled": False},
    {"key": "product_impact", "group": "Market", "label": "Product impact watches",
     "desc": "Sourced product milestones for companies you follow and related suppliers or "
             "competitors. Research watches, not trade signals.",
     # DEFAULTS OFF (2026-10-12). A BRAND-NEW channel has no established subscription to
     # preserve, so the reason the operational alerts above default on does not apply to it.
     # Absence of a row is not consent to receive a new class of mail, and a watchlist entry
     # is consent to track a symbol rather than to be emailed about it. One word to flip.
     "default_enabled": False},
    {"key": "volume_anomaly",      "group": "Market",    "label": "Volume anomalies",
     "desc": "Unusual volume against a stock's own baseline.",
     "default_enabled": True},
    {"key": "earnings_screener",   "group": "Market",    "label": "Earnings beat screener",
     "desc": "Fresh earnings surprises.",
     "default_enabled": True},
    {"key": "earnings_reminder",   "group": "Market",    "label": "Earnings reminders",
     "desc": "Upcoming reports for stocks you follow.",
     "default_enabled": True},
    {"key": "sr_watch",            "group": "Market",    "label": "Support/resistance watch",
     "desc": "Price approaching a level you are watching.",
     "default_enabled": True},
    {"key": "value_area",          "group": "Market",    "label": "Value area breakdown",
     "desc": "Price leaving its established value area.",
     "default_enabled": True},
    {"key": "theme_forecast",      "group": "Market",    "label": "Theme forecast",
     "desc": "Emerging cross-stock themes.",
     "default_enabled": True},

    # Digests
    {"key": "morning_digest",      "group": "Digests",   "label": "Morning digest",
     "default_enabled": True},
    {"key": "premarket_brief",     "group": "Digests",   "label": "Pre-market brief",
     "default_enabled": True},
    {"key": "post_open_digest",    "group": "Digests",   "label": "Post-open digest",
     "default_enabled": True},
    {"key": "portfolio_digest",    "group": "Digests",   "label": "Paper portfolio digest",
     "default_enabled": True},
    {"key": "trade_coach",         "group": "Digests",   "label": "Trade pattern coach",
     "default_enabled": True},

    # Portfolio
    {"key": "portfolio_drawdown",  "group": "Portfolio", "label": "Drawdown alerts",
     "desc": "Your paper portfolio breaching a drawdown limit.",
     "default_enabled": True},
    {"key": "trade_exit",          "group": "Portfolio", "label": "Trade exits",
     "desc": "A paper position closing.",
     "default_enabled": True},
    {"key": "squeeze_watch_revert", "group": "Portfolio", "label": "Squeeze watch reverts",
     "desc": "A stock you added to a squeeze watch losing the setup.",
     "default_enabled": True},
    # EA-05 (2026-09-28 email audit): the flow digest was sent to EVERY user with a nonempty
    # address — no active-account predicate, no preference check, and no entry here, so there
    # was no way to turn it off. Registered so the settings page can manage it and
    # test_every_manageable_alert_type_is_enforced can hold its delivery path to the same bar.
    {"key": "flow_digest",         "group": "Market", "label": "Options & dark-pool digest",
     "desc": "A periodic summary of unusual options activity and dark-pool prints.",
     "default_enabled": True},
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


# ── Effective preference: ONE answer, used by settings AND by every sender ────

def default_enabled_for(alert_type: str) -> bool:
    """What a type means for an account that has never configured it.

    An UNKNOWN type returns False. A type not in the registry is not a channel anyone was
    offered, so it has no established subscription to preserve, and guessing True would
    reintroduce the defect this function exists to close.
    """
    entry = _BY_KEY.get(alert_type)
    if entry is None:
        return False
    return bool(entry.get("default_enabled", False))


def effective_enabled(alert_type: str, stored: bool | None, *,
                      lookup_failed: bool = False) -> bool:
    """May this account be sent `alert_type`? The only implementation of that question.

    AQ-REVIEW-2 (2026-10-12). This existed in three places that did not agree. The settings
    endpoint read the registry's declared default; the SENDER did not — `_may_send()` returned
    True for a missing row and True again when the lookup raised, so an account could see a
    channel switched off in settings while the sender admitted it. Declaring a default that only
    the settings page honours is not a default; it is a label on a screen.

    FAILURE FALLS BACK TO THE DECLARED DEFAULT, which is deliberately not the same thing as
    "fail open". For the long-standing types whose default is True this IS fail-open, and the
    original reasoning still holds: silently dropping mail because a settings query failed is
    indistinguishable, from the outside, from the alert never having fired. For an opt-in
    channel it is fail-CLOSED, because the matching failure there is sending a kind of message
    to somebody who never agreed to receive it — and that one cannot be taken back.

    ESSENTIAL is answered first and never consults a preference at all; see ESSENTIAL above for
    why those are not representable as a preference.
    """
    if alert_type in ESSENTIAL:
        return True
    if lookup_failed or stored is None:
        return default_enabled_for(alert_type)
    return bool(stored)


def opt_in_only(alert_type: str) -> bool:
    """True when absence of a row means NOT subscribed.

    Callers that filter a whole recipient set need this: the long-standing query shape asks for
    `enabled = FALSE` rows and removes them, which cannot express "keep only those who said
    yes". A type answering True here needs the opposite query, not a tweak to that one.
    """
    return not default_enabled_for(alert_type) and alert_type not in ESSENTIAL


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
