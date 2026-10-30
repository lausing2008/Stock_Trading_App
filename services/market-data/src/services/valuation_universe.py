"""Free exchange-directory discovery with bounded rotating coverage, independent of watchlists."""
import csv
import hashlib
import io
import json
import re
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

SOURCES = {
    "nasdaq": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "other": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}
COMMON = re.compile(r"common stock|common shares|ordinary shares?|capital stock|american depositary|american depository", re.I)
OTHER_SECURITY = re.compile(r"preferred|preference|warrant|\bunits?\b|\brights?\b|\bnotes?\b|debenture|\bbonds?\b|closed.end|exchange.traded", re.I)


def parse(text, *, kind, now):
    rows = list(csv.DictReader(io.StringIO(text), delimiter="|"))
    if not rows:
        raise ValueError("Empty listing directory")
    symbol_key = "Symbol" if kind == "nasdaq" else "ACT Symbol"
    required = {symbol_key, "Security Name", "Test Issue", "ETF"}
    if not required <= rows[0].keys():
        raise ValueError("Listing directory headers changed")
    footer = re.search(r"File Creation Time:\s*(\d{8})", text)
    if not footer:
        raise ValueError("Listing directory date missing")
    source_day = datetime.strptime(footer[1], "%m%d%Y").date()
    age = (now.astimezone(ZoneInfo("America/New_York")).date() - source_day).days
    if not 0 <= age <= 7:
        raise ValueError("Listing directory is stale or future-dated")
    eligible, excluded = {}, 0
    for row in rows:
        original = row.get(symbol_key) or ""
        if original.startswith("File Creation Time:"):
            continue
        name = row.get("Security Name") or ""
        symbol = original.replace(".", "-").replace("/", "-")
        exchange = "NASDAQ" if kind == "nasdaq" else row.get("Exchange")
        if (row.get("Test Issue") != "N" or row.get("ETF") != "N"
                or row.get("NextShares") == "Y" or not COMMON.search(name) or OTHER_SECURITY.search(name)
                or not re.fullmatch(r"[A-Z][A-Z0-9-]{0,14}", symbol)
                or (kind == "nasdaq" and row.get("Market Category") not in {"Q", "G", "S"})
                or (kind == "other" and exchange not in {"N", "A", "P", "Z", "V"})):
            excluded += 1
            continue
        if symbol in eligible and eligible[symbol]["directory_symbol"] != original:
            raise ValueError("Provider symbol normalization collision")
        eligible[symbol] = {"symbol": symbol, "directory_symbol": original, "name": name, "exchange": exchange}
    if not eligible:
        raise ValueError("No eligible common-equity listings in directory")
    return {"rows": list(eligible.values()), "source_date": source_day.isoformat(), "excluded": excluded}


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "StockAI valuation research"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return response.read(4_000_000).decode("utf-8-sig")


def catalog(cache, *, now):
    key = "stockai:valuation:listing-catalog:v1"
    old = None
    try:
        old = json.loads(cache.get(key) or "null")
        if old and old.get("state") == "OK":
            stamp = datetime.fromisoformat(old["retrieved_at"])
            if stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() < 86400:
                return old
    except Exception:
        pass
    try:
        parsed = {kind: parse(_fetch(url), kind=kind, now=now) for kind, url in SOURCES.items()}
        listings = {}
        for value in parsed.values():
            for row in value["rows"]:
                listings.setdefault(row["symbol"], row)
        rows = sorted(listings.values(), key=lambda row: row["symbol"])
        result = {"state": "OK", "retrieved_at": now.isoformat(), "rows": rows,
                  "eligible_count": len(rows), "excluded_count": sum(v["excluded"] for v in parsed.values()),
                  "source_dates": {kind: value["source_date"] for kind, value in parsed.items()},
                  "sources": list(SOURCES.values()),
                  "fingerprint": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                  "scope": "Directory-derived common shares and ADR candidates; excludes ETFs, test issues and identifiable preferred/debt/warrant/unit listings. This is a discovery catalog, not verified fundamentals coverage."}
        try:
            cache.setex(key, 7 * 86400, json.dumps(result))
        except Exception:
            pass
        return result
    except Exception:
        if old:
            try:
                stamp = datetime.fromisoformat(old["retrieved_at"])
                if stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() < 7 * 86400:
                    return {**old, "state": "STALE", "reason": "Directory refresh unavailable; dated prior catalog retained"}
            except (KeyError, TypeError, ValueError):
                pass
        return {"state": "UNAVAILABLE", "rows": [], "eligible_count": 0,
                "reason": "Listing directories unavailable; bounded Yahoo/UW discovery retained"}


def rotating_symbols(cache, directory, *, limit=40):
    rows = directory.get("rows") or []
    if not rows:
        return [], None
    try:
        end = cache.incrby("stockai:valuation:catalog-cursor:v1", limit)
        if not isinstance(end, int) or isinstance(end, bool):
            raise ValueError("Rotation cursor unavailable")
        start = (end - limit) % len(rows)
    except Exception:
        # Deterministic fallback is explicit; never claim persisted rotation progress.
        start = 0
        return [row["symbol"] for row in rows[:limit]], None
    return [rows[(start + i) % len(rows)]["symbol"] for i in range(min(limit, len(rows)))], start
