"""Tests for TIER84-BROKER-PORTABILITY's broker registry (services/market-data/src/services/
broker/__init__.py) — the single metadata-driven source of truth api/broker.py's routes and
the frontend's credential form derive their behavior from, rather than each maintaining its
own hardcoded per-broker-type branch that could silently drift from the real adapter set.

The broker package only depends on `requests`/`requests_oauthlib` (both real, installed
packages — not part of this repo's conftest.py stub list), so it imports and runs normally
under pytest.
"""
import pytest

from src.services.broker import (
    AlpacaBroker, AuthStyle, EtradeBroker, ManualBroker, SUPPORTED_BROKER_TYPES,
    broker_class_for_type, broker_metadata, get_broker,
)


# ── SUPPORTED_BROKER_TYPES ────────────────────────────────────────────────────

def test_supported_broker_types_includes_every_real_adapters_types():
    assert "etrade" in SUPPORTED_BROKER_TYPES
    assert "etrade_sandbox" in SUPPORTED_BROKER_TYPES
    assert "fidelity_manual" in SUPPORTED_BROKER_TYPES
    assert "alpaca" in SUPPORTED_BROKER_TYPES
    assert "alpaca_paper" in SUPPORTED_BROKER_TYPES


def test_supported_broker_types_has_no_duplicates():
    assert len(SUPPORTED_BROKER_TYPES) == len(set(SUPPORTED_BROKER_TYPES))


# ── broker_class_for_type() ────────────────────────────────────────────────────

def test_broker_class_for_type_resolves_correctly():
    assert broker_class_for_type("etrade") is EtradeBroker
    assert broker_class_for_type("etrade_sandbox") is EtradeBroker
    assert broker_class_for_type("fidelity_manual") is ManualBroker
    assert broker_class_for_type("alpaca") is AlpacaBroker
    assert broker_class_for_type("alpaca_paper") is AlpacaBroker


def test_broker_class_for_type_raises_on_unknown_type():
    with pytest.raises(ValueError):
        broker_class_for_type("schwab")


# ── broker_metadata() ──────────────────────────────────────────────────────────

def test_etrade_metadata_declares_oauth1_and_consumer_key_secret_fields():
    meta = broker_metadata("etrade_sandbox")
    assert meta["auth_style"] == "oauth1"
    field_keys = {f["key"] for f in meta["config_fields"]}
    assert field_keys == {"consumer_key", "consumer_secret"}
    secret_field = next(f for f in meta["config_fields"] if f["key"] == "consumer_secret")
    assert secret_field["secret"] is True


def test_alpaca_metadata_declares_key_secret_auth_and_key_id_secret_key_fields():
    meta = broker_metadata("alpaca_paper")
    assert meta["auth_style"] == "key_secret"
    field_keys = {f["key"] for f in meta["config_fields"]}
    assert field_keys == {"key_id", "secret_key"}
    secret_field = next(f for f in meta["config_fields"] if f["key"] == "secret_key")
    assert secret_field["secret"] is True


def test_manual_metadata_declares_manual_auth_and_zero_required_config_fields():
    """account_number/notes are optional display metadata handled as a special case in
    api/broker.py's create_connection(), not real required credentials — CONFIG_FIELDS is
    deliberately empty here so the generic required-field validation loop never demands one."""
    meta = broker_metadata("fidelity_manual")
    assert meta["auth_style"] == "manual"
    assert meta["config_fields"] == []


def test_metadata_config_fields_are_json_serializable_plain_dicts():
    """The frontend consumes this over HTTP as plain JSON — must not leak a dataclass/Enum
    instance that FastAPI's default encoder can't handle."""
    meta = broker_metadata("alpaca")
    assert isinstance(meta["auth_style"], str)
    for f in meta["config_fields"]:
        assert isinstance(f, dict)
        assert isinstance(f["key"], str)
        assert isinstance(f["label"], str)
        assert isinstance(f["secret"], bool)


# ── get_broker() — construction still resolves each adapter's own sandbox/paper flag ────────

def test_get_broker_etrade_sandbox_flag():
    broker = get_broker("etrade_sandbox", {"consumer_key": "k", "consumer_secret": "s"})
    assert isinstance(broker, EtradeBroker)
    assert broker._sandbox is True


def test_get_broker_etrade_live_flag():
    broker = get_broker("etrade", {"consumer_key": "k", "consumer_secret": "s"})
    assert isinstance(broker, EtradeBroker)
    assert broker._sandbox is False


def test_get_broker_alpaca_paper_flag():
    broker = get_broker("alpaca_paper", {"key_id": "k", "secret_key": "s"})
    assert isinstance(broker, AlpacaBroker)
    assert broker._paper is True


def test_get_broker_alpaca_live_flag():
    broker = get_broker("alpaca", {"key_id": "k", "secret_key": "s"})
    assert isinstance(broker, AlpacaBroker)
    assert broker._paper is False


def test_get_broker_manual():
    broker = get_broker("fidelity_manual", {"account_number": "Z123"})
    assert isinstance(broker, ManualBroker)


def test_get_broker_raises_on_unknown_type():
    with pytest.raises(ValueError):
        get_broker("schwab", {})


# ── AuthStyle enum ─────────────────────────────────────────────────────────────

def test_auth_style_values_are_stable_strings():
    """These strings are part of the wire contract (GET /broker/types) — a silent rename
    would break the frontend's own dispatch logic."""
    assert AuthStyle.OAUTH1.value == "oauth1"
    assert AuthStyle.KEY_SECRET.value == "key_secret"
    assert AuthStyle.MANUAL.value == "manual"


# ── api/broker.py wiring — source-text regression checks ────────────────────────
#
# broker.py's routes can't be exercised via a real FastAPI TestClient in this test
# environment (no DB/app harness exists for this file) — covered instead via source-text
# checks, matching this repo's established pattern for routes with this exact constraint.

import pathlib

_broker_route_source = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "broker.py"
_source = _broker_route_source.read_text()


def _func_body(name: str) -> str:
    start = _source.index(f"def {name}(")
    end = _source.index("\n\n@router" if "\n\n@router" in _source[start + 1:] else "\n\ndef ", start + 1)
    return _source[start:end]


def test_create_connection_derives_supported_types_from_the_registry():
    assert "from src.services.broker import SUPPORTED_BROKER_TYPES as _SUPPORTED_TYPES" in _source


def test_create_connection_validates_every_declared_config_field_is_present():
    """A required credential field with no value must raise 400, not silently store an empty
    string — the exact regression a forgotten validation check would introduce."""
    body = _func_body("create_connection")
    assert "if not value:" in body
    assert "raise HTTPException(" in body


def test_create_connection_upfront_validates_key_secret_brokers():
    body = _func_body("create_connection")
    assert "AuthStyle.KEY_SECRET" in body
    assert "get_broker(body.broker_type, config).get_account()" in body


def test_create_connection_only_oauth1_brokers_start_unauthorized():
    body = _func_body("create_connection")
    assert "cls.AUTH_STYLE != AuthStyle.OAUTH1" in body


def test_oauth_routes_gate_on_auth_style_not_a_hardcoded_broker_type_tuple():
    """The 3 OAuth routes (start/complete/reconnect) must check AUTH_STYLE generically, not a
    hardcoded ("etrade", "etrade_sandbox") tuple that a future OAuth1 broker would silently
    fall outside of."""
    for name in ("oauth_start", "oauth_complete", "reconnect"):
        body = _func_body(name)
        assert "AuthStyle.OAUTH1" in body, f"{name} must gate on AuthStyle.OAUTH1"
        assert 'not in ("etrade", "etrade_sandbox")' not in body, (
            f"{name} still has the old hardcoded broker_type tuple check"
        )


def test_list_broker_types_endpoint_exists_and_uses_the_registry():
    assert "def list_broker_types(" in _source
    body = _func_body("list_broker_types")
    assert "SUPPORTED_BROKER_TYPES" in body
    assert "broker_metadata(t)" in body
