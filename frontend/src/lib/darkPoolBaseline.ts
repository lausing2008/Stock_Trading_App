import type { DarkPoolAlertBaseline } from './api';

const money = (n: number) => (n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${Math.round(n / 1e3)}k`);

const STATUS: Record<string, string> = {
  insufficient_history: 'too little captured history',
  query_failed: 'baseline query failed',
  no_prints: 'no captured prints',
};

/** AUD-DP-TAB-BASELINE (2026-11-10): what bar the alert actually cleared, in words. */
export function darkPoolBaselineText(b: DarkPoolAlertBaseline | null | undefined): { text: string; floorOnly: boolean } {
  if (!b) return { text: 'not recorded', floorOnly: false };
  if (b.qualified_by === 'floor_and_relative' && b.median != null) {
    const times = b.vs_baseline != null ? `${b.vs_baseline.toFixed(1)}× ` : '';
    const sample = b.prints != null && b.window_days != null ? `, ${b.prints} prints / ${b.window_days}d` : '';
    return { text: `${times}median ${money(b.median)}${sample}`, floorOnly: false };
  }
  return { text: `$1M floor only (${STATUS[b.status] ?? b.status})`, floorOnly: true };
}
