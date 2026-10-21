import { expect, it } from 'vitest';
import { RZY_CANCELLATION, RZY_EXCLUSION, cancellationParts, countReasons, groupExclusions, rzyReasonText } from './rzyReasons';
it('orders the specific cancellation reasons before standing data blockers', () => {
  expect(cancellationParts('data_basis_or_freshness_unverified,actual_fill_reward_risk_or_gap'))
    .toEqual(['actual_fill_reward_risk_or_gap', 'data_basis_or_freshness_unverified']);
  expect(cancellationParts(null)).toEqual([]);
});
it('counts each reason once per row and explains every engine cancellation reason', () => {
  const rows = [{ reason: 'data_basis_or_freshness_unverified,shortability_or_borrow_cost_unverified' }, { reason: 'data_basis_or_freshness_unverified' }];
  expect(countReasons(rows)).toEqual([['data_basis_or_freshness_unverified', 2], ['shortability_or_borrow_cost_unverified', 1]]);
  for (const r of ['data_basis_or_freshness_unverified', 'shortability_or_borrow_cost_unverified', 'borrow_cost_invalid', 'actual_fill_reward_risk_or_gap', 'liquidity_requirement', 'trend_epoch_changed'])
    expect(RZY_CANCELLATION[r]).toBeTruthy();
});
it('groups digest exclusions and explains backend reasons, including derived-bar staleness', () => {
  const g = groupExclusions([{ symbol: 'A', timeframe: '15m', reason: 'stale_15m_aggregate' }, { symbol: 'B', timeframe: '15m', reason: 'stale_15m_aggregate' },
    { symbol: 'C', timeframe: '5m', reason: 'data_rejected: Invalid open' }]);
  expect(g).toEqual([['stale_15m_aggregate', ['A 15m', 'B 15m']], ['data_rejected', ['C 5m']]]);
  for (const r of ['no_armed_structure', 'insufficient_planned_reward_risk', 'stale_1h_aggregate', 'restatement_pending', 'rebuild_pending', 'awaiting_backfill', 'not_evaluated_yet'])
    expect(RZY_EXCLUSION[r]).toBeTruthy();
  expect(rzyReasonText(RZY_EXCLUSION, 'data_rejected: Invalid open')).toContain('Invalid open');
  expect(rzyReasonText(RZY_EXCLUSION, 'something_new')).toContain('Unrecognised');
});
