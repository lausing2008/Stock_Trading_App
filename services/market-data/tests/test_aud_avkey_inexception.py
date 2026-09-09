"""AUD-AVKEY-INEXCEPTION — Alpha Vantage's key would leak through an exception message.

The third and last instance of the leak class found this session. Alpha Vantage has **no header
auth** — the key must travel as the `apikey` QUERY PARAM — so the sibling fix used for Polygon
(`Authorization: Bearer`, AUD-POLYGONKEY-INURL) is unavailable here.

THE LEAK PATH IS NOT THE REQUEST LOGGER, and that correction is the reusable part:

  * `configure_logging()` IS called for every service, via `common.service.create_app()` — I
    initially reported it as called by no service `main.py`, having grepped the 12 entrypoints
    and missed the shared factory they all use. Verified live: httpx sits at level 30 (WARNING),
    so it emits no request lines at all.
  * The real path is `raise_for_status()`, whose `HTTPStatusError` message **embeds the full
    URL** — and callers log that message at **ERROR** (`ingest.adapter_failed` /
    `ingest.symbol_failed`), which no log level suppresses. Measured on the Polygon twin: **58
    plaintext key occurrences** in one container's logs, every one from an exception message and
    none from a request line.

So suppressing a logger fixes nothing. Either the key leaves the URL (Polygon) or the exception
stops carrying the URL (here).

VERIFIED EMPIRICALLY that httpx's exception message contains the URL but **NOT headers** — which
is why Polygon's own `raise_for_status()` is safe now that its key is a header, and why only
Alpha Vantage needed a re-raise.
"""
import pathlib

import httpx
import pytest

MD = pathlib.Path(__file__).resolve().parents[1] / "src"
AV_SRC = (MD / "adapters/alpha_vantage_adapter.py").read_text()
POLY_SRC = (MD / "adapters/polygon_adapter.py").read_text()


def _code_only(src: str) -> str:
    """Strip comment lines — five tests this session matched their own prose."""
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


# ── The leak is closed ──────────────────────────────────────────────────────────────────

def test_alpha_vantage_no_longer_calls_raise_for_status():
    """THE FIX. Its message embeds the full URL, and callers log it at ERROR."""
    assert "raise_for_status" not in _code_only(AV_SRC)


def test_it_raises_a_url_free_error_instead():
    code = _code_only(AV_SRC)
    assert "if r.status_code >= 400:" in code
    assert "URL withheld" in AV_SRC
    assert "raise RuntimeError(" in code


def test_the_cause_chain_is_suppressed():
    """Without `from None`, the original exception's repr rides along in the traceback and
    carries the URL anyway — so the fix would be cosmetic."""
    assert "from None" in _code_only(AV_SRC)


def test_the_error_still_carries_what_a_caller_needs():
    """A caller needs the status code and the symbol to retry or fail over. Only the URL is
    dropped — an opaque error would be a different bug."""
    assert "HTTP {r.status_code}" in AV_SRC
    assert "{symbol}" in AV_SRC


def test_the_key_is_still_sent_as_a_query_param():
    """RECORDED, NOT A REGRESSION. Alpha Vantage has no header auth, so this is unavoidable —
    the fix is that the key never reaches a LOG, not that it leaves the URL."""
    assert '"apikey": key' in _code_only(AV_SRC)


def test_the_status_check_precedes_parsing():
    """Parsing a 4xx body first would raise a confusing pandas error instead of the real cause."""
    code = _code_only(AV_SRC)
    assert code.index("if r.status_code >= 400:") < code.index("pd.read_csv(")


# ── Why Polygon's raise_for_status() is safe ────────────────────────────────────────────

def test_httpx_exception_messages_carry_the_url_but_not_headers():
    """THE MEASUREMENT THAT SCOPES THIS FIX. If headers leaked too, Polygon's header fix would
    have been useless and it would need this same re-raise.

    `httpx` is MagicMock'd by this suite's conftest (Docker-only dep), so import the REAL library
    in a subprocess rather than asserting against a mock — a mocked raise_for_status() raises
    nothing and the test would pass vacuously either way.
    """
    import subprocess, sys, textwrap
    script = textwrap.dedent("""
        import httpx, sys
        req = httpx.Request("GET", "https://api.polygon.io/v2/aggs?adjusted=true",
                            headers={"Authorization": "Bearer SECRET123"})
        resp = httpx.Response(401, request=req)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            msg = str(e)
            print("URL" if "api.polygon.io" in msg else "NOURL")
            print("SECRET" if "SECRET123" in msg else "NOSECRET")
            sys.exit(0)
        sys.exit(1)
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip(f"real httpx unavailable in this environment: {out.stderr[:120]}")
    lines = out.stdout.split()
    assert lines[0] == "URL", "the URL IS in the exception message"
    assert lines[1] == "NOSECRET", "headers are NOT — this is why the Bearer fix works"


def test_polygon_keeps_raise_for_status_and_that_is_correct():
    """Its key is a header now, so its exception message cannot carry it."""
    code = _code_only(POLY_SRC)
    assert "r.raise_for_status()" in code
    assert 'headers={"Authorization": f"Bearer {key}"}' in code
    assert '"apiKey"' not in code


# ── Repo-wide parity ────────────────────────────────────────────────────────────────────

def test_no_adapter_combines_a_secret_query_param_with_raise_for_status():
    """THE PARITY ASSERTION. Fixing two of three instances is the wrong-path pattern this
    codebase keeps hitting — so check every adapter, not just the two known ones."""
    import re
    adapters = MD / "adapters"
    offenders = []
    for py in adapters.glob("*.py"):
        code = _code_only(py.read_text())
        has_secret_param = re.search(r'"(api_?key|apikey|token|secret)"\s*:', code)
        if has_secret_param and "raise_for_status" in code:
            offenders.append(py.name)
    assert offenders == [], (
        f"these adapters send a secret in params AND call raise_for_status, whose message "
        f"embeds the URL: {offenders}"
    )


def test_the_parity_check_is_not_vacuous():
    """A regex matching nothing would make the assertion above pass trivially — this codebase
    has shipped exactly that mistake."""
    import re
    code = _code_only(AV_SRC)
    assert re.search(r'"(api_?key|apikey|token|secret)"\s*:', code), \
        "the secret-param regex must actually match the known case"


def test_configure_logging_is_wired_via_the_shared_factory():
    """A CORRECTION TO MY OWN CLAIM. I reported this as called by no service main.py, having
    grepped the 12 entrypoints and missed the shared factory all of them use. It IS called —
    which is why httpx was already at WARNING and why the logger was never the leak."""
    svc = (pathlib.Path(__file__).resolve().parents[3] / "shared/common/service.py").read_text()
    assert "configure_logging(settings.log_level)" in svc
    i = svc.index("def create_app(")
    assert svc.index("configure_logging(settings.log_level)") > i, "inside create_app"


def test_the_httpx_suppression_still_exists_even_though_it_was_not_the_leak():
    """It is defence in depth for genuine request-line logging — worth keeping, just not the fix."""
    lg = (pathlib.Path(__file__).resolve().parents[3] / "shared/common/logging.py").read_text()
    assert 'logging.getLogger("httpx").setLevel(logging.WARNING)' in lg
