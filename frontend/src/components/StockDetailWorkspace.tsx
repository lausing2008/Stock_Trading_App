import WorkspaceSyncControls from './WorkspaceSyncControls';
import React, { useEffect, useRef, useState } from 'react';

import { emptyWorkspaceLayout, parseWorkspaceLayout, type WorkspaceLayout as Layout } from '@/lib/stockWorkspaceLayout';

type WidgetProps = { id: string; title: string; wide?: boolean; full?: boolean; collapsed?: boolean; onExpandedChange?: (open: boolean) => void; children: React.ReactNode };

export function StockDetailWidget({ children }: WidgetProps) { return <>{children}</>; }
function WidgetBody({ open, onExpandedChange, children }: { open: boolean; onExpandedChange?: (open: boolean) => void; children: React.ReactNode }) {
  useEffect(() => { onExpandedChange?.(open); }, [open, onExpandedChange]);
  return <>{children}</>;
}

/** One page grid; layout belongs to this browser/account, not a market-data snapshot. */
export default function StockDetailWorkspace({ storageKey, children }: { storageKey: string; children: React.ReactNode }) {
  const widgets = React.Children.toArray(children).filter(React.isValidElement<WidgetProps>);
  const [layout, setLayout] = useState<Layout>(emptyWorkspaceLayout);
  const [loadedKey, setLoadedKey] = useState('');
  const [arranging, setArranging] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const dragging = useRef<string | null>(null);
  const importFile = useRef<HTMLInputElement>(null);
  const currentKey = useRef(storageKey); currentKey.current = storageKey;
  const alive = useRef(true);
  const [layoutError, setLayoutError] = useState('');
  const [storageWarning, setStorageWarning] = useState('');
  useEffect(() => { alive.current = true; return () => { alive.current = false; dragging.current = null; }; }, []);
  useEffect(() => {
    let saved = emptyWorkspaceLayout();
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) saved = parseWorkspaceLayout(JSON.parse(raw));
    } catch { /* Storage may be unavailable; the workspace remains usable. */ }
    setLayoutError('');
    setLayout(saved); setLoadedKey(storageKey); setArranging(false);
  }, [storageKey]);
  function update(next: Layout) {
    setLayout(next);
    try { localStorage.setItem(storageKey, JSON.stringify(next)); setStorageWarning(''); }
    catch { setStorageWarning('Browser storage is unavailable. Changes apply for this visit; export your layout to keep them.'); }
  }
  const active = loadedKey === storageKey ? layout : emptyWorkspaceLayout();
  const ordered = [...widgets].sort((a, b) => {
    const index = (id: string) => { const i = active.order.indexOf(id); return i < 0 ? active.order.length + widgets.findIndex(w => w.props.id === id) : i; };
    return index(a.props.id) - index(b.props.id);
  });
  // Lazy data panels must expose their heading before they have content; expanding
  // that heading is what enables their fetch in the parent page.
  const available = ordered.filter(w => w.props.onExpandedChange || React.Children.toArray(w.props.children).length > 0);
  const visible = available.filter(w => !active.hidden[w.props.id]);
  function move(id: string, target: string) {
    if (id === target) return;
    const order = ordered.map(w => w.props.id);
    const from = order.indexOf(id), to = order.indexOf(target);
    if (from < 0 || to < 0) return;
    order.splice(from, 1); order.splice(to, 0, id);
    update({ ...active, order });
    setAnnouncement(`${widgets.find(w => w.props.id === id)?.props.title} moved.`);
  }
  async function importLayout(file?: File) {
    if (!file) return;
    const key = storageKey;
    try {
      if (file.size > 32768) throw new Error('Layout files must be smaller than 32 KB.');
      const next = parseWorkspaceLayout(JSON.parse(await file.text()));
      if (!alive.current || currentKey.current !== key) return;
      update(next); setLayoutError(''); setAnnouncement('Layout imported.');
    } catch (e) {
      if (alive.current && currentKey.current === key) setLayoutError(e instanceof Error ? e.message : 'Could not import layout.');
    }
  }
  function exportLayout() {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ version: 1, ...active }, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = url; link.download = 'stock-workspace-layout.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section aria-label="Stock workspace">
    <div className="stock-workspace-toolbar">
      <div><h2>Stock workspace</h2><p>Chart, plan and signals first. Expand supporting research when you need it.</p></div>
      <div className="stock-workspace-actions">
        <button type="button" aria-pressed={arranging} onClick={() => setArranging(!arranging)}>{arranging ? 'Done arranging' : 'Arrange widgets'}</button>
        <button type="button" onClick={() => { update(emptyWorkspaceLayout()); setAnnouncement('Default layout restored.'); }}>Reset layout</button>
        <button type="button" onClick={exportLayout}>Export layout</button>
        <button type="button" onClick={() => importFile.current?.click()}>Import layout</button>
        <input ref={importFile} type="file" accept="application/json,.json" aria-label="Import workspace layout" hidden
          onChange={e => { void importLayout(e.target.files?.[0]); e.target.value = ''; }} />
      </div>
    </div>
    {loadedKey === storageKey && <WorkspaceSyncControls key={storageKey} storageKey={storageKey} layout={active} onLoad={update} />}
    {arranging && <p className="stock-workspace-hint">Drag a handle onto another widget&apos;s title bar, or use the move buttons. Layout is shared across stocks in this browser for your account.</p>}
    {layoutError && <p role="alert">{layoutError}</p>}
    {storageWarning && <p className="stock-workspace-hint">{storageWarning}</p>}
    {available.some(w => active.hidden[w.props.id]) && <details className="stock-workspace-hidden">
      <summary>Hidden widgets ({available.filter(w => active.hidden[w.props.id]).length})</summary>
      {available.filter(w => active.hidden[w.props.id]).map(w => <button type="button" key={w.props.id}
        onClick={() => update({ ...active, hidden: { ...active.hidden, [w.props.id]: false } })}>Show {w.props.title}</button>)}
    </details>}
    <span className="stock-workspace-status" role="status">{announcement}</span>
    <div className="stock-workspace-grid">
      {available.map(widget => {
        const index = visible.findIndex(w => w.props.id === widget.props.id);
        const { id, title, wide, full, collapsed, onExpandedChange, children: content } = widget.props;
        const hidden = !!active.hidden[id];
        const width = active.widths[id] ?? (full ? 'full' : wide ? 'wide' : 'compact');
        const closed = active.closed[id] ?? !!collapsed;
        return <section key={id} hidden={hidden} aria-label={`${title} widget`} className={`stock-workspace-widget${width === 'full' ? ' stock-workspace-full' : width === 'wide' ? ' stock-workspace-wide' : ''}`}>
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
          {arranging && <div className="stock-workspace-widget-options">
            <label>Width <select aria-label={`Width for ${title}`} value={width}
              onChange={e => update({ ...active, widths: { ...active.widths, [id]: e.target.value as 'compact' | 'wide' | 'full' } })}>
              <option value="compact">One column</option><option value="wide">Two columns</option><option value="full">Full row</option>
            </select></label>
            <button type="button" onClick={() => update({ ...active, hidden: { ...active.hidden, [id]: true } })}>Hide {title}</button>
          </div>}
          <div id={`stock-widget-${id}`} hidden={closed} className="stock-workspace-widget-body"><WidgetBody open={!closed && !hidden} onExpandedChange={onExpandedChange}>{content}</WidgetBody></div>
        </section>;
      })}
    </div>
  </section>;
}
