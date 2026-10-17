import React, { useEffect, useRef, useState } from 'react';

type WidgetProps = { id: string; title: string; wide?: boolean; collapsed?: boolean; children: React.ReactNode };
type Layout = { order: string[]; closed: Record<string, boolean> };

export function StockDetailWidget({ children }: WidgetProps) { return <>{children}</>; }

/** One page grid; layout belongs to this browser/account, not a market-data snapshot. */
export default function StockDetailWorkspace({ storageKey, children }: { storageKey: string; children: React.ReactNode }) {
  const widgets = React.Children.toArray(children).filter(React.isValidElement<WidgetProps>);
  const [layout, setLayout] = useState<Layout>({ order: [], closed: {} });
  const [loadedKey, setLoadedKey] = useState('');
  const [arranging, setArranging] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const dragging = useRef<string | null>(null);
  useEffect(() => {
    let saved: Layout = { order: [], closed: {} };
    try {
      const value = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
      if (value && Array.isArray(value.order) && value.order.every((id: unknown) => typeof id === 'string')) {
        saved = { order: [...new Set<string>(value.order)], closed: {} };
        if (value.closed && typeof value.closed === 'object') {
          for (const [id, closed] of Object.entries(value.closed)) if (typeof closed === 'boolean') saved.closed[id] = closed;
        }
      }
    } catch { /* Storage may be unavailable; the workspace remains usable. */ }
    setLayout(saved); setLoadedKey(storageKey); setArranging(false);
  }, [storageKey]);
  function update(next: Layout) {
    setLayout(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* In-memory arrangement still works. */ }
  }
  const active = loadedKey === storageKey ? layout : { order: [], closed: {} };
  const ordered = [...widgets].sort((a, b) => {
    const index = (id: string) => { const i = active.order.indexOf(id); return i < 0 ? active.order.length + widgets.findIndex(w => w.props.id === id) : i; };
    return index(a.props.id) - index(b.props.id);
  });
  const visible = ordered.filter(w => React.Children.toArray(w.props.children).length > 0);
  function move(id: string, target: string) {
    if (id === target) return;
    const order = ordered.map(w => w.props.id);
    const from = order.indexOf(id), to = order.indexOf(target);
    if (from < 0 || to < 0) return;
    order.splice(from, 1); order.splice(to, 0, id);
    update({ ...active, order });
    setAnnouncement(`${widgets.find(w => w.props.id === id)?.props.title} moved. Layout saved in this browser.`);
  }
  return <section aria-label="Stock workspace">
    <div className="stock-workspace-toolbar">
      <div><h2>Stock workspace</h2><p>Chart, plan and signals first. Expand supporting research when you need it.</p></div>
      <div className="stock-workspace-actions">
        <button type="button" aria-pressed={arranging} onClick={() => setArranging(!arranging)}>{arranging ? 'Done arranging' : 'Arrange widgets'}</button>
        <button type="button" onClick={() => { update({ order: [], closed: {} }); setAnnouncement('Default layout restored.'); }}>Reset layout</button>
      </div>
    </div>
    {arranging && <p className="stock-workspace-hint">Drag a handle onto another widget&apos;s title bar, or use the move buttons. Layout is shared across stocks in this browser for your account.</p>}
    <span className="stock-workspace-status" role="status">{announcement}</span>
    <div className="stock-workspace-grid">
      {visible.map((widget, index) => {
        const { id, title, wide, collapsed, children: content } = widget.props;
        const closed = active.closed[id] ?? !!collapsed;
        return <section key={id} aria-label={`${title} widget`} className={`stock-workspace-widget${wide ? ' stock-workspace-wide' : ''}`}>
          <div className="stock-workspace-widget-header" data-arranging={arranging}
            onDragOver={e => { if (arranging && dragging.current) e.preventDefault(); }}
            onDrop={e => { if (!arranging || !dragging.current) return; e.preventDefault(); move(dragging.current, id); dragging.current = null; }}>
            {arranging && <button type="button" draggable aria-label={`Drag ${title}`} title="Drag to another widget"
              onDragStart={e => { dragging.current = id; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', id); }}
              onDragEnd={() => { dragging.current = null; }}>⠿</button>}
            <button type="button" className="stock-workspace-toggle" aria-expanded={!closed} aria-controls={`stock-widget-${id}`}
              onClick={() => update({ ...active, closed: { ...active.closed, [id]: !closed } })}>
              <span>{title}</span><span aria-hidden="true">{closed ? '+' : '−'}</span>
            </button>
            {arranging && <>
              <button type="button" aria-label={`Move ${title} earlier`} disabled={index === 0} onClick={() => move(id, visible[index - 1].props.id)}>↑</button>
              <button type="button" aria-label={`Move ${title} later`} disabled={index === visible.length - 1} onClick={() => move(id, visible[index + 1].props.id)}>↓</button>
            </>}
          </div>
          <div id={`stock-widget-${id}`} hidden={closed} className="stock-workspace-widget-body">{content}</div>
        </section>;
      })}
    </div>
  </section>;
}
