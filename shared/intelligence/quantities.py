"""A number with its identity attached, and the rules for comparing two of them.

THE DEFECT THIS EXISTS FOR. N1's first validator pooled every number in a report into one
untyped set and matched a draft's digits against it. That accepted "Revenue was $33.42 billion"
(the EPS figure) and "Revenue was $54.23 million" (off by a thousand), because a bare quantity
carries no metric, no units and no period. A number without those is not evidence of anything;
it is a digit sequence that happens to occur in the report.

So nothing downstream is allowed to see a bare number. Every figure is extracted once, here,
with the identity the issuer gave it, and anything that cannot be identified is marked as such
rather than silently treated as comparable.
"""
from __future__ import annotations

from collections.abc import Mapping

import re
from dataclasses import dataclass

#: Canonical metric names. The map exists so "eps_adjusted" in a document and "eps_actual" in a
#: report field are known to be the same MEASURE — and so that `revenue` and
#: `guidance_q1_revenue`, which share a word, are known not to be.
_FIELD_METRIC = {
    "revenue_actual": "revenue",
    "revenue_expectation": "revenue",
    "eps_actual": "eps",
    "eps_expectation": "eps",
}

#: Figures inside `official_figures` / `guidance_current`, keyed by their document fact name.
_FACT_METRIC = {
    "revenue": "revenue",
    "eps_adjusted": "eps",
    "gross_margin_gaap": "gross_margin",
    "gross_margin_non_gaap": "gross_margin",
    "operating_cash_flow": "operating_cash_flow",
    "adjusted_free_cash_flow": "free_cash_flow",
    "cash_and_investments": "cash_and_investments",
}

#: A guidance figure is a FORECAST of a metric, not an observation of it. Kept distinct so a
#: forecast can never satisfy a claim about reported results.
_GUIDANCE_PREFIX = "guidance_"

#: Unit families that are the same measure in different scales. Comparing across families is a
#: category error, not a conversion.
_UNIT_FAMILY = {
    "usd": "currency", "$": "currency", "usd_millions": "currency",
    "usd_billions": "currency",
    "usd/share": "per_share",
    "pct": "ratio", "%": "ratio",
}

_SCALE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
                "k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9, "t": 1e12}


@dataclass(frozen=True)
class Quantity:
    """One figure with everything needed to know what it is.

    `identified` is the gate: a quantity missing units, basis or period cannot take part in a
    comparison, because the comparison would be assuming the very things that are absent.
    """
    qid: str                      # stable id, e.g. "revenue_actual" or "official_figures.revenue"
    metric: str                   # canonical measure
    value: float
    units: str | None
    basis: str | None
    period: str | None
    kind: str                     # "actual" | "expectation" | "guidance" | "observation"
    source_field: str
    evidence_ids: tuple[str, ...] = ()

    @property
    def identified(self) -> bool:
        return bool(self.units) and bool(self.basis) and bool(self.period)

    @property
    def unit_family(self) -> str | None:
        return _UNIT_FAMILY.get((self.units or "").strip().lower())

    def missing_identity(self) -> tuple[str, ...]:
        return tuple(n for n, v in (("units", self.units), ("basis", self.basis),
                                    ("period", self.period)) if not v)


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def extract(fields: dict) -> dict[str, Quantity]:
    """Every identified figure in a report, keyed by a stable id.

    Only figures this function recognises become quantities. An unrecognised number stays out
    rather than entering as an anonymous value — the whole point is that nothing anonymous
    reaches a claim.
    """
    out: dict[str, Quantity] = {}

    def ev(f):
        return tuple(f.get("evidence_ids") or ())

    for key, metric in _FIELD_METRIC.items():
        f = fields.get(key)
        if not f or f.get("state") != "OK":
            continue
        v = f.get("value")
        body = v if isinstance(v, Mapping) else {"value": v}
        n = _num(body.get("value"))
        if n is None:
            continue
        out[key] = Quantity(
            qid=key, metric=metric, value=n, units=body.get("units"),
            basis=body.get("basis"), period=body.get("period"),
            kind="expectation" if key.endswith("_expectation") else "actual",
            source_field=key, evidence_ids=ev(f))

    for container, kind in (("official_figures", "actual"), ("guidance_current", "guidance")):
        f = fields.get(container)
        if not f or f.get("state") != "OK" or not isinstance(f.get("value"), Mapping):
            continue
        for name, body in f["value"].items():
            if name.startswith("_") or not isinstance(body, Mapping):
                continue
            n = _num(body.get("value"))
            if n is None:
                continue
            if name.startswith(_GUIDANCE_PREFIX):
                metric = _FACT_METRIC.get(name[len(_GUIDANCE_PREFIX):].split("_", 1)[-1], name)
                q_kind = "guidance"
            else:
                metric = _FACT_METRIC.get(name, name)
                q_kind = kind
            qid = f"{container}.{name}"
            out[qid] = Quantity(
                qid=qid, metric=metric, value=n, units=body.get("units"),
                basis=body.get("basis"), period=body.get("period"),
                kind=q_kind, source_field=container, evidence_ids=ev(f))
    return out


@dataclass(frozen=True)
class Comparability:
    """Whether two specific quantities may be compared, and what fails if not.

    PAIRWISE, NEVER REPORT-WIDE. The earlier rule asked whether the report recorded *an*
    accounting basis anywhere, so a GAAP estimate authorised a comparison against a non-GAAP
    actual — and an eligible EPS comparison silently authorised a revenue one.
    """
    left: str
    right: str
    allowed: bool
    reason: str


def comparable(a: Quantity, b: Quantity) -> Comparability:
    def no(reason):
        return Comparability(a.qid, b.qid, False, reason)

    if a.metric != b.metric:
        return no(f"different measures: {a.metric} and {b.metric}")
    miss = sorted(set(a.missing_identity()) | set(b.missing_identity()))
    if miss:
        return no(f"identity incomplete — missing {', '.join(miss)} on one or both sides, so a "
                  f"comparison would assume them")
    if (a.basis or "").strip().lower() != (b.basis or "").strip().lower():
        return no(f"different accounting bases: {a.basis!r} and {b.basis!r}. A difference "
                  f"across that boundary is a factual difference, NOT a beat or a miss")
    if a.period != b.period:
        return no(f"different periods: {a.period!r} and {b.period!r}")
    if a.unit_family is None or b.unit_family is None:
        return no(f"unrecognised units: {a.units!r} / {b.units!r}")
    if a.unit_family != b.unit_family:
        return no(f"incompatible units: {a.units!r} and {b.units!r}")
    return Comparability(a.qid, b.qid, True,
                         f"same measure, basis, period and unit family ({a.unit_family})")


#: Rendering. THE NARRATOR NEVER WRITES A NUMBER — it names a quantity, and this produces the
#: text. That removes the entire class of defect where a model transcribes a figure into the
#: wrong magnitude or attaches it to the wrong measure.
def render(q: Quantity, *, with_identity: bool = True) -> str:
    v, u = q.value, (q.units or "").strip().lower()
    if u in ("usd", "$"):
        body = _scaled_currency(v)
    elif u == "usd/share":
        body = f"${v:,.2f} per share"
    elif u in ("pct", "%"):
        body = f"{v:.2f}%"
    else:
        body = f"{v:,.4f}".rstrip("0").rstrip(".") + (f" {q.units}" if q.units else "")
    if not with_identity:
        return body
    bits = [b for b in (q.basis, q.period) if b]
    return f"{body} ({', '.join(bits)})" if bits else body


def _scaled_currency(v: float) -> str:
    a = abs(v)
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= scale:
            return f"${v / scale:,.2f}{suffix}"
    return f"${v:,.2f}"


_NUM_RE = re.compile(r"-?\$?\s?\d[\d,]*\.?\d*\s*(?:%|percent)?", re.I)


def literal_numbers(text: str) -> list[str]:
    """Numbers written out in free text, with any magnitude word that follows them.

    Used to REFUSE free-written figures, not to check them: a narrator that writes its own
    numbers is doing the one job it is not allowed to do.
    """
    out = []
    for m in _NUM_RE.finditer(text):
        tail = text[m.end():m.end() + 12].strip().lower()
        word = next((w for w in _SCALE_WORDS if tail.startswith(w)), "")
        out.append((m.group(0).strip() + (" " + word if word else "")).strip())
    return out
