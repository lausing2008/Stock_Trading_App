import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { OptionsGamePlan } from '@/lib/api';
import OptionStrategyMatrixPanel from './OptionStrategyMatrix';
import type { OptionResearchContext } from '@/lib/optionResearchContext';
import type { OptionCalendarContext } from '@/lib/optionCalendarEvidence';

/** T322-OPTIONS-GAMEPLAN: composes AI Signal's existing stop-loss/take-profit levels (same
 * numbers PositionSizer already shows) with a REAL, currently-listed options contract to
 * protect against a sharp drop (protective put) or collect income against a target (covered
 * call). Advanced-tier only — the backend enforces this (403 for a basic-tier user), this
 * component just doesn't render at all for one, matching the "don't show a feature a user
 * can't use" convention already established elsewhere in this app.
 *
 * Self-contained (its own fetch, no SWR), matching SrWatchButton's/StockGoalsPanel's own
 * established pattern for keeping stock/[symbol].tsx from growing further. */
export default function OptionsGamePlanCard({
  symbol, currentPrice, stopLoss, takeProfit, signal, researchContext, calendarContext,
}: {
  symbol: string;
  researchContext?: OptionResearchContext;
  calendarContext?: OptionCalendarContext;
  currentPrice: number | undefined;
  stopLoss: number | undefined;
  takeProfit: number | undefined;
  signal: string | undefined;
}) {
  const [plan, setPlan] = useState<OptionsGamePlan | null | undefined>(undefined); // undefined = loading
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setPlan(undefined);
    setError(null);
    api.getOptionsGamePlan(symbol, { stopLoss, takeProfit })
      .then(value => { if (active) setPlan(value); })
      .catch((e) => {
        if (!active) return;
        // A 403 here means the caller isn't Advanced-tier — a real, expected case for a basic
        // user viewing this page before the gate above them (in stock/[symbol].tsx) even mounts
        // this component in some code paths; fail silently to "nothing to show" rather than a
        // scary red error for what is really just "this feature isn't unlocked for you."
        if (String(e?.message ?? e).includes('403')) { setPlan(null); return; }
        setError('Failed to load the options game plan.');
        setPlan(null);
      });
    return () => { active = false; };
  }, [symbol, stopLoss, takeProfit]);

  if (plan === undefined || (plan && plan.symbol !== symbol)) {
    return (
      <div style={{ background: '#1e293b', borderRadius: 10, padding: '14px 18px', border: '1px solid #334155', marginTop: 12 }}>
        <div style={{ fontSize: 12, color: '#64748b' }}>Loading options game plan…</div>
      </div>
    );
  }
  if (error) {
    return (
      <div style={{ background: '#1e293b', borderRadius: 10, padding: '14px 18px', border: '1px solid #334155', marginTop: 12 }}>
        <div style={{ fontSize: 12, color: '#f87171' }}>{error}</div>
      </div>
    );
  }
  if (!plan) return null;
  if (!plan.available) return <div role="status" style={{ marginTop: 12 }}>
    <h3>{plan.strategy_matrix && plan.current_price && plan.chain_as_of ? 'Options Game Plan — original preset contracts unavailable' : 'Options Game Plan — data unavailable'}</h3>
    <p>{plan.reason === 'options_feed_unavailable' ? 'The options provider is unavailable. Refresh when the feed recovers.'
      : plan.reason === 'no_options_listed' ? 'No listed option contracts were returned for this symbol.'
      : plan.reason === 'unsupported_product_settlement' ? 'This product needs settlement and last-trading rules that are not supported. Its archived chain is available for historical research.'
      : plan.reason === 'no_current_expiries' ? 'Every expiration in the archived chain has stopped trading, so there is no current contract to plan. Refresh after the next session is captured.'
      : `No priced option plan is available (${plan.reason ?? 'no usable contracts'}). Refresh the chain before entry.`}</p>
    {plan.last_known_chain_date && <p>Last recorded chain: {plan.last_known_chain_date}.</p>}
    <OptionStrategyMatrixPanel key={symbol} symbol={symbol} calendarContext={calendarContext} researchContext={researchContext} spot={plan.current_price ?? currentPrice ?? 0} stopLoss={stopLoss} takeProfit={takeProfit} chainAsOf={plan.chain_as_of} matrix={plan.strategy_matrix ?? { singles: {}, combos: {}, recommendation: { primary: null, reason: 'No contract data' }, iv_rank: null, iv_regime: 'unknown' }} />
  </div>;

  const pp = plan.protective_put;
  const cc = plan.covered_call;
  const matrix = plan.strategy_matrix;
  const hasMatrix = !!matrix;
  // T402: previously `if (!pp && !cc) return null`. That now also hides the four-leg matrix in
  // the case where the two original legs could not be priced but other structures could —
  // e.g. no stop-loss set, so there is nothing to anchor a protective put to, yet a long call
  // and a cash-secured put are both perfectly constructible.
  // SF-02 RESIDUAL (2026-10-02): A REFUSED QUOTE IS NOT AN ABSENT CONTRACT.
  // The route now declines to price a crossed/one-sided/non-numeric quote and says why. If
  // this card still returned null on that, the fix would have replaced a wrong number with a
  // blank space — and a reader cannot tell "no such contract" from "the quote is unusable
  // right now", which are different situations with different next steps.
  const ppWhy = plan.protective_put_unavailable;
  const ccWhy = plan.covered_call_unavailable;

  return (
    <div style={{ background: '#1e293b', borderRadius: 10, padding: '14px 18px', border: '1px solid #334155', marginTop: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600, color: '#f1f5f9', fontSize: 13 }}>Options Game Plan</span>
        {(ppWhy || ccWhy) && (
          <span
            title={[ppWhy, ccWhy].filter(Boolean).join('\n')}
            style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 999,
                     background: 'rgba(234,179,8,0.12)', border: '1px solid rgba(234,179,8,0.35)',
                     color: '#fde047', cursor: 'help' }}
          >
            ⚠ quote unusable
          </span>
        )}
        <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }}>
          ⚡ Advanced
        </span>
        {signal && (
          <span style={{ fontSize: 10, color: '#64748b' }}>vs. current AI Signal: {signal}</span>
        )}
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 12 }}>
        Compare four common strategies, choose an objective, then review a conditional preference
        and each strategy's entry, exit and management playbook. Prices are research references,
        not an executable trade recommendation.
      </div>

      {!hasMatrix && <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {pp && (
          <div style={{ flex: '1 1 260px', minWidth: 240, background: '#0f172a', borderRadius: 8, padding: '10px 14px', border: '1px solid rgba(239,68,68,0.25)' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#f87171', marginBottom: 6 }}>
              🛡️ Protective Put — hedge downside
            </div>
            <div style={{ fontSize: 12, color: '#cbd5e1', lineHeight: 1.7 }}>
              Buy 1x ${pp.strike.toFixed(2)} put, exp {pp.expiry} ({pp.days_to_expiry}d
              {!pp.in_target_window && <span style={{ color: '#fbbf24' }}> — outside ideal 25-60d window</span>})
              <br />
              Cost: <b style={{ color: '#f1f5f9' }}>${pp.mid_price.toFixed(2)}/sh</b>
              {pp.cost_pct_of_position != null && <> ({pp.cost_pct_of_position.toFixed(1)}% of position)</>}
              <br />
              Caps downside near <b style={{ color: '#f1f5f9' }}>${pp.effective_floor_price.toFixed(2)}</b>
              <span style={{ color: '#64748b' }}> (stop ref: ${pp.reference_stop_loss.toFixed(2)})</span>
            </div>
          </div>
        )}
        {cc && (
          <div style={{ flex: '1 1 260px', minWidth: 240, background: '#0f172a', borderRadius: 8, padding: '10px 14px', border: '1px solid rgba(34,197,94,0.25)' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#4ade80', marginBottom: 6 }}>
              💰 Covered Call — collect income
            </div>
            <div style={{ fontSize: 12, color: '#cbd5e1', lineHeight: 1.7 }}>
              Sell 1x ${cc.strike.toFixed(2)} call, exp {cc.expiry} ({cc.days_to_expiry}d
              {!cc.in_target_window && <span style={{ color: '#fbbf24' }}> — outside ideal 14-45d window</span>})
              <br />
              Credit: <b style={{ color: '#f1f5f9' }}>${cc.mid_price.toFixed(2)}/sh</b>
              {cc.credit_pct_of_position != null && <> ({cc.credit_pct_of_position.toFixed(1)}% of position)</>}
              <br />
              Caps upside near <b style={{ color: '#f1f5f9' }}>${cc.effective_cap_price.toFixed(2)}</b>
              <span style={{ color: '#64748b' }}> (target ref: ${cc.reference_take_profit.toFixed(2)})</span>
            </div>
          </div>
        )}
      </div>}
      {plan.current_price != null && (
        <OptionStrategyMatrixPanel key={symbol} symbol={symbol} calendarContext={calendarContext} researchContext={researchContext} matrix={matrix ?? { singles: {}, combos: {}, recommendation: { primary: null, reason: 'Strategy matrix unavailable' }, iv_rank: null, iv_regime: 'unknown' }} spot={plan.current_price} stopLoss={stopLoss} takeProfit={takeProfit} chainAsOf={plan.chain_as_of} />
      )}
      {currentPrice != null && (
        <div style={{ fontSize: 10.5, color: '#475569', marginTop: 10 }}>
          Protective puts and covered calls require shares; cash-secured puts require reserved cash.
          This card compares plans and does not place trades. See{' '}
          <a href="/option-trading-guide" style={{ color: '#818cf8' }}>Option Trading Guide</a>{' '}
          for the basics, or follow the{' '}
          <Link href="/options-dark-pool-tutorial" style={{ color: '#38bdf8' }}>Options &amp; Dark Pool Tutorial</Link>{' '}
          for the new controls. Use{' '}
          <Link href={`/options-calculator?symbol=${plan.symbol}&spot=${plan.current_price ?? ''}`} style={{ color: '#818cf8' }}>
            the options calculator
          </Link>{' '}
          to price your own strikes and contract counts.
        </div>
      )}
    </div>
  );
}
