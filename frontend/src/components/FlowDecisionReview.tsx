import React from 'react';
import type { OptionsFlowAlertRow } from '@/lib/api';

export function FlowDecisionReview({ row, onClose }: { row: OptionsFlowAlertRow; onClose: () => void }) {
  const structures = row.direction === 'bullish'
    ? 'Long call or bull call spread; compare a bull put spread only with short-option approval and collateral.'
    : 'Long put or bear put spread; compare a bear call spread only with short-option approval and collateral.';
  return <section aria-label="Selected alert review" style={{ padding: 20, marginBottom: 20, color: '#cbd5e1', background: '#0d1424', border: '1px solid #475569', borderRadius: 10 }}>
    <button onClick={onClose} style={{ float: 'right' }}>Close review</button>
    <h2>{row.symbol} · {row.direction} activity inference</h2>
    <p><strong>Research only — trade eligibility not established.</strong> Captured {row.fired_date}; {row.option_chain}.</p>
    <p><strong>Evidence:</strong> {row.has_sweep ? 'Provider flags a sweep. ' : 'No sweep flag. '}
      {row.volume_oi_ratio == null ? 'Volume/OI unavailable.' : `Volume/OI ${row.volume_oi_ratio.toFixed(1)}×.`}
      {' '}Neither establishes opening positions or trader intent.</p>
    <p><strong>Strategies to compare, not recommendations:</strong> {structures}</p>
    <p><strong>Before entry:</strong> obtain fresh two-sided quotes, spread and size; verify the contract deliverable,
      last trading instant, event calendar, your permissions and available capital. This historical alert supplies none of those checks.</p>
    <p><strong>Maximum loss / quantity:</strong> unavailable until a specific structure, quotes, costs and account capital are supplied.
      Proposed initial limits: 0.25% of account value per trade and 1% combined open option risk. These are planning limits, not optimized thresholds.</p>
    <p><strong>Confirmation / exit:</strong> not defined by a tape alert. Record a price trigger, invalidation, time exit and handling of expiry before capturing a strategy.</p>
    <p><strong>Measurement:</strong> {(row.calibration_eligibility ?? 'unavailable').replace(/_/g, ' ')}.
      {' '}{row.eligibility_reason} The table measures the underlying; option net P&amp;L is unmeasured.</p>
    <a href={`/quality-value?symbols=${encodeURIComponent(row.symbol)}`}>Open company research</a>
  </section>;
}
