import type { Signal } from '@/lib/api';

const SIGNAL_COLOR: Record<string, string> = {
  BUY:  'bg-green-600',
  HOLD: 'bg-yellow-500',
  WAIT: 'bg-orange-500',
  SELL: 'bg-red-600',
};

type Reasons = {
  trend_above_sma50?: boolean;
  sma50_above_sma200?: boolean;
  golden_cross_event?: boolean;
  death_cross_event?: boolean;
  rsi?: number | null;
  stoch_rsi_k?: number | null;
  stoch_rsi_oversold?: boolean;
  stoch_rsi_overbought?: boolean;
  stoch_rsi_cross_up?: boolean;
  rsi_divergence?: string;
  macd_hist?: number | null;
  macd_rising?: boolean;
  macd_zero_cross_up?: boolean;
  bb_pct_b?: number | null;
  adx?: number | null;
  adx_bullish?: boolean;
  obv_bullish?: boolean;
  volume_z?: number | null;
  ml_probability?: number | null;
  market_regime?: string;
  ta_score?: number | null;
};

type Factor = { label: string; bullish: boolean; detail: string; warning?: boolean };

function buildReasons(r: Reasons): Factor[] {
  const factors: Factor[] = [];

  // Market regime — shown first if bear
  if (r.market_regime === 'bear') {
    factors.push({
      label: 'Market Regime',
      bullish: false,
      warning: true,
      detail: 'S&P 500 below 200MA — bear market, higher BUY threshold applied',
    });
  }

  // Death cross warning
  if (r.death_cross_event) {
    factors.push({
      label: 'Death Cross',
      bullish: false,
      warning: true,
      detail: 'SMA50 just crossed below SMA200 — major bearish signal',
    });
  }

  // Trend
  if (r.trend_above_sma50 != null) {
    factors.push({
      label: 'Trend (SMA50)',
      bullish: r.trend_above_sma50,
      detail: r.trend_above_sma50
        ? 'Price above 50-day MA — uptrend intact'
        : 'Price below 50-day MA — downtrend in play',
    });
  }

  if (r.sma50_above_sma200 != null) {
    factors.push({
      label: r.golden_cross_event ? '✦ Golden Cross' : 'SMA50 vs SMA200',
      bullish: r.sma50_above_sma200,
      detail: r.golden_cross_event
        ? 'SMA50 just crossed above SMA200 — long-term bull signal'
        : r.sma50_above_sma200
          ? 'SMA50 above SMA200 — bull regime'
          : 'SMA50 below SMA200 — bear regime',
    });
  }

  // RSI
  if (r.rsi != null) {
    const rsi = r.rsi;
    const isIdeal = rsi >= 45 && rsi < 65;
    const isRecovering = rsi >= 35 && rsi < 45;
    const isExtended = rsi >= 65 && rsi < 72;
    const isOverbought = rsi >= 72;
    const isOversold = rsi < 35;
    factors.push({
      label: `RSI ${rsi.toFixed(0)}`,
      bullish: isIdeal || isRecovering || isOversold,
      detail: isOverbought
        ? `RSI ${rsi.toFixed(0)} — overbought (>72), elevated pullback risk`
        : isExtended
          ? `RSI ${rsi.toFixed(0)} — extended but not extreme`
          : isIdeal
            ? `RSI ${rsi.toFixed(0)} — ideal entry zone (45–65)`
            : isRecovering
              ? `RSI ${rsi.toFixed(0)} — recovering from oversold`
              : `RSI ${rsi.toFixed(0)} — oversold (<35), potential reversal`,
    });
  }

  // Stochastic RSI
  if (r.stoch_rsi_k != null) {
    const k = r.stoch_rsi_k * 100;
    const oversold   = r.stoch_rsi_oversold;
    const overbought = r.stoch_rsi_overbought;
    const crossUp    = r.stoch_rsi_cross_up;
    factors.push({
      label: `Stoch RSI ${k.toFixed(0)}`,
      bullish: oversold || (crossUp ?? false),
      detail: crossUp
        ? `%K ${k.toFixed(0)} — just crossed up from oversold (strong entry signal)`
        : oversold
          ? `%K ${k.toFixed(0)} — oversold zone (<20), RSI at a low extreme`
          : overbought
            ? `%K ${k.toFixed(0)} — overbought zone (>80), RSI at a high extreme`
            : `%K ${k.toFixed(0)} — neutral zone`,
    });
  }

  // RSI divergence
  if (r.rsi_divergence && r.rsi_divergence !== 'none') {
    factors.push({
      label: 'RSI Divergence',
      bullish: r.rsi_divergence === 'bullish',
      warning: r.rsi_divergence === 'bearish',
      detail: r.rsi_divergence === 'bearish'
        ? 'Price making higher highs but RSI declining — momentum fading, reversal risk'
        : 'Price making lower lows but RSI recovering — hidden bullish momentum',
    });
  }

  // MACD
  if (r.macd_hist != null) {
    const bullish = r.macd_hist > 0;
    const zeroCross = r.macd_zero_cross_up;
    factors.push({
      label: zeroCross ? '✦ MACD Zero Cross' : 'MACD',
      bullish: bullish || (zeroCross ?? false),
      detail: zeroCross
        ? `MACD just crossed above zero — trend direction confirmed bullish`
        : bullish
          ? `Histogram +${r.macd_hist.toFixed(3)}${r.macd_rising ? ' ↑ rising' : ''} — bullish momentum`
          : `Histogram ${r.macd_hist.toFixed(3)}${r.macd_rising ? ' ↑ recovering' : ' ↓ falling'} — bearish momentum`,
    });
  }

  // ADX
  if (r.adx != null) {
    const trending = r.adx > 25;
    factors.push({
      label: `ADX ${r.adx.toFixed(0)}`,
      bullish: r.adx_bullish ?? false,
      detail: !trending
        ? `ADX ${r.adx.toFixed(0)} — weak/choppy market, signals less reliable`
        : r.adx_bullish
          ? `ADX ${r.adx.toFixed(0)} — strong bullish trend (+DI > −DI)`
          : `ADX ${r.adx.toFixed(0)} — trending but bearish direction`,
    });
  }

  // OBV
  if (r.obv_bullish != null) {
    factors.push({
      label: 'OBV (Volume)',
      bullish: r.obv_bullish,
      detail: r.obv_bullish
        ? 'On-Balance Volume trending up — volume confirming price direction'
        : 'OBV trending down — volume not confirming the price move',
    });
  }

  // ML
  if (r.ml_probability != null) {
    const pct = (r.ml_probability * 100).toFixed(1);
    const bullish = r.ml_probability > 0.5;
    factors.push({
      label: 'ML Model',
      bullish,
      detail: `XGBoost predicts ${pct}% probability of upward move`,
    });
  }

  return factors;
}

export default function SignalCard({ signal }: { signal: Signal }) {
  const reasons = signal.reasons as Reasons;
  const factors = buildReasons(reasons ?? {});
  const taScore = reasons?.ta_score;
  const regime  = reasons?.market_regime;

  return (
    <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-300">AI Signal</h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {regime === 'bear' && (
            <span style={{ fontSize: '9px', fontWeight: 700, color: '#f87171', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', padding: '1px 6px', borderRadius: '4px' }}>
              BEAR MKT
            </span>
          )}
          <span className={`rounded px-2.5 py-0.5 text-sm font-bold text-white ${SIGNAL_COLOR[signal.signal]}`}>
            {signal.signal}
          </span>
        </div>
      </div>

      {/* Scores */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <div className="text-center">
          <div className="text-lg font-bold text-slate-100">{signal.confidence.toFixed(0)}</div>
          <div className="text-xs text-slate-500">Confidence</div>
        </div>
        <div className="text-center">
          <div className="text-lg font-bold text-slate-100">{(signal.bullish_probability * 100).toFixed(0)}%</div>
          <div className="text-xs text-slate-500">Bullish</div>
        </div>
        <div className="text-center">
          <div className="text-lg font-bold text-slate-100">{signal.horizon}</div>
          <div className="text-xs text-slate-500">Horizon</div>
        </div>
      </div>

      {/* Confidence bar */}
      <div className="h-1 rounded-full bg-slate-800 mb-3 overflow-hidden">
        <div
          className={`h-full rounded-full ${signal.signal === 'BUY' ? 'bg-green-500' : signal.signal === 'SELL' ? 'bg-red-500' : signal.signal === 'WAIT' ? 'bg-orange-500' : 'bg-yellow-500'}`}
          style={{ width: `${signal.bullish_probability * 100}%` }}
        />
      </div>

      {/* Reasoning factors */}
      {factors.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs font-medium text-slate-400 mb-1">Why this signal:</div>
          {factors.map((f, i) => (
            <div key={i} className="flex items-start gap-2">
              <span style={{
                marginTop: '2px', flexShrink: 0, fontSize: '11px', fontWeight: 700,
                color: f.warning ? '#f97316' : f.bullish ? '#4ade80' : '#f87171',
              }}>
                {f.warning ? '⚠' : f.bullish ? '▲' : '▼'}
              </span>
              <div>
                <span className="text-xs font-medium text-slate-300">{f.label}:</span>{' '}
                <span className="text-xs text-slate-500">{f.detail}</span>
              </div>
            </div>
          ))}
          {taScore != null && (
            <div className="mt-2 pt-2 border-t border-slate-800 text-xs text-slate-500">
              TA composite score: <span className="text-slate-300 font-medium">{(taScore * 100).toFixed(0)}/100</span>
              {reasons?.ml_probability == null && (
                <span className="ml-2 text-amber-600">· ML unavailable (train model for better accuracy)</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
