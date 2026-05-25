import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Link from 'next/link';
import { api, type TradePlan } from '@/lib/api';

const STAGES = ['watch', 'planning', 'active', 'closed'] as const;
type Stage = typeof STAGES[number];

const STAGE_META: Record<Stage, { label: string; color: string; bg: string; border: string; desc: string }> = {
  watch:    { label: 'Watch',    color: '#94a3b8', bg: 'rgba(148,163,184,0.06)', border: 'rgba(148,163,184,0.15)', desc: 'Tracking — no position yet' },
  planning: { label: 'Planning', color: '#818cf8', bg: 'rgba(129,140,248,0.06)', border: 'rgba(129,140,248,0.2)',  desc: 'AI plan generated, evaluating entry' },
  active:   { label: 'Active',   color: '#4ade80', bg: 'rgba(74,222,128,0.06)',  border: 'rgba(74,222,128,0.2)',   desc: 'In trade — monitoring' },
  closed:   { label: 'Closed',   color: '#475569', bg: 'rgba(71,85,105,0.05)',   border: 'rgba(71,85,105,0.15)',   desc: 'Trade completed' },
};

const SOURCE_LABEL: Record<string, string> = {
  gameplan: '📋 Game Plan',
  forecast: '🔮 Forecast',
  manual:   '✏️ Manual',
};

function fmt(n: number | null | undefined) {
  if (n == null) return '—';
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function relDate(iso: string) {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (d === 0) return 'Today';
  if (d === 1) return 'Yesterday';
  return `${d}d ago`;
}

/* ── Card ─────────────────────────────────────────────── */
function PlanCard({ plan, onStageChange, onDelete }: {
  plan: TradePlan;
  onStageChange: (id: number, stage: Stage) => void;
  onDelete: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const meta = STAGE_META[plan.stage as Stage] ?? STAGE_META.watch;
  const gp = plan.game_plan as Record<string, unknown> | null;

  return (
    <div style={{ borderRadius: '10px', border: `1px solid ${meta.border}`, background: '#0f172a', overflow: 'hidden', marginBottom: '8px' }}>
      {/* Colour stripe */}
      <div style={{ height: '2px', background: meta.color, opacity: 0.6 }} />

      <div style={{ padding: '12px 14px' }}>
        {/* Top row */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '6px' }}>
          <div>
            <Link href={`/stock/${plan.symbol}`} style={{ fontSize: '16px', fontWeight: 800, color: '#818cf8', textDecoration: 'none', fontFamily: 'ui-monospace, monospace' }}>
              {plan.symbol}
            </Link>
            {plan.source && (
              <span style={{ marginLeft: '8px', fontSize: '10px', color: '#475569' }}>{SOURCE_LABEL[plan.source] ?? plan.source}</span>
            )}
          </div>
          <div style={{ display: 'flex', gap: '4px' }}>
            <button onClick={() => setExpanded(e => !e)} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: '11px', padding: '2px 5px' }}>
              {expanded ? '▲' : '▼'}
            </button>
            {confirmDelete ? (
              <>
                <button onClick={() => onDelete(plan.id)} style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.35)', color: '#f87171', borderRadius: '4px', padding: '2px 8px', fontSize: '10px', cursor: 'pointer', fontWeight: 700 }}>Delete</button>
                <button onClick={() => setConfirmDelete(false)} style={{ background: 'transparent', border: '1px solid #1e293b', color: '#475569', borderRadius: '4px', padding: '2px 8px', fontSize: '10px', cursor: 'pointer' }}>Cancel</button>
              </>
            ) : (
              <button onClick={() => setConfirmDelete(true)} style={{ background: 'none', border: 'none', color: '#334155', cursor: 'pointer', fontSize: '12px', padding: '2px 5px' }} title="Delete">✕</button>
            )}
          </div>
        </div>

        {/* Prices */}
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '8px' }}>
          {plan.entry_price != null && (
            <div style={{ fontSize: '11px' }}>
              <span style={{ color: '#475569' }}>Entry </span>
              <span style={{ color: '#818cf8', fontWeight: 700, fontFamily: 'monospace' }}>{fmt(plan.entry_price)}</span>
            </div>
          )}
          {plan.stop_loss != null && (
            <div style={{ fontSize: '11px' }}>
              <span style={{ color: '#475569' }}>Stop </span>
              <span style={{ color: '#f87171', fontWeight: 700, fontFamily: 'monospace' }}>{fmt(plan.stop_loss)}</span>
            </div>
          )}
          {plan.take_profit != null && (
            <div style={{ fontSize: '11px' }}>
              <span style={{ color: '#475569' }}>Target </span>
              <span style={{ color: '#4ade80', fontWeight: 700, fontFamily: 'monospace' }}>{fmt(plan.take_profit)}</span>
            </div>
          )}
          {plan.entry_price != null && plan.stop_loss != null && plan.take_profit != null && plan.entry_price > plan.stop_loss && (
            <div style={{ fontSize: '11px' }}>
              <span style={{ color: '#475569' }}>R:R </span>
              <span style={{ color: '#facc15', fontWeight: 700 }}>{((plan.take_profit - plan.entry_price) / (plan.entry_price - plan.stop_loss)).toFixed(1)}x</span>
            </div>
          )}
        </div>

        {/* Notes */}
        {plan.notes && (
          <div style={{ fontSize: '11px', color: '#475569', lineHeight: 1.4, marginBottom: '8px', borderLeft: '2px solid #1e293b', paddingLeft: '8px' }}>
            {plan.notes.length > 120 && !expanded ? plan.notes.slice(0, 120) + '…' : plan.notes}
          </div>
        )}

        {/* Expanded: full game plan details */}
        {expanded && gp && (
          <div style={{ marginBottom: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {gp.title && <div style={{ fontSize: '11px', fontWeight: 700, color: '#e2e8f0' }}>{String(gp.title)}</div>}
            {Array.isArray(gp.entries) && gp.entries.map((e: Record<string, unknown>, i: number) => (
              <div key={i} style={{ fontSize: '10px', color: '#64748b', paddingLeft: '8px', borderLeft: '2px solid #1e293b' }}>
                <span style={{ color: '#818cf8', fontWeight: 700 }}>{String(e.label)}</span> ${Number(e.price).toFixed(2)} — {String(e.rationale)}
              </div>
            ))}
            {Array.isArray(gp.catalysts) && (
              <div style={{ fontSize: '10px', color: '#64748b' }}>
                {(gp.catalysts as string[]).map((c: string, i: number) => <div key={i}>› {c}</div>)}
              </div>
            )}
            {gp.risk && <div style={{ fontSize: '10px', color: '#fbbf24' }}>⚠ {String(gp.risk)}</div>}
          </div>
        )}

        {/* Footer: stage selector + date */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
          <div style={{ display: 'flex', gap: '3px' }}>
            {STAGES.map(s => (
              <button
                key={s}
                onClick={() => onStageChange(plan.id, s)}
                style={{ padding: '2px 7px', borderRadius: '4px', fontSize: '10px', fontWeight: plan.stage === s ? 700 : 400, cursor: plan.stage === s ? 'default' : 'pointer', border: `1px solid ${plan.stage === s ? STAGE_META[s].color : 'transparent'}`, background: plan.stage === s ? STAGE_META[s].bg : 'transparent', color: plan.stage === s ? STAGE_META[s].color : '#334155' }}
              >
                {STAGE_META[s].label}
              </button>
            ))}
          </div>
          <span style={{ fontSize: '10px', color: '#334155' }}>{relDate(plan.updated_at)}</span>
        </div>
      </div>
    </div>
  );
}

/* ── Add card form ─────────────────────────────────────── */
function AddCardForm({ onAdd }: { onAdd: (symbol: string, notes: string) => Promise<void> }) {
  const [symbol, setSymbol] = useState('');
  const [notes, setNotes] = useState('');
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol.trim()) return;
    setAdding(true);
    await onAdd(symbol.trim().toUpperCase(), notes.trim());
    setSymbol(''); setNotes(''); setAdding(false); setOpen(false);
  }

  if (!open) return (
    <button onClick={() => setOpen(true)} style={{ width: '100%', padding: '8px', borderRadius: '8px', border: '1px dashed #1e293b', background: 'transparent', color: '#334155', cursor: 'pointer', fontSize: '12px', marginBottom: '8px' }}>
      + Add card
    </button>
  );

  return (
    <form onSubmit={submit} style={{ marginBottom: '8px', padding: '10px 12px', borderRadius: '8px', border: '1px solid rgba(99,102,241,0.25)', background: 'rgba(15,23,42,0.9)' }}>
      <input
        autoFocus
        value={symbol}
        onChange={e => setSymbol(e.target.value)}
        placeholder="Symbol (e.g. AAPL)"
        style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid #1e293b', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', color: '#f1f5f9', outline: 'none', marginBottom: '6px', boxSizing: 'border-box' }}
      />
      <textarea
        value={notes}
        onChange={e => setNotes(e.target.value)}
        placeholder="Notes (optional)"
        rows={2}
        style={{ width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid #1e293b', borderRadius: '6px', padding: '6px 10px', fontSize: '11px', color: '#94a3b8', outline: 'none', resize: 'none', boxSizing: 'border-box', fontFamily: 'inherit', marginBottom: '6px' }}
      />
      <div style={{ display: 'flex', gap: '6px' }}>
        <button type="submit" disabled={!symbol.trim() || adding} style={{ flex: 1, padding: '6px', borderRadius: '6px', border: 'none', background: symbol.trim() ? 'linear-gradient(135deg,#4f46e5,#6366f1)' : '#1e293b', color: symbol.trim() ? '#fff' : '#475569', fontSize: '12px', fontWeight: 700, cursor: symbol.trim() ? 'pointer' : 'default' }}>
          {adding ? '…' : 'Add'}
        </button>
        <button type="button" onClick={() => setOpen(false)} style={{ padding: '6px 12px', borderRadius: '6px', border: '1px solid #1e293b', background: 'transparent', color: '#475569', fontSize: '12px', cursor: 'pointer' }}>Cancel</button>
      </div>
    </form>
  );
}

/* ── Main ─────────────────────────────────────────────── */
export default function BoardPage() {
  const { data, mutate, isLoading } = useSWR<TradePlan[]>('board', () => api.listBoard(), { revalidateOnFocus: false });

  const byStage = useMemo(() => {
    const m: Record<Stage, TradePlan[]> = { watch: [], planning: [], active: [], closed: [] };
    for (const p of data ?? []) {
      const s = (p.stage as Stage) in m ? (p.stage as Stage) : 'watch';
      m[s].push(p);
    }
    return m;
  }, [data]);

  async function handleStageChange(id: number, stage: Stage) {
    await api.updateBoardPlan(id, { stage });
    mutate();
  }

  async function handleDelete(id: number) {
    await api.deleteBoardPlan(id);
    mutate();
  }

  async function handleAdd(symbol: string, notes: string) {
    await api.createBoardPlan({ symbol, stage: 'watch', notes: notes || null, source: 'manual' });
    mutate();
  }

  const total = (data ?? []).length;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e2e8f0', marginBottom: '4px' }}>Trade Board</h1>
          <p style={{ fontSize: '12px', color: '#475569' }}>
            {total > 0 ? `${total} idea${total !== 1 ? 's' : ''} · drag-free Kanban` : 'Save game plans and forecast picks here'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', fontSize: '11px', color: '#334155' }}>
          {STAGES.map(s => (
            <span key={s} style={{ padding: '4px 10px', borderRadius: '6px', background: STAGE_META[s].bg, border: `1px solid ${STAGE_META[s].border}`, color: STAGE_META[s].color, fontWeight: 600 }}>
              {STAGE_META[s].label} {byStage[s].length > 0 && `(${byStage[s].length})`}
            </span>
          ))}
        </div>
      </div>

      {isLoading && <div style={{ color: '#475569', fontSize: '13px', padding: '40px 0', textAlign: 'center' }}>Loading board…</div>}

      {!isLoading && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '16px', alignItems: 'start' }}>
          {STAGES.map(stage => {
            const m = STAGE_META[stage];
            const cards = byStage[stage];
            return (
              <div key={stage} style={{ borderRadius: '12px', border: `1px solid ${m.border}`, background: '#080f1e', overflow: 'hidden' }}>
                {/* Column header */}
                <div style={{ padding: '10px 14px 8px', background: m.bg, borderBottom: `1px solid ${m.border}` }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: '13px', fontWeight: 700, color: m.color }}>{m.label}</span>
                    <span style={{ fontSize: '11px', color: m.color, opacity: 0.7, background: m.bg, border: `1px solid ${m.border}`, borderRadius: '10px', padding: '1px 7px' }}>{cards.length}</span>
                  </div>
                  <div style={{ fontSize: '10px', color: '#334155', marginTop: '2px' }}>{m.desc}</div>
                </div>

                {/* Cards */}
                <div style={{ padding: '10px' }}>
                  {stage === 'watch' && <AddCardForm onAdd={handleAdd} />}
                  {cards.length === 0 && stage !== 'watch' && (
                    <div style={{ textAlign: 'center', padding: '24px 0', fontSize: '11px', color: '#1e293b' }}>
                      Move cards here as your trade progresses
                    </div>
                  )}
                  {cards.map(plan => (
                    <PlanCard key={plan.id} plan={plan} onStageChange={handleStageChange} onDelete={handleDelete} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!isLoading && total === 0 && (
        <div style={{ marginTop: '24px', padding: '20px 24px', borderRadius: '10px', background: 'rgba(99,102,241,0.05)', border: '1px solid rgba(99,102,241,0.15)', fontSize: '12px', color: '#475569', lineHeight: 1.7 }}>
          <strong style={{ color: '#818cf8' }}>How to populate your board:</strong>
          <ul style={{ margin: '8px 0 0 16px', padding: 0 }}>
            <li>On any stock detail page — generate a <strong>Game Plan</strong> then click <strong>📌 Save</strong></li>
            <li>On the <Link href="/forecast" style={{ color: '#818cf8' }}>Forecast</Link> page — click <strong>📌 Save to Board</strong> on any pick</li>
            <li>Add manually using the <strong>+ Add card</strong> in the Watch column above</li>
          </ul>
        </div>
      )}
    </div>
  );
}
