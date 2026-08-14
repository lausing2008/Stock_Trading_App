"""Tests for AUD265-BROKERERROR-RAWEXCEPTION-EXPOSURE (_sanitize_broker_error()) and
AUD265-RECONCILE-MISLABEL (the exit-fill-reconciliation branch must never write a
trade.broker_error message that reads as "the order failed" — the real order already placed
successfully at that point, per AUD232-SILENT-BROKER-RECONCILE's own comment; only the local
post-fill bookkeeping failed).

Both were found via a code-review pass against BUG-BROKERSTATUS-ERRORMSG-NOT-THREADED (the fix
that first started surfacing PaperTrade.broker_error through GET .../positions and .../trades
and rendering it directly in a browser tooltip): (1) that message would have been shown
confidently prefixed as "Real order failed: ..." even for a trade whose real broker order
genuinely succeeded, and (2) any future broker-library exception's raw text is now exposed
verbatim to any authenticated app user (get_current_user, not admin-gated) via that same
tooltip, with no sanitization applied.

paper_trading_engine.py can't be imported directly in this test environment — matching this
repo's established source-text-extraction technique for functions this heavily coupled to
Docker-only db/broker dependencies.
"""
import pathlib

_PTE_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py").read_text()


def _place_broker_exit_body() -> str:
    start = _PTE_SOURCE.index("def _place_broker_exit(")
    end = _PTE_SOURCE.index("\n\n\ndef poll_broker_order_fills(", start)
    return _PTE_SOURCE[start:end]


def _place_broker_entry_body() -> str:
    start = _PTE_SOURCE.index("def _place_broker_entry(")
    end = _PTE_SOURCE.index("\n\ndef _place_broker_exit(", start)
    return _PTE_SOURCE[start:end]


_EXIT_BODY = _place_broker_exit_body()
_ENTRY_BODY = _place_broker_entry_body()


# ── _sanitize_broker_error() — real behavioral tests, pure/dependency-free ──────────────────

_namespace: dict = {}
_sanitize_start = _PTE_SOURCE.index("def _sanitize_broker_error(")
_sanitize_end = _PTE_SOURCE.index("\n\n\ndef _place_broker_entry(")
_sanitize_source = _PTE_SOURCE[_sanitize_start:_sanitize_end]
# _sanitize_broker_error() references _BROKER_ERROR_REDACT_PATTERNS, defined a few lines above
# it in the real file — pull both into the exec namespace together so the function can run.
_patterns_start = _PTE_SOURCE.index("import re as _re")
exec(_PTE_SOURCE[_patterns_start:_sanitize_end], _namespace)
_sanitize_broker_error = _namespace["_sanitize_broker_error"]


class TestSanitizeBrokerError:
    def test_ordinary_short_error_message_passes_through_unchanged(self):
        msg = "E*Trade place_order failed: 400 {\"Error\":{\"code\":101,\"message\":\"timed out\"}}"
        assert _sanitize_broker_error(msg) == msg

    def test_long_opaque_token_is_redacted(self):
        token = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"
        msg = f"E*Trade auth failed: session_token={token} invalid"
        result = _sanitize_broker_error(msg)
        assert token not in result
        assert "[redacted]" in result

    def test_truncates_to_512_chars_after_redaction(self):
        # Space-separated words (not one long opaque run) so the redaction pattern doesn't
        # collapse this down before truncation is even exercised.
        msg = "error " * 200
        assert len(_sanitize_broker_error(msg)) == 512

    def test_short_alphanumeric_words_are_not_redacted(self):
        """Must not be so aggressive that it eats ordinary short words/codes like an HTTP
        status or a genuine short error code — only long (24+) opaque runs are targeted."""
        msg = "E*Trade place_order failed: 400 code101 rejected"
        assert _sanitize_broker_error(msg) == msg


# ── The reconciliation message must never claim the order failed ────────────────────────────

class TestReconciliationMessageNeverClaimsOrderFailed:
    def test_reconciliation_branch_message_does_not_contain_the_word_failed_referring_to_the_order(self):
        """The exact bug found by code review: a message that reads as an order failure when
        the SELL order genuinely placed successfully. The fix's own message must lead with
        confirmation the order succeeded, not a bare "failed" claim."""
        log_idx = _EXIT_BODY.index('log.error("broker.exit_fill_reconciliation_failed"')
        assign_idx = _EXIT_BODY.index("trade.broker_error = _sanitize_broker_error(", log_idx)
        surrounding = _EXIT_BODY[assign_idx:assign_idx + 300]
        assert "placed successfully" in surrounding
        assert "bookkeeping" in surrounding or "reconcile" in surrounding

    def test_reconciliation_message_goes_through_the_sanitizer(self):
        log_idx = _EXIT_BODY.index('log.error("broker.exit_fill_reconciliation_failed"')
        assign_idx = _EXIT_BODY.index("trade.broker_error = ", log_idx)
        assert _EXIT_BODY[assign_idx:assign_idx + 60].startswith(
            "trade.broker_error = _sanitize_broker_error("
        )

    def test_the_two_raw_exception_sites_also_go_through_the_sanitizer(self):
        """The two OUTER handlers (genuine order-placement/order-submission failure, where the
        raw exception text really does mean "the order failed") must still be wrapped by the
        sanitizer too — this is a separate concern (redacting opaque tokens) from the
        mislabeling fix above, and applies regardless of which branch is genuinely a real
        order failure."""
        assert _ENTRY_BODY.count("trade.broker_error = _sanitize_broker_error(str(exc))") == 1
        assert _EXIT_BODY.count("trade.broker_error = _sanitize_broker_error(str(exc))") == 1
