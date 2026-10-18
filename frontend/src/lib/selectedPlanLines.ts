import type { TradeSetup } from './copilot';

export type SelectedPlanLine = { price: number; label: string; role: string };
/** Only the selected, fresh scenario is an overlay; blocked alternatives are not orders. */
export function selectedPlanLines(setup?: TradeSetup): SelectedPlanLine[] {
  if (!setup || !['conditional', 'exit_triggered'].includes(setup.state)) return [];
  const lines = [{ price: setup.entry, label: setup.shares_basis === 'held' ? 'Original cost' : 'Entry', role: 'support' },
    ...(setup.stop == null ? [] : [{ price: setup.stop, label: 'Stop', role: 'stop' }]),
    ...setup.targets.slice(0, 2).map((t, i) => ({ price: t.price, label: `TP${i + 1}`, role: 'target' }))];
  return lines.filter(l => Number.isFinite(l.price) && l.price > 0);
}
