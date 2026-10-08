"""Shaping of the sourced action history — pure, no network.

The network call lives alone in `fetch` so everything that decides a FIGURE can be tested.
"""
import sys
from datetime import date, datetime
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


# ---- the shaping around the network call, which the pilot run broke on ----------------------

class _FakeSeries:
    """Stands in for a pandas Series: iterable `.items()`, and TRUTHINESS THAT RAISES.

    `if series:` on a real Series raises ValueError, which is what the first pilot run hit —
    in `fetch`, the one function left untested because it touches the network. A stub that
    merely returned a dict would have let the same bug through.
    """
    def __init__(self, pairs): self._pairs = pairs
    def items(self): return iter(self._pairs)
    def __bool__(self): raise ValueError("The truth value of a Series is ambiguous.")


class _Stamp:
    def __init__(self, d): self._d = d
    def date(self): return self._d


class _FakeTicker:
    """A RESOLVED ticker: it carries history metadata, which is the positive signal that the
    provider actually identified the symbol."""
    def __init__(self, splits, dividends):
        self.splits, self.dividends = splits, dividends
        self.history_metadata = {"symbol": "MU", "currency": "USD"}


def test_a_provider_series_is_shaped_without_testing_its_truthiness():
    from datetime import date as _date
    from intel_reports.corporate_actions import as_dated_map
    got = as_dated_map(_FakeSeries([(_Stamp(_date(2026, 6, 3)), 2.0)]))
    assert got == {"2026-06-03": 2.0}


def test_an_absent_series_is_an_empty_map_not_an_error():
    from intel_reports.corporate_actions import as_dated_map
    assert as_dated_map(None) == {}


def test_fetch_shapes_both_series_without_a_network():
    from datetime import date as _date
    from intel_reports.corporate_actions import fetch
    splits, divs, meta = fetch("MU", ticker=_FakeTicker(
        _FakeSeries([(_Stamp(_date(2026, 6, 3)), 2.0)]),
        _FakeSeries([(_Stamp(_date(2026, 6, 10)), 0.75)])))
    assert splits == {"2026-06-03": 2.0} and divs == {"2026-06-10": 0.75}
    assert meta["provider"] == SOURCE


def test_a_ticker_carrying_nothing_at_all_is_treated_as_unresolved():
    """An object with neither series nor metadata is indistinguishable from a failed lookup,
    and must not be reported as a symbol with no corporate actions."""
    from intel_reports.corporate_actions import fetch, SymbolNotResolved
    import pytest
    with pytest.raises(SymbolNotResolved):
        fetch("SPY", ticker=object())


def test_a_resolved_etf_with_no_splits_but_dividends_is_fine():
    """The real case that test used to stand for: an ETF genuinely has no splits."""
    from intel_reports.corporate_actions import fetch
    t = _FakeTicker(_FakeSeries([]), _FakeSeries([(_Stamp(date(2026, 6, 18)), 1.904)]))
    splits, divs, _ = fetch("SPY", ticker=t)
    assert splits == {} and divs == {"2026-06-18": 1.904}


# ---- a FAILED LOOKUP is not an empty history -------------------------------------------------
#
# AUD-OBS-UNRESOLVEDSYMBOL. yfinance answers an unknown ticker with `None` for both series and
# logs a 404 it does not raise. The shaping helper turned that into `{}`, and the universe run
# wrote a coverage row claiming "no corporate actions in this span" — a positive evidential
# claim produced by a lookup that failed. Seen live on 100.HK and 2476.

class _Unresolved:
    """What yfinance actually hands back for a symbol it cannot find."""
    splits = None
    dividends = None
    history_metadata = {}


class _Resolved:
    def __init__(self, splits, dividends):
        self.splits, self.dividends = splits, dividends
        self.history_metadata = {"symbol": "MU", "currency": "USD"}


def test_an_unresolved_symbol_raises_rather_than_reporting_no_actions():
    from intel_reports.corporate_actions import fetch, SymbolNotResolved
    import pytest
    with pytest.raises(SymbolNotResolved) as e:
        fetch("100.HK", ticker=_Unresolved())
    assert "did not resolve" in str(e.value)


def test_a_resolved_symbol_with_genuinely_no_actions_is_fine():
    """The distinction only matters if the benign case still works: an empty history from a
    symbol the provider DID identify is a real, usable answer."""
    from intel_reports.corporate_actions import fetch
    splits, divs, meta = fetch("MU", ticker=_Resolved(_FakeSeries([]), _FakeSeries([])))
    assert splits == {} and divs == {}
    assert meta["resolved_symbol"] == "MU"


def test_ingest_writes_no_coverage_row_for_an_unresolved_symbol():
    """The whole point: no coverage claim may be written on a failed lookup."""
    from intel_reports.corporate_actions import ingest, SymbolNotResolved
    import pytest
    calls = []

    class _Session:
        def execute(self, *a, **k): calls.append("execute"); raise AssertionError("no DB work")
        def add(self, *a, **k): calls.append("add")
        def commit(self): calls.append("commit")

    with pytest.raises(SymbolNotResolved):
        ingest(_Session(), "100.HK", covers_from=date(2025, 1, 1), covers_to=date(2026, 1, 1),
               ticker=_Unresolved())
    assert calls == [], "nothing was written for a symbol that does not resolve"


# ---- an unresolved identifier is QUARANTINED, not concluded about ---------------------------

def test_quarantine_asserts_nothing_beyond_what_was_observed():
    """The tempting conclusion — "dead or mistyped" — is not established by a provider failing
    to resolve an identifier: that is a fact about the provider's coverage as much as about the
    symbol. Zero stored bars corroborates; it does not prove."""
    import inspect
    from intel_reports.corporate_actions import quarantine
    doc = inspect.getdoc(quarantine)
    assert "hypothesis, not a finding" in doc
    for forbidden in ("delete", "rename", "mark delisted"):
        assert forbidden in doc, "the doc must name what this deliberately does NOT do"


def test_the_quarantine_row_keeps_the_question_open_until_someone_checks():
    from pathlib import Path as _P
    import re
    models = (_P(__file__).resolve().parents[3] / "shared/db/models.py").read_text()
    block = models[models.index("class IdentifierQuarantine"):]
    block = block[:block.index("\n\nclass ")]
    assert "checked_at" in block and "resolution" in block
    assert re.search(r"checked_at.*nullable=True", block), \
        "unchecked must be representable, and must be the default"
    assert "not deleted, not renamed and not marked delisted" in block


def test_the_evidence_field_is_prose_not_a_code():
    """A quarantine that records only a reason code cannot say what was seen or what it fails to
    establish, which is the whole content of the finding."""
    from pathlib import Path as _P
    models = (_P(__file__).resolve().parents[3] / "shared/db/models.py").read_text()
    block = models[models.index("class IdentifierQuarantine"):]
    assert "evidence: Mapped[str] = mapped_column(Text, nullable=False)" in block
