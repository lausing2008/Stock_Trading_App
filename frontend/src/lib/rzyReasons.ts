/** Plain-English meanings for every Little RZY status, exclusion, cancellation and email state. */

export const RZY_STREAM_STATUS: Record<string, string> = {
  research_only: 'Usable: complete, current bars. Signals can fire; paper fills stay disabled.',
  stale: 'The newest completed bar is late, so nothing is emailed from this stream.',
  awaiting_backfill: 'A bar went missing in the last 30 minutes; waiting for it to arrive before restarting after the gap.',
  restatement_pending: 'Stored bars were restated by the data source; the stream pauses (up to 30 minutes) and then rebuilds.',
  rebuild_pending: 'Needs a rebuild from stored bars; the next scheduled scan performs it.',
  no_completed_bars: 'No stored completed bars for this symbol and timeframe.',
  data_rejected: 'Stored bars failed a basic check (for example a high below the low).',
};

export const RZY_EXCLUSION: Record<string, string> = {
  ...RZY_STREAM_STATUS,
  no_armed_structure: 'No setup is armed on this stream right now.',
  insufficient_planned_reward_risk: 'A setup is armed, but its planned reward/risk is below 1.5.',
  stale_15m_aggregate: 'The newest 15-minute bar that should be complete is missing (15m bars are built from 5m bars).',
  stale_1h_aggregate: 'The newest 1-hour bar that should be complete is missing (1h bars are built from 5m bars).',
  stale_5m_source: 'The 5-minute bars used to build this timeframe are late.',
  not_evaluated_yet: 'No scan has evaluated this stream yet (for example, a symbol just added to the watchlist).',
  unsupported_timeframe: 'This timeframe is not supported.',
  US_stocks_only_or_unknown_symbol: 'Little RZY covers US stocks only.',
  calendar_coverage_expired: 'The exchange calendar does not cover this date.',
};

export const RZY_CANCELLATION: Record<string, string> = {
  data_basis_or_freshness_unverified: 'Stored prices carry no verified provider timing or execution basis, so paper fills are disabled by design. This applies to every stream today.',
  shortability_or_borrow_cost_unverified: 'A short needs verified borrow availability and a borrow rate; neither is stored.',
  borrow_cost_invalid: 'A supplied borrow rate was malformed. Before 2026-10-20, a missing rate was also labelled this way.',
  actual_fill_reward_risk_or_gap: 'At the next bar’s open the price had gapped past the stop or target, or reward/risk after costs fell below 1.5.',
  liquidity_requirement: 'The trigger candle’s volume was below the minimum.',
  trend_epoch_changed: 'The trend changed before the entry bar opened.',
};

export const RZY_ALERT_STATE: Record<string, string> = {
  accepted: 'Accepted by the email provider. Not proof it reached the inbox.',
  expired: 'Not sent: no longer fresh by the time it could be delivered.',
  suppressed: 'Not sent: you opted out, the stream was paused or rebuilt, or the figures changed in a rebuild.',
  pending: 'Waiting in the send queue.',
  claimed: 'Being sent now.',
  failed: 'The email provider refused it after retries.',
};

export function rzyReasonText(table: Record<string, string>, reason: string): string {
  if (table[reason]) return table[reason];
  if (reason.startsWith('data_rejected')) return RZY_STREAM_STATUS.data_rejected + ' ' + reason.slice('data_rejected'.length).replace(/^[:\s]+/, '');
  return 'Unrecognised reason; see the raw value.';
}

/** Split a comma-joined cancellation reason into its parts, standing blockers last. */
export function cancellationParts(reason?: string | null): string[] {
  const parts = (reason ?? '').split(',').map(x => x.trim()).filter(Boolean);
  const standing = new Set(['data_basis_or_freshness_unverified', 'shortability_or_borrow_cost_unverified']);
  return [...parts.filter(p => !standing.has(p)), ...parts.filter(p => standing.has(p))];
}

/** Count how many rows carry each reason (a row with three reasons counts once for each). */
export function countReasons(rows: { reason?: string | null }[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const r of rows) for (const p of cancellationParts(r.reason)) counts.set(p, (counts.get(p) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/** Group digest exclusions by reason, keeping the symbols for each. */
export function groupExclusions(rows: { symbol: string; timeframe: string; reason: string }[]) {
  const by = new Map<string, string[]>();
  for (const r of rows) {
    const key = r.reason.startsWith('data_rejected') ? 'data_rejected' : r.reason;
    by.set(key, [...(by.get(key) ?? []), `${r.symbol} ${r.timeframe}`]);
  }
  return [...by.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
}
