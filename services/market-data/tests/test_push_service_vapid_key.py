"""Tests for BUG-VAPID-PEM-NEVER-WORKED — _normalize_vapid_private_key() in push_service.py.

Every push notification this app has ever attempted has failed, for every user, since the
feature shipped. Root cause, confirmed live in production (2026-08-14): settings.vapid_private_key
is stored as a full PEM string (its own field comment even says "PEM-encoded EC private key"),
but pywebpush's Vapid.from_string() (the ONLY code path send_push_to_user() calls) can never
accept a PEM string — the "-----BEGIN/END PRIVATE KEY-----" markers and internal dashes/spaces
are not valid base64 characters, so base64-decoding a PEM blob always raises. This was made
additionally invisible in production by a SEPARATE data-corruption layer on top of the format
bug: the stored PEM had literal backslash-n text sequences instead of real newlines (an env-var
round-trip artifact), producing an opaque binascii error that gave no hint the real underlying
issue was the PEM-vs-raw format mismatch itself.

push_service.py's module-level imports (common.config/common.logging) are stubbed as MagicMock
in this test environment's conftest.py, so the full module can't be imported directly — but
_normalize_vapid_private_key() only needs `base64` (stdlib) and `cryptography` (a real,
installed transitive dependency of pywebpush/python-jose), so it's extracted via this repo's
established source-text-exec() technique and tested with real generated EC keys, not mocks.
"""
import base64
import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "push_service.py").read_text()
_start = _SOURCE.index("def _normalize_vapid_private_key(")
_end = _SOURCE.index("\n\n\ndef send_push_to_user(")
_func_source = _SOURCE[_start:_end]

_namespace: dict = {"base64": base64}
exec(_func_source, _namespace)
_normalize_vapid_private_key = _namespace["_normalize_vapid_private_key"]


def _real_pem_private_key() -> tuple[str, bytes]:
    """Generates a REAL EC P-256 private key (matching what the Web Push spec requires) and
    returns (pem_string_with_real_newlines, raw_32_byte_private_value) so tests can verify the
    normalized output actually corresponds to the same underlying key, not just "looks base64."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    return pem, raw


class TestNormalizeVapidPrivateKey:
    def test_real_pem_with_real_newlines_normalizes_to_the_correct_raw_key(self):
        pem, raw = _real_pem_private_key()
        result = _normalize_vapid_private_key(pem)
        expected = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        assert result == expected

    def test_pem_with_literal_backslash_n_corruption_still_normalizes_correctly(self):
        """The exact production bug: a PEM string that got its real newlines replaced with
        literal backslash-n text (a common env-var round-trip artifact) must still be parsed
        correctly, since the corruption is un-escaped before PEM parsing."""
        pem, raw = _real_pem_private_key()
        corrupted = pem.replace("\n", "\\n")
        assert "\\n" in corrupted
        assert "\n" not in corrupted.replace("\\n", "")  # no real newlines survive the replace
        result = _normalize_vapid_private_key(corrupted)
        expected = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        assert result == expected

    def test_bare_raw_base64_key_passes_through_unchanged(self):
        """pywebpush's own Vapid.from_string() already handles a bare raw/DER base64 blob
        directly — this function must not touch it."""
        _, raw = _real_pem_private_key()
        bare = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        assert _normalize_vapid_private_key(bare) == bare

    def test_malformed_key_that_is_neither_pem_nor_valid_base64_raises(self):
        """Must fail LOUDLY (an exception the caller can catch and log clearly) rather than
        silently returning garbage that fails at send-time with an opaque binascii error —
        the exact opacity that made the original bug hard to diagnose."""
        import pytest
        with pytest.raises(Exception):
            _normalize_vapid_private_key("-----BEGIN PRIVATE KEY-----\nnot valid base64 at all!!!\n-----END PRIVATE KEY-----")

    def test_normalized_key_actually_works_with_the_real_pywebpush_vapid_loader(self):
        """The real end-to-end proof: feed the normalized output into py_vapid's own
        Vapid.from_string() (the exact function that was raising in production) and confirm it
        succeeds without raising, using a real generated key."""
        pem, raw = _real_pem_private_key()
        normalized = _normalize_vapid_private_key(pem)
        from py_vapid import Vapid
        vv = Vapid.from_string(private_key=normalized)
        assert vv is not None


# ── send_push_to_user() wiring — source-text regression checks ──────────────────────────────
# send_push_to_user() itself can't be exercised behaviorally in this test environment (its
# module-level common.config/common.logging imports are stubbed as MagicMock, and a real send
# needs a live PushSubscription/User ORM object plus a real HTTP round-trip to a push service).

def test_webpush_call_uses_the_normalized_key_not_the_raw_settings_value():
    """The exact bug this whole fix closes: settings.vapid_private_key (the raw, possibly-PEM,
    possibly-corrupted value) must never be passed directly to webpush() again."""
    assert "vapid_private_key=vapid_key," in _SOURCE
    assert "vapid_private_key=settings.vapid_private_key," not in _SOURCE


def test_normalization_failure_is_caught_and_logged_before_the_send_loop():
    """A malformed key fails the SAME way for every subscription (it's one config value, not
    per-subscription data) — must be caught once, logged clearly, and the function must return
    early rather than letting every subscription in the loop hit the identical failure."""
    normalize_idx = _SOURCE.index("vapid_key = _normalize_vapid_private_key(")
    loop_idx = _SOURCE.index("for sub in subscriptions:")
    assert normalize_idx < loop_idx, "normalization must happen before the send loop, not inside it"
    surrounding = _SOURCE[normalize_idx - 200:normalize_idx + 500]
    assert "except Exception" in surrounding
    assert 'log.error("push.vapid_key_malformed"' in surrounding
