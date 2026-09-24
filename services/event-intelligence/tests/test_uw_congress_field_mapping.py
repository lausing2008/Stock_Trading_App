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


# ── Cross-feed name canonicalisation (AUD-UWCONGRESS-NAMEMERGE) ───────────────
#
# The same member reaches congress_trades under three spellings: UW's `name` ("Ro Khanna"),
# the kadoa feed's ("Rohit Khanna"), and the honorific `reporter` form ("Hon. David J. Taylor").
# Because politician_name is part of the uq_congress_trade key, each spelling becomes its own
# ranked trader — and the live data showed exactly that, with one person appearing twice at
# +5.26% (n=23) and +3.30% (n=55). A reader comparing those rows is comparing a man to himself.
#
# The merge is deliberately conservative: a WRONG merge silently pools two people's returns,
# which is worse than a visible duplicate. These tests pin both directions — that the real
# variants do merge, and that the genuinely-distinct members never do.

_canonicalize = _uwc.canonicalize_politician_name

# Shaped like get_congress_roster()'s output: keyed by lower-cased name, carrying the canonical
# spelling and chamber. Mirrors the real roster's own Khanna/Taylor entries.
ROSTER = {
    "ro khanna": {"canonical_name": "Ro Khanna", "chamber": "house", "party": "D"},
    "david taylor": {"canonical_name": "David Taylor", "chamber": "house", "party": "R"},
    "nicholas taylor": {"canonical_name": "Nicholas Taylor", "chamber": "house", "party": "R"},
    "marjorie taylor greene": {"canonical_name": "Marjorie Taylor Greene", "chamber": "house", "party": "R"},
    "john boozman": {"canonical_name": "John Boozman", "chamber": "senate", "party": "R"},
    # Two House members whose given names are BOTH prefix-compatible with "Jo" — the case the
    # unique-survivor rule exists for.
    "john smith": {"canonical_name": "John Smith", "chamber": "house", "party": "D"},
    "joseph smith": {"canonical_name": "Joseph Smith", "chamber": "house", "party": "R"},
    # A member with a compound surname, to pin that the SURNAME is the last token.
    "marjorie taylor greene": {"canonical_name": "Marjorie Taylor Greene", "chamber": "house", "party": "R"},
    # The live roster has entries with missing fields; one with no chamber must never be
    # matched by a row whose own chamber is unknown.
    "patrick noplace": {"canonical_name": "Patrick Noplace", "chamber": None, "party": None},
    # Real rosters carry generational suffixes; the DB holds "Angus S King, Jr." verbatim.
    "angus king": {"canonical_name": "Angus King", "chamber": "senate", "party": "I"},
}


def test_the_kadoa_spelling_merges_onto_the_roster_name():
    """"Rohit Khanna" and "Ro Khanna" are one person; the roster holds exactly one Khanna."""
    assert _canonicalize("Rohit Khanna", "house", ROSTER) == "Ro Khanna"


def test_the_honorific_reporter_form_merges_too():
    assert _canonicalize("Hon. David J. Taylor", "house", ROSTER) == "David Taylor"


def test_a_middle_initial_does_not_block_the_merge():
    assert _canonicalize("David J. Taylor", "house", ROSTER) == "David Taylor"


def test_an_already_canonical_name_is_returned_unchanged():
    assert _canonicalize("Ro Khanna", "house", ROSTER) == "Ro Khanna"


def test_two_members_sharing_a_surname_are_never_merged_into_each_other():
    """David Taylor and Nicholas Taylor are both House Republicans. The surname alone is
    ambiguous, so the given name must decide — and must never let one become the other."""
    assert _canonicalize("Nicholas Taylor", "house", ROSTER) == "Nicholas Taylor"
    assert _canonicalize("David Taylor", "house", ROSTER) == "David Taylor"


def test_a_compound_surname_member_is_not_captured_by_a_different_surname():
    """Marjorie Taylor Greene's surname is Greene. If "Taylor" were read as her surname she
    would compete with the two Taylors and could be merged into one of them."""
    assert _canonicalize("Marjorie Taylor Greene", "house", ROSTER) == "Marjorie Taylor Greene"


def test_an_incompatible_given_name_blocks_the_merge_even_on_a_unique_surname():
    """Only one Boozman exists in the roster, but "Sarah" is not "John". A unique surname is
    not on its own evidence of identity."""
    assert _canonicalize("Sarah Boozman", "senate", ROSTER) == "Sarah Boozman"


def test_the_chamber_must_match_so_a_same_surname_member_elsewhere_is_not_borrowed():
    assert _canonicalize("Rohit Khanna", "senate", ROSTER) == "Rohit Khanna"


def test_an_unknown_chamber_never_merges_because_it_cannot_disambiguate():
    assert _canonicalize("Rohit Khanna", "", ROSTER) == "Rohit Khanna"
    assert _canonicalize("Rohit Khanna", None, ROSTER) == "Rohit Khanna"
    assert _canonicalize("David J. Taylor", "Unknown", ROSTER) == "David J. Taylor"


def test_a_name_absent_from_the_roster_is_left_exactly_as_it_arrived():
    assert _canonicalize("Some Newcomer", "house", ROSTER) == "Some Newcomer"


def test_a_roster_outage_leaves_every_name_untouched():
    assert _canonicalize("Rohit Khanna", "house", {}) == "Rohit Khanna"


def test_a_single_token_name_is_never_merged_on_a_fragment():
    assert _canonicalize("Khanna", "house", ROSTER) == "Khanna"
    assert _canonicalize("", "house", ROSTER) == ""


def test_chamber_case_does_not_affect_the_match():
    """The upsert loop capitalises chamber ("House") while the roster stores it lower-case."""
    assert _canonicalize("Rohit Khanna", "House", ROSTER) == "Ro Khanna"


def test_two_equally_compatible_candidates_are_left_unmerged():
    """"Jo" is a legitimate prefix of both John and Joseph. Picking either would invent a track
    record by pooling two people — the exact failure the unique-survivor rule prevents, and the
    reason it is `!= 1` rather than `>= 1`."""
    assert _canonicalize("Jo Smith", "house", ROSTER) == "Jo Smith"


def test_an_exact_given_name_still_merges_even_when_a_longer_sibling_exists():
    """The guard above must not become so cautious that it blocks an unambiguous match: "John"
    is exactly John Smith, notwithstanding Joseph."""
    assert _canonicalize("John Smith", "house", ROSTER) == "John Smith"
    assert _canonicalize("Joseph R. Smith", "house", ROSTER) == "Joseph Smith"


def test_a_compound_surname_member_filed_under_a_middle_initial_still_merges():
    """"Marjorie T. Greene" and "Marjorie Taylor Greene" are one person. This pins that the
    SURNAME is the last token: reading the second token instead would make her surname
    "Taylor", and the two spellings would never meet."""
    assert _canonicalize("Marjorie T. Greene", "house", ROSTER) == "Marjorie Taylor Greene"


def test_a_roster_entry_with_no_chamber_is_not_matched_by_an_unknown_chamber_row():
    """Both sides missing a chamber is not agreement — it is two absences. Without the explicit
    guard, "" == "" would merge them."""
    assert _canonicalize("Pat Noplace", "", ROSTER) == "Pat Noplace"
    assert _canonicalize("Pat Noplace", None, ROSTER) == "Pat Noplace"
    # The canonical spelling differs from the input on purpose: if both were "Pat Noplace" a
    # wrong merge would be indistinguishable from no merge, and this test could not fail.
    assert _canonicalize("Pat Noplace", "house", ROSTER) == "Pat Noplace"


def test_a_generational_suffix_and_middle_initial_do_not_block_the_merge():
    """The live table holds names like "Angus S King, Jr." and "A. Mitchell McConnell, Jr.".
    Without suffix stripping the last token is "jr", so the surname is read as "Jr" and the
    member can never match — or worse, every Jr. in a chamber collapses together."""
    assert _canonicalize("Angus S King, Jr.", "senate", ROSTER) == "Angus King"
    assert _canonicalize("Angus King Jr", "senate", ROSTER) == "Angus King"
