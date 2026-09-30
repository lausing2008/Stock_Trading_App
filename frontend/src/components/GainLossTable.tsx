// T412-GAINLOSS: gain/loss by strike, for all four basic option positions, at a chosen expiry.
//
// See lib/optionsGainLoss.ts for the arithmetic and, more importantly, for why ROI's denominator
// changes with the position: a short measured against premium reads +100% on every row that
// expires worthless, which would make selling look like free money.
import { useMemo } from 'react';
import type { OptionsChainRow } from '@/lib/api';
import {
  buildGainLossRows, nearestStrikes, highestRoi, daysToExpiry, unrealized,
} from '@/lib/optionsGainLoss';
import type { OptionAction, OptionRight } from '@/lib/optionsGainLoss';
import NumberField from './NumberField';

const UP = '#4ade80';
const DOWN = '#f87171';

const money = (n: number) =>
  `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const pct = (n: number) => `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
const tone = (n: number) => (n > 0 ? UP : n < 0 ? DOWN : '#64748b');

const INPUT: React.CSSProperties = {
  background: '#0f172a', border: '1px solid #334155', borderRadius: 6, color: '#e2e8f0',
  padding: '6px 8px', fontSize: 12, width: '100%',
};
const LABEL: React.CSSProperties = {
  fontSize: 10, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em',
  display: 'block', marginBottom: 4,
};

const POSITIONS: { action: OptionAction; right: OptionRight; label: string }[] = [
  { action: 'buy', right: 'call', label: 'Buy Call' },
  { action: 'sell', right: 'call', label: 'Sell Call' },
  { action: 'buy', right: 'put', label: 'Buy Put' },
  { action: 'sell', right: 'put', label: 'Sell Put' },
];

interface Props {
  symbol: string;
  spot: number;
  onSpotChange: (n: number) => void;
  expiries: string[];
  expiry: string | undefined;
  onExpiryChange: (e: string) => void;
  calls: OptionsChainRow[];
  puts: OptionsChainRow[];
  loading: boolean;
  unavailableReason?: string;
  action: OptionAction;
  right: OptionRight;
  onPositionChange: (a: OptionAction, r: OptionRight) => void;
  targetPrice: number;
  onTargetChange: (n: number) => void;
  contracts: number;
  onContractsChange: (n: number) => void;
  fillStrike: number;
  onFillStrikeChange: (n: number) => void;
  fillPremium: number;
  onFillPremiumChange: (n: number) => void;
}

export default function GainLossTable({
  symbol, spot, onSpotChange, expiries, expiry, onExpiryChange, calls, puts, loading, unavailableReason,
  action, right, onPositionChange, targetPrice, onTargetChange, contracts, onContractsChange,
  fillStrike, onFillStrikeChange, fillPremium, onFillPremiumChange,
}: Props) {
  const fill = fillStrike > 0 && fillPremium > 0 ? { strike: fillStrike, premium: fillPremium } : null;
  const quotes = right === 'call' ? calls : puts;

  const rows = useMemo(
    () => nearestStrikes(
      buildGainLossRows(quotes, action, right, spot, targetPrice, contracts, fill), spot, 14),
    [quotes, action, right, spot, targetPrice, contracts, fill?.strike, fill?.premium],
  );
  const held = useMemo(() => rows.find(r => r.isUserFill) ?? null, [rows]);
  const mark = useMemo(
    () => (held ? unrealized(action, held.premium, held.quotedPremium, contracts) : null),
    [held, action, contracts],
  );
  const best = useMemo(() => highestRoi(rows), [rows]);
  const dte = expiry ? daysToExpiry(expiry) : null;

  const pill = (active: boolean): React.CSSProperties => ({
    padding: '5px 11px', borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: 'pointer',
    border: `1px solid ${active ? 'rgba(56,189,248,0.45)' : '#334155'}`,
    background: active ? 'rgba(56,189,248,0.15)' : 'transparent',
    color: active ? '#38bdf8' : '#94a3b8',
  });

  return (
    <div style={{ marginTop: 16 }}>
      <h2 style={{ fontSize: 15, fontWeight: 700, color: '#cbd5e1', margin: '0 0 4px' }}>
        Gain / Loss by strike {symbol && <span style={{ color: '#64748b', fontWeight: 400 }}>· {symbol}</span>}
      </h2>
      <p style={{ fontSize: 12, color: '#64748b', margin: '0 0 12px', lineHeight: 1.6 }}>
        Pick an expiry and a position, then say where you think the stock lands. Premiums are the
        real quoted midpoint for each strike at that expiry — not illustrative numbers.
      </p>

      <div style={{ background: '#0b1220', border: '1px solid #1e293b', borderRadius: 8, padding: 16 }}>
        {/* Expiry buttons */}
        <div style={{ marginBottom: 14 }}>
          <span style={LABEL}>Expiration</span>
          {expiries.length === 0 ? (
            <div style={{ fontSize: 12, color: '#64748b' }}>
              {loading ? 'Loading expirations…'
                : unavailableReason
                  ? `No option chain available for ${symbol || 'this symbol'} (${unavailableReason}).`
                  : 'Enter a symbol above to load its expirations.'}
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {expiries.slice(0, 12).map(e => {
                const d = daysToExpiry(e);
                return (
                  <button key={e} onClick={() => onExpiryChange(e)} style={pill(e === expiry)}>
                    {e}{d != null && <span style={{ fontWeight: 400, opacity: 0.7 }}> · {d}d</span>}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Position + inputs */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          {POSITIONS.map(p => (
            <button key={p.label} onClick={() => onPositionChange(p.action, p.right)}
              style={pill(p.action === action && p.right === right)}>{p.label}</button>
          ))}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 10, marginBottom: 14 }}>
          <div>
            <label style={LABEL}>Current price</label>
            <NumberField value={spot} onChange={onSpotChange} min={0.01} step={0.01} style={INPUT}
              aria-label="Current underlying price" />
          </div>
          <div>
            <label style={LABEL}>Stock price at expiry</label>
            <NumberField value={targetPrice} onChange={onTargetChange} min={0.01} step={1} style={INPUT} />
          </div>
          <div>
            <label style={LABEL}>Contracts</label>
            <NumberField value={contracts} onChange={onContractsChange} min={1} step={1} style={INPUT} />
          </div>
          <div>
            <label style={LABEL}>Your strike (optional)</label>
            <NumberField value={fillStrike} onChange={onFillStrikeChange} min={0} step={0.5} style={INPUT}
              aria-label="Strike you hold" />
          </div>
          <div>
            <label style={LABEL}>Your fill price</label>
            <NumberField value={fillPremium} onChange={onFillPremiumChange} min={0} step={0.05} style={INPUT}
              aria-label="Premium you paid or received" />
          </div>
          <div style={{ alignSelf: 'end', fontSize: 11, color: '#64748b' }}>
            {dte != null && <>{dte}d to expiry</>}
          </div>
        </div>

        {held && mark && (
          <div style={{
            background: 'rgba(56,189,248,0.07)', border: '1px solid rgba(56,189,248,0.25)',
            borderRadius: 6, padding: '10px 12px', marginBottom: 12, fontSize: 12, lineHeight: 1.7,
          }}>
            <strong style={{ color: '#38bdf8' }}>Your position.</strong>{' '}
            {contracts} × ${held.strike} {right} @ ${held.premium.toFixed(2)} = {money(mark.cost)}
            {' · '}now marked {money(mark.markValue)} (${held.quotedPremium.toFixed(2)} mid)
            {' · '}
            <strong style={{ color: tone(mark.pnl) }}>
              {mark.pnl >= 0 ? '+' : ''}{money(mark.pnl)} ({pct(mark.pnlPct)})
            </strong>
            <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
              Marked at the midpoint, which is not a fill — closing crosses the spread, so you
              would realise less than this. Your row below is priced from what you paid, so its
              breakeven and return are yours; every other row is the current market quote.
            </div>
          </div>
        )}

        {loading && <div style={{ fontSize: 12, color: '#64748b' }}>Loading chain…</div>}

        {!loading && rows.length === 0 && expiries.length > 0 && (
          <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
            No quoted {right}s at this expiry. A strike with no price is not a cheaper trade — it
            is one nobody is quoting, so it is left out rather than shown at zero.
          </div>
        )}

        {rows.length > 0 && (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 620 }}>
                <thead>
                  <tr style={{ color: '#64748b', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4 }}>
                    <th style={th('left')}>Strike</th>
                    <th style={th('right')}>Premium</th>
                    <th style={th('right')}>Breakeven</th>
                    <th style={th('right')}>{action === 'buy' ? 'Cost' : 'Credit'}</th>
                    <th style={th('right')}>Capital</th>
                    <th style={th('right')}>Value at ${targetPrice.toFixed(2)}</th>
                    <th style={th('right')}>Net P/L</th>
                    <th style={th('right')}>Return</th>
                    <th style={th('right')}>Max loss</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => {
                    const atm = r.moneyness === 'ATM';
                    const isBest = best?.strike === r.strike;
                    return (
                      <tr key={r.strike} style={{
                        borderTop: '1px solid #1e293b',
                        // The highest-return row wins the highlight over the ATM tint — it is the
                        // one the reader asked to have picked out.
                        // The user's own position outranks both the top-return and ATM tints:
                        // it is the row they came to the table to find.
                        background: r.isUserFill ? 'rgba(56,189,248,0.12)'
                          : isBest ? 'rgba(74,222,128,0.09)'
                          : atm ? 'rgba(56,189,248,0.06)' : undefined,
                        boxShadow: r.isUserFill ? 'inset 2px 0 0 #38bdf8'
                          : isBest ? 'inset 2px 0 0 #4ade80' : undefined,
                      }}>
                        <td style={td('left', '#e2e8f0')}>
                          ${r.strike}
                          <span style={{
                            fontSize: 9, marginLeft: 6, padding: '1px 4px', borderRadius: 3,
                            color: r.moneyness === 'ITM' ? '#4ade80' : atm ? '#38bdf8' : '#64748b',
                            border: `1px solid ${r.moneyness === 'ITM' ? 'rgba(74,222,128,0.3)' : atm ? 'rgba(56,189,248,0.3)' : '#334155'}`,
                          }}>{r.moneyness}</span>
                          {r.isUserFill && (
                            <span title={`Priced from your fill of $${r.premium.toFixed(2)}, not the current $${r.quotedPremium.toFixed(2)} midpoint.`}
                              style={{
                                fontSize: 9, marginLeft: 6, padding: '1px 5px', borderRadius: 3,
                                fontWeight: 700, cursor: 'help',
                                color: '#38bdf8', border: '1px solid rgba(56,189,248,0.4)',
                                background: 'rgba(56,189,248,0.15)',
                              }}>YOUR FILL</span>
                          )}
                          {isBest && (
                            <span title="Highest return at the price you entered — arithmetic at one assumed price, not a recommendation."
                              style={{
                                fontSize: 9, marginLeft: 6, padding: '1px 5px', borderRadius: 3,
                                fontWeight: 700, cursor: 'help',
                                color: '#4ade80', border: '1px solid rgba(74,222,128,0.35)',
                                background: 'rgba(74,222,128,0.12)',
                              }}>TOP RETURN</span>
                          )}
                          {r.illiquid && (
                            <span title="No open interest and no volume — this quote is one nobody has taken."
                              style={{ fontSize: 9, marginLeft: 4, color: '#f59e0b', cursor: 'help' }}>thin</span>
                          )}
                        </td>
                        <td style={td('right', '#cbd5e1')}
                          title={r.premiumSource === 'last'
                            ? 'One-sided quote — this is the last traded price, not a midpoint.'
                            : r.spreadPct != null ? `Bid/ask spread ${r.spreadPct}% of the mid` : undefined}>
                          ${r.premium.toFixed(2)}
                          {r.isUserFill && (
                            <span style={{ fontSize: 10, color: '#64748b', marginLeft: 5 }}>
                              (mkt ${r.quotedPremium.toFixed(2)})
                            </span>
                          )}
                          {r.premiumSource === 'last' && <span style={{ color: '#f59e0b' }}>*</span>}
                          {r.spreadPct != null && r.spreadPct >= 25 && (
                            <span title={`Wide spread: ${r.spreadPct}% of the mid`}
                              style={{ color: '#f59e0b', marginLeft: 3, cursor: 'help' }}>⚠</span>
                          )}
                        </td>
                        <td style={td('right', '#cbd5e1')}
                          title="The underlying price at which this position breaks even at expiry — identical for the buyer and the seller of the same contract.">
                          ${r.breakeven.toFixed(2)}
                          {r.breakevenMovePct != null && (
                            <span style={{ fontSize: 10, color: '#64748b', marginLeft: 5 }}>
                              {r.breakevenMovePct >= 0 ? '+' : ''}{r.breakevenMovePct.toFixed(1)}%
                            </span>
                          )}
                        </td>
                        <td style={td('right', r.cashFlow < 0 ? '#f59e0b' : UP)}>{money(Math.abs(r.cashFlow))}</td>
                        <td style={td('right', '#64748b')} title={r.capitalLabel}>{money(r.capitalBasis)}</td>
                        <td style={td('right', '#cbd5e1')}>{money(r.valueAtTarget)}</td>
                        <td style={td('right', tone(r.netPL))}>{money(r.netPL)}</td>
                        <td style={{ ...td('right', tone(r.roiPct)), fontWeight: 700 }}>{pct(r.roiPct)}</td>
                        <td style={td('right', r.maxLoss == null ? '#f87171' : '#64748b')}>
                          {r.maxLoss == null
                            ? <span title="A short call has no maximum loss — there is no highest price a stock can reach.">Unbounded</span>
                            : money(r.maxLoss)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <p style={{ fontSize: 11, color: '#64748b', margin: '12px 0 0', lineHeight: 1.6 }}>
              <strong style={{ color: '#94a3b8' }}>Reading the Return column.</strong>{' '}
              {action === 'buy' ? (
                <>For a long option the denominator is the premium paid, which is also the whole
                  risk — so a contract that expires worthless reads −100% and cannot read worse.</>
              ) : (
                <>For a short option the denominator is the capital the position ties up
                  {right === 'put' ? ' (the cash a cash-secured put must post)' : ' (the shares a covered call must hold)'},
                  not the premium collected. Measured against premium, every short that expires
                  worthless would read +100% — which tells you nothing about whether the trade was
                  worth the money it locked up.</>
              )}
              {best && (
                <> Highest return here is the <strong style={{ color: '#e2e8f0' }}>${best.strike}</strong> strike
                  at {pct(best.roiPct)} — that is arithmetic at one assumed price, not a
                  recommendation{action === 'buy' ? '; it is also the strike that needed the most movement to get there' : ''}.</>
              )}
            </p>
            <p style={{ fontSize: 10, color: '#475569', margin: '8px 0 0', lineHeight: 1.6 }}>
              Value at expiry is intrinsic value only — no time value, no Greeks, no early
              assignment, no commissions. Premiums marked <span style={{ color: '#f59e0b' }}>*</span> come
              from the last trade because the quote was one-sided; <span style={{ color: '#f59e0b' }}>⚠</span> marks
              a spread of 25% or more of the midpoint, where the shown premium is not a price you
              would actually get filled at.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

const th = (align: 'left' | 'right'): React.CSSProperties => ({
  textAlign: align, padding: '6px 8px', fontWeight: 700, whiteSpace: 'nowrap',
});
const td = (align: 'left' | 'right', color: string): React.CSSProperties => ({
  textAlign: align, padding: '5px 8px', color, whiteSpace: 'nowrap',
  fontVariantNumeric: 'tabular-nums',
});
