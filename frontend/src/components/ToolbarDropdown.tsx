/**
 * ToolbarDropdown — a labeled button that reveals a checkbox-list panel of toggleable
 * options. Built to replace PriceChart.tsx's flat row of ~15 individual toggle buttons
 * (SMA/EMA/BB/VWAP/Sig/RSI/MACD/etc.), which became illegible once Volume Profile was
 * added on top. Same open/close/outside-click pattern as _app.tsx's NavGroup dropdown,
 * adapted for checkboxes instead of navigation links.
 */
'use client';
import { useEffect, useRef, useState } from 'react';

export type ToolbarDropdownOption = {
  key: string;
  label: string;
  checked: boolean;
  onToggle: () => void;
  color?: string;
  /** Hover tooltip explaining what this option does — shown as a native title attribute. */
  title?: string;
};

export function ToolbarDropdown({ label, options }: { label: string; options: ToolbarDropdownOption[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const activeCount = options.filter(o => o.checked).length;

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  // This repo has no tailwind.config.js/postcss.config.js — globals.css is a hand-authored,
  // fixed set of utility classes rather than a live JIT compiler, so arbitrary-value and
  // opacity-modifier classes (bg-indigo-600/20, text-[10px], border-indigo-500/50, etc.) have
  // NO matching rule anywhere and silently no-op. Using inline styles here instead of chasing
  // each missing class individually, since nearly every class this component used turned out
  // to be unresolved — most visibly bg-[#0d1424] on the panel, which made it fully transparent
  // over the chart in production despite looking correct in source.
  const [hovered, setHovered] = useState<string | null>(null);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 rounded"
        style={{
          padding: '4px 10px',
          fontSize: 12,
          fontWeight: 500,
          transition: 'colors 0.15s',
          background: activeCount > 0 ? 'rgba(79, 70, 229, 0.2)' : 'transparent',
          border: `1px solid ${activeCount > 0 ? 'rgba(99, 102, 241, 0.5)' : '#334155'}`,
          color: activeCount > 0 ? '#a5b4fc' : '#94a3b8',
        }}
      >
        {label}
        {activeCount > 0 && <span style={{ fontSize: 10, color: '#818cf8', fontFamily: 'monospace' }}>({activeCount})</span>}
        <span style={{ fontSize: 9, display: 'inline-block', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>▾</span>
      </button>

      {open && (
        <div
          className="absolute z-50 rounded-lg"
          style={{
            top: 'calc(100% + 6px)', left: 0, minWidth: 160, padding: 6,
            border: '1px solid #334155', backgroundColor: '#0d1424',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.8)',
          }}
        >
          {options.map(opt => (
            <label
              key={opt.key}
              title={opt.title}
              className="flex items-center gap-2 rounded cursor-pointer"
              style={{
                padding: '6px 8px', fontSize: 12, color: '#cbd5e1', whiteSpace: 'nowrap',
                background: hovered === opt.key ? 'rgba(30, 41, 59, 0.7)' : 'transparent',
              }}
              onMouseEnter={() => setHovered(opt.key)}
              onMouseLeave={() => setHovered(null)}
            >
              <input type="checkbox" checked={opt.checked} onChange={opt.onToggle} style={{ accentColor: '#6366f1' }} />
              {opt.color && <span className="inline-block flex-shrink-0 rounded-full" style={{ width: 12, height: 2, backgroundColor: opt.color }} />}
              {opt.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
