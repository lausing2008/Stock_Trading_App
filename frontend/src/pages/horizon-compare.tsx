import React, { useState } from 'react';
import Head from 'next/head';
import Link from 'next/link';

// ── Types ─────────────────────────────────────────────────────────────────────

type Horizon = 'SHORT' | 'SWING' | 'LONG' | 'GROWTH';
type Market  = 'US' | 'HK';

type DimRow = {
  label:    string;
  category: string;
  SHORT:    string;
  SWING:    string;
  LONG:     string;
  GROWTH:   string;
  hk_note?: string;
};

// ── Style profile data (from signals.py _STYLE_PROFILES) ─────────────────────

const ROWS: DimRow[] = [
  // ── Holding periods ──────────────────────────────────────────────────────
  {
    category: 'Holding Period',
    label: 'Typical hold',
    SHORT:  '3–7 days',
    SWING:  '10–20 days',
    LONG:   '30–60 days',
    GROWTH: '10–30 days',
    hk_note: 'Same periods; HK entries limited to HKT market hours.',
  },
  {
    category: 'Holding Period',
    label: 'Stop-loss default',
    SHORT:  '2–3%',
    SWING:  '4–6%',
    LONG:   '6–10%',
    GROWTH: '5–8%',
    hk_note: 'Paper trading uses ATR-based stops; same logic applies for HK.',
  },
  {
    category: 'Holding Period',
    label: 'Profit target',
    SHORT:  '4–6%',
    SWING:  '8–15%',
    LONG:   '15–30%',
    GROWTH: '15–40%',
  },

  // ── AI Signal thresholds (bull regime) ───────────────────────────────────
  {
    category: 'AI Signal Thresholds (Bull Regime)',
    label: 'BUY threshold',
    SHORT:  '> 63%',
    SWING:  '> 72%',
    LONG:   '> 60%',
    GROWTH: '> 60%',
    hk_note: 'SWING HK uses 74% (tighter). LONG HK: 62%.',
  },
  {
    category: 'AI Signal Thresholds (Bull Regime)',
    label: 'HOLD threshold',
    SHORT:  '> 46%',
    SWING:  '> 50%',
    LONG:   '> 46%',
    GROWTH: '> 45%',
  },
  {
    category: 'AI Signal Thresholds (Bull Regime)',
    label: 'SELL threshold',
    SHORT:  '< 35%',
    SWING:  '< 35%',
    LONG:   '< 35%',
    GROWTH: '< 35%',
    hk_note: 'HK SHORT SELL → HOLD override: no SELL signal emitted for SHORT horizon on HK stocks (29.2% win rate — no edge).',
  },
  {
    category: 'AI Signal Thresholds (Bear Regime)',
    label: 'BUY threshold',
    SHORT:  '> 68%',
    SWING:  '> 76%',
    LONG:   '> 70%',
    GROWTH: '> 68%',
  },
  {
    category: 'AI Signal Thresholds (Bear Regime)',
    label: 'HOLD threshold',
    SHORT:  '> 52%',
    SWING:  '> 56%',
    LONG:   '> 54%',
    GROWTH: '> 52%',
  },

  // ── ML model weight ───────────────────────────────────────────────────────
  {
    category: 'ML Model Weight',
    label: 'ML weight cap',
    SHORT:  '30%',
    SWING:  '65%',
    LONG:   '45%',
    GROWTH: '60%',
    hk_note: 'SHORT is TA-dominant (30% ML max). SWING and GROWTH lean more on ML predictions.',
  },
  {
    category: 'ML Model Weight',
    label: 'ML weight floor',
    SHORT:  '10%',
    SWING:  '15%',
    LONG:   '12%',
    GROWTH: '20%',
    hk_note: 'Floor is AUC-scaled: max(0, (auc-0.50)/0.10) × floor. Near-random models (AUC≈0.50) get 0 floor uplift.',
  },
  {
    category: 'ML Model Weight',
    label: 'ML precision floor',
    SHORT:  '73% (HK: 78%)',
    SWING:  '63% (HK: 70%)',
    LONG:   '53% (HK: 60%)',
    GROWTH: '63% (HK: 70%)',
    hk_note: 'HK precision floors are tighter — less efficient market means model needs higher demonstrated accuracy before being trusted.',
  },
  {
    category: 'ML Model Weight',
    label: 'Ensemble weights (lgb/xgb/rf)',
    SHORT:  '45/30/25%',
    SWING:  '45/30/25%',
    LONG:   '45/30/25%',
    GROWTH: '45/30/25%',
    hk_note: 'LightGBM leads (45%) — better implicit regularization on 59-feature financial data.',
  },

  // ── TA indicators ─────────────────────────────────────────────────────────
  {
    category: 'Technical Analysis',
    label: 'ADX minimum',
    SHORT:  '27 (strict)',
    SWING:  '15 (moderate)',
    LONG:   'None',
    GROWTH: '12 (relaxed)',
    hk_note: 'ADX compression: signals dampened ×0.85–0.92 below threshold rather than blocked.',
  },
  {
    category: 'Technical Analysis',
    label: 'ADX compression ratio',
    SHORT:  '×0.85',
    SWING:  '×0.90',
    LONG:   'None',
    GROWTH: '×0.92',
  },
  {
    category: 'Technical Analysis',
    label: 'Weekly chart boost',
    SHORT:  '+8%',
    SWING:  '+12%',
    LONG:   '+18%',
    GROWTH: '+8% (gate skipped)',
    hk_note: 'GROWTH skips the weekly BUY gate — growth stocks legitimately run "overbought" on weekly charts.',
  },
  {
    category: 'Technical Analysis',
    label: 'Weekly chart compress',
    SHORT:  '×0.93',
    SWING:  '×0.85',
    LONG:   '×0.80',
    GROWTH: '×0.92',
  },
  {
    category: 'Technical Analysis',
    label: 'High-vol compression',
    SHORT:  '×0.92',
    SWING:  '×0.85',
    LONG:   '×0.90',
    GROWTH: '×0.88',
  },
  {
    category: 'Technical Analysis',
    label: 'Breadth compression',
    SHORT:  '×0.90',
    SWING:  '×0.90',
    LONG:   '×0.92',
    GROWTH: '×0.95',
  },
  {
    category: 'Technical Analysis',
    label: 'Relative strength compress',
    SHORT:  '×0.90',
    SWING:  '×0.85',
    LONG:   '×0.80',
    GROWTH: 'None',
    hk_note: 'GROWTH skips RS compression — growth names often lag sector before explosive moves.',
  },

  // ── Earnings & news gates ─────────────────────────────────────────────────
  {
    category: 'Earnings & News',
    label: 'Earnings compression (≤2d)',
    SHORT:  'None',
    SWING:  '×0.65',
    LONG:   'None',
    GROWTH: '×0.60',
    hk_note: 'SHORT avoids earnings compression — 3-7d hold clears past the event. LONG ignores it for the same reason.',
  },
  {
    category: 'Earnings & News',
    label: 'Earnings compression (≤5d)',
    SHORT:  'None',
    SWING:  '×0.85',
    LONG:   'None',
    GROWTH: '×0.80',
  },
  {
    category: 'Earnings & News',
    label: 'Negative news compress (≤25)',
    SHORT:  'None',
    SWING:  '×0.75',
    LONG:   'None',
    GROWTH: '×0.80',
    hk_note: 'SHORT and LONG ignore news sentiment — SHORT exits before news materializes; LONG trades through it.',
  },

  // ── Max compression ───────────────────────────────────────────────────────
  {
    category: 'Signal Compression',
    label: 'Max total compression',
    SHORT:  '×0.70 (floor)',
    SWING:  '×0.55 (floor)',
    LONG:   '×0.65 (floor)',
    GROWTH: '×0.60 (floor)',
    hk_note: 'Multiple compressions stack multiplicatively but are bounded below by this floor.',
  },
  {
    category: 'Signal Compression',
    label: 'Min active pillars',
    SHORT:  'None',
    SWING:  '3+ pillars',
    LONG:   '3+ pillars',
    GROWTH: 'None',
    hk_note: 'SWING and LONG require 3+ active TA pillars. 2-pillar BUYs get an additional ×0.70 compress.',
  },
  {
    category: 'Signal Compression',
    label: 'K-score boost',
    SHORT:  'No',
    SWING:  'No',
    LONG:   'Yes',
    GROWTH: 'No',
    hk_note: 'LONG uniquely rewards high K-score (ranking quality) with a mild BUY probability boost.',
  },

  // ── HK-specific gates ─────────────────────────────────────────────────────
  {
    category: 'HK-Specific Gates',
    label: 'Southbound flow compress',
    SHORT:  '×0.85 (if negative)',
    SWING:  '×0.85 (if negative)',
    LONG:   '×0.85 (if negative)',
    GROWTH: '×0.85 (if negative)',
    hk_note: 'HK only. Negative 5-day mainland southbound flow compresses BUY fused score by 15%. Applied before threshold check.',
  },
  {
    category: 'HK-Specific Gates',
    label: 'HKD 50M liquidity floor',
    SHORT:  'Not applied',
    SWING:  '×0.30 (if low)',
    LONG:   'Not applied',
    GROWTH: '×0.30 (if low)',
    hk_note: 'SWING and GROWTH only. If avg daily turnover (20d) < HKD 50M, BUY compressed ×0.30 → effectively HOLD.',
  },
  {
    category: 'HK-Specific Gates',
    label: 'SELL override',
    SHORT:  'SELL → HOLD',
    SWING:  'Normal',
    LONG:   'Normal',
    GROWTH: 'Normal',
    hk_note: 'HK SHORT SELL signals have 29.2% win rate (vs 53.2% for US). Overridden to HOLD to eliminate losing signals.',
  },
  {
    category: 'HK-Specific Gates',
    label: 'HSI regime gate',
    SHORT:  'HSI < SMA200',
    SWING:  'HSI < SMA200',
    LONG:   'HSI < SMA200',
    GROWTH: 'HSI < SMA200',
    hk_note: 'Bear gate uses HSI vs SMA200 instead of SPY. Suspension threshold: 7 days (vs 3 for US).',
  },

  // ── Calibration ───────────────────────────────────────────────────────────
  {
    category: 'Calibration',
    label: 'BUY threshold auto-calibration',
    SHORT:  'Weekly (outcomes sweep)',
    SWING:  'Weekly (outcomes sweep)',
    LONG:   'Weekly (outcomes sweep)',
    GROWTH: 'Weekly (outcomes sweep)',
    hk_note: 'POST /outcomes/calibrate/apply sweeps confidence 40–85. Redis key: stockai:signal_thresholds:{HORIZON}.',
  },
  {
    category: 'Calibration',
    label: 'SELL threshold auto-calibration',
    SHORT:  'Weekly (confidence ≤ sweep)',
    SWING:  'Weekly (confidence ≤ sweep)',
    LONG:   'Weekly (confidence ≤ sweep)',
    GROWTH: 'Weekly (confidence ≤ sweep)',
    hk_note: 'SELL sweep (T228): confidence 20–45, lower = stronger SELL. Redis key: stockai:signal_thresholds:SELL:{HORIZON}.',
  },
  {
    category: 'Calibration',
    label: 'TA weights calibration',
    SHORT:  'Shared (Sunday)',
    SWING:  'Shared (Sunday)',
    LONG:   'Shared (Sunday)',
    GROWTH: 'Shared (Sunday)',
    hk_note: 'stockai:ta_weights in Redis (90-day TTL). Survives Docker rebuilds.',
  },
];

// ── Category colors ───────────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<string, string> = {
  'Holding Period':                        '#38bdf8',
  'AI Signal Thresholds (Bull Regime)':    '#22c55e',
  'AI Signal Thresholds (Bear Regime)':    '#ef4444',
  'ML Model Weight':                       '#a78bfa',
  'Technical Analysis':                    '#fb923c',
  'Earnings & News':                       '#f59e0b',
  'Signal Compression':                    '#64748b',
  'HK-Specific Gates':                     '#e11d48',
  'Calibration':                           '#06b6d4',
};

const HORIZON_COLORS: Record<Horizon, string> = {
  SHORT:  '#ef4444',
  SWING:  '#22c55e',
  LONG:   '#a78bfa',
  GROWTH: '#fb923c',
};

const HORIZON_DESC: Record<Horizon, string> = {
  SHORT:  '3–7d • TA-dominant • tight stops',
  SWING:  '10–20d • balanced ML+TA • mainstream',
  LONG:   '30–60d • fundamentals + ML • patient',
  GROWTH: '10–30d • momentum + ML • high-vol names',
};

// ── Component ─────────────────────────────────────────────────────────────────

export default function HorizonComparePage() {
  const [market, setMarket]           = useState<Market>('US');
  const [filterCat, setFilterCat]     = useState<string>('All');
  const [showHkNote, setShowHkNote]   = useState(false);

  const categories = ['All', ...Array.from(new Set(ROWS.map(r => r.category)))];

  const visible = ROWS.filter(r =>
    filterCat === 'All' || r.category === filterCat
  );

  const groupedRows: [string, DimRow[]][] = [];
  let lastCat = '';
  for (const row of visible) {
    if (row.category !== lastCat) {
      groupedRows.push([row.category, []]);
      lastCat = row.category;
    }
    groupedRows[groupedRows.length - 1][1].push(row);
  }

  const COL_W = '15%';

  return (
    <>
      <Head><title>Horizon Comparison — StockAI</title></Head>

      <div style={{ minHeight: '100vh', background: '#060d1a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '24px 20px' }}>

        {/* Header */}
        <div style={{ maxWidth: 1200, margin: '0 auto 24px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
            <Link href="/paper-gates" style={{ color: '#64748b', fontSize: 13, textDecoration: 'none' }}>← Entry Gates</Link>
            <span style={{ color: '#1e293b' }}>|</span>
            <Link href="/paper-portfolio" style={{ color: '#64748b', fontSize: 13, textDecoration: 'none' }}>Paper Portfolio</Link>
          </div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Horizon Comparison</h1>
          <p style={{ margin: '6px 0 0', fontSize: 13, color: '#64748b' }}>
            Signal generation parameters across all 4 trading horizons — thresholds, ML weights, TA gates, HK-specific rules.
          </p>
        </div>

        {/* Horizon summary cards */}
        <div style={{ maxWidth: 1200, margin: '0 auto 20px', display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
          {(['SHORT','SWING','LONG','GROWTH'] as Horizon[]).map(h => (
            <div key={h} style={{
              background: '#0b1420', border: `1px solid ${HORIZON_COLORS[h]}44`,
              borderRadius: 10, padding: '14px 16px',
            }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: HORIZON_COLORS[h], letterSpacing: '0.06em', marginBottom: 4 }}>{h}</div>
              <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.5 }}>{HORIZON_DESC[h]}</div>
            </div>
          ))}
        </div>

        {/* Controls */}
        <div style={{ maxWidth: 1200, margin: '0 auto 16px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          {/* Market toggle */}
          <div style={{ display: 'flex', gap: 0, borderRadius: 7, overflow: 'hidden', border: '1px solid #1e293b' }}>
            {(['US','HK'] as Market[]).map(m => (
              <button key={m} onClick={() => setMarket(m)} style={{
                padding: '6px 18px', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                background: market === m ? '#1e40af' : '#0b1420',
                color: market === m ? '#93c5fd' : '#64748b',
                border: 'none', transition: 'all 0.15s',
              }}>{m}</button>
            ))}
          </div>

          {/* Category filter */}
          <select
            value={filterCat}
            onChange={e => setFilterCat(e.target.value)}
            style={{
              background: '#0b1420', border: '1px solid #1e293b', borderRadius: 7,
              color: '#e2e8f0', fontSize: 12, padding: '6px 10px', cursor: 'pointer',
            }}
          >
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>

          {market === 'HK' && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#94a3b8', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={showHkNote}
                onChange={e => setShowHkNote(e.target.checked)}
                style={{ accentColor: '#e11d48' }}
              />
              Show HK notes
            </label>
          )}
        </div>

        {/* Comparison table */}
        <div style={{ maxWidth: 1200, margin: '0 auto', overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: '10px 12px', color: '#64748b', fontWeight: 600, width: '28%', background: '#0b1420', borderBottom: '1px solid #1e293b' }}>
                  Parameter
                </th>
                {(['SHORT','SWING','LONG','GROWTH'] as Horizon[]).map(h => (
                  <th key={h} style={{
                    textAlign: 'center', padding: '10px 8px', fontWeight: 700, width: COL_W,
                    color: HORIZON_COLORS[h], background: '#0b1420', borderBottom: '1px solid #1e293b',
                    borderLeft: '1px solid #1e293b22',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groupedRows.map(([cat, rows]) => (
                <React.Fragment key={cat}>
                  {/* Category header row */}
                  <tr>
                    <td colSpan={5} style={{
                      padding: '10px 12px 4px',
                      fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
                      color: CATEGORY_COLORS[cat] || '#64748b',
                      borderTop: '1px solid #1e293b',
                      textTransform: 'uppercase',
                    }}>{cat}</td>
                  </tr>
                  {rows.map((row, i) => (
                    <tr key={row.label} style={{ background: i % 2 === 0 ? '#0b1420' : '#060d1a' }}>
                      <td style={{ padding: '7px 12px', color: '#94a3b8' }}>
                        {row.label}
                        {market === 'HK' && showHkNote && row.hk_note && (
                          <div style={{ marginTop: 3, fontSize: 10, color: '#e11d4888', lineHeight: 1.4 }}>
                            {row.hk_note}
                          </div>
                        )}
                      </td>
                      {(['SHORT','SWING','LONG','GROWTH'] as Horizon[]).map(h => (
                        <td key={h} style={{
                          padding: '7px 8px', textAlign: 'center',
                          color: row[h].startsWith('None') || row[h] === 'No' ? '#475569' : '#e2e8f0',
                          borderLeft: '1px solid #1e293b22',
                          fontVariantNumeric: 'tabular-nums',
                        }}>
                          {row[h]}
                        </td>
                      ))}
                    </tr>
                  ))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>

        {/* HK banner */}
        {market === 'HK' && (
          <div style={{
            maxWidth: 1200, margin: '20px auto 0',
            background: '#1a0a0f', border: '1px solid #e11d4844',
            borderRadius: 10, padding: '14px 16px',
          }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#e11d48', marginBottom: 8 }}>HK Market Summary</div>
            <div style={{ fontSize: 11, color: '#94a3b8', lineHeight: 1.7 }}>
              <b style={{ color: '#f1f5f9' }}>Tighter precision floors:</b> SHORT 78% / SWING 70% / LONG 60% / GROWTH 70% (vs US: 73/63/53/63%)<br/>
              <b style={{ color: '#f1f5f9' }}>Southbound flow gate:</b> Negative 5-day mainland buying compresses HK BUY signals ×0.85<br/>
              <b style={{ color: '#f1f5f9' }}>HKD 50M liquidity floor:</b> SWING &amp; GROWTH BUYs suppressed for stocks with &lt; HKD 50M avg daily turnover<br/>
              <b style={{ color: '#f1f5f9' }}>SHORT SELL disabled:</b> 29.2% win rate — SHORT SELL signals on HK stocks become HOLD<br/>
              <b style={{ color: '#f1f5f9' }}>Bear gate:</b> HSI vs SMA200 (not SPY). Suspension threshold: 7 consecutive days (vs 3 for US)
            </div>
          </div>
        )}

        {/* Legend */}
        <div style={{ maxWidth: 1200, margin: '16px auto 0', fontSize: 11, color: '#475569', textAlign: 'center' }}>
          All thresholds shown for the standard regime.
          Dynamic calibration may adjust BUY/SELL thresholds weekly based on live trade outcomes (Redis-backed, 30-day TTL).
          Toggle US/HK above to switch market context.
        </div>
      </div>
    </>
  );
}
