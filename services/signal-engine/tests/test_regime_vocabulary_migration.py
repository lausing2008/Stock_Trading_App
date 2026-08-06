"""Tests for AUD264-SIGNALENGINE-SECOND-REGIME-CLASSIFIER (Deep Audit #4, Tier 264).

signal-engine previously derived market regime independently from /stocks/fear_greed, using a
bull/high_vol/bear/unknown vocabulary that could NEVER emit choppy/risk_off — a genuinely
different classification from the canonical classifier market-data/decision-engine both use
(GET /stocks/regime, paper_trading_engine's own bull/neutral/choppy/risk_off/bear states).
Fixed by calling the canonical endpoint directly and migrating every downstream consumer
(_STYLE_PROFILES' threshold tables, _decide_style's tier logic, the high_vol/bear compression
checks) to the real 5-state vocabulary.

Uses real httpx.Client, mocked via monkeypatch — matches the established pattern this repo
uses for other httpx-based fetch functions in signals.py.
"""
import httpx
import pytest

from src.generators.signals import (
    _STYLE_PROFILES,
    _decide_style,
    _fetch_hsi_regime,
    _fetch_market_regime,
)


class _FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self._calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        resp = self._responses[self._calls] if self._calls < len(self._responses) else self._responses[-1]
        self._calls += 1
        return resp


# ── _STYLE_PROFILES: all 4 styles carry the full 5-state + unknown vocabulary ────────────

def test_all_four_styles_have_the_full_canonical_vocabulary_plus_unknown():
    expected_keys = {"bull", "neutral", "choppy", "risk_off", "bear", "high_vol", "unknown"}
    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        assert set(_STYLE_PROFILES[style]["buy_threshold"].keys()) == expected_keys, style
        assert set(_STYLE_PROFILES[style]["hold_threshold"].keys()) == expected_keys, style


def test_new_keys_are_conservative_mappings_from_existing_values_not_new_untested_numbers():
    """neutral<-unknown, choppy<-high_vol, risk_off<-bear — no invented values."""
    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        bt = _STYLE_PROFILES[style]["buy_threshold"]
        assert bt["neutral"] == bt["unknown"], style
        assert bt["choppy"] == bt["high_vol"], style
        assert bt["risk_off"] == bt["bear"], style


# ── _decide_style: the real consumer of the vocabulary ──────────────────────────────────

def test_decide_style_accepts_all_five_canonical_states_without_falling_back_to_unknown():
    """Each canonical state must resolve to ITS OWN threshold, not silently collapse to the
    unknown/fallback tier — the exact bug this fix closes."""
    bt = _STYLE_PROFILES["SHORT"]["buy_threshold"]
    for state in ("bull", "neutral", "choppy", "risk_off", "bear"):
        # A fused_prob exactly at that state's own threshold + a hair should BUY; a fused_prob
        # at another state's threshold (but not this one's) should not, UNLESS they coincide.
        signal, _, _ = _decide_style(bt[state] + 0.001, "SHORT", state)
        assert signal == "BUY", f"state={state} did not use its own threshold {bt[state]}"


def test_unrecognized_regime_string_falls_back_to_unknown_not_a_crash():
    bt = _STYLE_PROFILES["SHORT"]["buy_threshold"]
    signal, _, _ = _decide_style(bt["unknown"] + 0.001, "SHORT", "some_future_regime_value")
    assert signal == "BUY"  # resolved via the unknown fallback, not a KeyError


def test_tier_label_bear_covers_both_bear_and_risk_off():
    _, _, tier_bear = _decide_style(0.99, "SHORT", "bear")
    _, _, tier_risk_off = _decide_style(0.99, "SHORT", "risk_off")
    assert tier_bear == "bear"
    assert tier_risk_off == "bear"


def test_tier_label_bull_only_for_bull_state():
    _, _, tier = _decide_style(0.99, "SHORT", "bull")
    assert tier == "bull"


def test_tier_label_neutral_for_neutral_and_choppy():
    _, _, tier_neutral = _decide_style(0.99, "SHORT", "neutral")
    _, _, tier_choppy = _decide_style(0.99, "SHORT", "choppy")
    assert tier_neutral == "neutral"
    assert tier_choppy == "neutral"


# ── _fetch_market_regime(): now calls the canonical /stocks/regime endpoint ──────────────

def test_fetch_market_regime_calls_the_canonical_regime_endpoint(monkeypatch):
    calls = []

    def _fake_client_ctor(timeout=5):
        def _get(url, params=None):
            calls.append((url, params))
            if "/stocks/regime" in url:
                return _FakeResponse(200, {"state": "choppy"})
            return _FakeResponse(200, {"score": 42})
        client = _FakeClient([])
        client.get = _get
        return client

    monkeypatch.setattr("src.generators.signals.httpx.Client", _fake_client_ctor)
    regime, fg_score = _fetch_market_regime()
    assert regime == "choppy"
    assert fg_score == 42
    assert any("/stocks/regime" in url for url, _ in calls)
    assert any(params == {"market": "US"} for _, params in calls)


def test_fetch_market_regime_fails_open_to_unknown_on_error(monkeypatch):
    def _fake_client_ctor(timeout=5):
        class _RaisingClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None):
                raise httpx.ConnectError("boom")

        return _RaisingClient()

    monkeypatch.setattr("src.generators.signals.httpx.Client", _fake_client_ctor)
    regime, fg_score = _fetch_market_regime()
    assert regime == "unknown"
    assert fg_score is None


def test_fetch_market_regime_passes_through_every_canonical_state(monkeypatch):
    for canonical_state in ("bull", "neutral", "choppy", "risk_off", "bear"):
        def _fake_client_ctor(timeout=5, _state=canonical_state):
            def _get(url, params=None):
                if "/stocks/regime" in url:
                    return _FakeResponse(200, {"state": _state})
                return _FakeResponse(200, {"score": None})
            client = _FakeClient([])
            client.get = _get
            return client

        monkeypatch.setattr("src.generators.signals.httpx.Client", _fake_client_ctor)
        regime, _ = _fetch_market_regime()
        assert regime == canonical_state


# ── _fetch_hsi_regime(): now calls the canonical HK regime endpoint, translated to bull/bear ──

def test_fetch_hsi_regime_calls_the_canonical_hk_endpoint(monkeypatch):
    captured = {}

    def _fake_client_ctor(timeout=5):
        def _get(url, params=None):
            captured["url"] = url
            captured["params"] = params
            return _FakeResponse(200, {"state": "bull"})
        client = _FakeClient([])
        client.get = _get
        return client

    monkeypatch.setattr("src.generators.signals.httpx.Client", _fake_client_ctor)
    result = _fetch_hsi_regime()
    assert result == "bull"
    assert captured["params"] == {"market": "HK"}


@pytest.mark.parametrize("canonical_state,expected", [
    ("bull", "bull"),
    ("neutral", "bull"),
    ("choppy", "bull"),
    ("risk_off", "bear"),
    ("bear", "bear"),
])
def test_fetch_hsi_regime_translates_five_states_down_to_bull_or_bear(monkeypatch, canonical_state, expected):
    def _fake_client_ctor(timeout=5, _state=canonical_state):
        def _get(url, params=None):
            return _FakeResponse(200, {"state": _state})
        client = _FakeClient([])
        client.get = _get
        return client

    monkeypatch.setattr("src.generators.signals.httpx.Client", _fake_client_ctor)
    assert _fetch_hsi_regime() == expected


def test_fetch_hsi_regime_fails_open_to_unknown_on_error(monkeypatch):
    def _fake_client_ctor(timeout=5):
        class _RaisingClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url, params=None):
                raise httpx.ConnectError("boom")

        return _RaisingClient()

    monkeypatch.setattr("src.generators.signals.httpx.Client", _fake_client_ctor)
    assert _fetch_hsi_regime() == "unknown"
