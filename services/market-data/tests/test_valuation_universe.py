from datetime import datetime, timedelta, timezone
import json

import pytest

from src.services import valuation_universe as U

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def directory(kind="nasdaq"):
    if kind == "nasdaq":
        return "\n".join(["Symbol|Security Name|Market Category|Test Issue|ETF|NextShares",
            "NEW|New Corporation - Common Stock|Q|N|N|N",
            "ETF|Example ETF Common Shares|Q|N|Y|N",
            "TEST|Test Common Stock|Q|Y|N|N",
            "PREF|Example Preferred Common Stock|Q|N|N|N",
            "UNIT|Example Common Stock Units|Q|N|N|N", "File Creation Time: 1029202621:30|||||"])
    return "\n".join(["ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol",
        "BRK.B|Example Class B Common Stock|N|BRK.B|N|100|N|BRK.B",
        "ADR|Example American Depositary Shares|N|ADR|N|100|N|ADR",
        "DEBT|Example Notes|N|DEBT|N|100|N|DEBT", "File Creation Time: 1029202621:30|||||||"])


class Cache:
    def __init__(self): self.items = {}; self.cursor = 0
    def get(self, key): return self.items.get(key)
    def setex(self, key, ttl, value): self.items[key] = value
    def incrby(self, key, amount): self.cursor += amount; return self.cursor


def test_directory_excludes_test_etf_preferred_debt_and_units_and_normalizes_classes():
    value = U.parse(directory(), kind="nasdaq", now=NOW)
    assert [r["symbol"] for r in value["rows"]] == ["NEW"]
    assert value["excluded"] == 4
    other = U.parse(directory("other"), kind="other", now=NOW)
    assert [r["symbol"] for r in other["rows"]] == ["BRK-B", "ADR"]
    assert other["rows"][0]["directory_symbol"] == "BRK.B"


@pytest.mark.parametrize("issue", ["headers", "missing_date", "future", "stale"])
def test_directory_date_and_contract_failures_are_explicit(issue):
    text = directory()
    if issue == "headers": text = text.replace("Test Issue", "Unknown")
    if issue == "missing_date": text = text.replace("File Creation Time:", "unknown")
    if issue == "future": text = text.replace("10292026", "11012026")
    if issue == "stale": text = text.replace("10292026", "10012026")
    with pytest.raises(ValueError): U.parse(text, kind="nasdaq", now=NOW)


def test_catalog_deduplicates_sources_retains_dates_and_discloses_stale_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(U, "_fetch", lambda url: calls.append(url) or directory("nasdaq" if "nasdaqlisted" in url else "other"))
    cache = Cache()
    first = U.catalog(cache, now=NOW)
    assert first["eligible_count"] == 3 and first["excluded_count"] == 5
    assert U.catalog(cache, now=NOW + timedelta(hours=2)) == first
    assert len(calls) == 2
    def fail(url): raise OSError("directory unavailable")
    monkeypatch.setattr(U, "_fetch", fail)
    stale = U.catalog(cache, now=NOW + timedelta(days=2))
    assert stale["state"] == "STALE" and stale["retrieved_at"] == first["retrieved_at"]
    assert U.catalog(cache, now=NOW + timedelta(days=8))["state"] == "UNAVAILABLE"


def test_rotation_covers_catalog_without_changing_watchlists_and_wraps():
    cache = Cache()
    value = {"rows": [{"symbol": s} for s in ("ONE", "TWO", "THREE", "FOUR", "FIVE")]}
    first, start = U.rotating_symbols(cache, value, limit=3)
    second, next_start = U.rotating_symbols(cache, value, limit=3)
    assert first == ["ONE", "TWO", "THREE"] and start == 0
    assert second == ["FOUR", "FIVE", "ONE"] and next_start == 3
    assert set(first + second) == {r["symbol"] for r in value["rows"]}


def test_failed_catalog_load_preserves_screen_discovery_and_no_false_rotation():
    assert U.rotating_symbols(Cache(), {"rows": []}) == ([], None)
    class Broken:
        def incrby(self, *args): raise OSError("cache unavailable")
    rows = {"rows": [{"symbol": "NEW"}]}
    assert U.rotating_symbols(Broken(), rows) == (["NEW"], None)
