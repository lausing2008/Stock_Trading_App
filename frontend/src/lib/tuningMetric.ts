/** Historical watchdog rows stored a hit-rate fraction in an EV column. Keep units explicit. */
export function tuningMetric(row: { triggered_by: string | null; validation_ev_pct: number | null;
  new_value: Record<string, unknown> | null }): string {
  if (row.triggered_by === 'watchdog') {
    const rate = row.new_value?.hit_rate_fraction ?? row.validation_ev_pct;
    return typeof rate === 'number' && Number.isFinite(rate) && rate >= 0 && rate <= 1
      ? `Hit rate ${(rate * 100).toFixed(1)}%` : 'Hit rate unavailable';
  }
  return row.validation_ev_pct != null ? `Gross return ${row.validation_ev_pct.toFixed(2)}%` : 'Return unavailable';
}
