"""T241-POSITION-SCALING Phase 4: thesis persistence gate.

A rules-based circuit breaker, cheap to run, that sits alongside the position-scaling gate
(Phase 3) rather than replacing it. At entry, the system already stores a structured
snapshot of what justified the trade (PaperTrade.entry_reasons, confidence_at_entry,
market_regime_at_entry — all populated on every trade today, per the T241 Phase 0 gap
analysis). On every re-evaluation, this gate recomputes the same snapshot fields and diffs
them against the original. If enough of the original conditions have flipped, adds are
blocked and the position is flagged for possible exit — regardless of what the
position-scaling gate's probability says. This catches thesis-invalidation events the
probabilistic model wasn't trained to recognize (e.g. the first time a name gets a
regime flip or a sector-relative reversal), matching this component's design-doc framing
as "less about model accuracy, more about feature completeness — a missing invalidation
condition is worse than a noisy model."

Adapted from the reference thesis_persistence_gate.py in
Improvements/Position_Scaling/AI_Investment_Position_Scaling_Architecture.pdf Appendix B,
with these adaptations to this codebase's real field names/scales (per the Phase 0 gap
analysis, same discipline as position_scaling_gate.py):
  - regime_at_entry / current_regime use this codebase's existing rule-based state label
    (get_last_regime()/get_last_hk_regime() return "bull"/"neutral"/"choppy"/"risk_off"/
    "bear"/"unknown", not the reference's TRENDING_UP/TRENDING_DOWN/RANGE_BOUND/
    HIGH_VOLATILITY labels) — RegimeLabel below is a thin re-mapping, not a new taxonomy.
  - signal_probability_at_entry / current_signal_probability use AIConfidence.confidence's
    0-100 scale (matching position_scaling_gate.py's own resolved scale choice), NOT the
    reference's 0-1 probability — the decay threshold is rescaled accordingly (15 points on
    a 0-100 scale, not 0.15 on a 0-1 scale).
  - key_support_level reads reasons["sr_nearest_support"] (this codebase's existing S/R
    field, already populated on every signal per signals.py), not a hypothetical new field.
  - sector_relative_strength uses reasons["rs_score"] (this codebase's existing 0-100
    relative-strength-vs-sector-ETF score, from _fetch_relative_strength() in
    signal-engine/src/generators/signals.py), rescaled to match: the reference's
    sector_relative_strength is a raw return-differential (e.g. -0.05 = 5 points of
    underperformance), so the -0.05 break threshold becomes a -5-point rs_score threshold
    (rs_score already lives on a 0-100 scale where 50 is "in line with sector").
  - No new DB schema: ThesisSnapshot is built from data already captured in
    PaperTrade.entry_reasons/confidence_at_entry/market_regime_at_entry — this phase does
    NOT require a migration, unlike the design doc's Phase 4 estimate (see the Phase 0 gap
    analysis note: "Phase 4 should be scoped smaller than the doc estimates, since the
    schema work the doc assumes is needed is already done").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RegimeLabel(str, Enum):
    """Mirrors this codebase's existing rule-based regime state labels (get_last_regime()/
    get_last_hk_regime()), not the reference doc's TRENDING_UP/TRENDING_DOWN/RANGE_BOUND/
    HIGH_VOLATILITY taxonomy — using a new taxonomy here would require translating between
    two label sets on every read, adding a failure point for no benefit.
    """
    BULL = "bull"
    NEUTRAL = "neutral"
    CHOPPY = "choppy"
    RISK_OFF = "risk_off"
    BEAR = "bear"
    UNKNOWN = "unknown"


# T241-P4-THRESHOLD: signal confidence is 0-100 scale in this codebase (not 0-1 like the
# reference doc) — a drop of 15 points (e.g. 70 -> 55) is treated as meaningful decay.
_SIGNAL_DECAY_THRESHOLD = 15.0

# rs_score is 0-100, 50 = in line with sector. A 5-point drop mirrors the reference's -0.05
# return-differential break threshold, rescaled to this codebase's existing rs_score scale.
_RS_SCORE_REVERSAL_THRESHOLD = 5.0


@dataclass
class ThesisSnapshot:
    """Captured once, at entry (or at the most recent add). Keep this small and specific —
    every field here is something you're willing to say "if this changes, I was wrong."
    """
    symbol: str
    regime_at_entry: RegimeLabel
    signal_confidence_at_entry: float       # 0-100 scale
    signal_direction_at_entry: str          # "long" or "short"
    entry_price: float
    key_support_level: float | None = None
    rs_score_at_entry: float | None = None  # 0-100, this codebase's existing relative-strength score
    thesis_tags: list[str] = field(default_factory=list)  # free-form, e.g. "earnings_beat"


@dataclass
class ThesisCheckResult:
    conditions_checked: int
    conditions_broken: int
    broken_reasons: list[str]
    thesis_intact: bool
    recommendation: str  # "allow_add", "hold_only", "consider_exit"


class ThesisPersistenceGate:
    """max_broken_conditions: how many of the original conditions are allowed to flip
    before the gate blocks further adds. Keep this low (1-2) — this component's value comes
    from being strict, not from being smart.
    """

    def __init__(self, max_broken_conditions: int = 1):
        self.max_broken_conditions = max_broken_conditions

    def check(
        self,
        snapshot: ThesisSnapshot,
        current_regime: RegimeLabel,
        current_signal_confidence: float,
        current_rs_score: float | None,
        current_price: float,
    ) -> ThesisCheckResult:
        broken: list[str] = []

        # 1. Regime flip — the most common and most severe break.
        if current_regime != snapshot.regime_at_entry:
            broken.append(
                f"regime changed from {snapshot.regime_at_entry.value} to {current_regime.value}"
            )

        # 2. Signal decay — the model no longer believes in the direction as strongly.
        confidence_decay = snapshot.signal_confidence_at_entry - current_signal_confidence
        if confidence_decay > _SIGNAL_DECAY_THRESHOLD:
            broken.append(f"signal confidence decayed by {confidence_decay:.1f} points")

        # 3. Structural break — price took out the level that justified entry.
        if snapshot.key_support_level is not None:
            if snapshot.signal_direction_at_entry == "long" and current_price < snapshot.key_support_level:
                broken.append("price broke below key support level used at entry")

        # 4. Relative strength reversal — stock-specific edge disappeared. This is what
        # distinguishes "the whole sector dipped" (thesis fine) from "this stock
        # specifically is underperforming now" (thesis broken).
        if snapshot.rs_score_at_entry is not None and current_rs_score is not None:
            rs_delta = current_rs_score - snapshot.rs_score_at_entry
            if snapshot.signal_direction_at_entry == "long" and rs_delta < -_RS_SCORE_REVERSAL_THRESHOLD:
                broken.append("stock is now underperforming its sector, was outperforming at entry")

        conditions_checked = 4
        conditions_broken = len(broken)
        thesis_intact = conditions_broken <= self.max_broken_conditions

        if thesis_intact and conditions_broken == 0:
            recommendation = "allow_add"
        elif thesis_intact:
            recommendation = "hold_only"
        else:
            recommendation = "consider_exit"

        return ThesisCheckResult(
            conditions_checked=conditions_checked,
            conditions_broken=conditions_broken,
            broken_reasons=broken,
            thesis_intact=thesis_intact,
            recommendation=recommendation,
        )


def snapshot_from_paper_trade(
    symbol: str,
    market_regime_at_entry: str | None,
    confidence_at_entry: float | None,
    entry_price: float,
    entry_reasons: dict | None,
    signal_direction_at_entry: str = "long",
) -> ThesisSnapshot:
    """Build a ThesisSnapshot from an existing PaperTrade row's already-captured fields —
    no new schema, no new capture logic. market_regime_at_entry defaults to UNKNOWN if the
    stored string doesn't match a known RegimeLabel (e.g. older rows predating a regime
    label rename), rather than raising — a snapshot with an unknown regime still lets the
    other 3 conditions run.
    """
    entry_reasons = entry_reasons or {}
    try:
        regime = RegimeLabel(market_regime_at_entry) if market_regime_at_entry else RegimeLabel.UNKNOWN
    except ValueError:
        regime = RegimeLabel.UNKNOWN

    # T241-P4-SUPPORT-CONSISTENCY: sr_nearest_support is computed at signal-compute time as
    # strictly below THAT bar's price (see signals.py's nearest_sup = max(s for s in supports
    # if s < current)) — but PaperTrade.entry_price is the actual fill price, from a
    # possibly-later bar/tick. These can disagree slightly (confirmed on real production
    # data, e.g. CGNX: sr_nearest_support=63.73 vs entry_price=62.75, support ABOVE the
    # fill). Using an inconsistent support level would make the structural-break check
    # (current_price < key_support_level) misfire on the very first re-check even with zero
    # real price movement, since the "support" was never actually below the recorded entry
    # price to begin with. Drop the level (treat as unavailable) rather than propagate a
    # value that can't do its job as a real support level for this position.
    key_support = entry_reasons.get("sr_nearest_support")
    if key_support is not None and key_support >= entry_price:
        key_support = None

    return ThesisSnapshot(
        symbol=symbol,
        regime_at_entry=regime,
        signal_confidence_at_entry=float(confidence_at_entry) if confidence_at_entry is not None else 50.0,
        signal_direction_at_entry=signal_direction_at_entry,
        entry_price=entry_price,
        key_support_level=key_support,
        rs_score_at_entry=entry_reasons.get("rs_score"),
        thesis_tags=[],
    )


def current_regime_label(market: str) -> RegimeLabel:
    """Read the live rule-based regime state for a market and map it to RegimeLabel.
    Isolated in its own function (rather than called inline from check()) so this module
    stays testable without a live regime-engine call — tests pass a RegimeLabel directly,
    only real callers (e.g. a future Phase 5 wiring) need this live-lookup wrapper.
    """
    from ..services.paper_trading_engine import get_last_hk_regime, get_last_regime

    live = get_last_regime() if market == "US" else get_last_hk_regime()
    state = (live or {}).get("state", "unknown")
    try:
        return RegimeLabel(state)
    except ValueError:
        return RegimeLabel.UNKNOWN
