"""T383-DARKPOOL-UI — surfacing the dark-pool side where a person can actually see it.

USER: "where can I see those data?" then "also in the dark pool activity email too".

T377 computed and STORED the buy/sell side, the execution price and the live price — and then
they were visible in exactly two places: the database, and the 3x-daily Flow Digest. The
`/dark-pool-alerts-recent` endpoint that feeds the Options Flow page's Dark Pool tab returned
only `alert_price` and `premium`, so the page a person actually opens showed none of it.

That is the same shape as `AUD-GAMEPLANBATCH-WRONGIMPORT` and `T370-EARNINGS-DIRECTION`: the
data was computed correctly and simply never serialised, so from the UI it was indistinguishable
from never having been built.

THREE SURFACES NOW CARRY IT:
  * the Dark Pool tab (`/options-flow`) — Exec / Live / Side / Shares columns;
  * the dark-pool ALERT email (`send_dark_pool_alert_email`) — the per-print line;
  * the Flow Digest (already done in T377).

THE FRAMING MATTERED AS MUCH AS THE DATA. This email's entire premise is that it reports a
MEASURED fact and never a prediction — so adding a direction to it without extending that
disclaimer would have broken the one promise it makes. BUY/SELL says which side was the
AGGRESSOR; it does not say the stock will move. The disclaimer now says so explicitly, in both
the HTML and text bodies.

NULL IS A REAL THIRD STATE, ~10% of prints (measured live: 61 of 611). A midpoint cross, a
crossed quote, or a missing quote all render "—". Never "neutral", never defaulted to a side.
"""
import ast
import pathlib

import pytest

ROUTES = (
    pathlib.Path(__file__).resolve().parents[1] / "src/api/routes.py"
).read_text()
EMAIL = (
    pathlib.Path(__file__).resolve().parents[1] / "src/services/email_service.py"
).read_text()
PAGE = (
    pathlib.Path(__file__).resolve().parents[3] / "frontend/src/pages/options-flow.tsx"
).read_text()
API_TS = (
    pathlib.Path(__file__).resolve().parents[3] / "frontend/src/lib/api.ts"
).read_text()


def _endpoint() -> str:
    i = ROUTES.index('@router.get("/dark-pool-alerts-recent")')
    return ROUTES[i:ROUTES.index("\n@router", i + 10)]


def _email_fn() -> str:
    i = EMAIL.index("def send_dark_pool_alert_email")
    return EMAIL[i:EMAIL.index("\ndef ", i + 10)]


def _render(cands, omitted=0):
    """Exec the real email function, stubbing only the send."""
    lines = EMAIL.splitlines(keepends=True)
    ns = {}
    for n in ast.parse(EMAIL).body:
        if getattr(n, "name", "") == "send_dark_pool_alert_email":
            body = "".join(lines[n.lineno - 1:n.end_lineno]).replace(
                "return send_email(to, subject, body_html, body_text)",
                "return subject, body_html, body_text",
            )
            exec(body, ns)  # noqa: S102 — our own source
    return ns["send_dark_pool_alert_email"]("x@y.z", cands, omitted_count=omitted)


def _c(**kw):
    base = dict(symbol="MU", price=977.89, size=190, premium=185765.0, venue="L",
                side="buy", live_price=977.20)
    base.update(kw)
    return base


# ── The API actually serialises it ──────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["exec_price", "live_price", "side", "shares"])
def test_the_endpoint_returns_the_new_fields(field):
    """THE BUG. T377 stored these; no endpoint exposed them, so the UI could not show them."""
    assert f'"{field}"' in _endpoint()


def test_shares_are_derived_not_joined():
    """T376 tested the join to dark_pool_prints and REJECTED it: MU/AVGO matched no print,
    BULL matched SIX for one alert with nothing to disambiguate."""
    ep = _endpoint()
    assert "DarkPoolPrint" not in ep
    assert "qualifying_metric /" in ep


def test_shares_prefer_the_execution_price():
    """The premium was transacted at the EXEC price. Dividing by the live price would give a
    share count that never traded."""
    ep = _endpoint()
    assert "(row.exec_price or row.alert_price)" in ep


def test_a_missing_premium_yields_none_not_zero():
    """0 shares would be a confident false statement about a print that definitely had size."""
    ep = _endpoint()
    assert "else None" in ep
    i = ep.index('"shares"')
    assert "if row.qualifying_metric and" in ep[i:i + 400]


def test_alert_price_is_still_returned_unchanged():
    """Existing consumers and every forward-return column depend on it."""
    assert '"alert_price": row.alert_price' in _endpoint()


# ── The alert email ─────────────────────────────────────────────────────────────────────

def test_the_email_shows_the_side():
    _, html, text = _render([_c(side="buy"), _c(symbol="X", side="sell")])
    assert "BUY" in text and "SELL" in text
    assert "BUY" in html and "SELL" in html


def test_the_email_shows_the_live_price_and_the_difference():
    _, _, text = _render([_c()])
    assert "$977.20" in text
    assert "+0.07%" in text


def test_a_seller_priced_above_live_still_reads_SELL():
    """THE CASE THAT JUSTIFIES USING NBBO AT ALL. A real shape: the print is 0.63% ABOVE the
    live price, so the intuitive rule says "buying", while the spread position says the seller
    hit the bid."""
    _, _, text = _render([_c(symbol="INTC", price=24.10, live_price=23.95, side="sell")])
    assert "SELL" in text
    assert "+0.63%" in text, "the difference is still shown, as context"


def test_an_undeterminable_side_renders_a_dash():
    """~10% of prints, measured live (61 of 611). Never guessed."""
    _, _, text = _render([_c(side=None, live_price=None)])
    assert "[—]" in text
    # Scope to the DATA ROWS: the disclaimer below legitimately contains the words "BUY/SELL",
    # so asserting against the whole body matched my own explanatory prose — the recurring
    # "assert on code, not prose" trap in this repo.
    rows = text.split("Measured fact")[0]
    assert "BUY" not in rows and "SELL" not in rows


def test_a_missing_live_price_does_not_render_a_fake_zero_percent():
    """"0.00% vs live" would assert the block printed AT the live price — a measurement never
    taken."""
    _, _, text = _render([_c(live_price=None)])
    assert "0.00%" not in text


def test_the_side_is_never_defaulted():
    fn = _email_fn()
    assert 'side_str = {"buy": "BUY", "sell": "SELL"}.get(side or "", "—")' in fn


def test_the_existing_size_price_and_venue_survive():
    """A layout edit must not drop what the email already reported."""
    _, _, text = _render([_c()])
    assert "190 shares" in text and "$977.89" in text and "venue L" in text


# ── The framing, which matters as much as the data ──────────────────────────────────────

def test_both_bodies_say_the_side_is_measured_not_a_forecast():
    """This email's whole premise is that it reports a MEASURED fact. Adding a direction
    without extending that disclaimer would break the one promise it makes."""
    _, html, text = _render([_c()])
    for body in (html, text):
        low = body.lower()
        assert "aggressor" in low
        assert "not mean the stock will go" in low or "not a forecast" in low


def test_both_bodies_explain_that_a_dash_is_not_neutral():
    _, html, text = _render([_c(side=None)])
    # The HTML wraps "never\n      neutral" across a source line, so normalise whitespace
    # rather than asserting on a phrase that straddles the break.
    import re as _re
    for body in (html, text):
        assert "never neutral" in _re.sub(r"\s+", " ", body).lower()


def test_the_original_measured_fact_disclaimer_is_intact():
    _, html, text = _render([_c()])
    assert "MEASURED fact" in html
    assert "Not financial advice" in html and "Not financial advice" in text


# ── The Dark Pool tab ───────────────────────────────────────────────────────────────────

def test_the_tab_renders_all_four_new_columns():
    for h in ("'Exec'", "'Live'", "'Side'", "'Shares'"):
        assert h in PAGE, h


def test_the_header_count_matches_the_empty_state_colspan():
    """An empty-state colSpan that lags the header count breaks the table layout — the kind of
    thing only a null render shows."""
    assert "colSpan={7}" in PAGE
    assert "['Date', 'Symbol', 'Exec', 'Live', 'Side', 'Shares', 'Premium']" in PAGE


def test_the_tab_colours_buy_and_sell_differently():
    assert "row.side === 'buy' ? '#4ade80'" in PAGE
    assert "row.side === 'sell' ? '#f87171'" in PAGE


def test_the_tab_renders_a_dash_for_an_unknown_side():
    assert "row.side === 'buy' ? 'BUY' : row.side === 'sell' ? 'SELL' : '—'" in PAGE


def test_the_tab_does_not_falsy_test_the_prices():
    """`row.live_price != null`, not truthiness — a genuine 0 must not render as "—", and the
    falsy habit is what produces this codebase's recurring zero bugs."""
    assert "row.live_price != null" in PAGE
    assert "row.shares != null" in PAGE


def test_exec_falls_back_to_alert_price_for_legacy_rows():
    """Rows written before T377 have no exec_price, but their alert_price WAS the exec price
    when no live quote existed — so the fallback is correct, not merely convenient."""
    assert "(row.exec_price ?? row.alert_price)" in PAGE


def test_the_side_cell_explains_itself_on_hover():
    assert "NBBO spread at execution" in PAGE
    assert "Not a guess." in PAGE


def test_the_type_marks_every_new_field_optional():
    """An older backend must render "—" rather than "undefined"."""
    for f in ("exec_price?:", "live_price?:", "side?:", "shares?:"):
        assert f in API_TS, f
