"""AUD-SQZ-GAMMA-OIFILL (2026-11-10): missing open interest is unknown, not zero.

The gamma scan used `chain.puts.fillna(0)`, so a put side with no reported OI summed to 0 and
the call side read as 100% concentration.
"""
import importlib

import pandas as pd

sched = importlib.import_module("src.services.scheduler")
NaN = float("nan")


def test_a_side_with_no_reported_oi_is_unmeasured():
    calls = pd.Series([1000.0, 2000.0])
    assert sched._gamma_oi_unmeasured(calls, pd.Series([NaN, NaN])) is True
    assert sched._gamma_oi_unmeasured(pd.Series([NaN]), calls) is True


def test_an_empty_side_is_unmeasured():
    assert sched._gamma_oi_unmeasured(pd.Series([500.0]), pd.Series([], dtype=float)) is True


def test_reported_sides_are_measured_even_when_one_is_a_real_zero():
    assert sched._gamma_oi_unmeasured(pd.Series([1000.0]), pd.Series([0.0])) is False
    assert sched._gamma_oi_unmeasured(pd.Series([1000.0, NaN]), pd.Series([NaN, 10.0])) is False


def test_the_scan_no_longer_fills_the_whole_chain_with_zero():
    src = open(sched.__file__).read()
    body = src[src.index("def check_gamma_unwind_alerts("):]
    body = body[:body.index("\ndef ")]
    assert "chain.calls.fillna(0)" not in body and "chain.puts.fillna(0)" not in body
    assert body.index("_gamma_oi_unmeasured(_call_band, _put_band)") < body.index("call_share = call_oi / total_oi")
