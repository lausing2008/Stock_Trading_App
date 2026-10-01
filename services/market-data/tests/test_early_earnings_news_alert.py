import pathlib
"""Tests for check_early_earnings_news_alerts() — a user-requested follow-up (2026-08-06) to
check_earnings_reactions()/check_earnings_impact_alerts(). Both existing alerts only fire once
EarningsEvent.eps_actual lands via event-intelligence's yfinance-based sync_todays_earnings()
(every 15 min, 7am-9pm ET) — confirmed live that yfinance itself can lag a real after-hours
announcement by hours. This alert instead checks news-intelligence's already-classified
real-time feed (PR Newswire/Business Wire/SEC EDGAR/Alpaca, category=="earnings") for a
same-day heads-up ahead of the full numeric reaction, which check_earnings_reactions() still
owns once eps_actual is populated.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler and other unstubbed modules, and httpx is stubbed as a bare MagicMock by
conftest.py) — covered by source-text regression checks for the scheduler wiring, plus a
direct behavioral exec() of _fetch_earnings_news_headline() (pure Python + httpx.Client, no
DB/apscheduler dependency of its own) against a fake httpx.Client to exercise the real logic.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _function_body(name: str) -> str:
    start = _scheduler_source.index(f"\ndef {name}(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── _fetch_earnings_news_headline() — direct behavioral test via exec() ─────────────────────

class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body


class _FakeClient:
    def __init__(self, response, capture: dict):
        self._response = response
        self._capture = capture

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self._capture["url"] = url
        self._capture["params"] = params
        return self._response


class _FakeHttpx:
    def __init__(self, response):
        self._response = response
        self.capture = {}

    def Client(self, timeout=None):
        return _FakeClient(self._response, self.capture)


def _build_fetch_earnings_news_headline(fake_httpx):
    """Extracts _fetch_earnings_news_headline()'s real source and exec()s it with `httpx` and
    `_settings` injected — exercising the actual function under test, not a hand-copied
    reimplementation."""
    start = _scheduler_source.index("def _fetch_earnings_news_headline(")
    end = _scheduler_source.index("\n\n\n_FUTURES", start)
    func_source = _scheduler_source[start:end]
    fake_settings = type("S", (), {"news_intelligence_url": "http://news-intelligence:8011"})()
    namespace = {"httpx": fake_httpx, "_settings": fake_settings}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of the real function's source
    return namespace["_fetch_earnings_news_headline"]


def test_returns_headline_when_an_earnings_category_item_exists():
    items = [
        {"category": "analyst", "headline": "Analyst raises price target"},
        {"category": "earnings", "headline": "ACME reports Q2 results"},
    ]
    fake_httpx = _FakeHttpx(_FakeResponse(200, items))
    fetch = _build_fetch_earnings_news_headline(fake_httpx)
    result = fetch("ACME")
    assert result == "ACME reports Q2 results"


def test_returns_none_when_no_earnings_category_item_present():
    items = [{"category": "analyst", "headline": "Analyst raises price target"}]
    fake_httpx = _FakeHttpx(_FakeResponse(200, items))
    fetch = _build_fetch_earnings_news_headline(fake_httpx)
    assert fetch("ACME") is None


def test_returns_none_on_empty_list():
    fake_httpx = _FakeHttpx(_FakeResponse(200, []))
    fetch = _build_fetch_earnings_news_headline(fake_httpx)
    assert fetch("ACME") is None


def test_returns_none_on_non_200_status():
    fake_httpx = _FakeHttpx(_FakeResponse(500, []))
    fetch = _build_fetch_earnings_news_headline(fake_httpx)
    assert fetch("ACME") is None


def test_returns_none_on_network_exception():
    class _RaisingClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **kw):
            raise ConnectionError("news-intelligence unreachable")

    class _RaisingHttpx:
        def Client(self, timeout=None):
            return _RaisingClient()

    fetch = _build_fetch_earnings_news_headline(_RaisingHttpx())
    assert fetch("ACME") is None


def test_queries_the_correct_symbol_and_endpoint():
    fake_httpx = _FakeHttpx(_FakeResponse(200, []))
    fetch = _build_fetch_earnings_news_headline(fake_httpx)
    fetch("ACME")
    assert fake_httpx.capture["url"] == "http://news-intelligence:8011/news"
    assert fake_httpx.capture["params"]["symbol"] == "ACME"


def test_first_matching_earnings_item_wins_not_the_last():
    """Confirms the function returns the FIRST category=='earnings' item it finds (the API's
    own most-recent-first ordering), not scans past it to a later, staler one."""
    items = [
        {"category": "earnings", "headline": "First earnings item"},
        {"category": "earnings", "headline": "Second earnings item"},
    ]
    fake_httpx = _FakeHttpx(_FakeResponse(200, items))
    fetch = _build_fetch_earnings_news_headline(fake_httpx)
    assert fetch("ACME") == "First earnings item"


# ── check_early_earnings_news_alerts() — source-text wiring checks ──────────────────────────

def test_recipient_scoping_reuses_the_shared_helper():
    """Must reuse _earnings_alert_recipient_symbols() (PriceAlert OR EarningsAlertSubscription)
    rather than re-deriving its own, narrower recipient query."""
    body = _function_body("check_early_earnings_news_alerts")
    assert "user_symbols, users_by_id = _earnings_alert_recipient_symbols(session)" in body


def test_only_symbols_with_eps_actual_still_null_are_checked():
    """The core non-duplication guarantee: once eps_actual has landed for a symbol,
    check_earnings_reactions() already owns the alert for it — this early-heads-up alert must
    not also fire (or re-fire) once the real numbers are in."""
    body = _function_body("check_early_earnings_news_alerts")
    assert "EarningsEvent.eps_actual.is_(None)" in body


def test_dedup_key_is_scoped_per_user_symbol_EVENT_and_phase():
    """SUPERSEDED 2026-09-30 by MU-02. This required the key to be
    `{uid}:{sym}:{today_str}` — one notification per symbol per calendar day. That is exactly
    what suppressed MU's actual Q4 result: a "Micron Earnings Ahead" preview had already taken
    the day's single slot, so the print that arrived 5.08 seconds after publication was never
    sent. The release PHASE is now part of the key, so each stage notifies once.

    The send loop also moved into `_send_early_earnings_stage()` so the dedup key is defined in
    one place rather than three levels deep inside the job."""
    body = _function_body("_send_early_earnings_stage")
    # The key gained the EVENT date in the same round: a calendar day is the wrong identity for
    # an after-hours release, which crosses UTC midnight between the print and the retry.
    assert 'redis_key = f"stockai:early_earnings_news:{uid}:{sym}:{event_date}:{phase}"' in body


def test_dedup_key_written_only_after_a_successful_send():
    """Matches this codebase's established AUD266-DEDUP-KEY-SET-BEFORE-SEND fix — the dedup
    key must be set INSIDE the successful-send branch, not before the send is attempted, so a
    transient failure doesn't permanently suppress the alert for the rest of the day."""
    body = _function_body("_send_early_earnings_stage")  # MU-02: loop extracted
    send_idx = body.index("sent_ok = send_email(u_obj.email, subject, f\"<p>{body_text}</p>\", body_text)")
    if_ok_idx = body.index("if sent_ok:")
    # TTL 86400 -> 172800 in the MU-02 round: an after-hours release lands near UTC midnight and
    # the marker must still be visible on the following session's retries. Anchored on the call
    # rather than the literal TTL so a future TTL change does not fail an ordering test.
    setex_idx = body.index("_rc.setex(redis_key")
    assert send_idx < if_ok_idx < setex_idx


def test_send_call_is_isolated_per_recipient():
    """A single recipient's send raising must not abort the loop for every other recipient —
    matches this codebase's established AUD266-PER-RECIPIENT-ISOLATION-NEVER-PROPAGATED fix."""
    body = _function_body("_send_early_earnings_stage")  # MU-02: loop extracted
    # MU-02: anchored on the call rather than on a literal indentation level — extracting the
    # loop into its own function changed the indentation, and a test about exception isolation
    # should not break because code moved left.
    send_idx = body.index("sent_ok = send_email(")
    tail = body[send_idx:send_idx + 350]
    assert "except Exception as _send_exc:" in tail
    assert "sent_ok = False" in tail


def test_records_job_status_on_every_exit_path():
    """Matches this codebase's established AUD266-ALERT-JOBS-LACK-STATUS-CONSEQUENCE fix — a
    new alert job must not repeat the "invisible to the admin health page" bug class."""
    body = _function_body("check_early_earnings_news_alerts")
    assert body.count('_record_job_status("check_early_earnings_news_alerts"') >= 3


def test_lock_key_and_ttl_match_the_established_pattern():
    assert '_EARLY_EARNINGS_NEWS_LOCK_KEY = "stockai:lock:check_early_earnings_news_alerts"' in _scheduler_source
    assert "_EARLY_EARNINGS_NEWS_LOCK_TTL = 55" in _scheduler_source


def test_job_is_registered_in_start_scheduler_every_minute():
    assert 'id="early_earnings_news_alert_check"' in _scheduler_source
    start = _scheduler_source.index('id="early_earnings_news_alert_check"')
    window = _scheduler_source[start - 200:start + 50]
    assert "minutes=1" in window
    assert "check_early_earnings_news_alerts" in window


def test_email_body_frames_this_as_a_detection_not_a_confirmed_result():
    """SUPERSEDED 2026-09-30 by MU-02: the body text moved into `earnings_phase.phase_body()`,
    which writes a DIFFERENT body per release stage — a preview must not read like a print. The
    honesty requirement is unchanged and is asserted there against every stage; this checks the
    results stage, the one the MU incident suppressed."""
    import importlib.util, sys as _sys
    _src = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings_phase.py"
    _sp = importlib.util.spec_from_file_location("eearly_phase", _src)
    _ep = importlib.util.module_from_spec(_sp); _sys.modules[_sp.name] = _ep
    _sp.loader.exec_module(_ep)
    body = _ep.phase_body("MU", _ep.PHASE_RESULTS, "Micron Q4 Adj EPS $3.42 Beats")
    assert "not a verified result" in body
    assert "have not landed" in body
    return


def _superseded_test_email_body_frames_this_as_a_detection_not_a_confirmed_result():
    """Design invariant: this alert has no real EPS numbers, so its body must not imply one —
    matches this codebase's established alert-honesty discipline (T249-P3, T257-TOP3-CONVICTION
    etc. all explicitly disclaim what they are NOT claiming)."""
    body = _function_body("check_early_earnings_news_alerts")
    assert "not a confirmed result" in body
    assert "follow-up alert" in body
