/**
 * Evidence-linked copilot panel for one stock.
 *
 * Every answer arrives with its citations, the time the data describes and what is missing.
 * Figures in the answer were rendered by the server from stored data — the model is not
 * allowed to type numbers — so the evidence list is the source of each one.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  askCopilot, cancelCopilot, deleteCopilotConversation, getCopilotConversation, listCopilotConversations,
  newRequestId, type CopilotMessage,
} from '@/lib/copilot';
import { isAiConfigured, getAiProviderLabel } from '@/lib/ai';

const SUGGESTIONS = [
  'Explain this signal using its evidence',
  'What changed since the previous report?',
  'What risks does the data show?',
  'What data is missing or stale?',
];

const STATE_COLOR: Record<string, string> = { OK: '#4ade80', STALE: '#fbbf24' };

function fmtTime(iso?: string | null): string {
  if (!iso) return 'unknown';
  return iso.slice(0, 16).replace('T', ' ') + ' UTC';
}

/** One assistant turn with its evidence. Exported for tests. */
export function CopilotAnswer({ m, defaultOpen = false }: { m: CopilotMessage; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const refs = m.evidence_refs ?? [];
  const lims = m.limitations ?? [];
  const rejected = m.status === 'rejected';
  return (
    <div style={{
      maxWidth: '88%', padding: '10px 14px', borderRadius: '10px', fontSize: '13px', lineHeight: 1.6,
      background: 'rgba(255,255,255,0.04)',
      border: `1px solid ${rejected ? 'rgba(251,191,36,0.35)' : '#1e293b'}`, color: '#cbd5e1',
    }}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px', flexWrap: 'wrap' }}>
        <span style={{
          fontSize: '10px', fontWeight: 700, padding: '1px 7px', borderRadius: '999px',
          background: rejected ? 'rgba(251,191,36,0.15)' : 'rgba(74,222,128,0.12)',
          color: rejected ? '#fbbf24' : '#4ade80',
        }}>
          {rejected ? 'Evidence check failed — showing stored data' : 'Figures from stored data'}
        </span>
        <span style={{ fontSize: '11px', color: '#64748b' }}>Data as of {fmtTime(m.data_as_of)}</span>
      </div>
      <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
      {rejected && (m.rejection_reasons ?? []).length > 0 && (
        <div style={{ marginTop: '6px', fontSize: '11px', color: '#94a3b8' }}>
          Why the draft was rejected: {(m.rejection_reasons ?? []).join('; ')}
        </div>
      )}
      {lims.length > 0 && (
        <div style={{ marginTop: '8px', fontSize: '11px', color: '#94a3b8' }}>
          <div style={{ fontWeight: 700, color: '#64748b' }}>Limitations</div>
          <ul style={{ margin: '2px 0 0 16px', padding: 0 }}>
            {lims.map((l, i) => <li key={i}>{l}</li>)}
          </ul>
        </div>
      )}
      {refs.length > 0 && (
        <div style={{ marginTop: '8px' }}>
          <button
            onClick={() => setOpen(o => !o)}
            style={{ background: 'transparent', border: 'none', color: '#a78bfa', cursor: 'pointer', fontSize: '11px', padding: 0 }}
          >
            {open ? '▾' : '▸'} Evidence ({refs.length})
          </button>
          {open && (
            <ul style={{ margin: '4px 0 0 0', padding: 0, listStyle: 'none', fontSize: '11px' }}>
              {refs.map(r => (
                <li key={r.key} style={{ padding: '4px 0', borderTop: '1px solid #1e293b' }}>
                  <span style={{ color: STATE_COLOR[r.state] ?? '#f87171', fontWeight: 700 }}>{r.state}</span>{' '}
                  <span style={{ color: '#e2e8f0' }}>{r.label}</span>: {r.display}
                  {r.note && <span style={{ color: '#fbbf24' }}> ({r.note})</span>}
                  <div style={{ color: '#64748b' }}>
                    {r.evidence.map(e => (
                      <span key={e.id} style={{ marginRight: '10px' }}>
                        {r.url ? <a href={r.url} target="_blank" rel="noopener noreferrer" style={{ color: '#818cf8' }}>{e.id}</a> : e.id}
                        {e.observed_period ? ` · ${e.observed_period}` : ''}
                        {e.published_at ? ` · published ${fmtTime(e.published_at)}` : ''}
                      </span>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default function CopilotPanel({ symbol }: { symbol: string }) {
  const [open, setOpen] = useState(false);
  const [convId, setConvId] = useState<number | null>(null);
  const [msgs, setMsgs] = useState<CopilotMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retention, setRetention] = useState<number | null>(null);
  /** The question that failed, kept with its request id so a retry is idempotent. */
  const [pending, setPending] = useState<{ q: string; rid: string } | null>(null);
  const [stopNote, setStopNote] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Reset on symbol change; resume the most recent conversation for this symbol when opened.
  useEffect(() => {
    setConvId(null); setMsgs([]); setError(''); setPending(null);
    abortRef.current?.abort();
  }, [symbol]);
  useEffect(() => {
    if (!open || convId !== null || msgs.length > 0) return;
    let live = true;
    listCopilotConversations(symbol)
      .then(async r => {
        if (!live) return;
        setRetention(r.retention_days);
        const latest = r.conversations[0];
        if (!latest) return;
        const full = await getCopilotConversation(latest.id);
        if (!live) return;
        setConvId(latest.id);
        setMsgs(full.messages);
      })
      .catch(() => { /* history is a convenience; asking still works */ });
    return () => { live = false; };
  }, [open, symbol, convId, msgs.length]);

  const scroll = () => setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 60);

  async function send(question: string, rid: string) {
    if (!question.trim() || loading) return;
    setError('');
    setStopNote(false);
    setLoading(true);
    if (!pending || pending.rid !== rid) {
      setMsgs(prev => [...prev, { role: 'user', content: question, request_id: rid }]);
    }
    setPending({ q: question, rid });
    setInput('');
    scroll();
    const ctl = new AbortController();
    abortRef.current = ctl;
    try {
      const out = await askCopilot({ symbol, question, requestId: rid, conversationId: convId }, ctl.signal);
      setConvId(out.conversation_id);
      setMsgs(prev => prev.some(m => m.id === out.message.id) ? prev : [...prev, out.message]);
      setPending(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Copilot request failed');
    } finally {
      setLoading(false);
      abortRef.current = null;
      scroll();
    }
  }

  /** Tell the server first (it can skip the repair call), then stop waiting in the browser. */
  async function stop() {
    const rid = pending?.rid;
    if (rid) await cancelCopilot(rid).catch(() => undefined);
    abortRef.current?.abort();
    // No same-id Retry after a stop: the stopped call may still be running, and the server
    // refuses to start a second one for the same request. Asking again uses a new request id.
    setPending(null);
    setStopNote(true);
  }

  async function removeConversation() {
    if (convId === null) { setMsgs([]); return; }
    if (!window.confirm('Delete this conversation permanently?')) return;
    try {
      await deleteCopilotConversation(convId);
      setConvId(null); setMsgs([]); setPending(null); setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  }

  function startNew() {
    setConvId(null); setMsgs([]); setPending(null); setError('');
  }

  return (
    <div style={{ borderRadius: '12px', border: '1px solid rgba(167,139,250,0.25)', background: 'rgba(15,23,42,0.95)', overflow: 'hidden' }}>
      <div style={{ height: '3px', background: 'linear-gradient(90deg,#a78bfa,#c4b5fd,#a78bfa)' }} />
      <button
        onClick={() => setOpen(o => !o)}
        style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', background: 'transparent', border: 'none', cursor: 'pointer' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '15px' }}>🤖</span>
          <span style={{ fontSize: '14px', fontWeight: 700, color: '#c4b5fd' }}>Ask the copilot about {symbol}</span>
          {isAiConfigured()
            ? <span style={{ fontSize: '10px', padding: '1px 7px', borderRadius: '999px', background: 'rgba(167,139,250,0.15)', color: '#a78bfa', fontWeight: 700 }}>{getAiProviderLabel()} · cited</span>
            : <span style={{ fontSize: '11px', color: '#475569' }}>— configure in Settings</span>}
        </div>
        <span style={{ color: '#475569', fontSize: '12px', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>▼</span>
      </button>

      {open && (
        <div style={{ borderTop: '1px solid #1e293b' }}>
          {!isAiConfigured() ? (
            <div style={{ padding: '20px', textAlign: 'center', fontSize: '13px', color: '#475569' }}>
              No AI provider configured.{' '}
              <a href="/settings" style={{ color: '#a78bfa', textDecoration: 'none' }}>Go to Settings → AI Assistant</a>.
            </div>
          ) : (
            <>
              <div style={{ padding: '8px 16px', fontSize: '11px', color: '#64748b', borderBottom: '1px solid #1e293b' }}>
                Read-only. Answers use this platform&apos;s stored prices, signals, K-Score, earnings date, reports and headlines;
                every figure is cited. It cannot place orders or change alerts, and its scores are not probabilities of profit.
                {retention !== null && ` Conversations are kept ${retention} days after last use.`}
              </div>

              {msgs.length === 0 && (
                <div style={{ padding: '12px 16px', display: 'flex', gap: '6px', flexWrap: 'wrap', borderBottom: '1px solid #1e293b' }}>
                  {SUGGESTIONS.map(q => (
                    <button key={q} onClick={() => send(q, newRequestId())}
                      style={{ fontSize: '11px', padding: '4px 10px', borderRadius: '6px', background: 'rgba(167,139,250,0.1)', border: '1px solid rgba(167,139,250,0.2)', color: '#a78bfa', cursor: 'pointer' }}>
                      {q}
                    </button>
                  ))}
                </div>
              )}

              {(msgs.length > 0 || error) && (
                <div style={{ maxHeight: '440px', overflowY: 'auto', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {msgs.map((m, i) => (
                    <div key={m.id ?? `${m.request_id}-${i}`} style={{ display: 'flex', flexDirection: m.role === 'user' ? 'row-reverse' : 'row' }}>
                      {m.role === 'user' ? (
                        <div style={{ maxWidth: '80%', padding: '10px 14px', borderRadius: '10px', fontSize: '13px', background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.3)', color: '#c4b5fd', whiteSpace: 'pre-wrap' }}>
                          {m.content}
                        </div>
                      ) : <CopilotAnswer m={m} />}
                    </div>
                  ))}
                  {loading && (
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', fontSize: '13px', color: '#475569' }}>
                      ⟳ Checking the evidence…
                      <button onClick={stop}
                        title="Stops any further model call for this question. A call already running still completes and counts toward usage; its answer appears when you reopen this conversation."
                        style={{ fontSize: '11px', background: 'transparent', border: '1px solid #1e293b', color: '#64748b', borderRadius: '6px', padding: '2px 8px', cursor: 'pointer' }}>
                        Stop
                      </button>
                    </div>
                  )}
                  {stopNote && !loading && (
                    <div style={{ fontSize: '11px', color: '#94a3b8' }}>
                      Stopped waiting. No further model call will be made for this question, but one already running
                      still completes and counts toward usage — its answer appears when you reopen this conversation.
                    </div>
                  )}
                  {error && (
                    <div style={{ padding: '8px 12px', borderRadius: '8px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)', color: '#f87171', fontSize: '12px', display: 'flex', gap: '10px', alignItems: 'center' }}>
                      <span>{error}</span>
                      {pending && !loading && (
                        <button onClick={() => send(pending.q, pending.rid)}
                          style={{ fontSize: '11px', background: 'transparent', border: '1px solid rgba(239,68,68,0.4)', color: '#f87171', borderRadius: '6px', padding: '2px 8px', cursor: 'pointer' }}>
                          Retry
                        </button>
                      )}
                    </div>
                  )}
                  <div ref={bottomRef} />
                </div>
              )}

              <div style={{ padding: '12px 16px', borderTop: '1px solid #1e293b', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <input
                  value={input}
                  maxLength={1000}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input.trim(), newRequestId()); } }}
                  placeholder={`Ask about ${symbol}…`}
                  style={{ flex: '1 1 220px', background: '#0f172a', border: '1px solid #1e293b', borderRadius: '8px', padding: '9px 12px', fontSize: '13px', color: '#e2e8f0', outline: 'none' }}
                />
                <button
                  onClick={() => send(input.trim(), newRequestId())}
                  disabled={loading || !input.trim()}
                  style={{
                    padding: '9px 18px', borderRadius: '8px', fontSize: '13px', fontWeight: 700, border: 'none',
                    cursor: loading || !input.trim() ? 'not-allowed' : 'pointer',
                    background: loading || !input.trim() ? '#1e293b' : 'linear-gradient(135deg,#7c3aed,#a78bfa)',
                    color: loading || !input.trim() ? '#475569' : '#fff',
                  }}
                >
                  Ask
                </button>
                {msgs.length > 0 && (
                  <>
                    <button onClick={startNew} disabled={loading}
                      style={{ padding: '9px 12px', borderRadius: '8px', background: 'transparent', border: '1px solid #1e293b', color: '#64748b', cursor: 'pointer', fontSize: '12px' }}>
                      New
                    </button>
                    <button onClick={removeConversation} disabled={loading}
                      style={{ padding: '9px 12px', borderRadius: '8px', background: 'transparent', border: '1px solid #1e293b', color: '#64748b', cursor: 'pointer', fontSize: '12px' }}>
                      Delete
                    </button>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
