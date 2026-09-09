"""AUD-ADMIN-PROVIDERKEY-NOCLEAR — clearing a provider key in the UI silently did nothing.

FOUND WHILE ROTATING A LEAKED CREDENTIAL, which is the worst possible time to discover it: the
Polygon key had been exposed in container logs, and the Settings page could not remove it. It had
to be deleted by hand with `redis-cli`.

THE CHAIN, all three links required:

  1. `settings.tsx` sent `polygon_api_key: s.polygonApiKey || undefined`. An empty string is
     falsy, so clearing the field produced `undefined`.
  2. `JSON.stringify` DROPS `undefined` values, so the field vanished from the request body
     entirely — not sent as null, simply absent.
  3. `admin.py` guards `if req.polygon_api_key is not None:`, so an absent field is skipped.

Net effect: emptying the box showed "Saved" and changed nothing. The old key stayed live in Redis.
A confident success message over a no-op — the same family as every other finding this session.

WHY IT WAS ONLY THESE TWO. Claude, DeepSeek, Alpaca and Unusual Whales have ALL had an
`unshare_*` flag for exactly this purpose. `polygon` and `alpha_vantage` — the two data-provider
keys, routed through `adapters/registry.py` rather than `ai_keys.py` — were simply never given
one. The pattern existed; these two were outside it.

THE FIX follows the established convention rather than inventing a new one: an `unshare_*` flag
per key, a `clear_runtime_key()` helper mirroring `set_runtime_key()`, and a frontend that sends
the flag when the field is empty.
"""
import pathlib

import pytest

MD = pathlib.Path(__file__).resolve().parents[1] / "src"
ADMIN_SRC = (MD / "api/admin.py").read_text()
REG_SRC = (MD / "adapters/registry.py").read_text()

FE = pathlib.Path(__file__).resolve().parents[3] / "frontend/src"
SETTINGS_SRC = (FE / "pages/settings.tsx").read_text()
API_SRC = (FE / "lib/api.ts").read_text()


# ── The frontend no longer swallows an empty field ──────────────────────────────────────

def test_the_falsy_undefined_shortcut_is_gone_for_both_keys():
    """THE BUG, link 1. `value || undefined` + JSON.stringify = the field never arrives.

    ASSERT ON THE LIVE STATEMENT, NOT THE PROSE. My first version asserted the buggy string was
    absent from the whole file — but the fix's own explanatory comment QUOTES that string, so the
    test failed against correct code. Strip comment lines first; this is the fifth time this
    session that a source-text assertion matched a comment rather than a statement.
    """
    code = "\n".join(
        ln for ln in SETTINGS_SRC.splitlines() if not ln.strip().startswith("//")
    )
    assert "polygon_api_key: s.polygonApiKey || undefined" not in code
    assert "alpha_vantage_api_key: s.alphaVantageApiKey || undefined" not in code


def test_an_empty_field_now_sends_an_explicit_unshare_flag():
    assert "unshare_polygon_key: trimmedPolygon ? undefined : true" in SETTINGS_SRC
    assert "unshare_alpha_vantage_key: trimmedAlphaVantage ? undefined : true" in SETTINGS_SRC


def test_whitespace_only_counts_as_empty():
    """A field containing only spaces must clear, not store " ". `get_runtime_key()` already
    strips, so a whitespace key reads back as None while still existing in Redis — the exact
    misleading half-state this fix exists to remove."""
    assert ".trim()" in SETTINGS_SRC
    assert "const trimmedPolygon = (s.polygonApiKey || '').trim();" in SETTINGS_SRC


def test_a_nonempty_field_still_omits_the_flag():
    """Sending BOTH a value and an unshare flag on every save would be ambiguous. The flag must
    appear only when the field is actually empty."""
    i = SETTINGS_SRC.index("const trimmedPolygon")
    block = SETTINGS_SRC[i:i + 700]
    assert "trimmedPolygon || undefined" in block, "a real value is still sent as the value"


def test_the_api_client_declares_the_new_flags():
    """An untyped extra property would be silently dropped by TS callers."""
    assert "unshare_polygon_key?: boolean" in API_SRC
    assert "unshare_alpha_vantage_key?: boolean" in API_SRC


# ── The backend honours them ─────────────────────────────────────────────────────────────

def test_the_request_model_accepts_both_flags():
    assert "unshare_polygon_key: bool | None = None" in ADMIN_SRC
    assert "unshare_alpha_vantage_key: bool | None = None" in ADMIN_SRC


def test_the_handler_clears_on_each_flag():
    assert "if req.unshare_polygon_key:" in ADMIN_SRC
    assert 'clear_runtime_key("polygon")' in ADMIN_SRC
    assert "if req.unshare_alpha_vantage_key:" in ADMIN_SRC
    assert 'clear_runtime_key("alpha_vantage")' in ADMIN_SRC


def test_clear_runs_after_set_so_a_conflicting_request_ends_cleared():
    """A caller sending both a new value AND an unshare flag is ambiguous. Ending with NO
    credential is the safe reading; ending with a live one is not."""
    set_i = ADMIN_SRC.index('set_runtime_key("polygon", req.polygon_api_key)')
    clear_i = ADMIN_SRC.index('clear_runtime_key("polygon")')
    assert set_i < clear_i


def test_the_helper_is_imported():
    assert "from ..adapters.registry import clear_runtime_key, set_runtime_key" in ADMIN_SRC


# ── The registry helper ──────────────────────────────────────────────────────────────────

def test_clear_runtime_key_deletes_rather_than_writing_an_empty_string():
    """Writing "" would leave a real Redis entry that `--scan` still lists while the code treats
    it as absent — an operator auditing configured providers would see a key that isn't one."""
    i = REG_SRC.index("def clear_runtime_key(")
    fn = REG_SRC[i:REG_SRC.index("\ndef ", i + 10)]
    assert ".delete(" in fn
    assert '.set(' not in fn


def test_clear_runtime_key_uses_the_same_key_prefix_as_its_siblings():
    """A different prefix would delete nothing while reporting success."""
    i = REG_SRC.index("def clear_runtime_key(")
    fn = REG_SRC[i:REG_SRC.index("\ndef ", i + 10)]
    assert 'f"{_REDIS_KEY_PREFIX}{name}"' in fn


def test_clear_runtime_key_fails_silently_like_its_siblings():
    """A Redis hiccup must not 500 the entire Settings save — matching set/get_runtime_key."""
    i = REG_SRC.index("def clear_runtime_key(")
    fn = REG_SRC[i:REG_SRC.index("\ndef ", i + 10)]
    assert "except Exception:" in fn
    assert "pass" in fn


# ── Behavioural model of the three-link chain ───────────────────────────────────────────

def _body(field_value):
    """Mirrors the FIXED frontend: what actually reaches the backend as JSON."""
    trimmed = (field_value or "").strip()
    payload = {
        "polygon_api_key": trimmed or None,
        "unshare_polygon_key": None if trimmed else True,
    }
    # JSON.stringify drops undefined — model that by removing None entries.
    return {k: v for k, v in payload.items() if v is not None}


def _apply(body, redis_state):
    """Mirrors the FIXED handler, set-then-clear."""
    state = dict(redis_state)
    if body.get("polygon_api_key") is not None:
        state["polygon"] = body["polygon_api_key"]
    if body.get("unshare_polygon_key"):
        state.pop("polygon", None)
    return state


def test_clearing_the_field_removes_the_key():
    """THE WHOLE POINT."""
    assert _apply(_body(""), {"polygon": "OLDKEY"}) == {}


def test_clearing_a_whitespace_field_removes_the_key():
    assert _apply(_body("   "), {"polygon": "OLDKEY"}) == {}


def test_setting_a_new_value_replaces_the_old_one():
    assert _apply(_body("NEWKEY"), {"polygon": "OLDKEY"}) == {"polygon": "NEWKEY"}


def test_the_old_broken_behaviour_is_pinned():
    """What used to happen: emptying the field left the key untouched."""
    def _old_body(v):
        return {k: x for k, x in {"polygon_api_key": v or None}.items() if x is not None}
    assert _apply(_old_body(""), {"polygon": "OLDKEY"}) == {"polygon": "OLDKEY"}
    assert _apply(_body(""), {"polygon": "OLDKEY"}) != {"polygon": "OLDKEY"}


def test_other_provider_keys_are_untouched_by_a_polygon_clear():
    """Alpha Vantage must survive a Polygon clear — they are independent credentials."""
    out = _apply(_body(""), {"polygon": "P", "alpha_vantage": "A"})
    assert out == {"alpha_vantage": "A"}


# ── Parity: every credential in this model can now be removed ───────────────────────────

def test_every_provider_credential_has_a_removal_path():
    """THE PARITY ASSERTION THAT WOULD HAVE CAUGHT THIS. The unshare_* pattern existed for four
    credentials; these two were outside it for no reason anyone recorded."""
    for name in ("claude", "deepseek", "alpaca", "unusual_whales", "polygon", "alpha_vantage"):
        assert f"unshare_{name}_key: bool | None = None" in ADMIN_SRC, \
            f"{name} has no unshare flag — it cannot be removed through the API"
