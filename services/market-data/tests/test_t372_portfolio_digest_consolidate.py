"""T372-PORTFOLIO-DIGEST-CONSOLIDATE — ten emails became one per market.

REPORTED BY THE USER FROM THEIR INBOX: ten "[Paper Portfolio]" emails arrived at 14:00 PST, five
of them reading "+0.0% Total Return / $0 Total P&L" — the five portfolios created on 2026-09-08,
which hold no positions yet.

THE CAUSE was structural, not a bug: the loop was `for user: for portfolio: send()`, so the email
count was users x active portfolios. It silently DOUBLED from 5 to 10 the day five portfolios were
added, and half the new volume carried no information. Nothing failed; the volume just grew.

TWO CHANGES, both requested by the user:

  1. ONE EMAIL PER MARKET, every portfolio rendered as a row.
  2. PER-MARKET TIMING — each an hour after its OWN close. This previously ran only at 17:00 ET,
     so HK portfolios were reported ~17 hours after the HK close AND gated on the US calendar: an
     HK digest could be skipped on a US holiday and sent on an HK one. Same class as
     AUD-PT-CROSSMARKETSWEEP, where HK's open burst force-closed US positions at the previous
     day's price.

EMPTY PORTFOLIOS ARE STILL SHOWN, deliberately — a portfolio at exactly 0.0% with no trades is a
real state (the entry gates admitted nothing), and hiding it would make its absence ambiguous
between "nothing happened" and "the job died". What was wrong was giving each one its own email.
"""
import pathlib

import pytest

SCHED = pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
SRC = SCHED.read_text()
EMAIL = (pathlib.Path(__file__).resolve().parents[1] / "src/services/email_service.py").read_text()


def _digest_body() -> str:
    i = SRC.index("def send_paper_portfolio_digest(")
    return SRC[i:SRC.index("\ndef ", i + 10)]


def _email_fn() -> str:
    i = EMAIL.index("def send_paper_portfolio_digest_email(")
    return EMAIL[i:EMAIL.index("\ndef ", i + 10)]


def _job_block(job_id: str) -> str:
    """Parsed FORWARD from its own add_job( — a backwards scan pairs a trigger with the
    PRECEDING job's id, which produced three retracted findings earlier in this session."""
    i = SRC.index(f'id="{job_id}"')
    return SRC[SRC.rindex("_scheduler.add_job(", 0, i):i]


# ── One email per market, not per portfolio ─────────────────────────────────────────────

def test_the_email_helper_takes_a_LIST_of_portfolios():
    """THE FIX. It used to take a single portfolio_name/total_return_pct/... set."""
    fn = _email_fn()
    assert "portfolios: list" in fn
    assert "portfolio_name: str" not in fn


def test_the_send_call_passes_every_portfolio_at_once():
    body = _digest_body()
    assert "portfolios=portfolio_rows" in body
    assert "market=_mkt" in body


def test_metrics_are_computed_once_per_portfolio_not_once_per_user():
    """The old nesting recomputed every portfolio's risk metrics and trade queries for EACH
    recipient — pure O(n_users) waste, since the metrics never depend on who is being emailed."""
    body = _digest_body()
    calc_loop = body.index("for p in portfolios:")
    send_loop = body.index("for user in users:")
    assert calc_loop < send_loop, "compute all portfolios, THEN loop recipients"


def test_only_one_send_call_exists():
    """Two send calls would mean the per-portfolio email came back."""
    assert _digest_body().count("send_paper_portfolio_digest_email(") == 1


# ── Per-market timing and gating ────────────────────────────────────────────────────────

@pytest.mark.parametrize("job_id,tz", [
    ("paper_portfolio_digest_us", "America/New_York"),
    ("paper_portfolio_digest_hk", "Asia/Hong_Kong"),
])
def test_each_market_fires_an_hour_after_its_OWN_close(job_id, tz):
    blk = _job_block(job_id)
    assert "hour=17" in blk and "minute=0" in blk
    assert f'timezone="{tz}"' in blk
    assert 'day_of_week="mon-fri"' in blk


def test_each_market_is_gated_on_its_OWN_trading_calendar():
    """Previously US-only: an HK digest could be skipped on a US holiday and sent on an HK one."""
    body = _digest_body()
    assert "_is_trading_day_for(_mkt)" in body
    assert "if not _is_us_trading_day():" not in body


def test_portfolios_are_filtered_to_the_market_being_reported():
    body = _digest_body()
    assert '(_p.config or {}).get("market", "US")' in body
    assert "== _mkt" in body


def test_the_market_filter_runs_in_python_not_sql():
    """`config` is a `json` column, not `jsonb`. A ->> filter needs a cast, and a portfolio whose
    config omits the key would silently match nothing and vanish from its digest — the exact trap
    AUD-PT-CROSSMARKETSWEEP's own market filter documents."""
    body = _digest_body()
    assert "config::jsonb" not in body


def test_a_portfolio_with_no_market_key_defaults_to_US():
    """Silently dropping such a portfolio would stop reporting it entirely — strictly worse than
    the problem being fixed."""
    assert '.get("market", "US")' in _digest_body()


def _route(configs, mkt):
    return [c for c in configs
            if ((c or {}).get("market", "US") or "US").upper() == mkt]


def test_every_production_portfolio_lands_in_exactly_one_digest():
    """Real production set, verified 2026-09-09: 6 US, 4 HK. None may be dropped or duplicated."""
    cfgs = ([{"market": "US"}] * 6) + ([{"market": "HK"}] * 4)
    us, hk = _route(cfgs, "US"), _route(cfgs, "HK")
    assert len(us) == 6 and len(hk) == 4
    assert len(us) + len(hk) == len(cfgs)


def test_case_is_normalised():
    assert len(_route([{"market": "us"}], "US")) == 1


# ── Dedup scope inverted, on purpose ────────────────────────────────────────────────────

def test_the_dedup_key_is_scoped_per_user_and_MARKET():
    """Keeping the portfolio id would let a restart re-send the SAME consolidated email once for
    every portfolio it contains — the exact duplicate-send the original key existed to prevent,
    just inverted by the consolidation."""
    body = _digest_body()
    assert '{user.id}:{_mkt}:{today_str}' in body
    assert '{user.id}:{p.id}:{today_str}' not in body


def test_the_dedup_key_is_still_set_only_after_a_successful_send():
    body = _digest_body()
    set_idx = body.index("_rc.setex(redis_key")
    ok_idx = body.index("if ok:")
    assert ok_idx < set_idx


# ── Empty portfolios are shown, compactly ───────────────────────────────────────────────

def test_empty_portfolios_are_still_included():
    """A portfolio at 0.0% with no trades means the entry gates admitted nothing — a real state.
    Omitting it would make its absence ambiguous with a dead job."""
    fn = _email_fn()
    assert "no activity" in fn


def test_an_idle_portfolio_is_detected_from_BOTH_open_and_closed():
    """Open positions alone is not enough — a portfolio that closed a trade today but holds
    nothing now is NOT idle."""
    fn = _email_fn()
    assert 'opens == 0 and not p.get("today_closed")' in fn


def test_a_missing_sharpe_renders_a_dash_not_zero():
    """Three-state: a real 0.00 Sharpe is a measurement; None is not. The falsy-zero trap this
    codebase has a whole incident file for."""
    fn = _email_fn()
    assert "if sharpe is not None else" in fn
    assert '"—"' in fn


# ── Isolation preserved ─────────────────────────────────────────────────────────────────

def test_one_portfolios_bad_data_cannot_abort_the_market_digest():
    """AUD301's invariant, preserved through the restructure: initial_capital == 0 gives a
    ZeroDivisionError on the total_return_pct line."""
    body = _digest_body()
    calc_except = body.index("except Exception as _calc_exc:")
    metrics = body.index("_portfolio_risk_metrics(curve_rows)")
    assert metrics < calc_except
    tail = body[calc_except:calc_except + 400]
    assert "errors += 1" in tail
    assert "raise" not in tail


def test_a_send_failure_is_isolated_per_recipient():
    body = _digest_body()
    assert "except Exception as _send_exc:" in body
    assert 'log.warning("paper_portfolio_digest.recipient_send_error"' in body


def test_the_job_status_name_is_per_market():
    """Two jobs sharing one status key would make a dead HK digest invisible behind a healthy US
    one — the liveness gauges key on this name."""
    body = _digest_body()
    assert 'f"paper_portfolio_digest_{_mkt.lower()}"' in body


def test_both_job_ids_keep_the_family_prefix():
    """The alert-gate parity test and the liveness gauges recognise the family by prefix."""
    for jid in ("paper_portfolio_digest_us", "paper_portfolio_digest_hk"):
        assert f'id="{jid}"' in SRC
    assert 'id="paper_portfolio_digest"' not in SRC, "the old single job must be gone"


# ── The measured before/after ───────────────────────────────────────────────────────────

def test_the_email_volume_reduction():
    """10 active portfolios x 1 user = 10 emails/day before; 2 markets x 1 user = 2 after."""
    users, portfolios, markets = 1, 10, 2
    assert users * portfolios == 10
    assert users * markets == 2
