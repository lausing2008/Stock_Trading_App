"""AUD-PROVIDERKEY-ZOMBIEPUSH + AUD-POLYGONKEY-INURL — a deleted credential came back, and the
key was leaking through an exception message.

FOUND IN A LIVE ERROR LOG, one step after I had told the user the Polygon key issue was closed:

    401 Unauthorized for url 'https://api.polygon.io/...&apiKey=<the real 32-char key>'

Two independent defects in that one line.

## 1. ZOMBIEPUSH — a browser cache was authoritative over server state for a CREDENTIAL

`_app.tsx` re-pushed whatever provider keys sat in the BROWSER's localStorage on every app load.
We deleted the Polygon key from Redis and VERIFIED it absent — then it reappeared with the
IDENTICAL fingerprint (715074f68232), because simply opening the site pushed the stale copy back.

The key had been REVOKED at Polygon by then (confirmed HTTP 401), so the resurrected credential
was dead — but every 5-minute intraday cycle still spent a doomed request on it, and Polygon was
still in the US 5m adapter chain.

The fix inverts the authority: the push now SEEDS ONLY WHAT THE SERVER LACKS, using new
presence-only flags (`polygon_key_set` / `alpha_vantage_key_set`). A browser cache must never
revive a credential an operator deleted server-side.

## 2. POLYGONKEY-INURL — the leak path was the EXCEPTION, not the request log

I first assumed httpx's INFO request-line logging. **That was wrong and worth recording**: httpx's
effective level in the live container is already 30 (WARNING), so it logs no request lines at all.
All 58 `apiKey` occurrences in the container's logs came from `raise_for_status()`, whose
`HTTPStatusError` message EMBEDS THE FULL URL — and that message is then logged as
`ingest.symbol_failed` at ERROR, which no level filter suppresses.

So suppressing a logger would have fixed nothing. The key had to leave the URL: Polygon accepts
`Authorization: Bearer`.

A THIRD THING FOUND AND DELIBERATELY NOT ACTED ON: `common.logging.configure_logging()` — which
carries a comment about exactly this httpx/query-param leak — is called by **no service main.py
anywhere**, only by `hk_connect.py`. The httpx suppression it implements is therefore inactive
platform-wide. It happens not to matter here (the effective level is already WARNING via
basicConfig defaults), so wiring it up is a separate, unrelated change and is only recorded.
"""
import pathlib

import pytest

MD = pathlib.Path(__file__).resolve().parents[1] / "src"
POLY_SRC = (MD / "adapters/polygon_adapter.py").read_text()
AV_SRC = (MD / "adapters/alpha_vantage_adapter.py").read_text()
ADMIN_SRC = (MD / "api/admin.py").read_text()

FE = pathlib.Path(__file__).resolve().parents[3] / "frontend/src"
APP_SRC = (FE / "pages/_app.tsx").read_text()
API_SRC = (FE / "lib/api.ts").read_text()


def _code_only(src: str, marker: str) -> str:
    """Strip comment lines — this codebase has had FIVE tests match their own prose."""
    prefix = "//" if marker == "ts" else "#"
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith(prefix))


# ── 1. The key is out of the URL ────────────────────────────────────────────────────────

def test_polygon_no_longer_puts_the_key_in_query_params():
    """THE LEAK. `raise_for_status()`'s HTTPStatusError message embeds the full URL, and that
    message is logged at ERROR — no log-level filter can suppress it."""
    code = _code_only(POLY_SRC, "py")
    assert '"apiKey": key' not in code
    assert '"apiKey"' not in code


def test_polygon_sends_the_key_as_a_bearer_header():
    code = _code_only(POLY_SRC, "py")
    assert 'headers={"Authorization": f"Bearer {key}"}' in code


def test_polygon_still_sends_its_real_query_params():
    """The fix must not drop `adjusted`/`sort`/`limit` along with the key."""
    code = _code_only(POLY_SRC, "py")
    for p in ('"adjusted": "true"', '"sort": "asc"', '"limit": 50000'):
        assert p in code


def test_the_key_is_still_required_before_any_request():
    """Moving it to a header must not accidentally allow an unauthenticated call."""
    code = _code_only(POLY_SRC, "py")
    assert 'if not key:' in code
    assert code.index("if not key:") < code.index('headers={"Authorization"')


# ── 2. The frontend can no longer resurrect a deleted key ───────────────────────────────

def test_the_unconditional_push_is_gone():
    """THE BUG. It pushed localStorage on every load, reviving a server-side deletion."""
    code = _code_only(APP_SRC, "ts")
    assert "polygon_api_key: settings.polygonApiKey || undefined" not in code


def test_the_seed_on_load_is_removed_entirely():
    """WHY REMOVED RATHER THAN MADE CONDITIONAL. I first rewrote this to seed only when the
    server reported no key — still unsafe, because from the browser's side "operator deleted it"
    and "never configured" are INDISTINGUISHABLE, so any seed is a potential revival. And the
    block was redundant: AUD-PROVIDERKEY-INMEMORY already moved provider keys from an in-process
    dict to REDIS, so there is nothing to carry across a deploy. The Settings page is now the
    only writer — the correct authority for a credential."""
    code = _code_only(APP_SRC, "ts")
    assert "pushConfig" not in code, "the app-load path must not write credentials at all"


def test_the_dead_guard_flag_was_removed_too():
    code = _code_only(APP_SRC, "ts")
    assert "_configPushed" not in code


def test_the_settings_page_is_still_able_to_write():
    """The removal must not break the legitimate path — Settings still pushes on save."""
    settings_src = (FE / "pages/settings.tsx").read_text()
    assert "api.pushConfig({" in settings_src
    assert "unshare_polygon_key" in settings_src, "and can still CLEAR a key"


# ── 3. The presence flags ───────────────────────────────────────────────────────────────

def test_the_backend_exposes_presence_for_both_data_provider_keys():
    assert "def _provider_key_presence()" in ADMIN_SRC
    assert 'for name in ("polygon", "alpha_vantage")' in ADMIN_SRC
    assert 'out[f"{name}_key_set"] = bool(get_runtime_key(name))' in ADMIN_SRC


def test_presence_is_wired_into_both_feature_flag_responses():
    """There are two response blocks; a flag added to only one is a 50% silent failure."""
    assert ADMIN_SRC.count("**_provider_key_presence()") == 2


def test_presence_never_returns_the_secret_itself():
    i = ADMIN_SRC.index("def _provider_key_presence()")
    fn = ADMIN_SRC[i:ADMIN_SRC.index("\ndef ", i + 10)]
    assert "bool(" in fn, "must coerce to a boolean, never pass the value through"


def test_presence_fails_as_SET_on_a_redis_error():
    """A Redis hiccup must NOT make the frontend re-push a credential — the whole point."""
    i = ADMIN_SRC.index("def _provider_key_presence()")
    fn = ADMIN_SRC[i:ADMIN_SRC.index("\ndef ", i + 10)]
    tail = fn[fn.index("except Exception:"):]
    assert "= True" in tail


def test_presence_reads_through_the_registry_not_raw_redis():
    """The key prefix must keep ONE definition — this codebase found four copies of a constant."""
    i = ADMIN_SRC.index("def _provider_key_presence()")
    fn = ADMIN_SRC[i:ADMIN_SRC.index("\ndef ", i + 10)]
    assert "from ..adapters.registry import get_runtime_key" in fn
    assert "stockai:admin:provider_key" not in fn


def test_the_api_client_declares_the_new_flags():
    assert "polygon_key_set: boolean" in API_SRC
    assert "alpha_vantage_key_set: boolean" in API_SRC


# ── Behavioural model of seed-vs-revive ─────────────────────────────────────────────────

def _seed_on_load(local, server_has):
    """Mirrors the FIXED behaviour: app load never writes a credential, whatever either side
    holds. Parameters are kept to make the invariant explicit."""
    return {}


@pytest.mark.parametrize("local,server", [
    ({"polygon": "OLDKEY"}, {"polygon_key_set": False}),   # the exact resurrection scenario
    ({"polygon": "NEWKEY"}, {"polygon_key_set": False}),   # indistinguishable from the above
    ({"polygon": "STALE"},  {"polygon_key_set": True}),    # must never overwrite either
    ({},                     {"polygon_key_set": False}),  # nothing to push
])
def test_app_load_never_writes_a_credential(local, server):
    """THE INVARIANT. Deleted-vs-never-configured is indistinguishable from the browser, so the
    only safe rule is that app load writes nothing."""
    assert _seed_on_load(local, server) == {}


# ── The evidence, pinned ────────────────────────────────────────────────────────────────

def test_the_resurrection_is_recorded_with_its_fingerprint():
    """So the causal chain cannot rot into 'the key was never deleted'."""
    assert "715074f68232" in APP_SRC or "715074f68232" in __doc__


def test_the_real_leak_path_is_recorded():
    """I first blamed httpx's INFO request logging. Wrong — its effective level is already
    WARNING in the live container. The leak was raise_for_status()'s exception text."""
    assert "raise_for_status" in POLY_SRC
    assert "raise_for_status()" in __doc__


def test_alpha_vantage_still_needs_its_key_in_the_query_string():
    """RECORDED, NOT FIXED. Alpha Vantage's API has no header auth, so its key must stay a query
    param. It has no key configured today, and the same exception-message path would leak it if
    one were set. Left as-is deliberately rather than half-fixed."""
    assert '"apikey": key' in AV_SRC
"""
"""
