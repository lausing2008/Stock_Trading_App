import React, { useEffect, useRef, useState } from 'react';
import { askCopilot, cancelCopilot, newRequestId, type CopilotMessage, type PlanInputs, type PlanProposal, type TradeSetup } from '@/lib/copilot';
import type { SelectedPlanLine } from '@/lib/selectedPlanLines';
import { isAiConfigured, getAiProviderLabel } from '@/lib/ai';
import { CopilotAnswer } from './CopilotPanel';
import TradeSetupSummary from './TradeSetupSummary';

const RECOVERY_DELAY_MS = 4000;
const RECOVERY_ATTEMPTS = 60;   // about 4 minutes, longer than the server's model timeout

/** A second interpretation of the same snapshot, never independent corroboration. */
const LEVEL_NAMES: Record<string, string> = { entry: 'Entry', stop: 'Stop', target1: 'Target 1', target2: 'Target 2' };
const ANCHOR_NAMES: Record<string, string> = { 'level.close': 'latest close', 'level.low20': '20-bar low', 'level.high20': '20-bar high',
  'level.sma50': '50-bar average', 'level.high252': 'year high', 'level.low252': 'year low' };

/** How each code-computed price follows from the model's chosen anchor. */
function Derivation({ p, currency }: { p: PlanProposal; currency: string | null }) {
  const f = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 4 }) + (currency ? ` ${currency}` : '');
  if (!p.derivation?.length) return null;
  return <details open={p.state === 'rejected'}><summary style={{ cursor: 'pointer', color: '#a5b4fc' }}>How each price was derived</summary>
    <ul aria-label="Proposal derivation">{p.derivation.map(d => <li key={d.level}>
      {LEVEL_NAMES[d.level] ?? d.level}: {ANCHOR_NAMES[d.anchor] ?? d.anchor} ({f(d.anchor_price)}){d.atr_multiple ? ` ${d.atr_multiple > 0 ? '+' : '−'} ${Math.abs(d.atr_multiple)} × ATR (${f(d.atr)})` : ''} = {f(d.price)}
    </li>)}</ul>
  </details>;
}

export default function LLMAlternativePlan({ symbol, inputs, fingerprint, unavailable, currency = null, compact = false, onSelectionChange, onLevelSelect, onPlanChanged, onProposal, autoStart = false }: {
  symbol: string; inputs: PlanInputs; fingerprint: string; unavailable: boolean; currency?: string | null;
  compact?: boolean; onSelectionChange?: (setups: TradeSetup[]) => void; onLevelSelect?: (line: SelectedPlanLine) => void;
  /** The server refused the comparison because the plan's data changed since it was built (409).
   *  Retrying with the same snapshot can never succeed, so the parent rebuilds the plan. */
  onPlanChanged?: () => void;
  /** Start one generation on mount (used once, right after an automatic plan refresh). */
  autoStart?: boolean;
  /** The latest generated proposal and its message (null while generating or cancelled). */
  onProposal?: (value: { proposal: PlanProposal; message: CopilotMessage } | null) => void;
}) {
  const [message, setMessage] = useState<CopilotMessage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [cancelled, setCancelled] = useState(false);
  const [recovering, setRecovering] = useState(0);
  const alive = useRef(true);
  const request = useRef<string | null>(null);
  const active = useRef<{ id: string; ctl: AbortController } | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      const pending = active.current;
      active.current = null;
      if (pending) {
        pending.ctl.abort();
        void cancelCopilot(pending.id).catch(() => {});
      }
    };
  }, []);
  function cancel() {
    const pending = active.current;
    if (!pending) return;
    active.current = null; pending.ctl.abort();
    void cancelCopilot(pending.id).catch(() => {});
    setBusy(false); setCancelled(true);
    onSelectionChange?.([]); onProposal?.(null);
    request.current = null;
  }
  async function generate() {
    if (active.current) return;
    const id = request.current ?? newRequestId(); request.current = id;
    const pending = { id, ctl: new AbortController() }; active.current = pending;
    setBusy(true); setError(''); setCancelled(false); setMessage(null);
    onSelectionChange?.([]); onProposal?.(null);
    try {
      const ask = () => askCopilot({ symbol, requestId: id,
        question: 'Give an alternative conditional Game Plan, including your preferred scenario, invalidation and when to wait.',
        planContext: { ...inputs, fingerprint } }, pending.ctl.signal);
      let out: Awaited<ReturnType<typeof askCopilot>> | null = null;
      // A dropped connection does not stop the server: the answer is stored under this request
      // id, and re-sending the SAME id returns it (no second model call) or reports that it is
      // still being answered. So recover by re-asking with the same id instead of failing.
      for (let attempt = 0; out === null; attempt++) {
        try {
          out = await ask();
        } catch (e) {
          if (!alive.current || active.current !== pending || pending.ctl.signal.aborted) throw e;
          const status = (e as { status?: number }).status;
          const text = e instanceof Error ? e.message : '';
          const stillRunning = status === 409 && /already being answered/i.test(text);
          const dropped = (status === undefined && !/cancelled|took too long/i.test(text)) || status === 502 || status === 504;
          if (!(stillRunning || dropped) || attempt >= RECOVERY_ATTEMPTS) throw e;
          setRecovering(attempt + 1);
          await new Promise(r => setTimeout(r, RECOVERY_DELAY_MS));
          if (!alive.current || active.current !== pending || pending.ctl.signal.aborted) return;
        }
      }
      setRecovering(0);
      if (!alive.current || active.current !== pending) return;
      if (out.message.packet_fingerprint !== fingerprint) {
        throw new Error('The plan snapshot changed. Refresh the rules-based plan before comparing.');
      }
      const proposal = out.message.plan_proposal ?? null;
      if (out.message.status === 'grounded' && !proposal) {
        throw new Error('The model did not return a plan proposal. Generate a new comparison.');
      }
      setMessage(out.message); request.current = null;
      // Only a proposal that passed every code check is plotted or offered for saving.
      onSelectionChange?.(proposal && proposal.state === 'conditional' ? [proposal as unknown as TradeSetup] : []);
      onProposal?.(proposal ? { proposal, message: out.message } : null);
    } catch (e) {
      if (alive.current && active.current === pending && (e as { status?: number }).status === 409
          && /plan changed/i.test(e instanceof Error ? e.message : '') && onPlanChanged) {
        active.current = null; setBusy(false); request.current = null; onPlanChanged(); return;
      }
      if (alive.current && active.current === pending) setError(e instanceof Error ? e.message : 'Could not generate alternative');
    } finally {
      if (alive.current && active.current === pending) { active.current = null; setBusy(false); setRecovering(0); }
    }
  }
  const configured = isAiConfigured();
  const autoStarted = useRef(false);
  useEffect(() => {
    if (autoStart && !autoStarted.current && configured && !unavailable) { autoStarted.current = true; void generate(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart, configured, unavailable]);
  return <section aria-label="LLM alternative plan" style={{ minWidth: 0, padding: 12, border: '1px solid #475569', borderRadius: 8 }}>
    <h4 style={{ marginTop: 0 }}>LLM alternative plan</h4>
    <p>The model proposes its own entry, stop and targets from the same evidence snapshot. It may agree with the rules-based plan; agreement is not independent confirmation, and different numbers are not evidence of a better plan.</p>
    <p style={{ color: '#94a3b8' }}>The model names evidence levels (for example the 20-bar low minus half an ATR); code computes every price and checks direction, reward:risk, sizing, freshness and the trend rule. A proposal that fails is shown with the reasons and cannot be saved.</p>
    <p>Generated on request using {configured ? getAiProviderLabel() : 'your configured model'} and your copilot quota.</p>
    {!configured && <p>Select an AI provider in Settings to generate this plan.</p>}
    {unavailable && <p>A fresh, supported rules-based plan is required before comparing.</p>}
    <button disabled={busy || !configured || unavailable} onClick={generate}>
      {busy ? 'Generating alternative…' : error ? 'Retry LLM alternative' : message || cancelled ? 'Generate another LLM alternative' : 'Generate LLM alternative'}
    </button>
    {busy && recovering > 0 && <p role="status">Connection dropped while the model was answering. The answer is still being generated on the server; waiting for it (check {recovering} of {RECOVERY_ATTEMPTS}). This re-asks for the same answer and does not start a new model call.</p>}
    {busy && <><button onClick={cancel}>Cancel generation</button><p>A model call already started may still be billed and stored in Copilot history.</p></>}
    {cancelled && <p role="status">Generation cancelled here. A call already started may still be billed and stored in Copilot history.</p>}
    {error && <p role="alert">{error}</p>}
    {message && <>
      <p>Model: {message.model ?? 'not reported'}</p>
      {message.status === 'rejected' && <p role="alert">The model failed the evidence check. The stored-data fallback below is not a second plan.</p>}
      {message.status === 'grounded' && message.plan_proposal && (() => {
        const p = message.plan_proposal;
        if (p.state === 'wait') return <p role="status">{p.reasons?.[0] ?? 'The model proposes waiting: no new entry now.'}</p>;
        if (p.state === 'exit_triggered') return <p role="alert" style={{ color: '#fca5a5' }}><strong>Exit rule met:</strong> {p.reasons.join(' ')}</p>;
        if (p.state === 'rejected') return <div role="alert" style={{ color: '#fca5a5' }}>
          <p><strong>The model&apos;s proposal failed the code checks and cannot be selected or saved:</strong></p>
          <ul>{p.reasons.map(r => <li key={r}>{r}</li>)}</ul>
          <Derivation p={p} currency={currency} />
        </div>;
        return <>
          <TradeSetupSummary currency={currency} compact={compact} onLevelSelect={onLevelSelect} setups={[p as unknown as TradeSetup]} />
          <Derivation p={p} currency={currency} />
        </>;
      })()}
      {message.status === 'grounded' && !message.plan_proposal && message.selected_setup_id && <>
        {/* Answers stored before model-proposed levels: the model selected a rules-based setup. */}
        <p style={{ color: '#94a3b8' }}>Earlier comparison: the model selected a rules-based setup ({message.selected_setup_id}); it did not propose its own levels.</p>
        {message.selected_setup_id !== 'wait' && <TradeSetupSummary currency={currency} compact={compact}
          setups={(message.plan_setups ?? []).filter(s => s.id === message.selected_setup_id)} />}
      </>}
      <section aria-label="Model reasoning and playbook">
        <p style={{ margin: '8px 0 4px' }}><strong>{message.narration === 'code' ? 'Explanation (written by the platform)' : 'Model\u2019s reasoning and playbook'}</strong></p>
        {message.narration === 'code' && <p role="status" style={{ color: '#fbbf24' }}>
          The model&apos;s own explanation failed the evidence check ({(message.narration_rejection_reasons ?? []).join('; ') || 'no detail'}).
          Its proposal had already passed the code checks, so the numbers are unchanged; this explanation is written by the platform from that validated plan.</p>}
        <CopilotAnswer m={message} defaultOpen />
      </section>
    </>}
  </section>;
}
