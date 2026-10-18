import WorkspaceSyncControls from './WorkspaceSyncControls';
import React, { useEffect, useRef, useState } from 'react';

import { emptyWorkspaceLayout, parseWorkspaceLayout, type WorkspaceLayout as Layout } from '@/lib/stockWorkspaceLayout';

type ResearchGroup = 'signals' | 'technicals' | 'company';
type WidgetProps = { group?: ResearchGroup; id: string; title: string; wide?: boolean; full?: boolean; collapsed?: boolean; onExpandedChange?: (open: boolean) => void; children: React.ReactNode };

export function StockDetailWidget({ children }: WidgetProps) { return <>{children}</>; }
function WidgetBody({ open, onExpandedChange, children }: { open: boolean; onExpandedChange?: (open: boolean) => void; children: React.ReactNode }) {
  useEffect(() => { onExpandedChange?.(open); }, [open, onExpandedChange]);
  return <>{children}</>;
}

/** One page grid; layout belongs to this browser/account, not a market-data snapshot. */
export default function StockDetailWorkspace({ storageKey, children, trading = false }: { storageKey: string; children: React.ReactNode; trading?: boolean }) {
  const widgets = React.Children.toArray(children).filter(React.isValidElement<WidgetProps>);
  const [layout, setLayout] = useState<Layout>(emptyWorkspaceLayout);
  const [loadedKey, setLoadedKey] = useState('');
  const [arranging, setArranging] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const [group, setGroup] = useState<ResearchGroup>('signals');
  const [focus, setFocus] = useState(false);
  const dragging = useRef<string | null>(null);
  const importFile = useRef<HTMLInputElement>(null);
  const currentKey = useRef(storageKey); currentKey.current = storageKey;
  const alive = useRef(true);
  const [layoutError, setLayoutError] = useState('');
  const [storageWarning, setStorageWarning] = useState('');
  useEffect(() => {
    if (!trading || !focus) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setFocus(false); };
    document.addEventListener('keydown', escape);
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', escape); };
  }, [trading, focus]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; dragging.current = null; }; }, []);
  useEffect(() => {
    let saved = emptyWorkspaceLayout();
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) saved = parseWorkspaceLayout(JSON.parse(raw));
    } catch { /* Storage may be unavailable; the workspace remains usable. */ }
    setLayoutError('');
    setLayout(saved); setLoadedKey(storageKey); setArranging(false); setGroup('signals'); setFocus(false);
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
  const pinned = (id: string) => trading && ['chart', 'game-plan'].includes(id);
  const inView = (w: React.ReactElement<WidgetProps>) => !trading || pinned(w.props.id) || (!focus && (w.props.group ?? 'signals') === group);
  const visible = available.filter(w => !pinned(w.props.id) && !active.hidden[w.props.id] && inView(w));
  const displayed = trading ? [...available.filter(w => w.props.id === 'chart'), ...available.filter(w => w.props.id === 'game-plan'), ...available.filter(w => !pinned(w.props.id))] : available;
  function move(id: string, target: string) {
    if (id === target || pinned(id) || pinned(target)) return;
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
  function renderWidget(widget: React.ReactElement<WidgetProps>) {
        const index = visible.findIndex(w => w.props.id === widget.props.id);
        const { id, title, wide, full, collapsed, onExpandedChange, children: content } = widget.props;
        const isPinned = pinned(id);
        const hidden = !isPinned && (!!active.hidden[id] || !inView(widget));
        const width = isPinned ? 'full' : active.widths[id] ?? (full ? 'full' : wide ? 'wide' : 'compact');
        const closed = isPinned ? false : active.closed[id] ?? !!collapsed;
        return <section key={id} data-trading-part={isPinned ? id : undefined} hidden={hidden} aria-label={`${title} widget`} className={`stock-workspace-widget${width === 'full' ? ' stock-workspace-full' : width === 'wide' ? ' stock-workspace-wide' : ''}`}>
          <div className="stock-workspace-widget-header" data-arranging={arranging && !isPinned}
            onDragOver={e => { if (arranging && !isPinned && dragging.current) e.preventDefault(); }}
            onDrop={e => { if (!arranging || isPinned || !dragging.current) return; e.preventDefault(); move(dragging.current, id); dragging.current = null; }}>
            {arranging && !isPinned && <button type="button" draggable aria-label={`Drag ${title}`} title="Drag to another widget"
              onDragStart={e => { dragging.current = id; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', id); }}
              onDragEnd={() => { dragging.current = null; }}>⠿</button>}
            {isPinned ? <h3>{title}</h3> : <button type="button" className="stock-workspace-toggle" aria-expanded={!closed} aria-controls={`stock-widget-${id}`}
              disabled={isPinned}
              onClick={() => update({ ...active, closed: { ...active.closed, [id]: !closed } })}>
              <span>{title}</span>{!isPinned && <span aria-hidden="true">{closed ? '+' : '−'}</span>}
            </button>}
            {arranging && !isPinned && <>
              <button type="button" aria-label={`Move ${title} earlier`} disabled={index === 0} onClick={() => move(id, visible[index - 1].props.id)}>↑</button>
              <button type="button" aria-label={`Move ${title} later`} disabled={index === visible.length - 1} onClick={() => move(id, visible[index + 1].props.id)}>↓</button>
            </>}
          </div>
          {arranging && !isPinned && <div className="stock-workspace-widget-options">
            <label>Width <select aria-label={`Width for ${title}`} value={width}
              onChange={e => update({ ...active, widths: { ...active.widths, [id]: e.target.value as 'compact' | 'wide' | 'full' } })}>
              <option value="compact">One column</option><option value="wide">Two columns</option><option value="full">Full row</option>
            </select></label>
            <button type="button" onClick={() => update({ ...active, hidden: { ...active.hidden, [id]: true } })}>Hide {title}</button>
          </div>}
          <div id={`stock-widget-${id}`} hidden={closed} className="stock-workspace-widget-body"><WidgetBody open={!closed && !hidden} onExpandedChange={onExpandedChange}>{content}</WidgetBody></div>
        </section>;
  }
  return <section aria-label="Stock workspace" data-focus={trading && focus} className={trading ? 'stock-trading-workspace' : undefined}>
    <div className="stock-workspace-toolbar">
      <div><h2>{trading ? 'Trading workspace' : 'Stock workspace'}</h2><p>{trading ? 'One chart, one selected plan. Explore supporting research below.' : 'Chart, plan and signals first. Expand supporting research when you need it.'}</p></div>
      <div className="stock-workspace-actions">
        {trading && <button type="button" aria-pressed={focus} onClick={() => { setFocus(!focus); setArranging(false); }}>{focus ? 'Exit Focus mode' : 'Focus mode'}</button>}
        <button type="button" aria-pressed={arranging} onClick={() => { setArranging(!arranging); setFocus(false); }}>{arranging ? 'Done arranging' : 'Arrange widgets'}</button>
        <button type="button" onClick={() => { update(emptyWorkspaceLayout()); setAnnouncement('Default layout restored.'); }}>Reset layout</button>
        <button type="button" onClick={exportLayout}>Export layout</button>
        <button type="button" onClick={() => importFile.current?.click()}>Import layout</button>
        <input ref={importFile} type="file" accept="application/json,.json" aria-label="Import workspace layout" hidden
          onChange={e => { void importLayout(e.target.files?.[0]); e.target.value = ''; }} />
      </div>
    </div>
    <div hidden={trading && focus}>{loadedKey === storageKey && <WorkspaceSyncControls key={storageKey} storageKey={storageKey} layout={active} onLoad={update} />}</div>
    {arranging && <p className="stock-workspace-hint">Drag a handle onto another widget&apos;s title bar, or use the move buttons. Layout is shared across stocks in this browser for your account.</p>}
    {layoutError && <p role="alert">{layoutError}</p>}
    {storageWarning && <p className="stock-workspace-hint">{storageWarning}</p>}
    {available.some(w => !pinned(w.props.id) && active.hidden[w.props.id]) && <details className="stock-workspace-hidden">
      <summary>Hidden widgets ({available.filter(w => !pinned(w.props.id) && active.hidden[w.props.id]).length})</summary>
      {available.filter(w => !pinned(w.props.id) && active.hidden[w.props.id]).map(w => <button type="button" key={w.props.id}
        onClick={() => { update({ ...active, hidden: { ...active.hidden, [w.props.id]: false } }); setGroup(w.props.group ?? 'signals'); setFocus(false); }}>Show {w.props.title}</button>)}
    </details>}
    <span className="stock-workspace-status" role="status">{announcement}</span>
    <div className="stock-workspace-grid">
      {(trading ? displayed.filter(w => pinned(w.props.id)) : displayed).map(renderWidget)}
      {trading && <div className="stock-workspace-full" hidden={focus}>
        <div className="stock-research-navigation">
          <div role="tablist" aria-label="Supporting research">{(['signals', 'technicals', 'company'] as const).map((tab, i) =>
            <button type="button" key={tab} role="tab" tabIndex={group === tab ? 0 : -1} aria-selected={group === tab} id={`research-tab-${tab}`} aria-controls="stock-research-panels"
              onKeyDown={e => {
                const tabs = ['signals', 'technicals', 'company'] as const;
                const next = e.key === 'ArrowRight' ? (i + 1) % 3 : e.key === 'ArrowLeft' ? (i + 2) % 3 : e.key === 'Home' ? 0 : e.key === 'End' ? 2 : -1;
                if (next < 0) return; e.preventDefault(); setGroup(tabs[next]);
                (e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next])?.focus();
              }} onClick={() => setGroup(tab)}>{tab === 'signals' ? 'Signals' : tab === 'technicals' ? 'Technicals' : 'Company'}</button>)}</div>
          <p>{group === 'signals' ? 'Calls, confluence and position sizing' : group === 'technicals' ? 'Indicators, price structure and volume' : 'Company research, news and ownership'}</p>
        </div>
        <div role="tabpanel" id="stock-research-panels" aria-labelledby={`research-tab-${group}`} className="stock-workspace-grid">
          {displayed.filter(w => !pinned(w.props.id)).map(renderWidget)}
          {!visible.length && <p>No visible panels in this tab. Restore hidden widgets or reset the layout.</p>}
        </div>
      </div>}
    </div>
  </section>;
}
