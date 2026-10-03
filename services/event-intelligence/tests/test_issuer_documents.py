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
MU_FACTS = {
    "revenue": {"value": 54.23e9, "units": "USD", "basis": "GAAP", "period": "fiscal Q4 2026"},
    "eps_adjusted": {"value": 33.42, "units": "USD/share", "basis": "non-GAAP adjusted",
                     "period": "fiscal Q4 2026"},
    "gross_margin_gaap": {"value": 86.8, "units": "pct", "basis": "GAAP"},
    "gross_margin_non_gaap": {"value": 87.0, "units": "pct", "basis": "non-GAAP"},
    "operating_cash_flow": {"value": 43.97e9, "units": "USD", "basis": "GAAP"},
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
    assert out["offending_keys"] == ["revenue"]
    assert out["committed"] is False


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
    assert out["content_hash"].startswith("sha256:")


def test_the_content_hash_changes_when_a_figure_changes():
    """Identity of the bytes, so an issuer correction is detectable rather than silent."""
    a = M.content_hash(MU_FACTS)
    b = M.content_hash(MU_FACTS | {"revenue": {"value": 54.25e9, "units": "USD",
                                               "basis": "GAAP", "period": "fiscal Q4 2026"}})
    assert a != b
    assert M.content_hash(MU_FACTS) == a, "the same bytes hash the same"


class _Doc:
    def __init__(self, **kw):
        self.id = kw.get("id", 1); self.stock_id = kw.get("stock_id", 7)
        self.fiscal_period_end = kw.get("fiscal_period_end", date(2026, 9, 3))
        self.published_at = kw.get("published_at", datetime(2026, 9, 30, 20, 5))
        self.event_id = kw.get("event_id")


class _Event:
    def __init__(self, **kw):
        self.id = kw.get("id", 42694); self.stock_id = kw.get("stock_id", 7)
        self.report_date = kw.get("report_date", date(2026, 8, 31))
        self.period_end = kw.get("period_end", date(2026, 8, 31))
        self.report_date_source = kw.get("report_date_source", "substituted_period_end")
        self.fetched_at = None


def _patch(monkeypatch, doc, ev):
    class _S:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, model, pk): return doc if model.__name__ == "D" else ev
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
