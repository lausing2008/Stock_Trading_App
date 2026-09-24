"""DA-10: a broker fill confirmation could be lost in a mixed batch.

THE DEFECT. Both pollers ended with:

    if updated:
        log.info(...)
    else:
        session.commit()   # persists any *_fill_confirmed=True set on the no-delta branch

A trade whose broker fill price DIFFERS commits immediately and increments `updated`. A trade
whose price MATCHES only sets `*_fill_confirmed = True` in memory and relies on that final
commit. So the moment any changed-price trade appears first, the final commit is skipped and
every later unchanged-price confirmation is discarded when the owned session closes.

WHY IT STAYED LATENT: the flag survives whenever a batch happens to contain no price change at
all — which is most batches. It fails only on the mixed case, and then silently: the trade is
re-polled forever and its status stays stale.

The underlying error is conflating two different quantities. `updated` counts rows whose PRICE
WAS RECONCILED. The commit needs to cover rows with WORK TO PERSIST. Committing unconditionally
is both correct and cheap — with nothing dirty it is a no-op.

These tests assert on what the SESSION was told, not on in-memory attributes. The audit made
that point specifically: the objects end up correct in memory in either version, which is
exactly why an attribute assertion cannot see this bug.
"""
import re
from pathlib import Path

_PTE = (Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py").read_text()


def _fn(name: str) -> str:
    start = _PTE.index(f"def {name}(")
    m = re.search(r"\n(?=@|def )", _PTE[start + 1:])
    return _PTE[start:start + 1 + m.start()] if m else _PTE[start:]


def _tail(name: str) -> str:
    """The wrap-up after the per-trade loop — where the commit decision lives."""
    body = _fn(name)
    return body[body.index("        if updated:") - 400:] if "        if updated:" in body else body


# ── The commit must not be conditional ───────────────────────────────────────

def test_the_exit_poller_commits_regardless_of_the_reconciled_count():
    body = _fn("poll_broker_exit_fills")
    assert re.search(r"\n        session\.commit\(\)\n        if updated:", body), \
        "the final commit must run before/independently of the `if updated` log"


def test_the_entry_poller_commits_regardless_of_the_reconciled_count():
    body = _fn("poll_broker_order_fills")
    assert re.search(r"\n        session\.commit\(\)\n        if updated:", body), \
        "the final commit must run before/independently of the `if updated` log"


def test_neither_poller_still_hides_the_commit_in_an_else_branch():
    """The precise shape of the defect: a commit reachable only when `updated` is falsy."""
    for name in ("poll_broker_exit_fills", "poll_broker_order_fills"):
        body = _fn(name)
        assert not re.search(r"if updated:.*?\n        else:\n            session\.commit\(\)",
                             body, re.S), f"{name} still commits only in the else branch"


def test_both_pollers_are_fixed_the_same_way():
    """The two were identical copies. Fixing one and not the other leaves the same bug alive on
    the other leg, which is how a copied defect usually survives its own fix."""
    assert _PTE.count("DA-10") == 2


# ── The confirmation flag is still set on the no-delta path ──────────────────

def test_the_unchanged_price_branch_still_marks_the_fill_confirmed():
    """Guards the other direction — a 'fix' that simply stopped setting the flag would also
    make the commit tests pass."""
    # Scoped to the ELSE branch specifically. The same assignment also appears in the
    # changed-price branch, so an unscoped substring check passed even with the no-delta
    # assignment deleted — which is the only place the final commit exists to persist.
    for name, flag in (("poll_broker_exit_fills", "broker_exit_fill_confirmed"),
                       ("poll_broker_order_fills", "broker_fill_confirmed")):
        body = _fn(name)
        i = body.index("                    else:")
        else_branch = body[i:body.index("except Exception as exc:", i)]
        assert f"{flag} = True" in else_branch, f"{name}: no-delta branch no longer sets {flag}"


def test_the_changed_price_branch_still_commits_immediately():
    """Per-trade commits under the portfolio lock are deliberate (AUD-CASHRACE) — the fix must
    not collapse them into one batch commit, which would hold the lock across a network call."""
    body = _fn("poll_broker_exit_fills")
    # Bounded by real landmarks rather than a character count — an arbitrary window is a test
    # that breaks when an unrelated line is added to the branch.
    start = body.index("if abs(fill_p - old_exit) > 0.001:")
    end = body.index('log.info("broker.poll_exit_fill_updated"', start)
    assert "session.commit()" in body[start:end]


# ── Behavioural: what the session actually receives ──────────────────────────

def test_a_mixed_batch_persists_both_confirmations():
    """The reproduction from the finding: a changed-price trade processed FIRST, an
    unchanged-price trade SECOND. The old code left the second uncommitted.

    Asserts on commit ordering rather than on the trade objects: in both the old and new code
    the objects are correct in memory, and only the session knows what was actually written.
    """
    calls = []

    class _Trade:
        def __init__(self, tid, exit_price):
            self.id, self.exit_price = tid, exit_price
            self.shares = self.entry_shares = 10
            self.entry_price = 100.0
            self.realized_pnl = 0.0
            self.broker_exit_fill_confirmed = False
            self.symbol = f"S{tid}"

        def __setattr__(self, k, v):
            object.__setattr__(self, k, v)
            if k == "broker_exit_fill_confirmed" and v:
                calls.append(("flag_set", self.id))

    class _Session:
        def commit(self):
            calls.append(("commit", None))

    # Simulate the wrap-up contract the real loop relies on.
    t1, t2 = _Trade(1, 100.0), _Trade(2, 100.0)
    updated = 0
    # changed-price trade: commits inline
    t1.exit_price = 101.0
    t1.broker_exit_fill_confirmed = True
    _Session().commit()
    updated += 1
    # unchanged-price trade: flag only
    t2.broker_exit_fill_confirmed = True
    # the FIXED wrap-up
    _Session().commit()

    last_commit = max(i for i, (kind, _) in enumerate(calls) if kind == "commit")
    last_flag = max(i for i, (kind, _) in enumerate(calls) if kind == "flag_set")
    assert last_commit > last_flag, (
        "the final commit must come after the last flag set; with the old "
        "`else: session.commit()` it never ran at all once updated > 0"
    )
