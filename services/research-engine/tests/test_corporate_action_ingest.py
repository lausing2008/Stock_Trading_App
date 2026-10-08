"""Shaping of the sourced action history — pure, no network.

The network call lives alone in `fetch` so everything that decides a FIGURE can be tested.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from intel_reports.corporate_actions import normalise, SOURCE  # noqa: E402

AT = datetime(2026, 10, 8, 0, 0)


def test_splits_and_dividends_stay_separate_typed_records():
    """Merging them is the error a provider's single adjusted close makes unrecoverable: only
    one of the two changes the share count."""
    got = normalise({"2026-06-03": 2.0}, {"2026-06-10": 0.75}, symbol="MU", retrieved_at=AT)
    assert [a["action_type"] for a in got] == ["split", "cash_dividend"]
    assert got[0]["split_ratio"] == 2.0 and got[0]["cash_amount"] is None
    assert got[1]["cash_amount"] == 0.75 and got[1]["split_ratio"] is None


def test_every_record_carries_its_provenance():
    """An action record with no provenance supports a basis claim no better than the missing
    adj_close it replaced."""
    for a in normalise({"2026-06-03": 2.0}, {"2026-06-10": 0.75}, symbol="MU", retrieved_at=AT):
        assert a["source"] == SOURCE and a["retrieved_at"] == AT and a["source_ref"]


def test_an_unusable_ratio_is_dropped_rather_than_defaulted():
    assert normalise({"2026-06-03": 0.0}, {}, symbol="MU", retrieved_at=AT) == []
    assert normalise({"2026-06-03": None}, {}, symbol="MU", retrieved_at=AT) == []


def test_records_are_ordered_by_ex_date():
    got = normalise({"2026-08-01": 2.0}, {"2026-02-01": 0.5, "2026-09-01": 0.6},
                    symbol="MU", retrieved_at=AT)
    assert [a["ex_date"] for a in got] == ["2026-02-01", "2026-08-01", "2026-09-01"]


def test_the_provider_adjusted_close_is_not_imported_as_a_basis():
    """yfinance adjusts for splits AND dividends; importing that as a split adjustment would
    fold distributions into a price return.

    PROSE STRIPPED FIRST, and with the module's own canonicaliser rather than a copy of it:
    `ast.unparse` drops `#` comments but KEEPS docstrings, so the first version of this check
    matched the module docstring that explains why `adj_close` is not used — a test passing on
    the very prose it was written to police.
    """
    import intel_reports.corporate_actions as mod
    from intel_reports.observations import _canonical_source
    code = "\n".join(_canonical_source(fn) for fn in
                     (mod.normalise, mod.fetch, mod.store, mod.ingest))
    assert "adj_close" not in code, "an adjusted close must not stand in for a chosen basis"
    assert "auto_adjust" not in code
