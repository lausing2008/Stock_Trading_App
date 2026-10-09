// T411-IVHV: three weeks of gain/loss for the stock beside the call and put that were at the
// money when the window opened.
//
// The two option columns are ONE contract each, fixed at the first session and held to the
// last — not "whatever was at the money that day". That is what makes the table readable as a
// position rather than a sequence of unrelated instruments, and it is why a row can show the
// stock up and the call down: the call paid theta that day and the move did not cover it.
//
// Availability is the other thing this component has to get right. The per-contract price
// history behind the option columns exists only for the symbols this platform archives daily
// (35 as of 2026-09-28), because Unusual Whales' option-chain window is a rolling one and an
// uncaptured day expires permanently. For every other symbol the honest answer is to say so,
// not to render an empty pair of columns that reads as "the options did nothing".
import type { OptionsPerformance, OptionsPerformanceLeg } from '@/lib/api';
import { shortDate } from '@/lib/ivHvChart';

const UP = '#4ade80';
const DOWN = '#f87171';

function pct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

function toneOf(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v) || v === 0) return '#64748b';
  return v > 0 ? UP : DOWN;
}

const REASON_TEXT: Record<string, string> = {
  no_option_history:
    'This platform does not archive per-contract option prices for this symbol, so there is no way to show what its contracts actually did. The archive covers a fixed set of symbols captured daily; Unusual Whales serves option chains on a rolling window, so days that were never captured cannot be recovered later.',
  insufficient_option_history:
    'Only one archived session exists for this symbol so far — there is nothing to compare it against yet.',
  no_underlying_price: 'No daily closing price is stored for this symbol over the window.',
  query_error: 'The performance query failed. This is a fault on our side, not a statement about the symbol.',
};

export default function OptionsPerformanceTable({ data }: { data: OptionsPerformance }) {
  if (!data.available) {
    return (
      <Shell symbol={data.symbol}>
        <p style={{ fontSize: 12, color: '#94a3b8', margin: 0, lineHeight: 1.6 }}>
          {REASON_TEXT[data.reason ?? ''] ?? 'Not available for this symbol.'}
        </p>
      </Shell>
    );
  }

  const rows = data.rows ?? [];
  const contracts = data.contracts ?? {};
  const summary = data.summary ?? {};
  const hasLegs = Boolean(contracts.call || contracts.put);

  return (
    <Shell symbol={data.symbol}>
      <p style={{ fontSize: 11, color: '#64748b', margin: '0 0 12px', lineHeight: 1.6 }}>
        {data.sessions} sessions, {shortDate(data.start_date ?? '')} → {shortDate(data.end_date ?? '')}.
        {hasLegs ? (
          <>
            {' '}The option columns follow a single contract each, chosen as the strike nearest
            the {data.entry_spot?.toFixed(2)} open and held across every row — so the call can
            fall on a day the stock rises. Time decay, volatility and quote changes can contribute; this table does not isolate their effects.
          </>
        ) : (
          <>
            {' '}<strong style={{ color: '#f59e0b' }}>Option columns unavailable:</strong> the
            archive holds sessions for this symbol, but no contract was both quoted at the start
            of the window and still listed past its end, so there is no single position to track
            across these rows.
          </>
        )}
      </p>

      {hasLegs && (
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 12 }}>
          {(['call', 'put'] as const).map(side => {
            const c = contracts[side];
            if (!c) return null;
            return (
              <div key={side} style={{ fontSize: 11, color: '#64748b' }}>
                <span style={{ color: side === 'call' ? UP : DOWN, fontWeight: 700, textTransform: 'uppercase' }}>{side}</span>
                {' '}${c.strike} exp {shortDate(c.expiry)} · entry ${c.entry_mark.toFixed(2)}
                {c.marks_available < (data.sessions ?? 0) && (
                  <span title="Some sessions are missing from the archive; those rows show a dash rather than a carried-forward price."
                        style={{ color: '#f59e0b', marginLeft: 6, cursor: 'help' }}>
                    {c.marks_available}/{data.sessions} days quoted
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 560 }}>
          <thead>
            <tr style={{ color: '#64748b', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4 }}>
              <th style={th('left')}>Date</th>
              <th style={th('right')}>Close</th>
              <th style={th('right')}>Day</th>
              <th style={th('right')}>Total</th>
              {contracts.call && <><th style={th('right')}>Call</th><th style={th('right')}>Day</th><th style={th('right')}>Total</th></>}
              {contracts.put && <><th style={th('right')}>Put</th><th style={th('right')}>Day</th><th style={th('right')}>Total</th></>}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.date} style={{ borderTop: '1px solid #1e293b' }}>
                <td style={td('left', '#94a3b8')}>{shortDate(r.date)}</td>
                <td style={td('right', '#e2e8f0')}>{r.close != null ? r.close.toFixed(2) : '—'}</td>
                <td style={td('right', toneOf(r.change_pct))}>{pct(r.change_pct)}</td>
                <td style={td('right', toneOf(r.cum_pct))}>{pct(r.cum_pct)}</td>
                {contracts.call && <LegCells leg={r.call} />}
                {contracts.put && <LegCells leg={r.put} />}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: '2px solid #1e293b', fontWeight: 700 }}>
              <td style={td('left', '#64748b')}>{summary.underlying?.measured_sessions ?? 0} sessions</td>
              <td style={td('right', '#64748b')} />
              <td style={td('right', '#94a3b8')} title="Sessions the column closed up vs down. The first row has no prior session inside the window, so it is not counted either way.">
                {summary.underlying?.up ?? 0}↑ {summary.underlying?.down ?? 0}↓
              </td>
              <td style={td('right', toneOf(summary.underlying?.total_pct))}>{pct(summary.underlying?.total_pct)}</td>
              {contracts.call && <TallyCells t={summary.call} />}
              {contracts.put && <TallyCells t={summary.put} />}
            </tr>
          </tfoot>
        </table>
      </div>

      {data.mark_basis && (
        <p style={{ fontSize: 11, color: '#64748b', margin: '10px 0 0', lineHeight: 1.5 }}>
          <strong style={{ color: '#94a3b8' }}>Marks:</strong> {data.mark_basis} Hover a contract
          row above to see how many sessions it was actually quoted on.
        </p>
      )}
    </Shell>
  );
}

function LegCells({ leg }: { leg?: OptionsPerformanceLeg }) {
  return (
    <>
      <td style={td('right', '#e2e8f0')} title={leg?.spread_pct != null ? `Bid/ask spread ${leg.spread_pct}% of the mark` : undefined}>
        {leg?.mark != null ? leg.mark.toFixed(2) : '—'}
      </td>
      <td style={td('right', toneOf(leg?.change_pct))}>{pct(leg?.change_pct)}</td>
      <td style={td('right', toneOf(leg?.cum_pct))}>{pct(leg?.cum_pct)}</td>
    </>
  );
}

function TallyCells({ t }: { t?: { up: number; down: number; total_pct: number | null } }) {
  return (
    <>
      <td style={td('right', '#64748b')} />
      <td style={td('right', '#94a3b8')}>{t?.up ?? 0}↑ {t?.down ?? 0}↓</td>
      <td style={td('right', toneOf(t?.total_pct))}>{pct(t?.total_pct)}</td>
    </>
  );
}

function Shell({ symbol, children }: { symbol: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <h2 style={{ fontSize: 15, fontWeight: 700, color: '#cbd5e1', margin: '0 0 12px' }}>
        Gain / Loss — {symbol} and its at-the-money options
      </h2>
      <div style={{ background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, padding: '14px 16px' }}>
        {children}
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
