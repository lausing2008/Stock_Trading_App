export type WidgetWidth = 'compact' | 'wide' | 'full';
export type WorkspaceLayout = {
  order: string[]; closed: Record<string, boolean>; hidden: Record<string, boolean>; widths: Record<string, WidgetWidth>;
};
export const emptyWorkspaceLayout = (): WorkspaceLayout => ({ order: [], closed: {}, hidden: {}, widths: {} });
const idPattern = /^[a-z][a-z0-9-]{0,79}$/;
export function parseWorkspaceLayout(raw: unknown): WorkspaceLayout {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Invalid workspace layout.');
  const value = raw as Record<string, unknown>;
  if ('version' in value && value.version !== 1) throw new Error('Unsupported layout version.');
  if (!Array.isArray(value.order) || value.order.length > 100 || !value.order.every(id => typeof id === 'string' && idPattern.test(id))) throw new Error('Invalid widget order.');
  const layout = emptyWorkspaceLayout();
  layout.order = [...new Set<string>(value.order)];
  for (const field of ['closed', 'hidden', 'widths'] as const) {
    const entries = value[field];
    if (entries === undefined) continue; // Original browser layouts had only order and closed.
    if (!entries || typeof entries !== 'object' || Array.isArray(entries) || Object.keys(entries).length > 100) throw new Error('Invalid widget preferences.');
    for (const [id, setting] of Object.entries(entries)) {
      if (!idPattern.test(id)) throw new Error('Invalid widget identifier.');
      if (field === 'widths') {
        if (!['compact', 'wide', 'full'].includes(setting as string)) throw new Error('Invalid widget width.');
        layout.widths[id] = setting as WidgetWidth;
      } else {
        if (typeof setting !== 'boolean') throw new Error('Invalid widget visibility.');
        layout[field][id] = setting;
      }
    }
  }
  return layout;
}
