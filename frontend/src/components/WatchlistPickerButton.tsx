'use client';
import { useState, useEffect, useRef } from 'react';
import { api, type WatchlistMeta } from '@/lib/api';

type Props = {
  symbol: string;
  size?: 'xs' | 'sm';
  /** Already on the Trade Board? Pass true to show checkmark */
  onBoard?: boolean;
};

export default function WatchlistPickerButton({ symbol, size = 'sm', onBoard = false }: Props) {
  const [open, setOpen]         = useState(false);
  const [lists, setLists]       = useState<WatchlistMeta[] | null>(null);
  const [addedWl, setAddedWl]   = useState<Set<number>>(new Set());
  const [addedBoard, setAddedBoard] = useState(onBoard);
  const [busy, setBusy]         = useState<string | null>(null); // listId or 'board'
  const [error, setError]       = useState<string | null>(null);
  const ref                     = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [open]);

  async function toggle() {
    if (open) { setOpen(false); return; }
    if (!lists) {
      try {
        const data = await api.listWatchlists();
        setLists(data);
      } catch {
        setError('Failed to load');
      }
    }
    setOpen(true);
  }

  async function addToWatchlist(listId: number) {
    setBusy(String(listId));
    setError(null);
    try {
      await api.addToWatchlist(symbol, listId);
      setAddedWl(prev => new Set(prev).add(listId));
    } catch {
      setError('Failed');
    } finally {
      setBusy(null);
    }
  }

  async function addToBoard() {
    setBusy('board');
    setError(null);
    try {
      await api.createBoardPlan({ symbol, stage: 'watch' });
      setAddedBoard(true);
    } catch {
      setError('Failed');
    } finally {
      setBusy(null);
    }
  }

  const pad = size === 'xs' ? '2px 7px' : '4px 10px';
  const fs  = size === 'xs' ? '10px' : '11px';

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button
        onClick={toggle}
        title="Add to watchlist or Trade Board"
        style={{
          padding: pad, fontSize: fs, fontWeight: 600, borderRadius: '5px',
          border: '1px solid rgba(99,102,241,0.35)',
          background: open ? 'rgba(99,102,241,0.15)' : 'transparent',
          color: '#818cf8', cursor: 'pointer', whiteSpace: 'nowrap',
        }}
      >
        + Add ▾
      </button>

      {open && (
        <div style={{
          position: 'absolute', zIndex: 999, top: 'calc(100% + 4px)', right: 0,
          minWidth: '180px', background: '#0f172a',
          border: '1px solid #1e293b', borderRadius: '8px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.5)', overflow: 'hidden',
        }}>

          {/* ── Watchlists section ─────────────────────────── */}
          <div style={{ padding: '5px 10px 4px', fontSize: '9px', color: '#475569', fontWeight: 800, letterSpacing: '0.08em', background: '#0b1020' }}>
            WATCHLISTS
          </div>

          {!lists && (
            <div style={{ padding: '8px 12px', fontSize: '11px', color: '#64748b' }}>Loading…</div>
          )}
          {lists && lists.length === 0 && (
            <div style={{ padding: '8px 12px', fontSize: '11px', color: '#64748b' }}>No lists yet</div>
          )}
          {lists && lists.map(list => {
            const done = addedWl.has(list.id);
            const loading = busy === String(list.id);
            return (
              <button
                key={list.id}
                onClick={() => !done && addToWatchlist(list.id)}
                disabled={loading || done}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  width: '100%', padding: '7px 12px', fontSize: '12px', textAlign: 'left',
                  background: done ? 'rgba(34,197,94,0.08)' : 'transparent',
                  color: done ? '#4ade80' : '#94a3b8',
                  border: 'none', borderBottom: '1px solid #0f172a', cursor: done ? 'default' : 'pointer',
                }}
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '120px' }}>
                  {list.name}
                </span>
                <span style={{ flexShrink: 0, marginLeft: '8px', fontSize: '10px', color: done ? '#4ade80' : '#334155' }}>
                  {done ? '✓ Added' : loading ? '…' : list.item_count}
                </span>
              </button>
            );
          })}

          {/* ── Trade Board section ────────────────────────── */}
          <div style={{ padding: '5px 10px 4px', fontSize: '9px', color: '#475569', fontWeight: 800, letterSpacing: '0.08em', background: '#0b1020', borderTop: '1px solid #1e293b', marginTop: '2px' }}>
            TRADE BOARD
          </div>
          <button
            onClick={() => !addedBoard && addToBoard()}
            disabled={busy === 'board' || addedBoard}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              width: '100%', padding: '7px 12px', fontSize: '12px', textAlign: 'left',
              background: addedBoard ? 'rgba(99,102,241,0.08)' : 'transparent',
              color: addedBoard ? '#818cf8' : '#94a3b8',
              border: 'none', cursor: addedBoard ? 'default' : 'pointer',
            }}
          >
            <span>Add to Radar</span>
            <span style={{ flexShrink: 0, marginLeft: '8px', fontSize: '10px', color: addedBoard ? '#818cf8' : '#334155' }}>
              {addedBoard ? '✓ Added' : busy === 'board' ? '…' : '📋'}
            </span>
          </button>

          {error && (
            <div style={{ padding: '5px 12px', fontSize: '10px', color: '#f87171', borderTop: '1px solid #1e293b' }}>{error}</div>
          )}
        </div>
      )}
    </div>
  );
}
