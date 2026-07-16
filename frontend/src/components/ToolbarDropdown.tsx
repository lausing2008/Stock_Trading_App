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

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-colors ${
          activeCount > 0 ? 'bg-indigo-600/20 border border-indigo-500/50 text-indigo-300' : 'border border-slate-700 text-slate-400 hover:border-indigo-500 hover:text-indigo-300'
        }`}
      >
        {label}
        {activeCount > 0 && <span className="text-[10px] text-indigo-400 font-mono">({activeCount})</span>}
        <span className={`text-[9px] transition-transform ${open ? 'rotate-180' : ''}`}>▾</span>
      </button>

      {open && (
        <div className="absolute top-[calc(100%+6px)] left-0 z-50 min-w-[160px] rounded-lg border border-slate-700 bg-[#0d1424] p-1.5 shadow-2xl">
          {options.map(opt => (
            <label
              key={opt.key}
              title={opt.title}
              className="flex items-center gap-2 px-2 py-1.5 rounded text-xs text-slate-300 hover:bg-slate-800/70 cursor-pointer whitespace-nowrap"
            >
              <input type="checkbox" checked={opt.checked} onChange={opt.onToggle} className="accent-indigo-500" />
              {opt.color && <span className="inline-block w-3 h-0.5 rounded-full flex-shrink-0" style={{ backgroundColor: opt.color }} />}
              {opt.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
