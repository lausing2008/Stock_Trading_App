"""Ingesting an official release, and associating it with an event as an explicit act.

The MU case drives these: the PROVIDER reports the fiscal Q4 period ending 2026-08-31 while the
ISSUER's own release says 2026-09-03. Three days apart, and the issuer is authoritative. A
proximity rule would have silently picked one; association resolves it with the document as
evidence and records exactly what changed.
"""
import importlib.util
import pathlib
import sys
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
for _m in ["redis", "httpx", "structlog", "yfinance", "pandas"]:
    sys.modules.setdefault(_m, MagicMock())
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "shared"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "issuer_documents_under_test", _SRC / "services" / "issuer_documents.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load()

#: Micron's real fiscal Q4 2026 figures, each with the basis the issuer itself reported.
def _f(value, units, basis, period, citation):
    return {"value": value, "units": units, "basis": basis, "period": period,
            "citation": citation}


MU_FACTS = {
    "revenue": _f(54.23e9, "USD", "GAAP", "fiscal Q4 2026", "consolidated statements of operations"),
    "eps_adjusted": _f(33.42, "USD/share", "non-GAAP adjusted", "fiscal Q4 2026",
                       "non-GAAP reconciliation, diluted EPS"),
    "gross_margin_gaap": _f(86.8, "pct", "GAAP", "fiscal Q4 2026", "release headline table"),
    "gross_margin_non_gaap": _f(87.0, "pct", "non-GAAP", "fiscal Q4 2026",
                                "non-GAAP reconciliation"),
    "operating_cash_flow": _f(43.97e9, "USD", "GAAP", "fiscal Q4 2026",
                              "consolidated statements of cash flows"),
}


def test_a_fact_without_an_accounting_basis_is_refused():
    """A GAAP gross margin and a non-GAAP one are different values the issuer reported
    separately; a bare number cannot carry that distinction."""
    out = M.ingest_release(
        "MU", source_url="https://x/", publisher="Micron", title="Results",
        fiscal_period_end=date(2026, 9, 3), fiscal_label="FY2026 Q4",
        fiscal_source="release headline", published_at=datetime(2026, 9, 30, 20, 5),
        facts={"revenue": 54.23e9},            # a bare number
        extraction_method=M.MANUAL_TRANSCRIPTION, actor="t")
    assert "error" in out
    assert out["offending_keys"] == {"revenue": ["not an object"]}
    assert out["committed"] is False


def test_a_figure_without_units_period_or_citation_is_refused():
    """$54.23B and 54230 are the same revenue in different units; a figure for another period is
    simply a different fact; and a citation is what makes ONE number checkable."""
    out = M.ingest_release(
        "MU", source_url="https://x/", publisher="Micron", title="Results",
        fiscal_period_end=date(2026, 9, 3), fiscal_label="FY2026 Q4",
        fiscal_source="release headline", published_at=datetime(2026, 9, 30, 20, 5),
        facts={"revenue": {"value": 54.23e9, "basis": "GAAP"}},
        extraction_method=M.MANUAL_TRANSCRIPTION, actor="t")
    assert out["committed"] is False
    assert sorted(out["offending_keys"]["revenue"]) == ["citation", "period", "units"]


def test_an_unknown_extraction_method_is_refused():
    """A transcription and a parser fail in different ways; a reader deciding how far to trust a
    number needs to know which produced it."""
    out = M.ingest_release(
        "MU", source_url="https://x/", publisher="Micron", title="Results",
        fiscal_period_end=date(2026, 9, 3), fiscal_label="FY2026 Q4",
        fiscal_source="release headline", published_at=datetime(2026, 9, 30, 20, 5),
        facts=MU_FACTS, extraction_method="vibes", actor="t")
    assert "error" in out and out["committed"] is False


def test_ingestion_previews_without_writing():
    out = M.ingest_release(
        "MU", source_url="https://x/", publisher="Micron", title="Results",
        fiscal_period_end=date(2026, 9, 3), fiscal_label="FY2026 Q4",
        fiscal_source="release headline", published_at=datetime(2026, 9, 30, 20, 5),
        facts=MU_FACTS, extraction_method=M.MANUAL_TRANSCRIPTION, actor="t")
    assert out["committed"] is False
    assert out["facts"] == 5
    # No bytes were supplied, so the identity available is our own extraction — and it says so.
    assert out["content_hash"].startswith("sha256-facts:")
    assert out["source_bytes_hash"] is None
    assert "NOT AVAILABLE" in out["source_edit_detection"]


def test_supplying_the_document_bytes_enables_source_edit_detection():
    out = M.ingest_release(
        "MU", source_url="https://x/", publisher="Micron", title="Results",
        fiscal_period_end=date(2026, 9, 3), fiscal_label="FY2026 Q4",
        fiscal_source="release headline", published_at=datetime(2026, 9, 30, 20, 5),
        facts=MU_FACTS, extraction_method=M.MANUAL_TRANSCRIPTION, actor="t",
        source_bytes=b"<html>the release as retrieved</html>")
    assert out["content_hash"].startswith("sha256-bytes:")
    assert out["source_bytes_hash"] == out["content_hash"]
    assert out["facts_hash"].startswith("sha256-facts:")
    assert out["source_bytes_hash"] != out["facts_hash"]
    assert "available:" in out["source_edit_detection"]


def test_re_extracting_a_figure_does_not_claim_the_source_changed():
    """The defect this separates: one digest over our own transcription was documented as byte
    identity, so a correction WE made read as the issuer editing the release."""
    raw = b"<html>unchanged release</html>"
    kw = dict(source_url="https://x/", publisher="Micron", title="Results",
              fiscal_period_end=date(2026, 9, 3), fiscal_label="FY2026 Q4",
              fiscal_source="release headline",
              published_at=datetime(2026, 9, 30, 20, 5),
              extraction_method=M.MANUAL_TRANSCRIPTION, actor="t", source_bytes=raw)
    a = M.ingest_release("MU", facts=MU_FACTS, **kw)
    b = M.ingest_release("MU", facts=MU_FACTS | {
        "revenue": _f(54.25e9, "USD", "GAAP", "fiscal Q4 2026", "restated line")}, **kw)
    assert a["source_bytes_hash"] == b["source_bytes_hash"], "the source document did not change"
    assert a["facts_hash"] != b["facts_hash"], "our extraction did"


def test_the_facts_digest_changes_when_a_figure_changes():
    a = M.facts_digest(MU_FACTS)
    b = M.facts_digest(MU_FACTS | {
        "revenue": _f(54.25e9, "USD", "GAAP", "fiscal Q4 2026", "restated line")})
    assert a != b
    assert M.facts_digest(MU_FACTS) == a, "the same extraction hashes the same"


class _Doc:
    def __init__(self, **kw):
        self.id = kw.get("id", 1); self.stock_id = kw.get("stock_id", 7)
        self.fiscal_period_end = kw.get("fiscal_period_end", date(2026, 9, 3))
        self.published_at = kw.get("published_at", datetime(2026, 9, 30, 20, 5))
        self.event_id = kw.get("event_id")
        self.association = None


class _Event:
    def __init__(self, **kw):
        self.id = kw.get("id", 42694); self.stock_id = kw.get("stock_id", 7)
        self.report_date = kw.get("report_date", date(2026, 8, 31))
        self.period_end = kw.get("period_end", date(2026, 8, 31))
        self.report_date_source = kw.get("report_date_source", "substituted_period_end")
        self.fetched_at = None


class _Stock:
    def __init__(self, market="US"):
        self.market = market


def _patch(monkeypatch, doc, ev, stock=None):
    stock = stock or _Stock()
    class _S:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, model, pk):
            return {"D": doc, "E": ev, "S": stock}[model.__name__]
        def commit(self): self.committed = True
    monkeypatch.setattr(M, "_db", lambda: (
        type("E", (), {}), type("D", (), {}), (lambda: _S()), type("S", (), {})))
    return _S


def test_association_previews_every_value_it_would_change(monkeypatch):
    """An association rewrites the event's fiscal period AND its announcement date — exactly the
    kind of correction that should be read before it is applied."""
    _patch(monkeypatch, _Doc(), _Event())
    plan = M.associate_with_event(1, 42694, actor="t", rationale="issuer release")
    assert plan["committed"] is False
    c = plan["changes"]
    assert c["period_end"] == {"from": "2026-08-31", "to": "2026-09-03"}, \
        "the issuer's period end supersedes the provider's"
    assert c["report_date"] == {"from": "2026-08-31", "to": "2026-09-30"}, \
        "a substituted period end becomes the real announcement date"
    assert c["report_date_source"]["to"] == "issuer_release"


def test_association_does_not_lift_notification_suppression(monkeypatch):
    """A release ingested days late must not become deliverable because its date is now
    accurate: it still arrived through a historical import."""
    _patch(monkeypatch, _Doc(), _Event())
    plan = M.associate_with_event(1, 42694, actor="t", rationale="r")
    assert "unchanged" in plan["notification_suppression"]
    body = (_SRC / "services" / "issuer_documents.py").read_text()
    body = body[body.index("def associate_with_event("):]
    assert "notification_suppressed_at = None" not in body
    assert "notification_suppressed_at=None" not in body


def test_a_document_without_a_publication_time_cannot_establish_an_announcement(monkeypatch):
    _patch(monkeypatch, _Doc(published_at=None), _Event())
    out = M.associate_with_event(1, 42694, actor="t", rationale="r")
    assert "error" in out
    assert "announcement date" in out["error"]
    assert out["committed"] is False


def test_a_document_cannot_be_associated_with_another_issuers_event(monkeypatch):
    _patch(monkeypatch, _Doc(stock_id=7), _Event(stock_id=9))
    out = M.associate_with_event(1, 42694, actor="t", rationale="r")
    assert "error" in out and "different issuers" in out["error"]
    assert out["committed"] is False


def test_the_announcement_date_is_the_exchange_local_date_not_the_utc_one(monkeypatch):
    """A 20:05 ET release is 00:05 UTC the NEXT day. Taking `.date()` off the UTC stamp moved
    the announcement a day forward — one session off, in the single field the reaction window
    is measured from."""
    _patch(monkeypatch, _Doc(published_at=datetime(2026, 10, 1, 0, 5)), _Event())
    plan = M.associate_with_event(1, 42694, actor="t", rationale="r")
    d = plan["announcement_dating"]
    assert d["utc_calendar_date"] == "2026-10-01"
    assert d["announcement_date"] == "2026-09-30"
    assert d["exchange_timezone"] == "America/New_York"
    assert plan["changes"]["report_date"]["to"] == "2026-09-30"


def test_a_hong_kong_issuer_is_dated_in_hong_kong(monkeypatch):
    """22:00 UTC is 06:00 the next morning in Hong Kong — the opposite direction."""
    _patch(monkeypatch, _Doc(published_at=datetime(2026, 9, 30, 22, 0)), _Event(),
           stock=_Stock(market="HK"))
    plan = M.associate_with_event(1, 42694, actor="t", rationale="r")
    d = plan["announcement_dating"]
    assert d["exchange_timezone"] == "Asia/Hong_Kong"
    assert d["utc_calendar_date"] == "2026-09-30"
    assert d["announcement_date"] == "2026-10-01"


def test_committing_an_association_persists_who_why_and_what_it_replaced(monkeypatch):
    """A log line is not queryable from the row the association altered. Without this, the event
    asserts a corrected identity with no stored record of who asserted it or what it replaced."""
    doc, ev = _Doc(), _Event()
    _patch(monkeypatch, doc, ev)
    out = M.associate_with_event(1, 42694, actor="sing", rationale="official MU release",
                                 commit=True)
    assert out["committed"] is True
    a = doc.association
    assert a["actor"] == "sing"
    assert a["rationale"] == "official MU release"
    assert a["event_id"] == 42694
    assert a["before"]["report_date"] == "2026-08-31", "what the event said before the link"
    assert a["after"]["report_date"] == "2026-09-30"
    assert a["before"]["period_end"] == "2026-08-31"
    assert a["after"]["period_end"] == "2026-09-03"
    assert a["announcement_dating"]["exchange_timezone"] == "America/New_York"
    assert "not lifted" in a["notification_suppression"]


def test_a_preview_persists_nothing(monkeypatch):
    doc, ev = _Doc(), _Event()
    _patch(monkeypatch, doc, ev)
    M.associate_with_event(1, 42694, actor="sing", rationale="r")
    assert doc.association is None
    assert doc.event_id is None
    assert ev.report_date == date(2026, 8, 31)
