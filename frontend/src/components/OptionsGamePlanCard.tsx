import { useEffect, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { OptionsGamePlan } from '@/lib/api';
import OptionStrategyMatrixPanel from './OptionStrategyMatrix';

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
  symbol, currentPrice, stopLoss, takeProfit, signal,
}: {
  symbol: string;
  currentPrice: number | undefined;
  stopLoss: number | undefined;
  takeProfit: number | undefined;
  signal: string | undefined;
}) {
  const [plan, setPlan] = useState<OptionsGamePlan | null | undefined>(undefined); // undefined = loading
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!stopLoss && !takeProfit) {
      setPlan(null); // nothing to hedge/collect against yet — don't even fetch
      return;
    }
    setPlan(undefined);
    setError(null);
    api.getOptionsGamePlan(symbol, { stopLoss, takeProfit })
      .then(setPlan)
      .catch((e) => {
        // A 403 here means the caller isn't Advanced-tier — a real, expected case for a basic
        // user viewing this page before the gate above them (in stock/[symbol].tsx) even mounts
        // this component in some code paths; fail silently to "nothing to show" rather than a
        // scary red error for what is really just "this feature isn't unlocked for you."
        if (String(e?.message ?? e).includes('403')) { setPlan(null); return; }
        setError('Failed to load the options game plan.');
        setPlan(null);
      });
  }, [symbol, stopLoss, takeProfit]);

  if (plan === undefined) {
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
  // AUD-T403-SILENTFEEDOUTAGE: this used to `return null` for every unavailable reason, and so
  // did MarketPressurePanel and the Options Chain section. When the upstream chain feed went
  // down, all three vanished at once and the page looked like a deploy had broken it — which is
  // exactly how a multi-day outage went unnoticed. A feed outage now SAYS SO. A symbol that
  // genuinely has no listed options still renders nothing, because that is not news.
  if (plan && !plan.available && plan.reason === 'options_feed_unavailable') {
    return (
      <div style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.35)',
                    borderRadius: 10, padding: '12px 16px', marginTop: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#f59e0b' }}>
          Options data feed unavailable
        </div>
        <div style={{ fontSize: 11.5, color: '#cbd5e1', marginTop: 6, lineHeight: 1.6 }}>
          The upstream options-chain provider is not returning contracts right now, so the game
          plan, options flow, open-interest structure and chain are all unavailable for every
          symbol — not just {plan.symbol}. This is a data-source outage, not a change to this
          stock and not a problem with your view.
          {plan.last_known_chain_date && (
            <> The last chain we recorded for {plan.symbol} was <b>{plan.last_known_chain_date}</b>.</>
          )}
        </div>
        <div style={{ fontSize: 10.5, color: '#64748b', marginTop: 8 }}>
          Dealer gamma, dark-pool prints and NOPE come from a different provider and are still live.
        </div>
      </div>
    );
  }
  if (!plan || !plan.available) return null;

  const pp = plan.protective_put;
  const cc = plan.covered_call;
  const matrix = plan.strategy_matrix;
  const hasMatrix = !!matrix && (Object.keys(matrix.singles).length > 0 || Object.keys(matrix.combos).length > 0);
  // T402: previously `if (!pp && !cc) return null`. That now also hides the four-leg matrix in
  // the case where the two original legs could not be priced but other structures could —
  // e.g. no stop-loss set, so there is nothing to anchor a protective put to, yet a long call
  // and a cash-secured put are both perfectly constructible.
  if (!pp && !cc && !hasMatrix) return null;

  return (
    <div style={{ background: '#1e293b', borderRadius: 10, padding: '14px 18px', border: '1px solid #334155', marginTop: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600, color: '#f1f5f9', fontSize: 13 }}>Options Game Plan</span>
        <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }}>
          ⚡ Advanced
        </span>
        {signal && (
          <span style={{ fontSize: 10, color: '#64748b' }}>vs. current AI Signal: {signal}</span>
        )}
      </div>
      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 12 }}>
        Real, currently-listed contracts priced against your own stop-loss/take-profit levels —
        not a prediction of where the stock will go, just what protecting or collecting income
        against your existing plan would cost right now.
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
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
      </div>
      {hasMatrix && plan.current_price != null && (
        <OptionStrategyMatrixPanel matrix={matrix!} spot={plan.current_price} />
      )}
      {currentPrice != null && (
        <div style={{ fontSize: 10.5, color: '#475569', marginTop: 10 }}>
          The two legs above require owning shares; the four-leg grid says for each structure
          whether it does. This card shows numbers, it doesn't place trades. See{' '}
          <a href="/option-trading-guide" style={{ color: '#818cf8' }}>Option Trading Guide</a>{' '}
          for how to read them, or{' '}
          <Link href={`/options-calculator?symbol=${plan.symbol}&spot=${plan.current_price ?? ''}`} style={{ color: '#818cf8' }}>
            open the options calculator
          </Link>{' '}
          to price your own strikes and contract counts.
        </div>
      )}
    </div>
  );
}
