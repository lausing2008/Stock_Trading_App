"""Tests for _fetch_ml_price_direction() — T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE's reuse of
ml-prediction's EXISTING, already-trained, already-promoted per-symbol direction model as a
genuinely independent second signal alongside the rule-based coiling gate (see
check_prebreakout_alerts()'s own docstring for the full reasoning: a real squeeze-BREAKOUT-
specific classifier can't be honestly trained yet — only ~68 historical candidate days exist —
so this reuses an existing, unrelated model instead of fabricating one).

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler, and httpx is stubbed as a bare MagicMock by conftest.py) — covered by a direct
behavioral exec() of _fetch_ml_price_direction() (pure Python + httpx.Client, no DB/apscheduler
dependency of its own) against a fake httpx.Client, matching test_early_earnings_news_alert.py's
own established technique exactly (same fake-response/fake-client shape, just .post not .get).
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


class _FakeResponse:
    def __init__(self, status_code, json_body):
        self.status_code = status_code
        self._json_body = json_body

    def json(self):
        return self._json_body


class _FakeClient:
    def __init__(self, response_or_exc, capture: dict):
        self._response_or_exc = response_or_exc
        self._capture = capture

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        self._capture["url"] = url
        self._capture["json"] = json
        self._capture["headers"] = headers
        if isinstance(self._response_or_exc, Exception):
            raise self._response_or_exc
        return self._response_or_exc


class _FakeHttpx:
    def __init__(self, response_or_exc):
        self._response_or_exc = response_or_exc
        self.capture = {}

    def Client(self, timeout=None):
        return _FakeClient(self._response_or_exc, self.capture)


def _build_fetch_ml_price_direction(fake_httpx):
    """Extracts _fetch_ml_price_direction()'s real source and exec()s it with `httpx`,
    `_settings`, `_service_token`, and `log` injected — exercising the actual function under
    test, not a hand-copied reimplementation."""
    start = _scheduler_source.index("def _fetch_ml_price_direction(")
    end = _scheduler_source.index("\n\ndef check_prebreakout_alerts(", start)
    func_source = _scheduler_source[start:end]
    fake_settings = type("S", (), {"ml_prediction_url": "http://ml-prediction:8003"})()
    namespace = {
        "httpx": fake_httpx,
        "_settings": fake_settings,
        "_service_token": lambda: "fake-jwt",
        "log": type("L", (), {"warning": staticmethod(lambda *a, **kw: None)})(),
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of the real function's source
    return namespace["_fetch_ml_price_direction"], fake_httpx


def test_returns_confidence_and_model_version_on_a_normal_200():
    body = {"bullish_probability": 0.62, "direction": "up", "confidence": 61.5, "trained_at": "2026-08-01T00:00:00"}
    fake_httpx = _FakeHttpx(_FakeResponse(200, body))
    fetch, _ = _build_fetch_ml_price_direction(fake_httpx)

    confidence, model_version = fetch("AAPL")

    assert confidence == 61.5
    assert model_version == "2026-08-01T00:00:00"


def test_calls_the_real_endpoint_with_the_expected_style_and_auth_header():
    body = {"confidence": 40.0, "trained_at": "x"}
    fake_httpx = _FakeHttpx(_FakeResponse(200, body))
    fetch, wrapped = _build_fetch_ml_price_direction(fake_httpx)

    fetch("AAPL")

    assert wrapped.capture["url"] == "http://ml-prediction:8003/ml/predict"
    assert wrapped.capture["json"] == {"symbol": "AAPL", "model": "xgboost", "style": "SWING"}
    assert wrapped.capture["headers"] == {"Authorization": "Bearer fake-jwt"}


def test_404_no_trained_model_yet_fails_open_to_none_none_without_a_warning():
    """A 404 is a routine, expected state (this symbol/style has never been trained) — must
    fail open silently, matching signal-engine's own _fetch_ml_data() convention exactly."""
    fake_httpx = _FakeHttpx(_FakeResponse(404, {"detail": "No trained model"}))
    fetch, _ = _build_fetch_ml_price_direction(fake_httpx)

    confidence, model_version = fetch("NEWSTOCK")

    assert confidence is None
    assert model_version is None


def test_oos_suppressed_reports_unavailable_rather_than_the_misleading_neutral_defaults():
    """The real /ml/predict endpoint substitutes bullish_probability=0.5/confidence=0.0 when
    a model is suppressed for poor out-of-sample performance — reporting THAT verbatim would
    look like a genuine neutral read rather than "no usable signal," so this must degrade to
    (None, None) instead, the same honest-unavailability state as a 404."""
    body = {"bullish_probability": 0.5, "direction": "neutral", "confidence": 0.0, "oos_suppressed": True}
    fake_httpx = _FakeHttpx(_FakeResponse(200, body))
    fetch, _ = _build_fetch_ml_price_direction(fake_httpx)

    confidence, model_version = fetch("STALESTOCK")

    assert confidence is None
    assert model_version is None


def test_non_200_non_404_fails_open_and_logs_a_warning():
    fake_httpx = _FakeHttpx(_FakeResponse(500, {}))
    fetch, _ = _build_fetch_ml_price_direction(fake_httpx)

    confidence, model_version = fetch("AAPL")

    assert confidence is None
    assert model_version is None


def test_network_exception_fails_open():
    fake_httpx = _FakeHttpx(RuntimeError("connection refused"))
    fetch, _ = _build_fetch_ml_price_direction(fake_httpx)

    confidence, model_version = fetch("AAPL")

    assert confidence is None
    assert model_version is None


def test_missing_confidence_field_fails_open_rather_than_crashing():
    """The degenerate empty-features early-return path in /ml/predict omits `confidence`
    entirely (see trainer.py:893-894) — must degrade to (None, None), not raise a KeyError."""
    fake_httpx = _FakeHttpx(_FakeResponse(200, {"bullish_probability": 0.5}))
    fetch, _ = _build_fetch_ml_price_direction(fake_httpx)

    confidence, model_version = fetch("THINSYMBOL")

    assert confidence is None
    assert model_version is None


def test_missing_trained_at_falls_back_to_unknown_model_version():
    body = {"confidence": 55.0}
    fake_httpx = _FakeHttpx(_FakeResponse(200, body))
    fetch, _ = _build_fetch_ml_price_direction(fake_httpx)

    confidence, model_version = fetch("AAPL")

    assert confidence == 55.0
    assert model_version == "unknown"
