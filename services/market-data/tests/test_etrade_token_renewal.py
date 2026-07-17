"""Tests for T257-ETRADE-PROD-SYSTEMATIC.

renew_access_token() has existed on EtradeBroker since the original OAuth implementation but
was never scheduled anywhere — only a manual Settings "Reconnect" button called it, so a
token could go 2h-idle-dead mid-session with nothing noticing until the next 08:30 ET health
check. Separately, every broker call site (_place_broker_entry/_place_broker_exit/
poll_broker_order_fills) silently swallowed ALL exceptions, including a rejected/expired
token, with no user-visible signal until that same once-daily check.

_is_token_rejected_error() is pure/dependency-free, so it's loaded directly from source via
exec() (same technique as test_earnings_alert_bodies.py). scheduler.py itself can't be
imported in this test environment (its import chain pulls in apscheduler and other unstubbed
modules — see test_price_alert_price_check.py's docstring for the same constraint), so the
scheduling/notification wiring is covered by source-text regression checks instead, matching
test_scheduler_static_names.py's established pattern for this exact class of risk.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _load_function(name: str):
    start = _scheduler_source.index(f"def {name}")
    end = _scheduler_source.index("\n\n\n", start)
    namespace: dict = {}
    exec(_scheduler_source[start:end], namespace)  # noqa: S102 — isolated eval of one pure function's source
    return namespace[name]


_is_token_rejected_error = _load_function("_is_token_rejected_error")


# ── _is_token_rejected_error() — pure function, tested directly ─────────────────────────────

def test_token_rejected_string_is_detected():
    assert _is_token_rejected_error(Exception("E*Trade balance failed: 401 token_rejected")) is True


def test_bare_401_is_detected():
    assert _is_token_rejected_error(Exception("E*Trade get_order failed: 401 Unauthorized")) is True


def test_unauthorized_string_is_detected():
    assert _is_token_rejected_error(Exception("Some other message mentioning unauthorized access")) is True


def test_unrelated_error_is_not_detected():
    """A genuine network timeout or 500 must NOT be treated as a token rejection — that
    would incorrectly flip a healthy connection to unauthorized and spam a reauth email."""
    assert _is_token_rejected_error(Exception("Connection timed out")) is False
    assert _is_token_rejected_error(Exception("E*Trade place_order failed: 500 Internal Server Error")) is False


def test_case_insensitive_matching():
    assert _is_token_rejected_error(Exception("TOKEN_REJECTED")) is True
    assert _is_token_rejected_error(Exception("Unauthorized")) is True


# ── Scheduling + wiring — source-text regression checks (scheduler.py can't be imported) ────

def test_renewal_job_is_registered_five_times_across_the_trading_session():
    """Fixed clock times spanning 9:30-16:00 ET at ~90-minute spacing — NOT a raw minute-field
    interval (cron's minute field only spans 0-59, so minute="*/90" would silently never fire,
    a real mistake caught while writing this feature — checked here as an actual CronTrigger
    kwarg, not just absence of the substring anywhere in the file, since the file's own
    explanatory comment about avoiding this mistake legitimately mentions the string)."""
    assert 'minute="*/90"' not in _scheduler_source, "cron minute field cannot express a 90-minute interval — this must use fixed clock times instead"
    assert 'id=f"broker_token_renewal_{_hour}{_minute:02d}"' in _scheduler_source
    # the loop driving the 5 registrations — assert the exact 5 (hour, minute) pairs spanning
    # the trading session are present, not just that SOME loop exists
    assert "for _hour, _minute in ((9, 45), (11, 15), (12, 45), (14, 15), (15, 45)):" in _scheduler_source


def test_renewal_function_calls_renew_access_token_not_start_oauth():
    """The whole point is proactive keepalive via renew_access_token() — must not accidentally
    call start_oauth() (which would mint a new request token and break the existing session)."""
    start = _scheduler_source.index("def _renew_broker_tokens(")
    end = _scheduler_source.index("\n\n\n", start)
    body = _scheduler_source[start:end]
    assert "broker.renew_access_token()" in body
    assert "start_oauth()" not in body


def test_renewal_skips_non_etrade_brokers():
    """renew_access_token is an E*Trade OAuth 1.0a concept — must not be called for other
    broker types (e.g. a future Alpaca connection) that don't implement it."""
    start = _scheduler_source.index("def _renew_broker_tokens(")
    end = _scheduler_source.index("\n\n\n", start)
    body = _scheduler_source[start:end]
    assert 'conn.broker_type.startswith("etrade")' in body


def test_renewal_only_targets_active_and_authorized_connections():
    start = _scheduler_source.index("def _renew_broker_tokens(")
    end = _scheduler_source.index("\n\n\n", start)
    body = _scheduler_source[start:end]
    assert "BrokerConnection.is_active == True" in body
    assert "BrokerConnection.is_authorized == True" in body


def test_check_broker_auth_reuses_the_shared_notify_helper_not_duplicated_logic():
    """AUD-style regression: _check_broker_auth used to duplicate the token-rejection string
    matching and the mark-unauthorized-and-notify logic inline — now both must be factored
    into the shared helpers so the renewal cron and in-loop detection can reuse them instead
    of re-implementing (and potentially drifting from) the same checks."""
    start = _scheduler_source.index("def _check_broker_auth(")
    end = _scheduler_source.index("\ndef _renew_broker_tokens", start)
    body = _scheduler_source[start:end]
    assert "_is_token_rejected_error(_err)" in body
    assert "_mark_broker_unauthorized_and_notify(s, conn)" in body


# ── In-loop detection in paper_trading_engine.py's broker call sites ────────────────────────

def test_all_three_broker_call_sites_use_the_shared_token_rejection_handler():
    """_place_broker_entry/_place_broker_exit/poll_broker_order_fills previously swallowed
    EVERY exception identically (log.warning/log.debug + pass) with no way to distinguish a
    token rejection from a transient network error — meaning a dead token failed silently at
    every one of these call sites until the once-daily 08:30 ET check noticed. Each must now
    route through _handle_broker_error_if_token_rejected()."""
    assert _pte_source.count("_handle_broker_error_if_token_rejected(") >= 4  # def + 3 call sites


def test_handle_broker_error_helper_lazily_imports_scheduler_to_avoid_a_cycle():
    """scheduler.py imports several names from paper_trading_engine.py at module level
    (get_last_regime, paper_trading_step, etc.) — a module-level import in the other direction
    would create a circular import. The shared helpers must be imported lazily (inside the
    function body), not at paper_trading_engine.py's top level."""
    start = _pte_source.index("def _handle_broker_error_if_token_rejected(")
    end = _pte_source.index("\n\n\n", start)
    body = _pte_source[start:end]
    assert "from .scheduler import" in body
    # must NOT appear in the top-of-file import block (before the first function def)
    first_def = _pte_source.index("\ndef ")
    assert "from .scheduler import" not in _pte_source[:first_def]
