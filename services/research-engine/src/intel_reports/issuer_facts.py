"""Issuer-verified financials from SEC EDGAR's XBRL company facts.

WHY THIS EXISTS. Everything the Quality & Value screen could say was bounded by three gaps in
the stored provider series, measured in docs/audits/2026-10-06-quality-value-readiness-inventory.md:
no share count anywhere, no accounting basis on any row, and no filing date — only a retrieval
time. All three are present in EDGAR's XBRL facts, attached to the filing that reported them:

  * `shares` — CommonStockSharesOutstanding and the diluted weighted average, each dated and
    carrying the form that reported it. This is what makes a per-share figure possible at all.
  * `basis` — the facts are drawn from the `us-gaap` taxonomy, so they ARE US GAAP by
    construction rather than by assumption. A provider row cannot say that about itself.
  * `filed` and `form` — the date the issuer reported it and the document it appeared in, which
    is what a point-in-time claim needs and a retrieval time can never supply.

WHAT IT STILL DOES NOT ESTABLISH. XBRL tags are chosen by the filer. Two issuers can report the
same economic quantity under different concepts (Revenues vs RevenueFromContractWithCustomer...),
and a concept absent from a filing is absent from here — which is a fact about the filing, not
about the company. So every extracted figure carries the concept it came from, and a figure we
could not find is reported as not found rather than as zero or as a substituted near-neighbour.

NO ORM IMPORT: pure, so it is testable.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field as dc_field
from datetime import date

#: SEC requires a declaring User-Agent. Theirs is a fair-access policy, not an obstacle.
USER_AGENT = "StockAI research (r_lau@learcapital.com)"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

#: The concepts to look for, IN PREFERENCE ORDER, per economic quantity.
#:
#: ORDER IS A CLAIM, not a convenience. `RevenueFromContractWithCustomerExcludingAssessedTax` is
#: the post-ASC-606 concept most filers now use; `Revenues` is the older umbrella and some filers
#: still use both for different things. Taking the first one FOUND, and recording WHICH, keeps
#: the choice visible instead of burying it in a coalesce.
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "current_debt": ["LongTermDebtCurrent", "DebtCurrent"],
    "operating_cashflow": ["NetCashProvidedByUsedInOperatingActivities",
                           "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    # THREE DIFFERENT SHARE MEASURES, kept apart because they are not interchangeable and the
    # first version of this module reported one of them as "shares" with no type.
    # Measured on MU's FY2025 10-K: 1,125.0m / 1,122.0m / 1,122.5m — three dates, three meanings.
    #   diluted_shares     a DURATION average over the year; the EPS denominator
    #   shares_outstanding an INSTANT at the balance-sheet date
    #   (cover-page count lives in the `dei` taxonomy and is fetched separately — it is an
    #    instant at a date AFTER the fiscal year end, 2025-09-26 for MU)
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "basic_shares": ["WeightedAverageNumberOfSharesOutstandingBasic",
                     "WeightedAverageNumberOfSharesOutstanding"],
    "shares_outstanding": ["CommonStockSharesOutstanding"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
}

#: Forms that report a completed fiscal YEAR. 10-K only — a 10-Q's annual-to-date figure is not
#: a fiscal year, and 20-F/40-F are foreign issuers whose basis may be IFRS, which would break
#: the "these are US GAAP by construction" guarantee above.
ANNUAL_FORMS = ("10-K", "10-K/A")

#: A fiscal year's length, in days, with tolerance for 52/53-week calendars.
#:
#: MEASURED, AND THIS IS A REAL DEFECT THE FIRST VERSION HAD. A 10-K reports the fiscal year AND
#: its quarters, so the form alone does not identify an annual figure: of MU's NetIncomeLoss
#: facts carrying form "10-K", 77 span 90 days and 3 span 97, against 45 spanning a year. Taking
#: them all meant a Q4 figure could be selected as a fiscal year — it did not corrupt MU's or
#: CRDO's latest year by luck of sort order, not by design.
MIN_ANNUAL_DAYS, MAX_ANNUAL_DAYS = 340, 400

#: The unit each quantity must be reported in. A concept can carry several (USD and USD/shares),
#: and mixing them silently produces a figure that is arithmetically fine and economically
#: meaningless.
EXPECTED_UNITS = {"diluted_shares": "shares", "basic_shares": "shares",
                  "shares_outstanding": "shares"}
DEFAULT_UNIT = "USD"


@dataclass
class Fact:
    """One reported figure, with the filing that reported it and the context that bounds it."""
    concept: str
    value: float
    unit: str
    period_start: str | None
    period_end: str
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    filed: str
    accession: str | None = None
    frame: str | None = None
    #: When the SEC ACCEPTED the submission. A filing DATE is a calendar day and cannot order
    #: anything within it — measured: CRDO's FY2026 10-K is dated 2026-06-15 but was accepted
    #: 2026-06-16T01:09:27Z, which is the following day in UTC. Any cutoff finer than a day must
    #: use this, not `filed`.
    accepted: str | None = None

    @property
    def duration_days(self) -> int | None:
        if not self.period_start:
            return None
        return (date.fromisoformat(self.period_end)
                - date.fromisoformat(self.period_start)).days

    @property
    def measure(self) -> str:
        """DURATION (a flow over a period) or INSTANT (a stock at a point).

        Not decoration: a weighted-average diluted share count is a duration average and a
        balance-sheet share count is an instant, and dividing by the wrong one is a different
        number. The first version of this module reported both as "shares".
        """
        return "instant" if self.period_start is None else "duration"

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in
             ("concept", "value", "unit", "period_start", "period_end", "fiscal_year",
              "fiscal_period", "form", "filed", "accession", "frame", "accepted")}
        d["measure"] = self.measure
        d["duration_days"] = self.duration_days
        return d


@dataclass
class IssuerFacts:
    cik: str
    entity_name: str
    fiscal_year_end: str | None = None
    #: quantity -> Fact for the newest completed fiscal year
    latest_annual: dict = dc_field(default_factory=dict)
    #: quantity -> list[Fact], newest first, one per fiscal year
    annual_series: dict = dc_field(default_factory=dict)
    #: Quantities looked for and NOT found. Reported, never silently zero.
    not_found: tuple = ()
    #: The fiscal year the `latest_annual` block describes.
    latest_fiscal_year: int | None = None
    latest_period_end: str | None = None
    latest_filed: str | None = None

    def as_dict(self) -> dict:
        return {
            "cik": self.cik, "entity_name": self.entity_name,
            "fiscal_year_end": self.fiscal_year_end,
            "latest_fiscal_year": self.latest_fiscal_year,
            "latest_period_end": self.latest_period_end,
            "latest_filed": self.latest_filed,
            "accounting_basis": "us-gaap",
            "basis_evidence": "drawn from the us-gaap XBRL taxonomy in the issuer's own 10-K, "
                              "so the basis is a property of the source rather than an "
                              "assumption about it",
            # WHAT THE TAXONOMY ALONE DOES NOT ESTABLISH, stated so it is not read as more than
            # it is. The taxonomy names the accounting CONCEPT; these four are separate checks,
            # and each is now enforced or disclosed rather than assumed:
            "context_checks": {
                "duration": f"duration facts are required to span {MIN_ANNUAL_DAYS}-"
                            f"{MAX_ANNUAL_DAYS} days, because a 10-K reports its quarters too "
                            f"and the form alone does not identify an annual figure",
                "units": "each quantity accepts ONE unit; USD and USD/shares are never mixed",
                "consolidation": "the companyfacts API returns only undimensioned facts — "
                                 "observed key set is accn/end/filed/form/fp/frame/fy/start/val "
                                 "with no segment or member axis — so these are consolidated "
                                 "figures. This is a property of the endpoint, not a filter "
                                 "applied here, and would need re-checking if the API changed",
                "amendment_vintage": "where a period is reported more than once the LATEST-FILED "
                                     "value wins, so a 10-K/A supersedes the original",
                "intraday_ordering": "`filed` is a calendar day and cannot order events within "
                                     "it; `accepted` carries the SEC acceptance timestamp and is "
                                     "what any cutoff finer than a day must use",
            },
            "latest_annual": {k: v.as_dict() for k, v in self.latest_annual.items()},
            "not_found": list(self.not_found),
        }


def _get(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return json.loads(raw)


def _is_annual_duration(start, end) -> bool:
    """A duration fact spans a fiscal year; an instant fact has no duration to check."""
    if not start:
        return True                      # instant (balance-sheet) facts are point-in-time
    try:
        n = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return False
    return MIN_ANNUAL_DAYS <= n <= MAX_ANNUAL_DAYS


def _annual_facts(units: dict, concept: str, *, expected_unit: str = DEFAULT_UNIT) -> list:
    """Every fiscal-year fact for one concept, newest period first, deduplicated.

    A FIGURE CAN BE REPORTED MANY TIMES — in its own 10-K, then as a comparative in the next
    two. Those can DIFFER after a restatement, and the later filing is the one the issuer now
    stands behind. So facts are grouped by period and the LATEST-FILED one wins, with the
    earlier value preserved as `restated_from` so a restatement is visible rather than silently
    adopted.
    """
    by_period: dict = {}
    for unit, entries in units.items():
        if unit != expected_unit:
            continue                     # never mix USD with USD/shares under one quantity
        for e in entries:
            if e.get("form") not in ANNUAL_FORMS:
                continue
            end = e.get("end")
            if not end:
                continue
            # THE FORM DOES NOT IDENTIFY AN ANNUAL FIGURE. A 10-K reports its quarters too.
            if not _is_annual_duration(e.get("start"), end):
                continue
            key = (e.get("start"), end)
            prev = by_period.get(key)
            if prev is None or (e.get("filed") or "") > (prev.get("filed") or ""):
                by_period[key] = {**e, "unit": unit,
                                  "restated_from": (prev or {}).get("val")
                                  if prev and prev.get("val") != e.get("val") else None}
    out = []
    for (start, end), e in by_period.items():
        out.append(Fact(concept=concept, value=float(e["val"]), unit=e["unit"],
                        period_start=start, period_end=end,
                        fiscal_year=e.get("fy"), fiscal_period=e.get("fp"),
                        form=e.get("form"), filed=e.get("filed"),
                        accession=e.get("accn"), frame=e.get("frame"),
                        accepted=e.get("accepted")))
    out.sort(key=lambda f: (f.period_end, f.filed or ""), reverse=True)
    return out


def extract(facts_json: dict, *, cik: str, fiscal_year_end: str | None = None) -> IssuerFacts:
    """Pull the quantities we need out of a companyfacts payload. PURE — no network."""
    gaap = (facts_json.get("facts") or {}).get("us-gaap") or {}
    out = IssuerFacts(cik=cik, entity_name=facts_json.get("entityName") or "",
                      fiscal_year_end=fiscal_year_end)
    missing = []
    for quantity, candidates in CONCEPTS.items():
        series = []
        for concept in candidates:
            node = gaap.get(concept)
            if not node:
                continue
            series = _annual_facts(node.get("units") or {}, concept,
                                   expected_unit=EXPECTED_UNITS.get(quantity, DEFAULT_UNIT))
            if series:
                break                      # FIRST FOUND, and the Fact records which
        if series:
            out.annual_series[quantity] = series
        else:
            missing.append(quantity)
    out.not_found = tuple(missing)

    # The newest fiscal year is the one the REVENUE series reaches, because a balance-sheet
    # item can carry an instant from a later 10-Q comparative and would otherwise pull the
    # "latest year" forward past any year actually reported.
    rev = out.annual_series.get("revenue") or []
    duration = [f for f in rev if f.period_start]
    if duration:
        anchor = duration[0]
        out.latest_fiscal_year = anchor.fiscal_year
        out.latest_period_end = anchor.period_end
        out.latest_filed = anchor.filed
        for quantity, series in out.annual_series.items():
            match = next((f for f in series if f.period_end == anchor.period_end), None)
            if match:
                out.latest_annual[quantity] = match
    return out


def fetch(cik: str) -> IssuerFacts:
    """Fetch and extract one issuer's facts. The only function here that touches the network."""
    n = int(str(cik).lstrip("0") or "0")
    fye = None
    try:
        fye = (_get(SUBMISSIONS_URL.format(cik=n)) or {}).get("fiscalYearEnd")
    except Exception:
        pass                              # the facts are usable without it; it is a label
    return extract(_get(FACTS_URL.format(cik=n)), cik=str(cik), fiscal_year_end=fye)
