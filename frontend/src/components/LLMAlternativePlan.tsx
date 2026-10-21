import React, { useEffect, useRef, useState } from 'react';
import { askCopilot, cancelCopilot, newRequestId, type CopilotMessage, type PlanInputs, type TradeSetup } from '@/lib/copilot';
import type { SelectedPlanLine } from '@/lib/selectedPlanLines';
import { isAiConfigured, getAiProviderLabel } from '@/lib/ai';
import { CopilotAnswer } from './CopilotPanel';
import TradeSetupSummary from './TradeSetupSummary';

const RECOVERY_DELAY_MS = 4000;
const RECOVERY_ATTEMPTS = 60;   // about 4 minutes, longer than the server's model timeout

/** A second interpretation of the same snapshot, never independent corroboration. */
export default function LLMAlternativePlan({ symbol, inputs, fingerprint, unavailable, currency = null, compact = false, onSelectionChange, onLevelSelect, onPlanChanged, autoStart = false }: {
  symbol: string; inputs: PlanInputs; fingerprint: string; unavailable: boolean; currency?: string | null;
  compact?: boolean; onSelectionChange?: (setups: TradeSetup[]) => void; onLevelSelect?: (line: SelectedPlanLine) => void;
  /** The server refused the comparison because the plan's data changed since it was built (409).
   *  Retrying with the same snapshot can never succeed, so the parent rebuilds the plan. */
  onPlanChanged?: () => void;
  /** Start one generation on mount (used once, right after an automatic plan refresh). */
  autoStart?: boolean;
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
    onSelectionChange?.([]);
    request.current = null;
  }
  async function generate() {
    if (active.current) return;
    const id = request.current ?? newRequestId(); request.current = id;
    const pending = { id, ctl: new AbortController() }; active.current = pending;
    setBusy(true); setError(''); setCancelled(false); setMessage(null);
    onSelectionChange?.([]);
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
      if (out.message.status === 'grounded' && (!Array.isArray(out.message.plan_setups) ||
          (out.message.selected_setup_id !== 'wait' && !out.message.plan_setups.some(s =>
            s.id === out.message.selected_setup_id && ['conditional', 'exit_triggered'].includes(s.state))))) {
        throw new Error('The model did not select a validated trade setup. Generate a new comparison.');
      }
      setMessage(out.message); request.current = null;
      onSelectionChange?.(out.message.status === 'grounded' && out.message.selected_setup_id !== 'wait'
        ? (out.message.plan_setups ?? []).filter(s => s.id === out.message.selected_setup_id) : []);
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
    <p>A model-selected scenario using the same prices and position inputs. It may agree with the rules-based plan; agreement is not independent confirmation.</p>
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
      {message.status === 'grounded' && message.selected_setup_id === 'wait' && <p>LLM selected wait: no new entry recommended. Conditional alternatives are shown below.</p>}
      {message.status === 'grounded' && message.selected_setup_id !== 'wait' && <p style={{ color: '#94a3b8' }}>
        The model chose the {(message.plan_setups ?? []).find(s => s.id === message.selected_setup_id)?.title ?? 'selected'} setup.
        Its prices are the same code-calculated levels the rules-based plan uses (the model is not allowed to invent prices), so they can match exactly.
        What differs is the model&apos;s reasoning and its reading of the playbook, below.</p>}
      {message.status === 'rejected' && <p role="alert">The model failed the evidence check. The stored-data fallback below is not a second plan.</p>}
      <section aria-label="Model reasoning and playbook">
        <p style={{ margin: '8px 0 4px' }}><strong>Model&apos;s reasoning and playbook</strong></p>
        <CopilotAnswer m={message} defaultOpen />
      </section>
      {message.status === 'grounded' && message.selected_setup_id !== 'wait' && <TradeSetupSummary currency={currency} compact={compact} onLevelSelect={onLevelSelect}
        setups={(message.plan_setups ?? []).filter(s => s.id === message.selected_setup_id)} />}
      {message.status === 'grounded' && message.selected_setup_id === 'wait' && <details><summary>Conditional alternatives — no active entry</summary>
        <TradeSetupSummary currency={currency} compact={compact} setups={message.plan_setups ?? []} />
      </details>}
    </>}
  </section>;
}
