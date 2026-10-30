"""A dated SEC filing inventory, distinct from a human review of its contents."""
from datetime import date, datetime, timedelta
import json
import re
import time

from .issuer_facts import _get, SUBMISSIONS_URL
from urllib.parse import urlparse

IGNORED_FORMS = {"3", "3/A", "4", "4/A", "5", "5/A", "144", "144/A"}
REQUIRED_COLUMNS = ("accessionNumber", "form", "filingDate", "primaryDocument")


def inventory(payload, *, cik, symbol, since, now):
    """Validate column alignment and issuer identity before claiming window coverage.

    Filing-day granularity only: same-day submissions are retained conservatively. This
    inventory cannot establish historical intraday availability or the filing's materiality.
    """
    n = int(cik)
    cutoff = now.date()
    start = date.fromisoformat(since)
    if start > cutoff:
        raise ValueError("Filing inventory start is future-dated")
    if int(payload.get("cik") or 0) != n or symbol not in (payload.get("tickers") or []):
        raise ValueError("SEC issuer identity does not match the reviewed company")
    recent = (payload.get("filings") or {}).get("recent") or {}
    columns = [recent.get(key) for key in REQUIRED_COLUMNS]
    if any(not isinstance(values, list) or not values for values in columns):
        raise ValueError("SEC filing inventory is empty or missing required columns")
    if len({len(values) for values in columns}) != 1:
        raise ValueError("SEC filing columns are misaligned")
    rows = []
    dates = []
    for accession, form, filed, document in zip(*columns):
        stamp = date.fromisoformat(filed)
        dates.append(stamp)
        if stamp > cutoff:
            raise ValueError("SEC inventory includes a future filing date")
        if (not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)
                or not isinstance(form, str) or not form
                or not isinstance(document, str)
                or not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+", document)
                or document in {".", ".."}):
            raise ValueError("SEC filing identifier or document is invalid")
        if stamp < start or form in IGNORED_FORMS:
            continue
        rows.append({"accession": accession, "form": form, "filed": filed,
                     "url": f"https://www.sec.gov/Archives/edgar/data/{n}/{accession.replace('-', '')}/{document}"})
    # Never infer that an un-fetched older history page contains no relevant filings.
    if min(dates) > start:
        raise ValueError("SEC recent history does not cover the requested review window")
    if len({r["accession"] for r in rows}) != len(rows):
        raise ValueError("SEC inventory contains duplicate filing identities")
    rows.sort(key=lambda row: (row["filed"], row["accession"]), reverse=True)
    return {"state": "OK", "cik": n, "symbol": symbol, "since": since,
            "checked_at": now.isoformat(), "checked_through": cutoff.isoformat(),
            "source_url": SUBMISSIONS_URL.format(cik=n), "filings": rows,
            "scope": "SEC recent submissions in the displayed date window, excluding Forms 3/4/5/144 and their amendments. Inventory only; content and materiality require review."}


def collect(cache, reviews, *, now):
    """One bounded request per reviewed issuer; failures remain explicit in the report."""
    results = {}
    deadline = time.monotonic() + 45
    for symbol, review in reviews.items():
        cik = review.get("cik")
        dates = [s["published_at"] for s in review.get("sources", []) if s.get("published_at")]
        since = min(dates) if dates else (now.date() - timedelta(days=365)).isoformat()
        key = f"stockai:valuation:sec-inventory:v1:{cik}:{symbol}:{since}"
        try:
            try:
                cached = cache.get(key)
            except Exception:
                cached = None
            if cached:
                value = json.loads(cached)
                # Keep the original observation time; a cached read is not a new SEC check.
                observed = datetime.fromisoformat(value["checked_at"])
                if (observed.tzinfo is not None and 0 <= (now - observed).total_seconds() < 3600
                        and value.get("cik") == int(cik) and value.get("symbol") == symbol
                        and value.get("since") == since and value.get("state") == "OK"):
                    results[symbol] = value
                    continue
            time.sleep(.15)  # Serial requests, below SEC's ten-request/second ceiling.
            if time.monotonic() >= deadline:
                raise TimeoutError("SEC enrichment budget exhausted")
            value = inventory(_get(SUBMISSIONS_URL.format(cik=int(cik)), timeout=5),
                              cik=cik, symbol=symbol, since=since, now=now)
            try:
                cache.setex(key, 3600, json.dumps(value))
            except Exception:
                pass  # Cache availability cannot discard successfully retrieved evidence.
            results[symbol] = value
        except Exception:
            results[symbol] = {"state": "UNAVAILABLE", "symbol": symbol, "cik": cik,
                               "checked_at": now.isoformat(), "since": since,
                               "reason": "SEC filing inventory could not be verified; issuer qualification remains blocked"}
    return results


def blockers(check, review, now):
    """A filing list is not content approval; each relevant accession needs a disposition."""
    if not check or check.get("state") != "OK":
        return ["A current SEC filing inventory is required"]
    if not isinstance(check.get("filings"), list) or not check.get("since"):
        return ["SEC filing inventory coverage is incomplete"]
    try:
        observed = datetime.fromisoformat(check["checked_at"])
        age = (now - observed).total_seconds()
        if (observed.tzinfo is None or not 0 <= age <= 86400
                or check.get("cik") != review.get("cik") or check.get("symbol") != review.get("symbol")):
            return ["SEC filing inventory is stale, future-dated or issuer-mismatched"]
        source_dates = [s["published_at"] for s in review.get("sources", []) if s.get("published_at")]
        if not source_dates or date.fromisoformat(check["since"]) > date.fromisoformat(min(source_dates)):
            return ["SEC filing inventory does not cover the review source window"]
    except (KeyError, TypeError, ValueError):
        return ["SEC filing inventory timestamp is invalid"]
    dispositions = review.get("filing_dispositions") or {}
    by_accession = {f["accession"]: f for f in check.get("filings", [])}
    source_gaps = []
    for source in review.get("sources", []):
        match = re.search(r"/Archives/edgar/data/\d+/(\d{18})/", urlparse(source.get("url", "")).path)
        if match:
            compact = match[1]
            accession = f"{compact[:10]}-{compact[10:12]}-{compact[12:]}"
            filing = by_accession.get(accession)
            if not filing or filing["filed"] != source.get("published_at"):
                source_gaps.append("Review source accession or publication date does not match the SEC inventory")
    pending = []
    for filing in check.get("filings", []):
        decision = dispositions.get(filing["accession"]) or {}
        if (decision.get("status") not in {"reviewed", "not_material"}
                or not decision.get("rationale") or not decision.get("reviewed_at")):
            pending.append(filing)
            continue
        try:
            reviewed = date.fromisoformat(decision["reviewed_at"])
            if not date.fromisoformat(filing["filed"]) <= reviewed <= now.date():
                pending.append(filing)
        except (ValueError, TypeError):
            pending.append(filing)
    return source_gaps + [f"SEC filing needs content review: {f['form']} {f['filed']} ({f['accession']})" for f in pending]
