"""AUD-CONNPOOL-NESTEDSESSION follow-up (2026-09-22) — _compute_hk_breadth() and
_fetch_hk_market_regime() were the second and third instances of the same bug class found
while auditing the codebase after docs/incidents/db-connection-pool-exhaustion.md's production
incident (the first instance, _persist_scan_log(), is covered by test_paper_entry_scan_log.py).

_compute_hk_breadth() used to unconditionally open its own `with SessionLocal() as session:` —
a second connection from the pool, on top of whatever paper_trading_step()'s own outer session
already held, every time it ran (dormant most cycles thanks to _fetch_hk_market_regime()'s own
30-minute cache, but real on every cache-refresh). Fixed by making `session` an OPTIONAL
parameter: reuse the caller's already-open session when one is passed (the nested-in-a-scan
case), fall back to opening its own only when called standalone (e.g. the
`/stocks/regime?market=HK` route via get_last_hk_regime(), which has no session to lend).
_fetch_hk_market_regime() threads the same optional `session` through to it.

Unlike _should_enter()'s own AUD-CONNPOOL-NESTEDSESSION follow-up test (which gets a real,
end-to-end behavioral proof because its macro-blackout query issues a LOCAL `from sqlalchemy
import text` + raw SQL, evaluated fresh at call time — real sqlalchemy dropped in via a stub-pop
is enough), _compute_hk_breadth_with()'s query uses `select(Stock.id, ...)`/`Price.stock_id` —
module-level names bound once, at import time, to whatever `db`/`sqlalchemy` were in scope then.
Testing that behaviorally would require a genuine fresh import of paper_trading_engine.py against
a REAL `db` package, which isn't importable in this environment (`shared/db/session.py` calls
`get_settings().database_url` and `create_engine(...)` at import time — a real Postgres
connection string this local unit-test environment doesn't have). This matches
test_redis_pooling_and_delisted_sweep.py's own established precedent for this exact function
("paper_trading_engine.py... can't be imported directly in this test environment") — verified
here via source-text regression checks only.
"""
import pathlib

_PTE_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
).read_text()


# ── Source-text: signatures and call sites ──────────────────────────────────────────────────

def test_compute_hk_breadth_takes_an_optional_session_parameter():
    assert "def _compute_hk_breadth(session=None) -> float | None:" in _PTE_SOURCE


def test_fetch_hk_market_regime_takes_an_optional_session_parameter():
    assert "def _fetch_hk_market_regime(cfg: dict, session=None) -> dict:" in _PTE_SOURCE


def test_compute_hk_breadth_never_unconditionally_opens_its_own_sessionlocal():
    """The exact regression this fix prevents — must only open its own SessionLocal() in the
    `session is None` (standalone-caller) fallback branch, never unconditionally."""
    start = _PTE_SOURCE.index("def _compute_hk_breadth(")
    end = _PTE_SOURCE.index("\n\ndef _fetch_hk_market_regime(")
    body = _PTE_SOURCE[start:end]
    assert "if session is not None:" in body
    assert "with SessionLocal() as _own_session:" in body
    # The real query logic must live in the shared helper both branches call into, not be
    # duplicated per-branch (which would let the two paths silently drift apart).
    assert "return _compute_hk_breadth_with(session)" in body
    assert "return _compute_hk_breadth_with(_own_session)" in body


def test_fetch_hk_market_regime_forwards_its_own_session_to_compute_hk_breadth():
    start = _PTE_SOURCE.index("def _fetch_hk_market_regime(")
    end = _PTE_SOURCE.index("\n\ndef get_last_hk_regime(")
    body = _PTE_SOURCE[start:end]
    assert "breadth_pct = _compute_hk_breadth(session)" in body


def test_get_regime_for_call_site_passes_the_scan_session():
    """paper_trading_step()'s own per-scan-cycle regime lookup — the nested-in-a-hot-loop case
    this fix exists for — must pass its already-open `session`, not rely on the standalone
    fallback."""
    assert "_regime_by_market[mkt] = _fetch_hk_market_regime(pcfg, session)" in _PTE_SOURCE


def test_standalone_get_last_hk_regime_call_site_is_deliberately_left_without_a_session():
    """Regression guard the OTHER direction: get_last_hk_regime() (the /stocks/regime?market=HK
    route) has no session of its own to lend — it must keep relying on the `session=None`
    fallback, not be forced to thread one through where none exists."""
    assert "return _fetch_hk_market_regime(_DEFAULT_CONFIG)" in _PTE_SOURCE

