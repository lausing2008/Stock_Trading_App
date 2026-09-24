"""AUD-INSIDERUW / AUD-INSIDERROLE: market-wide Form 4 ingestion, and the role bug.

WHY THIS EXISTS. The EDGAR path works but barely reaches anything: a PER-TICKER, on-demand
scrape produced 1,049 rows in two years, of which only 147 are open-market purchases. Those
measure +0.69% mean 21-day alpha vs SPY (58.0% beat rate, n=112 resolved) at **t = 0.93 naive,
t = 0.60 day-clustered** over 46 filing days. |t| < 2 means NOT YET MEASURABLE, never "no edge" —
the standard deviation is 7.85% against a 0.69% mean, so what is missing is sample, not signal.
UW's feed is market-wide (~850 filings/day, ~196 tickers per filing day), which is the difference
between answering this next quarter and never answering it.

THE TWO THINGS THAT WOULD DESTROY THIS DATASET'S MEANING, both pinned below:

  1. STORING COMPENSATION AS A DECISION. UW returns S/M/A/F/P/C/J/G. An award (A), an option
     exercise (M) and a tax-withholding disposal (F) are compensation mechanics — nobody chose
     to express a view. Only P and S are stored. In a real 500-row sample, P was 27 rows and the
     non-decisions were 261: storing them would bury the signal under ten times its own volume.
  2. DOUBLE-COUNTING A FILING. UW returns no SEC accession number, and this table's unique key
     IS the accession. A synthetic id hashed over the identifying fields keeps a re-run, a later
     page, and the EDGAR path from inserting the same event three times.

AND THE ROLE BUG (AUD-INSIDERROLE): `isDirector` / `isTenPercentOwner` are BOOLEAN Form 4 tags
valued "1"/"true". The parser fell back to one when `officerTitle` was absent, so it stored the
literal string "1" as a person's job title — 254 of 1,049 rows read "1" (180) or "true" (74),
which is every director who filed without an officer title.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from src.services import insider as I


def _uw_row(**kw):
    base = {
        "ticker": "AAPL", "owner_name": "COOK TIMOTHY", "officer_title": "Chief Executive Officer",
        "transaction_code": "P", "transaction_date": "2026-09-20", "filing_date": "2026-09-22",
        "amount": 1000, "price": "150.00", "is_10b5_1": False,
        "is_officer": True, "is_director": False, "is_ten_percent_owner": False,
    }
    base.update(kw)
    return base


# ── Which transactions represent a decision ──────────────────────────────────

def test_only_open_market_buys_and_sells_are_stored():
    """P and S are choices. A/M/F/G/C/J are compensation mechanics — in a real 500-row sample
    P was 27 rows against 261 non-decisions, so storing them buries the signal in its own noise."""
    assert I._UW_STORED_CODES == {"P": "purchase", "S": "sale"}
    for code in ("A", "M", "F", "G", "C", "J", "X", "D"):
        assert code not in I._UW_STORED_CODES, code


def test_the_stored_vocabulary_matches_what_the_edgar_path_already_writes():
    """Both sources feed one column. Two vocabularies in one column means every consumer has to
    know which row came from where."""
    for code, word in I._UW_STORED_CODES.items():
        assert I._TRANSACTION_CODES[code] == word


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_the_same_filing_yields_the_same_synthetic_id():
    assert I._uw_synthetic_accession(_uw_row()) == I._uw_synthetic_accession(_uw_row())


def test_a_different_transaction_yields_a_different_id():
    a = I._uw_synthetic_accession(_uw_row())
    assert a != I._uw_synthetic_accession(_uw_row(amount=2000))
    assert a != I._uw_synthetic_accession(_uw_row(owner_name="SOMEONE ELSE"))
    assert a != I._uw_synthetic_accession(_uw_row(transaction_date="2026-09-19"))
    assert a != I._uw_synthetic_accession(_uw_row(ticker="MSFT"))


def test_the_id_is_namespaced_so_it_can_never_collide_with_a_real_edgar_accession():
    acc = I._uw_synthetic_accession(_uw_row())
    assert acc.startswith("uw:")
    assert len(acc) <= 32  # the column is String(32)


# ── Roles ─────────────────────────────────────────────────────────────────────

def test_a_real_officer_title_is_used_verbatim():
    assert I._uw_insider_role(_uw_row()) == "Chief Executive Officer"


def test_a_director_without_a_title_gets_a_name_not_a_boolean():
    """The exact bug: 180 stored rows have the role "1" and 74 have "true"."""
    role = I._uw_insider_role(_uw_row(officer_title=None, is_officer=False, is_director=True))
    assert role == "Director"
    assert role not in ("1", "true", "True")


def test_multiple_flags_are_combined():
    role = I._uw_insider_role(_uw_row(officer_title="", is_director=True, is_ten_percent_owner=True,
                                      is_officer=False))
    assert "Director" in role and "10% Owner" in role


def test_an_insider_with_no_title_and_no_flags_still_gets_a_readable_role():
    assert I._uw_insider_role(_uw_row(officer_title=None, is_officer=False,
                                      is_director=False, is_ten_percent_owner=False)) == "Insider"


# ── The EDGAR-side flag parser ────────────────────────────────────────────────

def test_form4_booleans_are_recognised_in_both_spellings():
    """SEC emits "1"/"0"; some filing agents emit "true"/"false"."""
    assert I._is_true_flag("1") is True
    assert I._is_true_flag("true") is True
    assert I._is_true_flag("True") is True
    assert I._is_true_flag("0") is False
    assert I._is_true_flag("false") is False


def test_a_job_title_is_never_mistaken_for_a_boolean_flag():
    """Guards the other direction: the fix must not start reading titles as truth values."""
    assert I._is_true_flag("Chief Executive Officer") is False
    assert I._is_true_flag(None) is False
    assert I._is_true_flag("") is False


# ── Ingestion behaviour ───────────────────────────────────────────────────────

class _Sess:
    """Dispatches on CALL ORDER, not on the statement object.

    The obvious approach — inspecting the statement to tell a SELECT from an INSERT — cannot
    work here: this suite stubs sqlalchemy, so `pg_insert(...)` is a MagicMock, and a MagicMock
    auto-creates ANY attribute as truthy. A `getattr(stmt, "_is_select_stub", False)` probe
    therefore matched every insert too, and the first version of this harness silently reported
    zero rows stored. The real function issues exactly one SELECT (the ticker map) before its
    insert loop, so ordering is both simpler and actually reliable.
    """

    def __init__(self, tickers=("AAPL",)):
        self.inserted = []
        self._tickers = tickers
        self._calls = 0

    def execute(self, stmt):
        self._calls += 1
        if self._calls == 1:  # the ticker map
            return SimpleNamespace(all=lambda: [(i + 1, t) for i, t in enumerate(self._tickers)])
        self.inserted.append(stmt)
        return SimpleNamespace(rowcount=1)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(rows, tickers=("AAPL",), available=True):
    import sys as _sys

    sess = _Sess(tickers)
    fake_resp = SimpleNamespace(status_code=200, json=lambda: {"data": rows})
    ai_keys = _sys.modules["common.ai_keys"]
    prev = (getattr(ai_keys, "is_unusual_whales_enabled", None),
            getattr(ai_keys, "get_unusual_whales_key", None))
    # Set the attributes on the stub module directly: the function imports them INSIDE its own
    # body, so the binding it sees is whatever the module holds at call time.
    ai_keys.is_unusual_whales_enabled = lambda: available
    ai_keys.get_unusual_whales_key = lambda: "test-key"

    # Capture what is actually WRITTEN. pg_insert is a MagicMock under this suite's sqlalchemy
    # stub, so the values are otherwise unreachable — and a column written with the wrong value
    # is invisible to any test that only counts rows.
    sess.values_seen = []

    def _rec_insert(_model):
        def _values(**kw):
            sess.values_seen.append(kw)
            return SimpleNamespace(on_conflict_do_nothing=lambda **_k: SimpleNamespace())
        return SimpleNamespace(values=_values)

    try:
        with patch.object(I, "SessionLocal", lambda: sess), \
             patch.object(I, "pg_insert", _rec_insert), \
             patch.object(I.httpx, "get", lambda *a, **k: fake_resp):
            return I.sync_insider_from_uw(), sess
    finally:
        ai_keys.is_unusual_whales_enabled, ai_keys.get_unusual_whales_key = prev


def test_compensation_rows_are_counted_as_skipped_not_silently_lost():
    """A skip counter is how anyone notices the code filter has drifted."""
    res, sess = _run([_uw_row(transaction_code=c) for c in ("P", "A", "M", "F", "S")])
    assert res["stored"] == 2          # P and S only
    assert res["skipped_non_open_market"] == 3


def test_a_ticker_this_platform_does_not_track_is_skipped_and_counted():
    res, _ = _run([_uw_row(ticker="NOTATRACKEDSYMBOL")])
    assert res["stored"] == 0
    assert res["skipped_untracked_ticker"] == 1


def test_an_unavailable_upstream_returns_a_reason_rather_than_raising():
    """This runs on a scheduler; an exception here would take the job down."""
    res, _ = _run([_uw_row()], available=False)
    assert "skipped" in res
    assert res.get("stored") is None


def test_a_malformed_row_does_not_drop_the_rest_of_a_real_response():
    res, _ = _run([_uw_row(), {"ticker": "AAPL"}, _uw_row(amount=50)])
    assert res["stored"] == 2


# ── What actually gets written ────────────────────────────────────────────────

def test_a_disposal_stores_a_POSITIVE_share_count():
    """UW's `amount` is SIGNED — negative on a disposal. Direction already lives in
    transaction_type, so a negative share count here is not extra information: it silently
    flips every total_value that multiplies by it, turning a $150k sale into -$150k."""
    _, sess = _run([_uw_row(transaction_code="S", amount=-1000, price="150.00")])
    v = sess.values_seen[0]
    assert v["shares"] == 1000
    assert v["transaction_type"] == "sale"
    assert v["total_value"] == 150000.0


def test_a_purchase_stores_the_same_magnitude_and_a_positive_value():
    _, sess = _run([_uw_row(transaction_code="P", amount=1000, price="150.00")])
    v = sess.values_seen[0]
    assert v["shares"] == 1000
    assert v["total_value"] == 150000.0


def test_the_10b5_1_flag_is_carried_through_untouched():
    """This flag is the entire signal/noise line for insider activity — a sale scheduled six
    months ago says nothing about anyone's view today. UW populates it on every row."""
    _, sess = _run([_uw_row(is_10b5_1=True)])
    assert sess.values_seen[0]["is_10b5_1"] is True
    _, sess2 = _run([_uw_row(is_10b5_1=False)])
    assert sess2.values_seen[0]["is_10b5_1"] is False


def test_both_dates_are_stored_and_not_conflated():
    """The EDGAR path approximates filing_date with transaction_date. UW gives both, and the
    gap between them is what a reader could actually have acted on."""
    _, sess = _run([_uw_row(transaction_date="2026-09-20", filing_date="2026-09-22")])
    v = sess.values_seen[0]
    assert v["transaction_date"] == date(2026, 9, 20)
    assert v["filing_date"] == date(2026, 9, 22)
