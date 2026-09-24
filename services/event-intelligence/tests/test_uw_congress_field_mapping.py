"""AUD-UWCONGRESS-FIELDNAMES: the parse bug that silently discarded 81% of the congress dataset.

WHAT HAPPENED. `shared/common/uw_congress.py` probed for `transaction_type`, `filing_date`,
`amount_min` and `chamber`. Unusual Whales' `/api/congress/recent-trades` actually sends
`txn_type`, `filed_at_date`, `amounts` and `member_type`. Not one key matched, so every field
fell through to its default and 7,691 of 9,453 stored rows (81%) carried no direction, no
disclosure date, no amount and no party — including all 2,036 rows for the single most active
filer, which is why he could not appear in the follow-return leaderboard at all.

Nothing was ever missing from the feed. The rows arrived complete and were dropped at parse.
Confirmed two independent ways before this fix: a live payload capture, and UW's published
OpenAPI spec.

WHY THIS CLASS OF BUG IS DANGEROUS. It is invisible from the inside. `row.get("transaction_type")`
on a dict that has no such key is not an error — it is None, which `_normalize_congress_txn_type`
faithfully turns into the legitimate-looking value "unknown". Every layer above behaved
correctly on the data it was handed. The only way to catch it is to assert against the REAL
payload shape, which is what the fixtures below are: verbatim rows captured from production.

So these tests are deliberately written against UW's key spellings, not ours. A test built from
a hand-written dict using the field names we *expected* would have passed throughout the entire
period the bug was live — that is precisely how it survived.
"""
import importlib.util
import pathlib
import sys

if "common.uw_congress" in sys.modules:
    del sys.modules["common.uw_congress"]
_uwc_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "uw_congress.py"
_spec = importlib.util.spec_from_file_location("common.uw_congress", _uwc_path)
_uwc = importlib.util.module_from_spec(_spec)
sys.modules["common.uw_congress"] = _uwc
_spec.loader.exec_module(_uwc)
setattr(sys.modules["common"], "uw_congress", _uwc)

from src.services.congress import _uw_rows_to_kadoa_shape  # noqa: E402

_parse_amount_range = _uwc._parse_amount_range
_normalize_txn = _uwc._normalize_congress_txn_type
_normalize_party = _uwc._normalize_party


# A verbatim row from UW's live /api/congress/recent-trades. Do not "tidy" these key names —
# their exact spelling IS the thing under test.
UW_ROW = {
    "name": "Pete Sessions",
    "ticker": "GOOGL",
    "issuer": "undisclosed",
    "is_active": True,
    "notes": "Alphabet Inc. - Class A Common Stock (GOOGL) [ST]",
    "transaction_date": "2026-09-14",
    "txn_type": "Sell",
    "politician_id": "303d03e9-9035-4fdd-a99f-19af5c2ae8c5",
    "reporter": "Hon. Pete Sessions",
    "amounts": "$15,001 - $50,000",
    "filed_at_date": "2026-09-21",
    "member_type": "house",
}


def _parse_one(row: dict):
    """Run UW's payload through the module's own row loop and return the CongressTradeRow."""
    return _uwc._parse_congress_rows([row])[0]


# ── The four keys that were wrong ─────────────────────────────────────────────

def test_direction_is_read_from_txn_type_the_key_uw_actually_sends():
    """The headline bug: 7,691 rows became 'unknown' because neither probed key existed."""
    assert _parse_one(UW_ROW).transaction_type == "sale"


def test_disclosure_date_is_read_from_filed_at_date():
    """Without this the row has no disclosure date, and get_smart_money_leaderboard() filters on
    `disclosure_date IS NOT NULL` — so the row does not merely lose a field, it vanishes from
    the report entirely."""
    assert _parse_one(UW_ROW).disclosure_date == "2026-09-21"


def test_amount_is_parsed_from_the_amounts_range_string():
    r = _parse_one(UW_ROW)
    assert (r.amount_min, r.amount_max) == (15001.0, 50000.0)
    assert r.amount_range_label == "$15,001 - $50,000"


def test_chamber_is_read_from_member_type():
    assert _parse_one(UW_ROW).chamber == "house"


def test_trade_date_still_parses_from_the_one_key_that_always_matched():
    """transaction_date was the single key that happened to line up, which is why trade_date was
    populated while everything around it was null — the symptom that made the bug look like a
    sparse feed rather than a mapping error."""
    assert _parse_one(UW_ROW).trade_date == "2026-09-14"


def test_the_standard_name_is_preferred_over_the_honorific_reporter_form():
    """`reporter` carries an "Hon." prefix on 184 of 200 sampled rows. Preferring it split one
    person's history across two spellings and stopped the UW and kadoa feeds joining."""
    assert _parse_one(UW_ROW).politician_name == "Pete Sessions"


def test_a_row_missing_the_modern_keys_still_parses_via_the_legacy_fallbacks():
    """The old spellings are kept as fallbacks so a kadoa-shaped dict, or a future UW rename
    back, does not silently start dropping fields again."""
    legacy = {
        "politician_name": "Old Shape", "ticker": "MSFT",
        "transaction_type": "Purchase", "filing_date": "2026-01-02",
        "transaction_date": "2026-01-01", "amount_min": 1000, "amount_max": 5000,
        "chamber": "Senate",
    }
    r = _parse_one(legacy)
    assert r.transaction_type == "purchase"
    assert r.disclosure_date == "2026-01-02"
    assert (r.amount_min, r.amount_max) == (1000.0, 5000.0)
    assert r.chamber == "Senate"


# ── The nine-value txn_type enum ──────────────────────────────────────────────

def test_every_documented_uw_txn_type_maps_into_the_known_vocabulary():
    """UW's enum has nine spellings with inconsistent casing. Anything falling through to
    raw[:32] would introduce a category that _congress_score_from_trades() and
    get_smart_money_leaderboard() do not match on — a silent fourth bucket."""
    expected = {
        "Buy": "purchase", "Purchase": "purchase",
        "Sell": "sale", "Sell (partial)": "sale", "Sell (PARTIAL)": "sale",
        "Sale (Partial)": "sale", "Sale (Full)": "sale",
        "Exchange": "exchange", "Receive": "exchange",
    }
    for raw, want in expected.items():
        assert _normalize_txn(raw) == want, f"{raw!r} -> {_normalize_txn(raw)!r}, want {want!r}"


def test_receive_is_not_left_to_fall_through_as_a_raw_string():
    """A grant/transfer IN is not an open-market purchase and must not be counted as one, but it
    also must not become the literal string 'receive' in a column three other functions match
    against."""
    assert _normalize_txn("Receive") == "exchange"
    assert _normalize_txn("Receive") != "receive"


def test_an_absent_direction_still_yields_unknown_rather_than_a_guess():
    assert _normalize_txn(None) == "unknown"
    assert _normalize_txn("") == "unknown"


# ── Amount range parsing ──────────────────────────────────────────────────────

def test_the_real_disclosure_bands_all_parse():
    """Congressional amounts are banded by statute — these are the six bands observed live."""
    bands = {
        "$1,001 - $15,000": (1001.0, 15000.0),
        "$15,001 - $50,000": (15001.0, 50000.0),
        "$50,001 - $100,000": (50001.0, 100000.0),
        "$100,001 - $250,000": (100001.0, 250000.0),
        "$250,001 - $500,000": (250001.0, 500000.0),
        "$500,001 - $1,000,000": (500001.0, 1000000.0),
    }
    for raw, want in bands.items():
        assert _parse_amount_range(raw) == want, raw


def test_an_open_ended_top_band_reports_no_upper_bound_rather_than_inventing_one():
    """"$1,000,001+" genuinely has no disclosed ceiling. Returning the floor as the ceiling
    would understate the largest positions in the dataset."""
    assert _parse_amount_range("$1,000,001+") == (1000001.0, None)


def test_an_unparseable_band_returns_nulls_instead_of_raising():
    """One odd band must never drop an otherwise-good trade row."""
    assert _parse_amount_range("undisclosed") == (None, None)
    assert _parse_amount_range(None) == (None, None)
    assert _parse_amount_range("") == (None, None)


# ── Party enrichment from the roster ──────────────────────────────────────────

def test_party_comes_from_the_roster_because_trade_rows_carry_none():
    """UW's trade rows have no party key at all (0 of 200 sampled). It exists only on
    /api/congress/politicians, so without the join every UW row stores a NULL party."""
    row = _uwc.CongressTradeRow(
        politician_name="Pete Sessions", party=None, chamber="house", ticker="GOOGL",
        transaction_type="sale", amount_min=None, amount_max=None,
        trade_date="2026-09-14", disclosure_date="2026-09-21",
    )
    roster = {"pete sessions": {"party": "R", "chamber": "house", "politician_id": "x"}}
    out = _uw_rows_to_kadoa_shape([row], roster)
    assert out[0]["party"] == "R"


def test_an_unmatched_name_leaves_party_null_rather_than_borrowing_someone_elses():
    """The join is by display name. A miss must degrade to today's behaviour (NULL), never
    assign a party from a near-match — a wrong party is worse than no party."""
    row = _uwc.CongressTradeRow(
        politician_name="Nobody In Roster", party=None, chamber="house", ticker="AAPL",
        transaction_type="purchase", amount_min=None, amount_max=None,
        trade_date="2026-09-14", disclosure_date="2026-09-21",
    )
    out = _uw_rows_to_kadoa_shape([row], {"pete sessions": {"party": "R"}})
    assert out[0]["party"] is None


def test_a_roster_outage_degrades_to_null_party_and_never_drops_the_trade():
    row = _uwc.CongressTradeRow(
        politician_name="Pete Sessions", party=None, chamber="house", ticker="GOOGL",
        transaction_type="sale", amount_min=1.0, amount_max=2.0,
        trade_date="2026-09-14", disclosure_date="2026-09-21",
    )
    out = _uw_rows_to_kadoa_shape([row], {})
    assert len(out) == 1
    assert out[0]["party"] is None
    assert out[0]["transaction_type"] == "sale"


def test_uw_party_words_are_normalised_to_the_letters_the_kadoa_rows_already_use():
    """Both feeds write to one column. "democrat" and "D" in the same column would split one
    party into two rows under any GROUP BY party."""
    assert _normalize_party("democrat") == "D"
    assert _normalize_party("republican") == "R"
    assert _normalize_party("independent") == "I"
    assert _normalize_party(None) is None
    assert _normalize_party("libertarian-ish") is None


def test_an_explicit_row_party_is_not_overwritten_by_the_roster():
    """kadoa rows already carry a real party; the roster is a fallback for the feed that lacks
    one, not an authority that overrides a source which has it."""
    row = _uwc.CongressTradeRow(
        politician_name="Pete Sessions", party="D", chamber="house", ticker="GOOGL",
        transaction_type="sale", amount_min=None, amount_max=None,
        trade_date="2026-09-14", disclosure_date="2026-09-21",
    )
    out = _uw_rows_to_kadoa_shape([row], {"pete sessions": {"party": "R"}})
    assert out[0]["party"] == "D"


def test_the_amount_label_survives_the_shape_translation():
    """amount_range_label was hardcoded to None in the translator, so CongressTrade.amount_range
    was NULL for every UW row even once the numbers parsed."""
    row = _uwc.CongressTradeRow(
        politician_name="Pete Sessions", party=None, chamber="house", ticker="GOOGL",
        transaction_type="sale", amount_min=15001.0, amount_max=50000.0,
        trade_date="2026-09-14", disclosure_date="2026-09-21",
        amount_range_label="$15,001 - $50,000",
    )
    out = _uw_rows_to_kadoa_shape([row], {})
    assert out[0]["amount_range_label"] == "$15,001 - $50,000"
    assert out[0]["amount_range_low"] == 15001.0
