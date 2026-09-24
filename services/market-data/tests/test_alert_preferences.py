"""AUD-ALERTPREFS: per-alert-type opt-out, one-click unsubscribe, and the audience filter.

THE DEFECT. Every scheduled alert addressed "any user holding at least one untriggered
PriceAlert row", and never compared the alert's own symbol against that row. One price alert on
one ticker therefore subscribed a user to every candidate on every symbol across squeeze,
pre-breakout, gamma-unwind, options-flow and dark-pool. There was no per-type preference and no
unsubscribe path anywhere in send_email(), so the only way to stop any of it was to delete your
price alerts — which also stopped the alerts you wanted.

THE TWO PROPERTIES THAT MAKE THIS SAFE TO SHIP, both pinned below:

  1. ABSENCE MEANS SUBSCRIBED. No rows are created up front, and a missing row reads as opted
     IN. The opposite default would have silently switched off every alert on the platform the
     moment this deployed — a far worse failure than the one being fixed, and one that would
     have looked exactly like the mail system breaking.
  2. THE FILTER FAILS OPEN. If the preference lookup raises, everyone still gets their mail.
     Dropping alerts because a settings query failed is indistinguishable, from the outside,
     from the alert never having fired.

And the property that makes the unsubscribe link safe to leave unauthenticated: it is an HMAC
over exactly (user_id, alert_type), so a correctly-signed link can DISABLE one type for one
account and do nothing else.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from common.alert_prefs import (
    ALERT_TYPES,
    ESSENTIAL,
    is_manageable,
    label_for,
    make_unsubscribe_token,
    verify_unsubscribe_token,
)
from src.services.email_service import _unsubscribe_footer

SECRET = "test-secret-not-a-real-one"


# ── Token security ────────────────────────────────────────────────────────────

def test_a_valid_token_verifies():
    tok = make_unsubscribe_token(42, "short_squeeze", SECRET)
    assert verify_unsubscribe_token(42, "short_squeeze", tok, SECRET) is True


def test_a_token_is_scoped_to_one_user_and_one_alert_type():
    """This is the whole security model of an unauthenticated endpoint. A link that worked for
    a different user, or silenced a different alert, would turn a convenience into a way to
    switch off someone else's mail."""
    tok = make_unsubscribe_token(42, "short_squeeze", SECRET)
    assert verify_unsubscribe_token(43, "short_squeeze", tok, SECRET) is False
    assert verify_unsubscribe_token(42, "gamma_unwind", tok, SECRET) is False


def test_a_token_signed_with_a_different_secret_is_rejected():
    tok = make_unsubscribe_token(42, "short_squeeze", "other-secret")
    assert verify_unsubscribe_token(42, "short_squeeze", tok, SECRET) is False


def test_an_empty_or_missing_token_is_rejected_rather_than_treated_as_absent():
    assert verify_unsubscribe_token(42, "short_squeeze", "", SECRET) is False
    assert verify_unsubscribe_token(42, "short_squeeze", None, SECRET) is False


def test_an_empty_secret_never_validates_anything():
    """A misconfigured service must refuse every link, not accept every link.

    The token here is FORGED WITH THE EMPTY SECRET, not a random string — anyone can compute
    it, since the "secret" is known to be "". A test passing garbage would pass whether or not
    the empty-secret guard exists, because garbage fails the comparison anyway."""
    forged = make_unsubscribe_token(42, "short_squeeze", "")
    assert verify_unsubscribe_token(42, "short_squeeze", forged, "") is False


# ── What may and may not be turned off ────────────────────────────────────────

def test_the_four_alerts_the_user_asked_about_are_all_manageable():
    for key in ("short_squeeze", "squeeze_ignition", "prebreakout", "gamma_unwind"):
        assert is_manageable(key), key


def test_essential_mail_can_never_be_switched_off():
    """These are not merely important — suppressing them breaks something the user explicitly
    asked for, or strands an account. A user's own price alert is unsubscribed by deleting the
    alert, which is where that control already lives."""
    for key in ("price_alert", "conditional_order", "broker_reauth"):
        assert key in ESSENTIAL
        assert not is_manageable(key)


def test_an_essential_key_stays_unmanageable_even_if_added_to_the_registry():
    """ESSENTIAL is unreachable today — essential keys are simply absent from ALERT_TYPES, so
    the registry check already rejects them. The guard exists for the future maintainer who
    adds "price_alert" to the registry thinking it ought to be toggleable. This test makes that
    guard reachable, and would fail if it were removed as apparently-dead code."""
    import common.alert_prefs as ap
    patched = dict(ap._BY_KEY)
    patched["price_alert"] = {"key": "price_alert", "group": "X", "label": "Your price alerts"}
    with patch.object(ap, "_BY_KEY", patched):
        assert ap.is_known_alert_type("price_alert") is True
        assert ap.is_manageable("price_alert") is False


def test_a_type_absent_from_the_registry_is_not_manageable_even_when_not_essential():
    """Pins is_manageable()'s OWN registry check, distinct from is_known_alert_type()'s."""
    import common.alert_prefs as ap
    assert ap.is_manageable("totally_made_up") is False


def test_the_token_comparison_is_constant_time():
    """A timing difference cannot be observed from a unit test, so this asserts the mechanism
    instead: an equality comparison on an HMAC leaks how many leading characters matched, which
    is enough to recover a valid token one character at a time. Structural assertion, no
    threshold pinned (AUD-T401-SOURCETEXTTESTS)."""
    import inspect

    import common.alert_prefs as ap
    src = inspect.getsource(ap.verify_unsubscribe_token)
    assert "compare_digest" in src
    assert "== token" not in src


def test_an_unknown_alert_type_is_not_manageable():
    """Guards the settings API against storing a row that looks effective but is consulted by
    no sender — a user who 'turned something off' and kept receiving it has every reason to
    distrust the entire screen."""
    assert not is_manageable("not_a_real_alert")
    assert not is_manageable(None)
    assert not is_manageable("")


def test_every_registry_entry_has_the_fields_the_settings_ui_renders():
    for a in ALERT_TYPES:
        assert a.get("key") and a.get("group") and a.get("label"), a


def test_registry_keys_are_unique():
    keys = [a["key"] for a in ALERT_TYPES]
    assert len(keys) == len(set(keys))


def test_no_registry_entry_is_also_marked_essential():
    """A type in both lists would render a toggle that silently does nothing."""
    assert not ({a["key"] for a in ALERT_TYPES} & ESSENTIAL)


# ── The email footer ──────────────────────────────────────────────────────────

def test_the_footer_carries_a_working_link_for_a_manageable_alert():
    with patch("src.services.email_service._settings",
               SimpleNamespace(jwt_secret=SECRET, public_base_url="https://example.test")):
        html, text = _unsubscribe_footer("short_squeeze", 42)
    tok = make_unsubscribe_token(42, "short_squeeze", SECRET)
    assert f"u=42&t=short_squeeze&sig={tok}" in html
    assert "https://example.test/api/alerts/unsubscribe" in html
    assert label_for("short_squeeze") in html
    assert tok in text


def test_essential_mail_is_not_offered_an_unsubscribe_it_cannot_honour():
    with patch("src.services.email_service._settings",
               SimpleNamespace(jwt_secret=SECRET, public_base_url="https://example.test")):
        assert _unsubscribe_footer("price_alert", 42) == ("", "")
        assert _unsubscribe_footer("broker_reauth", 42) == ("", "")


def test_an_untagged_email_renders_no_footer():
    with patch("src.services.email_service._settings",
               SimpleNamespace(jwt_secret=SECRET, public_base_url="https://example.test")):
        assert _unsubscribe_footer(None, 42) == ("", "")


def test_no_footer_is_rendered_without_a_resolved_recipient():
    """Better a missing footer than a link signed for the wrong account."""
    with patch("src.services.email_service._settings",
               SimpleNamespace(jwt_secret=SECRET, public_base_url="https://example.test")):
        assert _unsubscribe_footer("short_squeeze", None) == ("", "")


def test_a_missing_secret_suppresses_the_footer_rather_than_emitting_an_unsigned_link():
    with patch("src.services.email_service._settings",
               SimpleNamespace(jwt_secret="", public_base_url="https://example.test")):
        assert _unsubscribe_footer("short_squeeze", 42) == ("", "")


# ── The audience filter ───────────────────────────────────────────────────────

class _Sess:
    def __init__(self, opted_out=(), raises=False):
        self._opted_out = list(opted_out)
        self._raises = raises

    def execute(self, *a, **k):
        if self._raises:
            raise RuntimeError("preferences table unavailable")
        return SimpleNamespace(all=lambda: [(uid,) for uid in self._opted_out])


def _filter(recipients, opted_out=(), raises=False):
    from src.services.scheduler import _filter_by_alert_pref
    return _filter_by_alert_pref(_Sess(opted_out, raises), recipients, "short_squeeze")


def test_a_user_who_opted_out_stops_receiving_that_alert():
    assert set(_filter({1: "a", 2: "b", 3: "c"}, opted_out=[2])) == {1, 3}


def test_a_user_with_no_stored_preference_still_receives_it():
    """Absence means subscribed — this is what makes the deploy a no-op on day one."""
    assert set(_filter({1: "a", 2: "b"}, opted_out=[])) == {1, 2}


def test_the_filter_fails_open_when_the_preference_lookup_raises():
    """Silently dropping alerts because a settings query failed looks, from outside, exactly
    like the alert never firing — the hardest kind of outage to notice."""
    assert set(_filter({1: "a", 2: "b"}, raises=True)) == {1, 2}


def test_an_empty_recipient_set_is_returned_unchanged():
    assert _filter({}, opted_out=[1]) == {}


def test_opting_out_of_one_alert_does_not_affect_another_users_subscription():
    assert set(_filter({7: "x", 8: "y", 9: "z"}, opted_out=[8])) == {7, 9}
