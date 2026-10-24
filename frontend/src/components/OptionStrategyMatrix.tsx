/**
 * T402-OPTIONS-STRATEGY-MATRIX — all four legs, the combinations, and which to use.
 *
 * The Options Game Plan showed two plays: BUY a put (hedge) and SELL a call (income). This
 * renders the full grid — the other two corners are BUY a call (leveraged upside, defined
 * risk) and SELL a put (paid to wait for a lower entry) — plus the structures built by
 * combining them, and the recommendation among them with its reasoning stated so it can be
 * disagreed with.
 *
 * Every number comes from a REAL currently-listed contract. Nothing here predicts direction.
 */
import React, { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { COMMON_OPTION_KEYS, COMMON_OPTION_NAMES, recommendCommonOption, type OptionPlanContext, type OptionGoal, type CommonOptionKey } from '@/lib/commonOptionPlans';
import type { OptionStrategy, OptionStrategyMatrix } from '@/lib/api';
import { OPTION_HOLDING_DEFAULTS, reviewOptionResearchContext, type OptionResearchContext } from '@/lib/optionResearchContext';
import { useNow } from '@/lib/useNow';
import { optionEventWindow, reviewOptionCalendar, type OptionCalendarContext } from '@/lib/optionCalendarEvidence';
import { optionPreferenceBlocker, parseOptionPreferences, type OptionSearchPreferences } from '@/lib/optionSearchPreferences';
import OptionCandidateComparison from './OptionCandidateComparison';

const CARD: React.CSSProperties = {
  background: '#0b1220', border: '1px solid #1e293b', borderRadius: 8, padding: 14,
};
const LABEL: React.CSSProperties = {
  fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em',
};

const fmtUSD = (n: number | null | undefined) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const measurement = (n: number | null | undefined) =>
  n == null || !Number.isFinite(n) ? 'unavailable' : n.toFixed(4);
const candidateReason = (reason: string) => ({
  unsupported_contract: 'unsupported contract terms', invalid_or_expired_contract: 'invalid or expired contract',
  two_sided_quote_required: 'missing a usable two-sided quote', crossed_quote: 'crossed bid/ask',
  spread_exceeds_20_percent: 'spread above the 20% research ceiling', non_positive_max_profit: 'non-positive maximum payoff',
  no_chain_rows: 'no chain rows for this side', invalid_payoff: 'inconsistent payoff inputs',
  outside_abs_delta_range: 'outside the requested absolute-delta range',
  delta_unavailable_or_invalid: 'unavailable or invalid delta',
  delta_sign_mismatch: 'delta sign inconsistent with call/put',
}[reason] ?? 'unavailable contract evidence');

/** Unbounded is rendered as a word, never as a number — "$0" would read as "no profit". */
const fmtBound = (n: number | null | undefined, unbounded: string) =>
  n == null ? unbounded : fmtUSD(n);

/** Payoff at expiry for one structure, per contract, ignoring commissions.
 *  Long stock is included for structures that require it, because a covered call's payoff
 *  without the stock underneath it is not the thing the user is holding. */
function payoffAt(s: OptionStrategy, spotAtExpiry: number, entrySpot: number): number {
  let pnl = 0;
  if (s.requires_shares) pnl += (spotAtExpiry - entrySpot) * 100;
  for (const leg of s.legs) {
    const intrinsic = leg.right === 'call'
      ? Math.max(0, spotAtExpiry - leg.strike)
      : Math.max(0, leg.strike - spotAtExpiry);
    const sign = leg.action === 'buy' ? 1 : -1;
    pnl += sign * (intrinsic * 100 - leg.cost_per_contract);
  }
  return pnl;
}

function PayoffChart({ s, spot }: { s: OptionStrategy; spot: number }) {
  const lo = spot * 0.75, hi = spot * 1.25;
  const pts = Array.from({ length: 61 }, (_, i) => {
    const px = lo + ((hi - lo) * i) / 60;
    return { px, pnl: payoffAt(s, px, spot) };
  });
  const maxAbs = Math.max(...pts.map(p => Math.abs(p.pnl)), 1);
  const w = 260, h = 90, pad = 4;
  const x = (px: number) => pad + ((px - lo) / (hi - lo)) * (w - pad * 2);
  const y = (pnl: number) => h / 2 - (pnl / maxAbs) * (h / 2 - pad);
  const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.px).toFixed(1)},${y(p.pnl).toFixed(1)}`).join(' ');

  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 90 }} aria-label="Payoff at expiry">
      {/* zero line — the visual break-even */}
      <line x1={pad} y1={h / 2} x2={w - pad} y2={h / 2} stroke="#334155" strokeWidth={1} />
      {/* today's price */}
      <line x1={x(spot)} y1={0} x2={x(spot)} y2={h} stroke="#475569" strokeWidth={1} strokeDasharray="2,2" />
      <path d={d} fill="none" stroke="#38bdf8" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function StrategyCard({ s, spot, highlight, review }: { s: OptionStrategy; spot: number; highlight: boolean;
  review?: ReturnType<typeof recommendCommonOption>['plans'][CommonOptionKey] }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ ...CARD, borderColor: highlight ? '#22c55e' : '#1e293b' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: highlight ? '#22c55e' : '#e2e8f0' }}>
          {s.name}{highlight && ' ★'}
        </div>
        <div style={{ fontSize: 11, color: s.net === 'credit' ? '#22c55e' : '#f59e0b' }}>
          {s.net === 'credit' ? 'You receive' : 'You pay'} {fmtUSD(Math.abs(s.net_per_contract))}
        </div>
      </div>

      <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6, lineHeight: 1.5 }}>{s.what_it_does}</div>
      <p style={{ fontSize: 12, color: '#cbd5e1' }}>{s.legs.map((leg, i) => <span key={i}>
        {i > 0 && ' + '}{leg.action.toUpperCase()} {fmtUSD(leg.strike)} {leg.right} · expires {leg.expiry ?? 'unknown'}
        {leg.days_to_expiry != null && ` (${leg.days_to_expiry} days)`}
      </span>)}</p>
      <p style={{ fontSize: 11, color: '#94a3b8' }}>{s.legs.map((leg, i) => <span key={i}>
        {i > 0 && ' · '}OI {leg.oi == null ? 'unavailable' : leg.oi.toLocaleString()}
        {' · '}Delta {measurement(leg.delta)} · Gamma {measurement(leg.gamma)}
        {' · '}Theta {measurement(leg.theta)} · Vega {measurement(leg.vega)}
        {' · '}Spread {leg.spread_pct == null ? 'unavailable' : `${leg.spread_pct}%`}
      </span>)}</p>
      <p style={{ fontSize: 10, color: '#94a3b8' }}>Greeks describe the option contract, not the combined strategy exposure. Snapshot estimates; units are provider conventions. They do not establish a probability of profit.</p>
      <p style={{ fontSize: 10, color: '#94a3b8' }}>Expiry economics per standard contract, before fees. Stock legs use the reference spot.</p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(88px,1fr))', gap: 8, marginTop: 10 }}>
        <div><div style={LABEL}>Max loss</div>
          <div style={{ fontSize: 12, color: '#ef4444' }}>{fmtBound(s.max_loss_per_contract, 'Unlimited')}</div></div>
        <div><div style={LABEL}>Max profit</div>
          <div style={{ fontSize: 12, color: '#22c55e' }}>{fmtBound(s.max_profit_per_contract, 'Unlimited')}</div></div>
        <div><div style={LABEL}>Breakeven</div>
          <div style={{ fontSize: 12, color: '#cbd5e1' }}>{fmtUSD(s.breakeven)}
            {s.breakeven_move_pct != null && <span style={{ color: '#64748b' }}> ({s.breakeven_move_pct > 0 ? '+' : ''}{s.breakeven_move_pct}%)</span>}
          </div></div>
        <div><div style={LABEL}>Capital</div>
          <div style={{ fontSize: 12, color: '#cbd5e1' }}>{fmtUSD(s.collateral_per_contract)}</div></div>
      </div>

      {review && <div className="option-strategy-playbook">
        <p><strong>Entry:</strong> {review.entry}</p>
        <p><strong>Take profit / objective:</strong> {review.target}</p>
        <p><strong>Stop / invalidation:</strong> {review.stop}</p>
        {!!review.blockers.length && <p className="option-strategy-blockers"><strong>Not eligible for preference:</strong> {review.blockers.join(' ')}</p>}
        <details><summary>Playbook — before entry, while open & exit</summary><ol>{review.steps.map(step => <li key={step}>{step}</li>)}</ol></details>
      </div>}
      <details><summary>Expiry payoff illustration</summary><PayoffChart s={s} spot={spot} /></details>

      <button onClick={() => setOpen(o => !o)} style={{
        marginTop: 4, background: 'transparent', border: 'none', color: '#38bdf8',
        fontSize: 11, cursor: 'pointer', padding: 0,
      }}>{open ? '▾ Hide legs' : '▸ Show legs & when to use'}</button>

      {open && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, color: '#cbd5e1', marginBottom: 8, lineHeight: 1.5 }}>
            <strong style={{ color: '#94a3b8' }}>Use when:</strong> {s.use_when}
          </div>
          {s.legs.map((l, i) => (
            <div key={i} style={{ fontSize: 11, color: '#94a3b8', padding: '4px 0', borderTop: '1px solid #0f172a' }}>
              <span style={{ color: l.action === 'buy' ? '#38bdf8' : '#f59e0b', fontWeight: 700 }}>
                {l.action.toUpperCase()}
              </span>{' '}
              1× ${l.strike.toFixed(2)} {l.right} · exp {l.expiry} ({l.days_to_expiry}d) · {fmtUSD(l.price_per_share)}/sh
              {l.spread_pct != null && l.spread_pct > 20 && (
                <span style={{ color: '#f59e0b' }}> · wide spread {l.spread_pct}% — you will not fill at this price</span>
              )}
              {/* SF-04: WHERE the number came from. A last-trade price has no spread, so the
                  warning above cannot fire for it — absence of a warning was reading as
                  reassurance on a price that may be hours old and may not be obtainable. */}
              {l.price_source === 'last_trade' && (
                <span style={{ color: '#f59e0b' }} title="No two-sided quote was available for this contract, so the figure shown is the last price it actually traded at — at an unknown time. There is no spread to report, which is why no spread warning appears.">
                  {' '}· last traded price, not a live quote — no spread available
                </span>
              )}
            </div>
          ))}
          {s.requires_shares && (
            <div style={{ fontSize: 10, color: '#f59e0b', marginTop: 6 }}>
              Requires owning 100 shares per contract. The payoff above includes the stock.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function OptionStrategyMatrixPanel({ matrix, spot, stopLoss, takeProfit, chainAsOf, researchContext, calendarContext, symbol }: {
  symbol?: string;
  researchContext?: OptionResearchContext;
  calendarContext?: OptionCalendarContext;
  matrix: OptionStrategyMatrix; spot: number; stopLoss?: number; takeProfit?: number; chainAsOf?: string | null;
}) {
  const now = useNow();
  const research = researchContext ? reviewOptionResearchContext(researchContext, now) : null;
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(now));
  const holdingKey = researchContext ? `${researchContext.symbol}:${researchContext.horizon}` : '';
  const [holdingOverride, setHoldingOverride] = useState<{ key: string; value: string } | null>(null);
  const holdingText = holdingOverride?.key === holdingKey ? holdingOverride.value : String(Object.hasOwn(OPTION_HOLDING_DEFAULTS, researchContext?.horizon ?? '') ? OPTION_HOLDING_DEFAULTS[researchContext!.horizon] : '');
  const [goal, setGoal] = useState<OptionGoal>('compare');
  const [shares, setShares] = useState('');
  const [cash, setCash] = useState('');
  const [premiumBudget, setPremiumBudget] = useState('');
  const [requireEarningsAfterExpiry, setRequireEarningsAfterExpiry] = useState(false);
  const [preferenceText, setPreferenceText] = useState<Record<keyof OptionSearchPreferences, string>>({ min_dte: '', max_dte: '', min_abs_delta: '', max_abs_delta: '' });
  const preferences = parseOptionPreferences(preferenceText);
  const preferencesActive = Object.values(preferences.values).some(value => value !== null);
  type CandidateReview = NonNullable<NonNullable<OptionStrategyMatrix['candidate_review']>['covered_call']>;
  const [comparisonKey, setComparisonKey] = useState<CommonOptionKey>('covered_call');
  const [choices, setChoices] = useState<Partial<Record<CommonOptionKey, { source: CandidateReview; index: number }>>>({});
  const [assignmentAcceptance, setAssignmentAcceptance] = useState<string | null>(null);
  const searchGeneration = useRef(0);
  const [search, setSearch] = useState<{ key: string; source: OptionStrategyMatrix; reviews?: OptionStrategyMatrix['candidate_review']; pending?: boolean; error?: string } | null>(null);
  const searchKey = JSON.stringify([holdingKey, holdingText, chainAsOf, spot, stopLoss, takeProfit, today, preferenceText]);
  useEffect(() => {
    searchGeneration.current += 1;
    setSearch(null);
    setChoices({});
    setAssignmentAcceptance(null);
    return () => { searchGeneration.current += 1; };
  }, [matrix, searchKey]);
  const currentSearch = search?.key === searchKey && search.source === matrix ? search : null;
  const reviews = currentSearch?.reviews ?? matrix.candidate_review;
  const candidates = reviews?.[comparisonKey];
  const coveredChoice = choices[comparisonKey];
  const alternative = coveredChoice && coveredChoice.source === candidates ? candidates?.alternatives[coveredChoice.index] : undefined;
  const chooseComparison = (index: number | null) => {
    if (!candidates || (index !== null && (!Number.isInteger(index) || index < 0 || index >= candidates.alternatives.length || candidates.alternatives[index]?.legs.length !== 1))) return;
    setChoices(previous => ({ ...previous, [comparisonKey]: index === null ? undefined : { source: candidates, index } }));
    setAssignmentAcceptance(null);
  };
  const searchExpiries = async () => {
    if (!researchContext || preferences.error || !Number.isInteger(Number(holdingText)) || Number(holdingText) < 1 || Number(holdingText) > 365) return;
    const generation = ++searchGeneration.current;
    setSearch({ key: searchKey, source: matrix, pending: true });
    setChoices({});
    setAssignmentAcceptance(null);
    try {
      const result = await api.getOptionsGamePlan(researchContext.symbol, { stopLoss, takeProfit, holdingDays: Number(holdingText), ...(preferencesActive ? { searchPreferences: preferences.values } : {}) });
      if (generation !== searchGeneration.current) return;
      const reviews = result.strategy_matrix?.candidate_review;
      const review = reviews?.covered_call;
      if (result.symbol !== researchContext.symbol || result.chain_as_of !== chainAsOf || result.current_price !== spot || review?.method_version !== 'covered_call_candidates_v2' || review.holding_days !== Number(holdingText) || (review.search_date && review.search_date !== today)) {
        throw new Error('Search context changed or is unavailable. Refresh the game plan before searching again.');
      }
      if (preferencesActive && Object.values(reviews ?? {}).some(r => (Object.keys(preferences.values) as (keyof OptionSearchPreferences)[])
        .some(key => (r?.search_preferences?.[key] ?? null) !== preferences.values[key]))) {
        throw new Error('Search preferences were not acknowledged. Refresh the plan before comparing results.');
      }
      setSearch({ key: searchKey, source: matrix, reviews });
    } catch (error) {
      if (generation === searchGeneration.current) setSearch({ key: searchKey, source: matrix, error: error instanceof Error ? error.message : 'Expiration search failed. Retry when the service recovers.' });
    }
  };
  const comparedMatrix = { ...matrix, singles: { ...matrix.singles }, candidate_review: { ...reviews } };
  for (const key of COMMON_OPTION_KEYS) {
    const choice = choices[key];
    const source = reviews?.[key];
    const selected = choice?.source === source && choice ? source?.alternatives[choice.index] : undefined;
    if (selected) {
      comparedMatrix.singles[key] = selected;
      delete comparedMatrix.candidate_review[key];
    }
  }
  const selectionName = comparisonKey === 'covered_call' ? 'Covered-call' : COMMON_OPTION_NAMES[comparisonKey];
  const sectionName = comparisonKey === 'covered_call' ? 'Covered call' : COMMON_OPTION_NAMES[comparisonKey];
  const assignmentKey = JSON.stringify([comparedMatrix.singles.covered_call?.legs, comparedMatrix.singles.cash_secured_put?.legs, chainAsOf]);
  const acceptAssignment = assignmentAcceptance === assignmentKey;
  const amount = (text: string) => text.trim() && Number.isFinite(Number(text)) && Number(text) >= 0 ? Number(text) : null;
  const holdingDays = researchContext ? (holdingText.trim() ? Number(holdingText) : null) : undefined;
  const calendar = reviewOptionCalendar(calendarContext, symbol ?? researchContext?.symbol ?? '', now);
  const eventRows = COMMON_OPTION_KEYS.map(key => {
    const expiry = comparedMatrix.singles[key]?.legs[0]?.expiry;
    return { key, expiry, earnings: optionEventWindow(calendar, calendar.earningsDate, expiry, today, holdingDays),
      dividend: optionEventWindow(calendar, calendar.exDividendDate, expiry, today, holdingDays) };
  });
  const calendarBlockers = Object.fromEntries(eventRows.filter(row => requireEarningsAfterExpiry && row.earnings.code !== 'after_expiry')
    .map(row => [row.key, 'Earnings timing filter: requires a current matched calendar record reporting next earnings after this option expires.']));
  const contractPreferenceBlockers = Object.fromEntries(COMMON_OPTION_KEYS.flatMap(key => {
    const reason = preferences.error ?? optionPreferenceBlocker(comparedMatrix.singles[key]?.legs[0], preferences.values, today);
    return reason ? [[key, reason]] : [];
  }));
  const context: OptionPlanContext = { goal, shares: amount(shares), cash: amount(cash), premiumBudget: amount(premiumBudget), acceptAssignment, stop: stopLoss, target: takeProfit, holdingDays, calendarBlockers, contractPreferenceBlockers };
  const review = recommendCommonOption(comparedMatrix, context, today, chainAsOf);
  const comboKeys = Object.keys(matrix.combos);
  return <section aria-label="Four common option strategies" style={{ marginTop: 16 }}>
    <h3>Four common strategies & conditional preference</h3>
    <p>Compare one standard 100-share contract. These are research illustrations using settled provider prices
      {chainAsOf ? ` observed ${chainAsOf}` : ' with an unknown observation date'}, not live executable quotes.</p>
    <p>Original contract selection uses the nearest strike to spot, the stock stop or target, and preset expiry windows. Covered-call alternatives initially screen the selected call expiry; an explicit holding-period search compares all four strategies across other expirations. Choosing one changes this comparison only. Delta, gamma, open interest and breakeven are not jointly optimized. This comparison does not establish the best return.</p>
    {researchContext && research && <section aria-label="Horizon and market context evidence" style={{ ...CARD, marginBottom: 14 }}>
      <h4>Strategy research horizon: {researchContext.horizon}</h4>
      <div className="option-strategy-inputs"><label style={{ maxWidth: 300 }}>Planned holding period (calendar days)<input aria-label="Planned holding period (calendar days)" type="number" min="1" max="365" step="1" value={holdingText}
        onChange={e => setHoldingOverride({ key: holdingKey, value: e.target.value })} /></label></div>
      <p>The editable default follows the platform outcome-evaluation window. It is not an optimized holding time. Each contract needs this period plus a two-calendar-day research buffer before expiry.</p>
      <p>Signal record: {research.recordedAt ?? 'unavailable'} · {research.statusLabel}. Record-age policy: at most 72 hours; underlying observations need their own dates.</p>
      <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', fontSize: 12 }}>
        <thead><tr>{['Evidence', 'Recorded value', 'Source', 'Date / limitation'].map(h => <th key={h} style={{ textAlign: 'left', padding: 6 }}>{h}</th>)}</tr></thead>
        <tbody>{research.rows.map(row => <tr key={row.label}><th scope="row" style={{ textAlign: 'left', padding: 6 }}>{row.label}</th><td>{row.value}</td><td>{row.source}</td><td>{row.observation}</td></tr>)}</tbody>
      </table></div>
      {research.conflict && <p>Recorded signal and regime conflict. Verify current observations before choosing a directional strategy.</p>}
      <p>{research.note}</p>
    </section>}
    <section aria-label="Quote and event evidence" style={{ ...CARD, marginBottom: 14 }}>
      <h4>Quote and event evidence</h4>
      <p>Archive session: {chainAsOf ?? 'unavailable'}. Quote observation time, bid/ask size and reference-spot observation time are unavailable in this plan. Archive ingestion time is not quote observation time. Fresh execution evidence remains unavailable.</p>
      <p>Calendar source: yfinance fundamentals · retrieved {calendar.retrievedAt ?? 'not recorded'} · {calendar.label}.
        {' '}Reported next earnings: {calendar.earningsDate ?? 'not reported'} · reported ex-dividend date: {calendar.exDividendDate ?? 'not reported'}.</p>
      <p>Retrieval age is checked against a 24-hour research policy. Event dates are date-only reports; issuer confirmation, event time and complete future calendar coverage are unverified. A missing or past date does not mean no event.</p>
      <label><input type="checkbox" checked={requireEarningsAfterExpiry} onChange={e => setRequireEarningsAfterExpiry(e.target.checked)} /> Require a current calendar record with reported next earnings after expiration</label>
      <p>This optional research filter applies to each compared contract through expiry, even when the planned holding period is shorter. It does not prove an event-free window.</p>
      <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', fontSize: 12 }}>
        <thead><tr>{['Strategy', 'Compared expiry', 'Reported earnings timing', 'Reported ex-dividend timing'].map(h => <th key={h} style={{ textAlign: 'left', padding: 6 }}>{h}</th>)}</tr></thead>
        <tbody>{eventRows.map(row => <tr key={row.key}><th scope="row" style={{ textAlign: 'left', padding: 6 }}>{COMMON_OPTION_NAMES[row.key]}</th><td>{row.expiry ?? 'unavailable'}</td><td>{row.earnings.label}</td><td>{row.dividend.label}</td></tr>)}</tbody>
      </table></div>
      <p>Covered calls need an early-assignment review around ex-dividend dates. Assignment probability is not computed: fresh option time value, dividend amount and verified deliverables are missing. Both short calls and short puts can be assigned early.</p>
    </section>
    <div className="option-strategy-inputs">
      <label>Objective<select aria-label="Objective" value={goal} onChange={e => setGoal(e.target.value as OptionGoal)}>
        <option value="compare">Compare only — choose an objective</option><option value="upside">Upside / bullish exposure</option>
        <option value="income">Income / willing to own stock</option><option value="protect">Protect existing shares</option><option value="bearish">Bearish / reduce stock risk</option>
      </select></label>
      <label>Unencumbered shares<input type="number" min="0" value={shares} onChange={e => setShares(e.target.value)} /></label>
      <label>Cash reserved for assignment ($)<input type="number" min="0" value={cash} onChange={e => setCash(e.target.value)} /></label>
      <label>Option premium-loss budget ($)<input type="number" min="0" value={premiumBudget} onChange={e => setPremiumBudget(e.target.value)} /></label>
    </div>
    <label><input type="checkbox" checked={acceptAssignment} onChange={e => setAssignmentAcceptance(e.target.checked ? assignmentKey : null)} /> I accept buying or selling 100 shares at the listed strike if assigned.</label>
    <div role="status" style={{ ...CARD, borderColor: review.primary ? '#38bdf8' : '#475569', margin: '14px 0' }}>
      <strong>{review.primary ? `${researchContext ? 'Goal-fit only' : 'Preferred strategy to research'}: ${COMMON_OPTION_NAMES[review.primary]}` : 'Wait / no strategy preference yet'}</strong>
      <p>{review.reason}</p><p>{review.ivNote}</p>
      <p>Entry status: WAIT for fresh quotes and broker checks. Holdings and budgets above are your declarations;
        account permissions, deliverables, event coverage and portfolio risk are unverified. No quantity or order is recommended.</p>
      <p>Premium targets and exits below are illustrative policies, not calibrated forecasts or guaranteed fills.
        For hedges and covered calls, the premium budget does not cap stock losses.</p>
    </div>
    {(matrix.candidate_review || researchContext) && <section aria-label={`${sectionName} contract selection`} style={{ ...CARD, marginBottom: 14 }}>
      <h4>{sectionName} — contract selection</h4>
      {researchContext && <fieldset style={{ border: '1px solid #334155', marginBottom: 12 }}><legend>Research contract preferences — all four strategies</legend>
        <div className="option-strategy-inputs">{([
          ['min_dte', 'Minimum days to expiry', 1, 1095, 1], ['max_dte', 'Maximum days to expiry', 1, 1095, 1],
          ['min_abs_delta', 'Minimum absolute delta', 0, 1, .01], ['max_abs_delta', 'Maximum absolute delta', 0, 1, .01],
        ] as const).map(([key, label, min, max, step]) => <label key={key}>{label}<input aria-label={label} type="number" min={min} max={max} step={step}
          placeholder="No preference" value={preferenceText[key]} onChange={e => setPreferenceText(previous => ({ ...previous, [key]: e.target.value }))} /></label>)}</div>
        <button type="button" onClick={() => setPreferenceText({ min_dte: '', max_dte: '', min_abs_delta: '', max_abs_delta: '' })}>Clear contract preferences</button>
        {preferences.error && <p role="alert">{preferences.error}</p>}
        <p>Blank means no preference. Days are calendar days from the current New York date; the holding period plus two-day buffer still applies. Delta uses the absolute provider contract estimate, with positive calls and negative puts, before buy/sell position signs. A requested delta range excludes missing, invalid or wrong-sign values.</p>
        <p>Preferences apply before the server shortlist and also gate goal fit for original or chosen contracts. They are shared across strategies and local to this page. Editing them clears comparisons; click Search to fetch new alternatives. Earliest four eligible expirations are searched; failures do not trigger an expanded search. No values are presented as optimal or as profit probabilities.</p>
      </fieldset>}
      {COMMON_OPTION_KEYS.some(key => choices[key]?.source === reviews?.[key] && choices[key]) && <p>Selected comparisons: {COMMON_OPTION_KEYS.flatMap(key => {
        const choice = choices[key];
        const selected = choice && choice.source === reviews?.[key] ? reviews?.[key]?.alternatives[choice.index] : undefined;
        return selected ? [`${COMMON_OPTION_NAMES[key]}: ${fmtUSD(selected.legs[0].strike)} · ${selected.legs[0].expiry}`] : [];
      }).join('; ')}. Combinations retain their original legs.</p>}
      <label>Strategy for contract comparison<select aria-label="Strategy for contract comparison" value={comparisonKey} onChange={e => setComparisonKey(e.target.value as CommonOptionKey)}>
        {COMMON_OPTION_KEYS.map(key => <option key={key} value={key}>{COMMON_OPTION_NAMES[key]}</option>)}
      </select></label>
      {researchContext && <p><button type="button" disabled={!!currentSearch?.pending || !!preferences.error || !Number.isInteger(Number(holdingText)) || Number(holdingText) < 1 || Number(holdingText) > 365} onClick={searchExpiries}>
        {currentSearch?.pending ? 'Searching expirations…' : 'Find expirations for holding period'}</button> Searches all four strategies across up to four eligible expirations from the same archived session, on request only.</p>}
      {currentSearch?.error && <p role="alert">{currentSearch.error} Original comparison remains available.</p>}
      {candidates ? <>
      <p>Original anchor {fmtUSD(candidates.anchor)} · expiry {candidates.expiry ?? 'unknown'} · selected strike {fmtUSD(candidates.original.strike)}.</p>
      <p>{candidates.original.reason ? `Original candidate: ${candidateReason(candidates.original.reason)}.` : 'Original candidate passed the basic quote screen; holdings, freshness and broker checks remain separate.'}</p>
      <p>{candidates.screened_count} of {candidates.evaluated_count} rows passed the research quote/payoff screen. {candidates.method_version !== 'covered_call_candidates_v1' ? 'Counts include unsupported rows refused by this screen. ' : 'Counts cover supplied rows after upstream contract exclusions. '}{candidates.method_version !== 'covered_call_candidates_v1'
        ? `Scope: ${candidates.completed_expiries?.length ?? 0} of ${candidates.searched_expiries?.length ?? 0} attempted expirations read; ${candidates.eligible_expiry_count ?? 0} eligible expirations in this archive. Minimum remaining life: ${candidates.holding_days! + candidates.buffer_days!} calendar days. Read: ${candidates.completed_expiries?.join(', ') || 'none'}.`
        : 'Scope: one selected expiration; other expirations are not searched.'}</p>
      {!!candidates.failed_expiries?.length && <p role="alert">Expiration reads failed: {candidates.failed_expiries.join(', ')}. Results are incomplete; a failure is not absence of contracts.</p>}
      {!!Object.keys(candidates.rejected_counts).length && <p>Excluded: {Object.entries(candidates.rejected_counts).map(([reason, count]) => `${count} ${candidateReason(reason)}`).join('; ')}.</p>}
      <p>{candidates.ranking} The 20% spread ceiling is a research policy; quote age, size and actual deliverables are unverified.</p>
      {candidates.search_preferences && <p>Search preference coverage: {candidates.holding_eligible_expiry_count ?? 'unknown'} expirations fit the holding period; {candidates.eligible_expiry_count ?? 'unknown'} also fit the declared expiry band. Delta exclusions are included in the row rejection counts. Scope remains this archived session.</p>}
      {candidates.alternatives.length ? <label>{selectionName} contract to compare<select aria-label={`${selectionName} contract to compare`}
        value={alternative && coveredChoice ? String(coveredChoice.index) : 'original'} onChange={e => {
          chooseComparison(e.target.value === 'original' ? null : Number(e.target.value));
        }}>
        <option value="original">{comparisonKey === 'covered_call' ? 'Original target-strike selection' : 'Original plan selection'}</option>
        {candidates.alternatives.map((s, i) => <option key={s.legs[0].option_symbol ?? i} value={String(i)}>
          Compare {fmtUSD(s.legs[0].strike)} {s.legs[0].right} · {s.legs[0].expiry} · delta {s.legs[0].delta ?? 'unavailable'} · {fmtUSD(s.net_per_contract)} {s.net} before fees
        </option>)}
      </select></label> : <p>No other contract passed this screen. This does not establish absence in other expirations.</p>}
      <OptionCandidateComparison key={comparisonKey} strategyKey={comparisonKey} original={matrix.singles[comparisonKey]}
        originalStrike={candidates.original.strike} originalExpiry={candidates.expiry} originalSymbol={candidates.original.option_symbol} alternatives={candidates.alternatives}
        today={today} selectedIndex={alternative && coveredChoice ? coveredChoice.index : null} onSelect={chooseComparison} />
      {alternative && <p role="status">Comparing an explicitly selected alternative at {fmtUSD(alternative.legs[0].strike)}.
        {' '}Stock target remains {fmtUSD(takeProfit)}; stock stop remains {fmtUSD(stopLoss)}. {comparisonKey === 'covered_call' ? 'Selling this call caps stock upside at its strike.' : 'Review the selected strike, expiry and payoff for this strategy.'}
        {' '}Confirm assignment acceptance again. Combinations still use their original legs. This is not an expected-return ranking.</p>}
      </> : <p>Search expirations to compare contracts for this strategy. Its original card remains below.</p>}
    </section>}
    <div className="common-option-strategy-grid">
      {COMMON_OPTION_KEYS.map(key => comparedMatrix.singles[key]
        ? <StrategyCard key={key} s={comparedMatrix.singles[key]} spot={spot} highlight={review.primary === key} review={review.plans[key]} />
        : <article key={key} style={CARD}><h4>{COMMON_OPTION_NAMES[key]}</h4>
          <p>No usable priced contract at the selected strike and expiry. {reviews?.[key] ? 'Select this strategy and a screened alternative above if available.' : 'Nearby strikes have not been exhaustively searched.'} Entry, premium targets and stops are unavailable; refresh the chain.</p>
          <p>{review.plans[key].blockers.join(' ')}</p><details><summary>Playbook — before entry, while open & exit</summary><ol>{review.plans[key].steps.map(step => <li key={step}>{step}</li>)}</ol></details>
        </article>)}
    </div>
    {!!comboKeys.length && <details style={{ marginTop: 16 }}><summary>More strategies — combinations</summary><div className="common-option-strategy-grid">
      {comboKeys.map(key => <StrategyCard key={key} s={matrix.combos[key]} spot={spot} highlight={false} />)}
    </div></details>}
    {!!Object.keys(matrix.unavailable ?? {}).length && <details><summary>Unavailable combinations and reasons</summary>
      {Object.entries(matrix.unavailable ?? {}).map(([key, item]) => <p key={key}><strong>{item.name}:</strong> {item.reason}</p>)}
    </details>}
    <p>Strategy mechanics: <a href="https://www.optionseducation.org/strategies/all-strategies/long-call">OIC long call</a>, <a href="https://www.optionseducation.org/strategies/all-strategies/protective-put">protective put</a>, <a href="https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write">covered call</a>, <a href="https://www.optionseducation.org/strategies/all-strategies/cash-secured-put">cash-secured put</a>.</p>
  </section>;
}
