"""Black-Scholes reference values (Hull, 'Options, Futures and Other Derivatives', Example 15.6:
S=42, K=40, r=10%, sigma=20%, T=0.5 -> call 4.76) plus put-call parity and greek identities."""
import math

import pytest

from src.services.option_pricing import call_greeks, call_price, prob_above


def test_hull_reference_call_value():
    assert call_price(42, 40, 0.5, 0.20, rate=0.10) == pytest.approx(4.76, abs=0.005)


def test_put_call_parity_holds_for_the_model():
    s, k, t, v, r = 100.0, 105.0, 0.75, 0.35, 0.04
    c = call_price(s, k, t, v, r)
    d1 = (math.log(s / k) + (r + v * v / 2) * t) / (v * math.sqrt(t))
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    put = k * math.exp(-r * t) * n(-(d1 - v * math.sqrt(t))) - s * n(-d1)
    assert c - put == pytest.approx(s - k * math.exp(-r * t), abs=1e-9)


def test_greeks_agree_with_finite_differences():
    s, k, t, v = 100.0, 100.0, 0.25, 0.4
    g = call_greeks(s, k, t, v)
    h = 0.01
    assert g["delta"] == pytest.approx((call_price(s + h, k, t, v) - call_price(s - h, k, t, v)) / (2 * h), abs=1e-4)
    d_up = (call_price(s + 2 * h, k, t, v) - call_price(s, k, t, v)) / (2 * h)
    d_dn = (call_price(s, k, t, v) - call_price(s - 2 * h, k, t, v)) / (2 * h)
    assert g["gamma"] == pytest.approx((d_up - d_dn) / (2 * h), abs=1e-3)
    assert g["vega_per_point"] == pytest.approx(call_price(s, k, t, v + 0.005) - call_price(s, k, t, v - 0.005), abs=1e-4)
    assert g["theta_per_day"] < 0


def test_expiry_and_invalid_inputs():
    assert call_price(110, 100, 0, 0.3) == 10.0
    assert call_price(90, 100, 0, 0.3) == 0.0
    assert call_greeks(100, 100, 0, 0.3) is None
    for bad in (None, float("nan"), -1.0, 0.0, True):
        assert call_price(100, 100, 0.5, bad) is None
    assert prob_above(100, 100, 0, 0.3) is None


def test_probability_is_sensible():
    assert 0.4 < prob_above(100, 100, 0.25, 0.3) < 0.5        # slightly below 50% (drift - sigma^2/2)
    assert prob_above(100, 150, 0.25, 0.3) < 0.05
