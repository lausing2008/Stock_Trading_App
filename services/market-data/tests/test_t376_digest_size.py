"""T376-DIGEST-SIZE — the Flow Digest showed a price but never the SIZE of what traded.

REPORTED BY THE USER, twice: "they have the price but don't have the share volume", then
"yes I want all the information".

Both halves of the digest were under-reporting, for different reasons:

**Dark pool** rendered only symbol/time/price. A dark-pool print's whole significance IS its
size — a $9.42 price tells the reader nothing about whether 500 shares or 500,000 crossed.

**Options flow** rendered premium but not the CONTRACT (strike + expiry) or the volume/OI ratio,
so an alert said "$1.5M bullish put" with no way to know which put, expiring when, or how
unusual the volume actually was. Every one of those columns already existed on
`options_flow_alert_outcomes` and simply was not read.

WHY SHARES ARE DERIVED, NOT JOINED — the design decision worth pinning:
`dark_pool_alert_outcomes` has no size column, only `alert_price` and `qualifying_metric`. The
richer `dark_pool_prints` table does have `size`/`premium`/`venue`, so a join looks like the
obvious answer. It was TESTED AND REJECTED as unreliable: MU and AVGO returned NO matching print
at all, and BULL matched **six** rows for a single alert, with nothing in the outcome row to
disambiguate which. Deriving `shares = premium / price` needs no join, and was verified against
the one print that did match unambiguously — BULL's 541,762 shares reproduced exactly.

`qualifying_metric` IS the premium: verified against BULL's real print ($5,103,398.04 matched to
the cent), and `check_dark_pool_alerts()` selects its candidate by premium.

TWO RENDER DEFECTS THIS TEST FILE CAUGHT, both in the code added for this fix, both found by
rendering a fully-null row rather than by reasoning about it:
  * the text body printed `— sh` — a unit attached to a non-value.
  * a contract with no strike, type, or expiry rendered as `?? ?`, which reads as corruption
    rather than as absence.
"""
import ast
import pathlib
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

SCHED = pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
SRC = SCHED.read_text()


def _load():
    """Exec just the three render helpers, so this file needs no DB/Redis/settings import.

    Boundaries come from an AST walk, not a `s.index("\\ndef ")` scan — the naive scan lands on
    the first NESTED def inside the function and silently truncates it mid-body.
    """
    lines = SRC.splitlines(keepends=True)
    ns = {"datetime": datetime, "date": date, "timezone": timezone, "ZoneInfo": ZoneInfo}
    for n in ast.parse(SRC).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in {
            "_money", "_fmt_hit_rate", "_render_flow_digest",
        }:
            exec("".join(lines[n.lineno - 1:n.end_lineno]), ns)  # noqa: S102 — our own source
    return ns


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _dp(symbol="BULL", price=9.42, premium=5103398.04, hour=16):
    return _Row(symbol=symbol, fired_at=datetime(2026, 9, 9, hour, 30),
                alert_price=price, qualifying_metric=premium)


def _of(**kw):
    base = dict(symbol="MU", fired_at=datetime(2026, 9, 9, 18, 3), direction="bearish",
                option_type="put", strike=955.0, expiry=date(2026, 9, 16),
                total_premium=432320.0, volume_oi_ratio=6.911,
                ask_side_dominant=True, has_sweep=True)
    base.update(kw)
    return _Row(**base)


_ACC = {"n": 3, "rate": None, "adequate": False}


def _render(dp_rows, of_rows):
    ns = _load()
    return ns["_render_flow_digest"](
        dp_rows, of_rows, _ACC, _ACC, "session", datetime(2026, 9, 9, 4, 0),
    )


def _text(dp_rows=(), of_rows=()):
    return _render(list(dp_rows), list(of_rows))[-1]


def _html(dp_rows=(), of_rows=()):
    return _render(list(dp_rows), list(of_rows))[0]


# ── The reported gap: dark-pool size ────────────────────────────────────────────────────

def test_the_share_count_appears_at_all():
    """THE BUG. The user could see $9.42 and no indication of how much crossed."""
    assert "541,762" in _text(dp_rows=[_dp()])


def test_shares_reproduce_a_real_print_exactly():
    """BULL's real 2026-09-08 alert: premium $5,103,398.04 at $9.42 = 541,762 shares, which
    matched the actual dark_pool_prints row. Pinned as arithmetic so the derivation cannot
    silently change."""
    assert round(5103398.04 / 9.42) == 541762


def test_the_premium_is_still_shown_alongside():
    """Shares alone would be a regression for a high-priced name — 2,047 MU shares and
    541,762 BULL shares are the same order of dollars. Both columns are needed."""
    t = _text(dp_rows=[_dp(), _dp(symbol="MU", price=1002.5, premium=2052112.0)])
    assert "2,047" in t and "$2.1M" in t and "$5.1M" in t


def test_a_missing_premium_renders_a_dash_not_a_zero():
    """`qualifying_metric` is nullable. `0 shares` would be a confident false statement about a
    print that definitely had size — the AUD-CONVICTION-RSIDIV-NOWRITER error class."""
    t = _text(dp_rows=[_dp(symbol="NULLQ", premium=None)])
    assert "—" in t
    assert " 0 " not in t and "0 sh" not in t


def test_no_unit_is_printed_on_a_missing_value():
    """CAUGHT BY RENDERING, NOT BY REASONING. The first version emitted `— sh`, attaching a
    unit to a non-value."""
    assert "— sh" not in _text(dp_rows=[_dp(symbol="NULLQ", premium=None)])


def test_a_zero_price_cannot_divide_by_zero():
    """`alert_price` is non-nullable but a 0.0 would still be arithmetically fatal. It must
    degrade to a dash, not raise inside a scheduled email job."""
    assert "—" in _text(dp_rows=[_dp(price=0.0)])


# ── "All the information": the options contract ─────────────────────────────────────────

def test_the_contract_strike_and_expiry_are_shown():
    """Premium without a contract is unactionable — "$432K bearish put" names no put."""
    assert "955P 26-09-16" in _text(of_rows=[_of()])


def test_the_volume_oi_ratio_is_shown():
    """This is the "unusual" in unusual options activity: 6.9x means seven times more contracts
    traded than exist as open interest."""
    assert "6.9x" in _text(of_rows=[_of()])


def test_a_call_is_labelled_C_and_a_put_P():
    assert "195C" in _text(of_rows=[_of(option_type="call", strike=195.0)])
    assert "955P" in _text(of_rows=[_of(option_type="put", strike=955.0)])


def test_a_fully_null_contract_renders_a_dash_not_punctuation_noise():
    """CAUGHT BY RENDERING A NULL ROW. The naive version produced `?? ?`, which reads as
    corruption. Real production rows do carry nulls: strike, expiry, total_premium and
    volume_oi_ratio are all nullable on this table."""
    t = _text(of_rows=[_of(strike=None, expiry=None, option_type=None,
                           total_premium=None, volume_oi_ratio=None)])
    assert "?? ?" not in t
    assert "—" in t


def test_a_partially_known_contract_still_shows_what_is_known():
    """Collapsing to a dash whenever anything is missing would discard a real strike."""
    t = _text(of_rows=[_of(expiry=None)])
    assert "955P" in t, "a known strike must survive an unknown expiry"


# ── The bought/sold axis ────────────────────────────────────────────────────────────────

def test_ask_side_dominant_is_rendered_as_bought_or_sold():
    """OptionsFlowAlertOutcome's docstring says the alert encodes FOUR reads, not two: which
    side was aggressive is half the signal, and `direction` alone hides it."""
    assert "bought" in _text(of_rows=[_of(ask_side_dominant=True)])
    assert "sold" in _text(of_rows=[_of(ask_side_dominant=False)])


def test_a_sold_put_is_not_rendered_as_a_contradiction():
    """A REAL PRODUCTION ROW: MU 1010P, direction=bullish, ask_side_dominant=False — a put
    being SOLD, which is genuinely bullish. Without the side column "bullish put" reads as a
    bug in the platform rather than as a sold-to-open position."""
    t = _text(of_rows=[_of(direction="bullish", option_type="put",
                           strike=1010.0, ask_side_dominant=False)])
    assert "bullish" in t and "sold" in t and "1010P" in t


def test_false_is_a_real_value_not_a_missing_one():
    """THE FALSY-ZERO TRAP, in Boolean form. `ask_side_dominant` is a non-nullable Boolean, so
    `r.ask_side_dominant or "—"` would print "—" for every aggressive SELL — silently deleting
    the "option sell" half of the feature the user originally asked for."""
    assert "—" not in _text(of_rows=[_of(ask_side_dominant=False)]).split("UNUSUAL")[1].split("\n")[1]
    i = SRC.index("def _side(r)")
    fn = SRC[i:SRC.index("\n\n", i)]
    assert "is not None" not in fn, "a non-nullable Boolean needs no null test"
    assert "or '—'" not in fn and 'or "—"' not in fn


# ── The rejected join, pinned so it is not 'fixed' later ────────────────────────────────

def test_shares_are_derived_not_joined():
    """The join to dark_pool_prints was tested and REJECTED: MU and AVGO matched no print,
    BULL matched SIX for one alert. A future reader seeing a division where a join 'should' be
    needs to find that reasoning in the source, not rediscover it."""
    i = SRC.index("def _shares(r)")
    fn = SRC[i:SRC.index("\n\n", i)]
    assert "DarkPoolPrint" not in fn, "must not join — the join is ambiguous"
    assert "qualifying_metric" in fn and "alert_price" in fn


def test_the_render_helper_needs_no_extra_query():
    """`_render_flow_digest` takes already-loaded ORM entities and its callers select whole
    rows, so every column used here was already in memory. A per-row query inside a render
    loop would be an N+1 in an email job."""
    i = SRC.index("def _render_flow_digest")
    fn_end = next(
        n.end_lineno for n in ast.parse(SRC).body
        if getattr(n, "name", None) == "_render_flow_digest"
    )
    fn = "".join(SRC.splitlines(keepends=True)[SRC[:i].count("\n"):fn_end])
    assert "session.execute" not in fn and "select(" not in fn


# ── Headers match cells, in both bodies ─────────────────────────────────────────────────

def test_the_dark_pool_header_and_cell_counts_match():
    """A header added without its cell (or vice versa) silently shifts every column right."""
    # Render BOTH sections — with no of_rows the options block is omitted entirely and the
    # slice below would have nothing to bound against.
    h = _html(dp_rows=[_dp()], of_rows=[_of()])
    block = h[h.index("Dark Pool ("):h.index("Unusual Options")]
    header, first_row = block.split("</tr>")[0], block.split("</tr>")[1]
    assert header.count("<th") == first_row.count("<td") > 0


def test_the_options_header_and_cell_counts_match():
    h = _html(of_rows=[_of()])
    block = h[h.index("Unusual Options"):]
    rows = block.split("</tr>")
    assert rows[0].count("<th") == rows[1].count("<td")


def test_both_bodies_carry_the_new_columns():
    """The email sends multipart — a text-only or HTML-only fix leaves half the readers with
    the old view."""
    subj_html, text = _render([_dp()], [_of()])
    for token in ("541,762", "955P", "6.9x", "bought"):
        assert token in subj_html, f"{token} missing from HTML"
        assert token in text, f"{token} missing from text"


def test_numeric_columns_are_right_aligned_in_html():
    """Digits that do not line up defeat the purpose of showing size at a glance."""
    assert "text-align:right" in _html(dp_rows=[_dp()])


# ── Nothing else regressed ──────────────────────────────────────────────────────────────

def test_the_observation_disclaimer_survives():
    """A large block can be a hedge or a roll. This line is why the digest is not read as a
    recommendation and must not be lost to a layout edit."""
    assert "OBSERVATIONS, not recommendations" in _text(dp_rows=[_dp()])


def test_the_hit_rate_footer_survives_on_both_sections():
    t = _text(dp_rows=[_dp()], of_rows=[_of()])
    assert "Dark pool 30-day hit rate" in t
    assert "Options flow 30-day hit rate" in t


def test_an_empty_digest_still_renders():
    assert _text() is not None
