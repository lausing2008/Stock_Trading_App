import React, { useEffect, useRef, useState } from 'react';
import { askCopilot, cancelCopilot, newRequestId, type CopilotMessage, type PlanInputs } from '@/lib/copilot';
import { isAiConfigured, getAiProviderLabel } from '@/lib/ai';
import { CopilotAnswer } from './CopilotPanel';
import TradeSetupSummary from './TradeSetupSummary';

/** A second interpretation of the same snapshot, never independent corroboration. */
export default function LLMAlternativePlan({ symbol, inputs, fingerprint, unavailable, currency = null }: {
  symbol: string; inputs: PlanInputs; fingerprint: string; unavailable: boolean; currency?: string | null;
}) {
  const [message, setMessage] = useState<CopilotMessage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [cancelled, setCancelled] = useState(false);
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
    request.current = null;
  }
  async function generate() {
    if (active.current) return;
    const id = request.current ?? newRequestId(); request.current = id;
    const pending = { id, ctl: new AbortController() }; active.current = pending;
    setBusy(true); setError(''); setCancelled(false); setMessage(null);
    try {
      const out = await askCopilot({ symbol, requestId: id,
        question: 'Give an alternative conditional Game Plan, including your preferred scenario, invalidation and when to wait.',
        planContext: { ...inputs, fingerprint } }, pending.ctl.signal);
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
    } catch (e) {
      if (alive.current && active.current === pending) setError(e instanceof Error ? e.message : 'Could not generate alternative');
    } finally {
      if (alive.current && active.current === pending) { active.current = null; setBusy(false); }
    }
  }
  const configured = isAiConfigured();
  return <section aria-label="LLM alternative plan" style={{ minWidth: 0, padding: 12, border: '1px solid #475569', borderRadius: 8 }}>
    <h4 style={{ marginTop: 0 }}>LLM alternative plan</h4>
    <p>A model-selected scenario using the same prices and position inputs. It may agree with the rules-based plan; agreement is not independent confirmation.</p>
    <p>Generated on request using {configured ? getAiProviderLabel() : 'your configured model'} and your copilot quota.</p>
    {!configured && <p>Select an AI provider in Settings to generate this plan.</p>}
    {unavailable && <p>A fresh, supported rules-based plan is required before comparing.</p>}
    <button disabled={busy || !configured || unavailable} onClick={generate}>
      {busy ? 'Generating alternative…' : error ? 'Retry LLM alternative' : message || cancelled ? 'Generate another LLM alternative' : 'Generate LLM alternative'}
    </button>
    {busy && <><button onClick={cancel}>Cancel generation</button><p>A model call already started may still be billed and stored in Copilot history.</p></>}
    {cancelled && <p role="status">Generation cancelled here. A call already started may still be billed and stored in Copilot history.</p>}
    {error && <p role="alert">{error}</p>}
    {message && <>
      <p>Model: {message.model ?? 'not reported'}</p>
      {message.status === 'grounded' && message.selected_setup_id === 'wait' && <p>LLM selected wait: no new entry recommended. Conditional alternatives are shown below.</p>}
      {message.status === 'grounded' && <TradeSetupSummary currency={currency}
        setups={(message.plan_setups ?? []).filter(s => message.selected_setup_id === 'wait' || s.id === message.selected_setup_id)} />}
      {message.status === 'rejected' && <p role="alert">The model failed the evidence check. The stored-data fallback below is not a second plan.</p>}
      <CopilotAnswer m={message} defaultOpen />
    </>}
  </section>;
}
