"""Read-only chronological comparison of confidence-calibration candidates.

Raw engine strength is a binning feature, never a probability. Probabilities are fitted from
previously RESOLVED labels only. No production weights, thresholds or models are changed.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from collections import Counter, defaultdict


@dataclass(frozen=True)
class Observation:
    signal_id: int
    symbol: str
    market: str
    horizon: str
    direction: str
    window: int
    score: float
    issued_at: datetime | None
    resolved_at: datetime | None
    correct: bool | None
    evidence_status: str | None


def compare(observations, *, now, include_provisional=False, min_train=30):
    statuses = Counter(r.evidence_status or 'unknown' for r in observations)
    allowed = {'verified', 'provisional'} if include_provisional else {'verified'}
    eligible, excluded, seen = [], Counter(), set()
    for r in observations:
        if r.evidence_status not in allowed:
            excluded['evidence_not_eligible'] += 1; continue
        if r.issued_at is None:
            excluded['missing_or_mismatched_prediction_snapshot'] += 1; continue
        if r.resolved_at is None or not r.issued_at < r.resolved_at <= now:
            excluded['label_clock_unavailable_or_invalid'] += 1; continue
        if r.correct is None or not math.isfinite(r.score) or not 0 <= r.score <= 100:
            excluded['missing_or_invalid_score_or_target'] += 1; continue
        key = (r.signal_id, r.window)
        if key in seen:
            excluded['duplicate_signal_window'] += 1; continue
        seen.add(key); eligible.append(r)
    eligible.sort(key=lambda r: (r.issued_at, r.signal_id, r.window))
    days = sorted({r.issued_at.date() for r in eligible})
    base = {'mode': 'provisional_research' if include_provisional else 'verified_only',
            'evidence_counts': dict(statuses), 'eligible': len(eligible), 'excluded': dict(excluded),
            'production_changed': False, 'minimum_training_labels': min_train,
            'note': 'Research only. More labels, pooled markets and a lower diagnostic error do not establish improved trading returns. No candidate is promoted.',
            'label_definition': 'The existing direction-specific correct-call outcome, at its named calendar-day window.',
            'candidates': [], 'test_first': None, 'test_last': None, 'test_signals': 0,
            'limitations': ['Overlapping outcomes are correlated; row count is not independent sample size.',
                            'Cross-market pooling is explicit and may mix regimes.',
                            'Labels resolved later than the prediction cannot enter training.']}
    if len(days) < 3:
        return {**base, 'status': 'insufficient_evidence', 'reason': 'At least three signal dates with eligible resolved labels are needed.'}
    split = days[max(1, int(len(days)*.8))]
    tests = [r for r in eligible if r.issued_at.date() >= split]
    groups = defaultdict(list)
    for r in eligible: groups[(r.horizon, r.direction, r.window)].append(r)
    variants = [('recent_market', 30, False), ('long_market', 180, False), ('long_pooled', 180, True)]
    predictions = {name: {} for name, _, _ in variants}
    for target in tests:
        key = (target.signal_id, target.window)
        for name, lookback, pooled in variants:
            train = [r for r in groups[(target.horizon, target.direction, target.window)]
                     if target.issued_at-timedelta(days=lookback) <= r.issued_at < target.issued_at
                     and r.resolved_at < target.issued_at and (pooled or r.market == target.market)]
            if len(train) < min_train: continue
            prior = (sum(bool(r.correct) for r in train)+1)/(len(train)+2)
            bucket = min(9, int(target.score//10))
            local = [r for r in train if min(9,int(r.score//10)) == bucket]
            probability = (sum(bool(r.correct) for r in local)+10*prior)/(len(local)+10)
            predictions[name][key] = (probability, int(target.correct))
    paired = set.intersection(*(set(v) for v in predictions.values()))
    for name, days_back, pooled in variants:
        pred = predictions[name]
        brier = sum((pred[k][0]-pred[k][1])**2 for k in paired)/len(paired) if paired else None
        base['candidates'].append({'candidate': name, 'lookback_days': days_back,
            'scope': 'all_markets' if pooled else 'same_market', 'forecast_count': len(pred),
            'paired_count': len(paired), 'paired_brier': round(brier,6) if brier is not None else None})
    return {**base, 'status': 'research_only' if paired else 'insufficient_chronological_training',
            'reason': 'Compared on the same held-out observations; no deployment decision.' if paired else 'No common held-out set has enough labels actually available before prediction time.',
            'test_first': str(min(r.issued_at.date() for r in tests)),
            'test_last': str(max(r.issued_at.date() for r in tests)),
            'test_signals': len({r.signal_id for r in tests}), 'test_rows': len(tests),
            'symbols': len({r.symbol for r in eligible})}
