import DailyCloseAlerts from './DailyCloseAlerts';
import React, { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { draftPlanLevel, getStockPlan, newRequestId, type CopilotMessage, type PlanInputs,
  type PlanItem, type PlanLevel, type StockPlan } from '@/lib/copilot';
import { DraftCard } from './CopilotPanel';
import LLMAlternativePlan from './LLMAlternativePlan';
import TradeSetupSummary from './TradeSetupSummary';

export interface PlanChartLine { price: number; label: string; role: string }
const fieldStyle = { width: '100%', padding: '6px', background: '#0f172a', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 5 };
const buttonStyle = { padding: '6px 10px', border: '1px solid #475569', background: '#1e293b', color: '#e2e8f0', borderRadius: 6, cursor: 'pointer' };

/** Same code-written rules as the copilot; no AI key or model call. */
export default function StockGamePlanCard({ symbol, onLevelsChange, onBoardSaved }: {
  symbol: string; onLevelsChange: (lines: PlanChartLine[]) => void; onBoardSaved?: () => void;
}) {
  const [mode, setMode] = useState<'entry' | 'holding' | 'forecast'>('entry');
  const [values, setValues] = useState({ entry: '', shares: '', stop: '', risk: '' });
  const [data, setData] = useState<StockPlan | null>(null);
  const [inputs, setInputs] = useState<PlanInputs>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [chart, setChart] = useState(true);
  const [draft, setDraft] = useState<CopilotMessage | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [boardSetupId, setBoardSetupId] = useState('');
  const [evidence, setEvidence] = useState<PlanItem | null>(null);
  const [perspective, setPerspective] = useState<'rules' | 'llm'>('rules');
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const draftRequest = useRef<{ level: string; id: string } | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; generation.current++; abort.current?.abort(); };
  }, []);
  function reset() {
    generation.current++; abort.current?.abort(); setBusy(false);
    setData(null); setDraft(null); setEvidence(null); setSaved(false); setError(''); setPerspective('rules');
    draftRequest.current = null; onLevelsChange([]);
  }
  function lines(result: StockPlan, visible: boolean) {
    onLevelsChange(visible && !result.plan.unsupported ? result.plan.levels.filter(x => x.state === 'OK') : []);
  }
  async function load() {
    const request: PlanInputs = { kind: mode };
    for (const [key, text] of Object.entries(values)) {
      if (!text.trim()) continue;
      const n = Number(text);
      if (!Number.isFinite(n) || n <= 0 || (key === 'shares' && !Number.isInteger(n))) {
        setError(`${key} must be a positive ${key === 'shares' ? 'whole number' : 'number'}`); return;
      }
      request[key as 'entry' | 'shares' | 'risk' | 'stop'] = n;
    }
    if (mode === 'holding' && request.entry === undefined) { setError('Enter your average cost for a held position.'); return; }
    if (mode !== 'holding') { delete request.entry; delete request.shares; delete request.stop; }
    reset(); const token = ++generation.current;
    abort.current = new AbortController(); setBusy(true);
    try {
      const result = await getStockPlan(symbol, request, abort.current.signal);
      if (!alive.current || token !== generation.current) return;
      setData(result); setInputs(request); lines(result, chart);
      const setups = result.plan.setups ?? [];
      setBoardSetupId((setups.find(s => ['conditional', 'exit_triggered'].includes(s.state)) ?? setups[0])?.id ?? '');
    } catch (e) {
      if (alive.current && token === generation.current) setError(e instanceof Error ? e.message : 'Could not load plan');
    } finally { if (alive.current && token === generation.current) setBusy(false); }
  }
  async function propose(level: PlanLevel) {
    if (!data) return;
    if (draftRequest.current?.level !== level.key) draftRequest.current = { level: level.key, id: newRequestId() };
    setActionBusy(true); setError(''); const token = generation.current;
    try {
      const result = await draftPlanLevel(symbol, inputs, level.key, data.packet_fingerprint, draftRequest.current.id);
      if (alive.current && token === generation.current) setDraft(result.message);
    } catch (e) { if (alive.current && token === generation.current) setError(e instanceof Error ? e.message : 'Could not draft alert'); }
    finally { if (alive.current && token === generation.current) setActionBusy(false); }
  }
  async function saveBoard() {
    if (!data) return;
    const at = (key: string) => data.plan.levels.find(x => x.key === key && x.state === 'OK')?.price;
    const setup = data.plan.setups?.find(s => s.id === boardSetupId);
    if (setup && !['conditional', 'exit_triggered'].includes(setup.state)) { setError('This setup is blocked or unavailable.'); return; }
    const entry = setup?.entry ?? inputs.entry ?? at('level.low20');
    const stop = setup ? setup.stop ?? undefined : inputs.stop ?? at('level.stop_candidate') ?? at('level.pullback_stop');
    if (entry === undefined || stop === undefined) { setError('Fresh entry and stop levels are required to save a plan.'); return; }
    const target = setup ? setup.targets.find(t => t.price > entry)?.price : [at('level.high20'), at('level.high252')].find(x => x !== undefined && x > entry);
    const snapshot = {
      title: `${symbol} — ${setup?.title ?? 'code-calculated plan'}`,
      entries: setup ? (setup.shares_basis === 'held' ? [] : [{ label: setup.title, condition: setup.id === 'pullback' ? 'below' : 'above', price: entry, rationale: setup.trigger }]) : [{ label: 'Pullback reference', condition: 'below', price: at('level.low20'), rationale: 'Daily-close confirmation required' },
        { label: 'Breakout reference', condition: 'above', price: at('level.high20'), rationale: 'Daily close and volume confirmation required' }],
      stop_loss: { price: stop, rationale: setup ? 'Assessed candidate, not maintained' : inputs.stop ? 'User-stated held stop' : 'Candidate, not maintained' },
      take_profit: target === undefined ? null : { price: target, rationale: 'Conditional objective above entry, not a forecast' },
      setup,
      rules: data.plan.groups.rules, price_as_of: data.plan.price_as_of, currency: data.plan.currency,
      packet_fingerprint: data.packet_fingerprint, parameters: data.plan.parameters,
    };
    setActionBusy(true); setError(''); const token = generation.current;
    try {
      await api.createBoardPlan({ symbol, stage: 'planning', game_plan: snapshot, entry_price: entry,
        stop_loss: stop, take_profit: target ?? null, source: 'gameplan', notes: 'Calculated candidates; no orders placed.' });
      if (alive.current && token === generation.current) { setSaved(true); onBoardSaved?.(); }
    } catch (e) { if (alive.current && token === generation.current) setError(e instanceof Error ? e.message : 'Could not save to board'); }
    finally { if (alive.current && token === generation.current) setActionBusy(false); }
  }
  return <section aria-label="Game Plan" style={{ padding: 14, background: '#0f172a', border: '1px solid #334155', borderRadius: 10, color: '#cbd5e1', fontSize: 12 }}>
    <details><summary>Daily-close monitoring</summary><DailyCloseAlerts symbol={symbol} currency={data?.plan.currency} levels={data?.plan.levels} asOf={data?.plan.price_as_of} /></details>
    <h3 style={{ margin: '0 0 6px', color: '#c4b5fd' }}>Game Plan</h3>
    <p style={{ color: '#94a3b8', margin: '4px 0 12px' }}>Entry, targets, stop and next steps. Build the rules-based plan first; compare an LLM alternative if useful.</p>
    <label>Plan for <select aria-label="Plan for" disabled={busy || actionBusy} value={mode} onChange={e => { reset(); setMode(e.target.value as typeof mode); }} style={fieldStyle}>
      <option value="entry">A potential purchase</option><option value="holding">A position I hold</option><option value="forecast">Calls and history</option>
    </select></label>
    <div className="game-plan-inputs">
      {(mode === 'holding' ? ['entry', 'shares', 'stop', 'risk'] : mode === 'entry' ? ['risk'] : []).map(key =>
        <label key={key}>{({ entry: 'Average cost', shares: 'Shares held', stop: 'Your held stop (optional)', risk: 'Risk budget (stock currency)' } as Record<string, string>)[key]}
          <input aria-label={key} disabled={busy || actionBusy} type="number" min="0" step={key === 'shares' ? '1' : 'any'} value={values[key as keyof typeof values]}
            onChange={e => { reset(); setValues(v => ({ ...v, [key]: e.target.value })); }} style={fieldStyle} />
        </label>)}
    </div>
    <button style={buttonStyle} disabled={busy || actionBusy} onClick={load}>{busy ? 'Loading plan…' : data ? 'Refresh plan' : 'Build plan'}</button>
    {error && <p role="alert" style={{ color: '#fca5a5' }}>{error}</p>}
    {data && <>
      <div role="group" aria-label="Plan perspective" style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 12 }}>
        <button style={buttonStyle} aria-pressed={perspective === 'rules'} onClick={() => setPerspective('rules')}>Rules-based plan</button>
        <button style={buttonStyle} aria-pressed={perspective === 'llm'} onClick={() => setPerspective('llm')}>LLM alternative plan</button>
      </div>
      <div style={{ marginTop: 12 }}>
      <div hidden={perspective !== 'rules'}>
      <section aria-label="Rules-based plan" style={{ minWidth: 0 }}>
      <p>Prices as of {data.plan.price_as_of?.slice(0, 16).replace('T', ' ') ?? 'unknown'} UTC · {data.plan.currency ?? 'currency unknown'}</p>
      {!!data.plan.setups?.length && <label>Scenario <select aria-label="Setup to save" style={fieldStyle} disabled={actionBusy} value={boardSetupId} onChange={e => { setBoardSetupId(e.target.value); setSaved(false); }}>
        {data.plan.setups.map(s => <option key={s.id} value={s.id}>{s.title} — {s.state.replaceAll('_', ' ')}</option>)}
      </select></label>}
      <TradeSetupSummary setups={(data.plan.setups ?? []).filter(s => s.id === boardSetupId)} currency={data.plan.currency} />
      {data.plan.position_refusals.map((why, i) => <p key={i} role="alert">{why}</p>)}
      {Object.entries(data.plan.unavailable).map(([key, why]) => <p key={key} style={{ color: '#fbbf24' }}>{why}</p>)}
      <label><input type="checkbox" checked={chart} onChange={e => { setChart(e.target.checked); lines(data, e.target.checked); }} /> Show fresh levels on the daily chart</label>
      <details style={{ marginTop: 12 }}><summary style={{ cursor: 'pointer', color: '#a5b4fc' }}>Show rules, history & evidence</summary>
      <p>{data.plan.parameters.note}</p>
      {Object.entries(data.plan.groups).filter(([group]) => group !== 'setups').map(([group, items]) => <details key={group} style={{ marginTop: 10 }}>
        <summary style={{ textTransform: 'capitalize', cursor: 'pointer', color: '#c4b5fd' }}>{group === 'moves' ? 'Past moves — history' : group}</summary>
        {items.map(item => <div key={item.key} style={{ padding: '7px 0', borderBottom: '1px solid #1e293b' }}>
          <span title={item.meaning}>{item.display}</span>
          {item.state === 'STALE' && <strong style={{ color: '#fbbf24' }}> · Stale: {item.note}</strong>}
          {!!item.evidence_ids.length && <button style={{ ...buttonStyle, fontSize: 10, marginLeft: 5 }} onClick={() => setEvidence(item)}>Evidence</button>}
        </div>)}
      </details>)}
      {evidence && <aside aria-label="Plan evidence" style={{ marginTop: 10 }}>
        <strong>{evidence.meaning}</strong><button style={buttonStyle} onClick={() => setEvidence(null)}>Close evidence</button>
        <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 240, overflow: 'auto', fontSize: 10 }}>{JSON.stringify(evidence.evidence_ids.map(id => ({ id, record: data.evidence[id] ?? 'Not available' })), null, 2)}</pre>
      </aside>}
      </details>
      {!data.plan.unsupported && <>
        <details style={{ marginTop: 12 }}><summary style={{ cursor: 'pointer', color: '#a5b4fc' }}>Create price-alert drafts</summary>
        <p>Alert drafts use fixed prices and can trigger intraday. They do not confirm a daily-close rule.</p>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {data.plan.levels.filter(x => x.state === 'OK' && ['stop', 'resistance', 'target'].includes(x.role)).map(level =>
            <button key={level.key} style={buttonStyle} disabled={actionBusy} onClick={() => propose(level)}>Draft alert: {level.label}</button>)}
        </div>
        {draft?.drafts?.map(d => <DraftCard key={d.id} messageId={draft.id} draft={d} />)}
        </details>
        {mode !== 'forecast' && <>
          <button style={{ ...buttonStyle, marginTop: 10 }} disabled={actionBusy || saved ||
            (data.plan.setups?.length ? !data.plan.setups.some(s => s.id === boardSetupId && ['conditional', 'exit_triggered'].includes(s.state)) : !data.plan.levels.some(x => x.state === 'OK' && x.role === 'stop'))}
            onClick={saveBoard}>{saved ? 'Saved to Trade Board' : 'Save plan to Trade Board'}</button>
        </>}
      </>}
      </section>
      </div>
      <div hidden={perspective !== 'llm'}>
      <LLMAlternativePlan key={data.packet_fingerprint} symbol={symbol} inputs={inputs}
        currency={data.plan.currency}
        fingerprint={data.packet_fingerprint} unavailable={data.plan.unsupported || !!data.plan.position_refusals.length ||
          (mode !== 'forecast' && !data.plan.setups?.length) ||
          !data.plan.levels.some(x => x.state === 'OK')} />
      </div>
      </div>
    </>}
  </section>;
}
