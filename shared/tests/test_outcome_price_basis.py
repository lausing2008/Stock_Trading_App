from datetime import date, datetime
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "common/outcome_price_basis.py"
_spec = importlib.util.spec_from_file_location("outcome_price_basis_under_test", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
resolve_window = _module.resolve_window
VERIFIED, PROVISIONAL, UNVERIFIED = _module.VERIFIED, _module.PROVISIONAL, _module.UNVERIFIED


def _coverage(kind="source_guarantee"):
    return {"evidenced_from": date(2026, 1, 1), "evidenced_to": date(2026, 12, 31),
            "completeness_basis": kind, "retrieved_at": datetime(2026, 12, 31)}


def test_split_is_applied_to_pre_split_prices_only():
    bars = [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 52.0)]
    actions = [{"action_type": "split", "ex_date": date(2026, 1, 5), "split_ratio": 2.0}]
    got = resolve_window(bars, actions, _coverage(), start=bars[0][0], end=bars[-1][0])
    assert got["prices"] == [(date(2026, 1, 2), 50.0), (date(2026, 1, 5), 52.0)]
    assert got["evidence_status"] == VERIFIED


def test_response_only_computes_but_is_provisional():
    bars = [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 101.0)]
    got = resolve_window(bars, [], _coverage("response_only"),
                         start=bars[0][0], end=bars[-1][0])
    assert got["prices"] == bars and got["evidence_status"] == PROVISIONAL
    assert "no documented completeness guarantee" in got["reason"]


def test_missing_span_evidence_is_unverified_and_produces_no_adjusted_prices():
    bars = [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 101.0)]
    got = resolve_window(bars, [], None, start=bars[0][0], end=bars[-1][0])
    assert got["prices"] == [] and got["evidence_status"] == UNVERIFIED


def test_a_share_action_without_ratio_never_becomes_a_clean_return():
    bars = [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 52.0)]
    actions = [{"action_type": "split", "ex_date": date(2026, 1, 5), "split_ratio": None}]
    got = resolve_window(bars, actions, _coverage(), start=bars[0][0], end=bars[-1][0])
    assert got["prices"] == [] and got["evidence_status"] == UNVERIFIED
