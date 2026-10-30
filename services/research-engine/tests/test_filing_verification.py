from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from src.intel_reports import filing_verification as F

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
REVIEW = {"symbol": "ADBE", "cik": 796343,
          "sources": [{"published_at": "2026-01-15"}]}


def payload():
    return {"cik": "0000796343", "tickers": ["ADBE"], "filings": {"recent": {
        "accessionNumber": ["0000796343-26-000160", "0000796343-26-000156", "0000796343-25-000001"],
        "form": ["4", "10-Q", "10-K"], "filingDate": ["2026-10-29", "2026-09-22", "2025-01-15"],
        "primaryDocument": ["xslF345X05/form4.xml", "adbe-20260828.htm", "adbe-20241130.htm"]}}}


def check(data=None):
    return F.inventory(data or payload(), cik=796343, symbol="ADBE", since="2026-01-15", now=NOW)


def test_inventory_matches_issuer_covers_window_and_preserves_dated_filing_links():
    result = check()
    assert result["checked_at"] == NOW.isoformat()
    assert len(result["filings"]) == 1
    filing = result["filings"][0]
    assert filing["form"] == "10-Q"
    assert filing["url"] == "https://www.sec.gov/Archives/edgar/data/796343/000079634326000156/adbe-20260828.htm"
    assert "Inventory only" in result["scope"]


@pytest.mark.parametrize("issue", ["cik", "ticker", "empty", "misaligned", "future", "coverage", "path", "duplicate"])
def test_bad_or_incomplete_inventory_cannot_claim_verification(issue):
    data = payload()
    recent = data["filings"]["recent"]
    if issue == "cik": data["cik"] = 1
    if issue == "ticker": data["tickers"] = ["OTHER"]
    if issue == "empty": recent["accessionNumber"] = []
    if issue == "misaligned": recent["form"].pop()
    if issue == "future": recent["filingDate"][0] = "2027-01-01"
    if issue == "coverage": recent["filingDate"][-1] = "2026-02-01"
    if issue == "path": recent["primaryDocument"][1] = "../evil.htm"
    if issue == "duplicate":
        for values in recent.values(): values.append(values[1])
    with pytest.raises(ValueError): check(data)


def test_filings_need_dated_content_dispositions_and_new_amendments_reopen_review():
    value = check()
    assert "needs content review" in F.blockers(value, REVIEW, NOW)[0]
    reviewed = {**REVIEW, "filing_dispositions": {value["filings"][0]["accession"]: {
        "status": "reviewed", "rationale": "Reviewed financials and risk changes", "reviewed_at": "2026-10-29"}}}
    assert F.blockers(value, reviewed, NOW) == []
    value["filings"].append({"accession": "0000796343-26-000161", "form": "10-Q/A", "filed": "2026-10-30", "url": "https://www.sec.gov/filing"})
    assert "10-Q/A" in F.blockers(value, reviewed, NOW)[0]
    reviewed["filing_dispositions"][value["filings"][0]["accession"]]["reviewed_at"] = "2026-09-01"
    assert len(F.blockers(value, reviewed, NOW)) == 2


@pytest.mark.parametrize("mutation", ["old", "future", "naive", "issuer", "coverage", "absent", "missing_filings"])
def test_missing_stale_and_wrong_coverage_checks_block_qualification(mutation):
    value = check()
    if mutation == "old": value["checked_at"] = (NOW - timedelta(days=2)).isoformat()
    if mutation == "future": value["checked_at"] = (NOW + timedelta(days=1)).isoformat()
    if mutation == "naive": value["checked_at"] = "2026-10-30T12:00:00"
    if mutation == "issuer": value["cik"] = 1
    if mutation == "coverage": value["since"] = "2026-08-01"
    if mutation == "absent": value = None
    if mutation == "missing_filings": value.pop("filings")
    assert F.blockers(value, REVIEW, NOW)


def test_collection_keeps_cached_observation_time_and_fails_closed_on_provider_error(monkeypatch):
    class Cache:
        value = None
        def get(self, key): return self.value
        def setex(self, key, ttl, value): self.value = value
    cache = Cache()
    calls = []
    monkeypatch.setattr(F.time, "sleep", lambda _: None)
    monkeypatch.setattr(F, "_get", lambda url, **kwargs: calls.append(url) or payload())
    first = F.collect(cache, {"ADBE": REVIEW}, now=NOW)["ADBE"]
    second = F.collect(cache, {"ADBE": REVIEW}, now=NOW + timedelta(minutes=10))["ADBE"]
    assert first == second and len(calls) == 1
    assert second["checked_at"] == NOW.isoformat()
    def fail(url, **kwargs): raise OSError("network unavailable")
    monkeypatch.setattr(F, "_get", fail)
    stale = F.collect(cache, {"ADBE": REVIEW}, now=NOW + timedelta(hours=2))["ADBE"]
    assert stale["state"] == "UNAVAILABLE"
    assert "network unavailable" not in json.dumps(stale)
    assert F.blockers(stale, REVIEW, NOW)


def test_cache_outage_does_not_discard_successful_sec_response(monkeypatch):
    class Cache:
        def get(self, key): raise OSError("cache down")
        def setex(self, *args): raise OSError("cache down")
    monkeypatch.setattr(F.time, "sleep", lambda _: None)
    monkeypatch.setattr(F, "_get", lambda url, **kwargs: payload())
    assert F.collect(Cache(), {"ADBE": REVIEW}, now=NOW)["ADBE"]["state"] == "OK"
