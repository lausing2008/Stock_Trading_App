"""Scenario valuation from explicit, recorded assumptions.

WHAT A SCENARIO IS HERE. A bear/base/bull triple is three arithmetic consequences of three
stated assumption sets. It is NOT a confidence interval, NOT a probability distribution, and the
spread between bear and bull measures how much the assumptions matter, not how uncertain the
future is. Nothing in this module assigns a likelihood to any scenario, because nothing here has
evidence that would support one.

THE CYCLICAL TRAP THIS EXISTS TO MAKE VISIBLE. A memory or commodity business earns its peak
margin and its trough margin on similar revenue, so a P/E computed on peak earnings is at its
LOWEST exactly when the risk is highest — the multiple looks cheap because the denominator is
temporarily enormous. `normalized_earnings()` therefore reports the peak-year multiple and the
through-cycle multiple side by side and refuses to pick one, because which is right is a
judgement about the cycle, not a calculation.

EVERY INPUT IS DATED. A valuation compared against an undated market capitalisation is not a
comparison of two things at one moment, and `Valuation.as_of` carries both.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field


@dataclass(frozen=True)
class Assumption:
    """One input, its value, where it came from, and what would change it."""
    name: str
    value: float | str
    units: str
    source: str
    #: What a reader should doubt. An assumption with no stated vulnerability is not an
    #: assumption; it is a number someone has stopped thinking about.
    sensitivity: str

    def as_dict(self) -> dict:
        return {"name": self.name, "value": self.value, "units": self.units,
                "source": self.source, "sensitivity": self.sensitivity}


@dataclass(frozen=True)
class Scenario:
    name: str
    earnings: float
    multiple: float
    rationale: str

    @property
    def equity_value(self) -> float:
        return self.earnings * self.multiple

    def as_dict(self) -> dict:
        return {"scenario": self.name, "earnings": self.earnings, "multiple": self.multiple,
                "equity_value": self.equity_value, "rationale": self.rationale}


def normalized_earnings(history: list) -> dict:
    """Peak, trough and mean earnings across a supplied cycle, with the multiples each implies.

    `history` is [(label, earnings)], oldest first. REFUSES on fewer than four periods: three
    points cannot describe a cycle, and a "through-cycle average" over one upswing is just the
    upswing with extra steps.
    """
    vals = [v for _, v in history if v is not None]
    if len(vals) < 4:
        return {"available": False,
                "reason": f"{len(vals)} periods supplied; a through-cycle figure needs at least "
                          f"four, and an average over one upswing is the upswing restated"}
    mean = sum(vals) / len(vals)
    return {"available": True, "periods": len(vals),
            "peak": max(vals), "trough": min(vals), "mean": mean,
            "latest": vals[-1],
            "latest_vs_mean": (vals[-1] / mean) if mean else None,
            "note": "the mean is arithmetic over the periods SUPPLIED and is only a cycle "
                    "average if those periods actually span a cycle — a judgement the caller "
                    "makes, not this function"}


@dataclass
class Valuation:
    symbol: str
    as_of: str
    market_cap: float
    market_cap_as_of: str
    scenarios: list = dc_field(default_factory=list)
    assumptions: list = dc_field(default_factory=list)
    #: Stated limits that survive the arithmetic.
    limits: list = dc_field(default_factory=list)

    def implied(self) -> list:
        """Each scenario's value against the market capitalisation, both denominators named."""
        out = []
        for s in self.scenarios:
            ev = s.equity_value
            out.append({
                **s.as_dict(),
                "discount_to_value": (ev - self.market_cap) / ev if ev > 0 else None,
                "upside_to_price": ((ev - self.market_cap) / self.market_cap
                                    if self.market_cap > 0 else None),
            })
        return out

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "as_of": self.as_of,
                "market_cap": self.market_cap, "market_cap_as_of": self.market_cap_as_of,
                "scenarios": self.implied(),
                "assumptions": [a.as_dict() for a in self.assumptions],
                "limits": list(self.limits),
                "per_share": "NOT CONVERTED. A per-share target needs a share count contemporary "
                             "with the market capitalisation; the issuer-filed count is as of "
                             "its fiscal year end, which is not the same date.",
                "what_this_is_not": "Three scenarios are three arithmetic consequences of three "
                                    "assumption sets. They are not a confidence interval, carry "
                                    "no probabilities, and the spread measures how much the "
                                    "assumptions matter — not how uncertain the future is."}


def sensitivity(base: Scenario, *, earnings_deltas=(-0.3, -0.15, 0.15, 0.3),
                multiple_deltas=(-0.3, -0.15, 0.15, 0.3)) -> dict:
    """How much of the base value is the earnings assumption, and how much the multiple.

    Shown as a grid rather than a single number because the two move together in practice: a
    cyclical's multiple compresses exactly when its earnings peak, so the corner where both fall
    is not a remote tail — it is the normal way the cycle turns.
    """
    grid = []
    for ed in earnings_deltas:
        row = []
        for md in multiple_deltas:
            row.append({"earnings_delta": ed, "multiple_delta": md,
                        "equity_value": base.earnings * (1 + ed) * base.multiple * (1 + md)})
        grid.append(row)
    return {"base_equity_value": base.equity_value, "grid": grid,
            "note": "the both-fall corner is not a remote tail for a cyclical: the multiple "
                    "compresses as the earnings peak, so those two deltas are correlated"}
