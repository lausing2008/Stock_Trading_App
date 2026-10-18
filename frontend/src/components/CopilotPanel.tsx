/**
 * Evidence-linked copilot panel for one stock.
 *
 * Every answer arrives with its citations, the time the data describes and what is missing.
 * Figures in the answer were rendered by the server from stored data — the model is not
 * allowed to type numbers — so the evidence list is the source of each one.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  askCopilot, cancelCopilot, confirmCopilotDraft, deleteCopilotConversation, getCopilotConversation,
  getCopilotEvidence, listCopilotConversations, newRequestId, type CopilotDraft, type CopilotEvidence,
  type CopilotMessage,
} from '@/lib/copilot';
import { isAiConfigured, getAiProviderLabel } from '@/lib/ai';

import { parseChartCommand, type ChartCommand, type ChartSnapshot } from '@/lib/chartCommands';

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

/** A proposed action: exact parameters, disclosed effects, saved only on the user's click. */
export function DraftCard({ messageId, draft }: { messageId?: number; draft: CopilotDraft }) {
  const [d, setD] = useState(draft);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const expired = d.status !== 'saved' && Date.now() > Date.parse(d.expires_at + (d.expires_at.endsWith('Z') ? '' : 'Z'));
  async function save() {
    if (messageId === undefined) return;
    setBusy(true); setErr('');
    try {
      setD((await confirmCopilotDraft(messageId, d.id)).draft);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save');
    } finally {
      setBusy(false);
    }
  }
  return (
    <div data-testid="draft-card" style={{ marginTop: '8px', padding: '8px 10px', borderRadius: '8px', border: '1px dashed rgba(167,139,250,0.5)', background: 'rgba(167,139,250,0.06)', fontSize: '12px' }}>
      <div style={{ fontWeight: 700, color: '#c4b5fd' }}>
        {d.status === 'saved' ? 'Saved' : d.status === 'saving' ? 'Save not yet confirmed' : 'Draft — not saved'}: {d.summary}
      </div>
      {d.threshold_source && (
        <div style={{ color: '#94a3b8' }}>
          Threshold {d.threshold_source.display}
          {d.threshold_source.kind === 'packet' ? ` — from stored ${d.threshold_source.key}${d.threshold_source.state === 'STALE' ? ' (stale)' : ''}` : ' — the number you typed'}
        </div>
      )}
      {(d.rule_notes ?? []).map((note, i) => <div key={`rule-${i}`} style={{ color: '#94a3b8', marginTop: '4px' }}>{note}</div>)}
      <ul style={{ margin: '4px 0 0 16px', padding: 0, color: '#cbd5e1' }}>
        {d.side_effects.map((x, i) => <li key={i}>{x}</li>)}
      </ul>
      {d.status !== 'saved' && (
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '6px' }}>
          <button onClick={save} disabled={busy || (expired && d.status !== 'saving') || messageId === undefined}
            style={{ padding: '4px 12px', borderRadius: '6px', border: '1px solid #7c3aed', background: 'rgba(124,58,237,0.25)', color: '#e9d5ff', cursor: busy || expired ? 'not-allowed' : 'pointer', fontSize: '12px' }}>
            {busy ? 'Saving…' : 'Save'}
          </button>
          <span style={{ color: '#64748b', fontSize: '11px' }}>
            {d.status === 'saving'
              ? 'A save was started but not confirmed. Press Save to check; it will not create a duplicate.'
              : expired ? 'Expired — ask again for a fresh draft.' : `Expires ${fmtTime(d.expires_at)}`}
          </span>
        </div>
      )}
      {err && <div style={{ color: '#f87171', marginTop: '4px' }}>{err}</div>}
    </div>
  );
}

/** One assistant turn with its evidence. Exported for tests. */
export function CopilotAnswer({ m, defaultOpen = false }: { m: CopilotMessage; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [full, setFull] = useState<Record<string, CopilotEvidence[]>>({});
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
          {rejected ? 'Evidence check failed — showing stored data' : 'Cited figures from stored data'}
        </span>
        <span style={{ fontSize: '11px', color: '#64748b' }}>Prices as of {fmtTime(m.price_as_of)}</span>
      </div>
      <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '6px' }}>
        {/* What the check proves, and what it does not (independent review R1). */}
        Every figure was checked against stored data; the wording around the figures is the model&apos;s and is not verified.
      </div>
      <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
      {(m.drafts ?? []).map(d => <DraftCard key={d.id} messageId={m.id} draft={d} />)}
      {(m.dropped_drafts ?? []).length > 0 && (
        <div style={{ marginTop: '6px', fontSize: '11px', color: '#94a3b8' }}>
          Not drafted: {(m.dropped_drafts ?? []).map(x => `${x.kind} — ${x.reason}`).join('; ')}
        </div>
      )}
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
                    {(full[r.key] ?? r.evidence).map(e => (
                      <span key={e.id} style={{ marginRight: '10px' }}>
                        {r.url ? <a href={r.url} target="_blank" rel="noopener noreferrer" style={{ color: '#818cf8' }}>{e.id}</a> : e.id}
                        {e.observed_period ? ` · ${e.observed_period}` : ''}
                        {e.published_at ? ` · published ${fmtTime(e.published_at)}` : ''}
                      </span>
                    ))}
                    {m.id !== undefined && (r.evidence_count ?? 0) > r.evidence.length && !full[r.key] && (
                      <button
                        onClick={() => getCopilotEvidence(m.id as number, r.key)
                          .then(x => setFull(f => ({ ...f, [r.key]: x.evidence })))
                          .catch(() => undefined)}
                        style={{ background: 'transparent', border: 'none', color: '#a78bfa', cursor: 'pointer', fontSize: '11px', padding: 0 }}>
                        show all {r.evidence_count}
                      </button>
                    )}
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

export default function CopilotPanel({ symbol, onChartCommand }: { symbol: string; onChartCommand?: (command: ChartCommand, symbol: string) => Promise<ChartSnapshot> }) {
  const [open, setOpen] = useState(false);
  const [convId, setConvId] = useState<number | null>(null);
  const [msgs, setMsgs] = useState<CopilotMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [retention, setRetention] = useState<number | null>(null);
  /** The question that failed, kept with its request id so a retry is idempotent. */
  const [pending, setPending] = useState<{ q: string; rid: string } | null>(null);
  /** What Stop actually achieved — confirmed by the server, or only in this browser. */
  const [stopNote, setStopNote] = useState<'' | 'confirmed' | 'nothing' | 'unconfirmed'>('');
  /**
   * 'resume' loads the latest conversation for this symbol when the panel opens; 'fresh' is set
   * by New and must not. R3 (independent review): New used to clear the conversation, which
   * satisfied the history effect's "nothing loaded" condition, so it reloaded the conversation
   * the user had just left and the next question continued it.
   */
  const [mode, setMode] = useState<'resume' | 'fresh'>('resume');
  /**
   * Bumped whenever the panel's subject changes (symbol, New, Delete). Every async result
   * carries the generation it started in and is dropped if the panel has moved on, so a late
   * answer for AAPL can never land in MSFT's conversation.
   */
  const gen = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    gen.current += 1;
    setConvId(null); setMsgs([]); setError(''); setPending(null); setStopNote(''); setMode('resume');
    abortRef.current?.abort();
  }, [symbol]);

  // On every OPEN: resume the latest conversation (resume mode, nothing loaded yet), or refresh
  // the one on screen — a stopped request's paid answer is saved server-side and must appear.
  useEffect(() => {
    if (!open || loading) return;
    const g = gen.current;
    let live = true;
    const load = async () => {
      let id = convId;
      if (id === null) {
        if (mode !== 'resume') return;
        const r = await listCopilotConversations(symbol);
        if (!live || g !== gen.current) return;
        setRetention(r.retention_days);
        id = r.conversations[0]?.id ?? null;
        if (id === null) return;
      }
      const full = await getCopilotConversation(id);
      if (!live || g !== gen.current) return;
      setRetention(full.retention_days);
      setConvId(id);
      setMsgs(full.messages);
    };
    load().catch(() => { /* history is a convenience; asking still works */ });
    return () => { live = false; };
    // Deliberately keyed on open/symbol/mode only: re-running on convId or msgs is exactly the
    // reload-after-New defect this replaces.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, symbol, mode]);

  const scroll = () => setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 60);

  async function send(question: string, rid: string) {
    if (!question.trim() || loading) return;
    const g = gen.current;
    const chartCommand = parseChartCommand(question);
    if (chartCommand && onChartCommand) {
      setLoading(true); setInput(''); setError('');
      try {
        const state = await onChartCommand(chartCommand, symbol);
        if (g !== gen.current) return;
        setMsgs(prev => [...prev, {role:'user', content:question, request_id:rid}, {role:'assistant',localAction:true,content:`Chart updated for ${state.symbol}. Range: ${state.range}.`,request_id:rid}]);
      } catch (e) { if (g === gen.current) setError(e instanceof Error ? e.message : 'Chart command failed'); }
      finally { if (g === gen.current) setLoading(false); }
      return;
    }
    setError('');
    setStopNote('');
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
      if (g !== gen.current) return;          // the panel moved on; this result is not for it
      // Mode is left alone: it only matters while no conversation is loaded, and flipping it
      // here would re-run the load effect and replace the messages just received.
      setConvId(out.conversation_id);
      setMsgs(prev => prev.some(m => m.id === out.message.id) ? prev : [...prev, out.message]);
      setPending(null);
    } catch (e) {
      if (g !== gen.current) return;
      setError(e instanceof Error ? e.message : 'Copilot request failed');
    } finally {
      if (g === gen.current) {
        setLoading(false);
        abortRef.current = null;
        scroll();
      }
    }
  }

  /** Ask the server to skip further model calls, then stop waiting. Report what happened. */
  async function stop() {
    const rid = pending?.rid;
    let outcome: 'confirmed' | 'nothing' | 'unconfirmed' = 'unconfirmed';
    if (rid) {
      try {
        outcome = (await cancelCopilot(rid)).cancelled ? 'confirmed' : 'nothing';
      } catch {
        outcome = 'unconfirmed';
      }
    }
    abortRef.current?.abort();
    // No same-id Retry after a stop: the stopped call may still be running, and the server
    // refuses to start a second one for the same request. Asking again uses a new request id.
    setPending(null);
    setStopNote(outcome);
  }

  async function removeConversation() {
    if (convId === null) { startNew(); return; }
    if (!window.confirm('Delete this conversation permanently?')) return;
    try {
      await deleteCopilotConversation(convId);
      startNew();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  }

  function startNew() {
    gen.current += 1;
    setMode('fresh');
    setConvId(null); setMsgs([]); setPending(null); setError(''); setStopNote('');
    setLoading(false);
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
                Chart commands: “show RSI”, “hide MACD”, “show 1Y”, “set 5m”, “undo chart”. Trading objects still require confirmation. Answers use this platform&apos;s stored prices, signals, K-Score, earnings date, reports and headlines;
                figures are checked against that data and cited; the wording is the model&apos;s. It cannot place orders or change alerts,
                and its scores are not probabilities of profit.
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
                      ) : m.localAction ? <p role="status">{m.content}</p> : <CopilotAnswer m={m} />}
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
                    <div style={{ fontSize: '11px', color: '#94a3b8' }} data-testid="stop-note">
                      {stopNote === 'confirmed' && 'Cancellation confirmed: no further model call will be made for this question. A call already running still completes and counts toward usage; its answer appears when you reopen this conversation.'}
                      {stopNote === 'nothing' && 'Stopped waiting. The server had nothing left to cancel — the answer may already be saved; reopen this conversation to see it.'}
                      {stopNote === 'unconfirmed' && 'Stopped waiting in this browser only. Cancellation could not be confirmed with the server, so the answer may still be generated, counted and saved.'}
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
