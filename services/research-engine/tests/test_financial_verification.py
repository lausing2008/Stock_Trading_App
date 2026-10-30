from copy import deepcopy
from datetime import timedelta

import pytest

from src.intel_reports import financial_verification as F
from test_filing_verification import NOW


def payload():
    def entries(value, unit="USD"):
        return {"units": {unit: [{"val": value, "start": "2024-11-30", "end": "2025-11-28",
            "filed": "2026-01-15", "form": "10-K", "fy": 2025, "fp": "FY", "accn": "0000796343-26-000003"}]}}
    return {"cik": 796343, "entityName": "Adobe", "facts": {"us-gaap": {
        "Revenues": entries(23769e6), "NetCashProvidedByUsedInOperatingActivities": entries(10031e6),
        "PaymentsToAcquirePropertyPlantAndEquipment": entries(179e6),
        "ShareBasedCompensation": entries(1942e6),
        "WeightedAverageNumberOfDilutedSharesOutstanding": entries(427e6, "shares")}}}


def test_facts_preserve_filing_period_units_and_distinct_diluted_denominator():
    data = payload()
    original = deepcopy(data)
    value = F.verify(data, cik=796343, now=NOW)
    assert data == original
    assert value["cik"] == 796343
    assert value["latest_period_end"] == "2025-11-28"
    assert value["latest_annual"]["stock_compensation"]["value"] == 1942e6
    assert value["latest_annual"]["diluted_shares"]["unit"] == "shares"
    assert "shares_outstanding" in value["not_found"]


def test_future_restatements_and_quarterly_comparatives_cannot_replace_annual_facts():
    data = payload()
    entries = data["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    entries.append({**entries[0], "val": 1, "filed": "2027-01-01"})
    entries.append({**entries[0], "val": 2, "start": "2025-08-30", "filed": "2026-02-01"})
    assert F.verify(data, cik=796343, now=NOW)["latest_annual"]["revenue"]["value"] == 23769e6


def test_period_mismatch_is_not_silently_relabelled_and_numeric_checks_use_capex_sign():
    value = F.verify(payload(), cik=796343, now=NOW)
    raw = {"annuals": [{"period_end": "2025-11-30", "revenue": 23769e6}]}
    assert "differs from SEC" in F.reconcile(raw, value)[0]
    raw["annuals"][0].update(period_end="2025-11-28", capital_expenditure=-179e6)
    assert F.reconcile(raw, value) == []
    raw["annuals"][0]["capital_expenditure"] = 179e6
    assert "accounting scope" in F.reconcile(raw, value)[0]


def test_wrong_issuer_and_missing_annual_anchor_are_rejected():
    with pytest.raises(ValueError): F.verify(payload(), cik=1, now=NOW)
    with pytest.raises(ValueError): F.verify({"cik": 796343, "facts": {}}, cik=796343, now=NOW)


def test_cache_preserves_evidence_time_and_provider_failure_is_explicit(monkeypatch):
    class Cache:
        value = None
        def get(self, key): return self.value
        def setex(self, key, ttl, value): self.value = value
    cache = Cache()
    calls = []
    monkeypatch.setattr(F.time, "sleep", lambda _: None)
    monkeypatch.setattr(F, "_get", lambda url, **kwargs: calls.append(url) or payload())
    first = F.collect(cache, {"ADBE": {"cik": 796343}}, now=NOW)
    assert F.collect(cache, {"ADBE": {"cik": 796343}}, now=NOW + timedelta(hours=2)) == first
    assert len(calls) == 1
    def fail(url, **kwargs): raise OSError("unavailable")
    monkeypatch.setattr(F, "_get", fail)
    assert F.collect(cache, {"ADBE": {"cik": 796343}}, now=NOW + timedelta(days=2))["ADBE"]["state"] == "UNAVAILABLE"
