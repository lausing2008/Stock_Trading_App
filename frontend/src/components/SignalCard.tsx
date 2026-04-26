import type { Signal } from '@/lib/api';

const SIGNAL_COLOR: Record<string, string> = {
  BUY:  'bg-green-600',
  HOLD: 'bg-yellow-500',
  WAIT: 'bg-orange-500',
  SELL: 'bg-red-600',
};

type Reasons = {
  trend_above_sma50?: boolean;
  golden_cross?: boolean;
  rsi?: number | null;
  macd_hist?: number | null;
  volume_z?: number | null;
  ml_probability?: number | null;
  ta_score?: number | null;
};

type Factor = { label: string; bullish: boolean; detail: string };

function buildReasons(r: Reasons): Factor[] {
  const factors: Factor[] = [];

  if (r.trend_above_sma50 != null) {
    factors.push({
      label: 'Trend (SMA50)',
      bullish: r.trend_above_sma50,
      detail: r.trend_above_sma50
        ? 'Price above 50-day MA — uptrend intact'
        : 'Price below 50-day MA — downtrend in play',
    });
  }

  if (r.golden_cross != null) {
    factors.push({
      label: r.golden_cross ? 'Golden Cross' : 'Death Cross',
      bullish: r.golden_cross,
      detail: r.golden_cross
        ? 'SMA50 > SMA200 — long-term bull signal'
        : 'SMA50 < SMA200 — long-term bear signal',
    });
  }

  if (r.rsi != null) {
    const rsi = r.rsi;
    const overbought = rsi > 70;
    const oversold = rsi < 30;
    const healthy = rsi >= 40 && rsi <= 70;
    factors.push({
      label: `RSI ${rsi.toFixed(0)}`,
      bullish: healthy || oversold,
      detail: overbought
        ? `RSI ${rsi.toFixed(0)} — overbought, watch for pullback`
        : oversold
          ? `RSI ${rsi.toFixed(0)} — oversold, potential reversal`
          : `RSI ${rsi.toFixed(0)} — healthy momentum range`,
    });
  }

  if (r.macd_hist != null) {
    const bullish = r.macd_hist > 0;
    factors.push({
      label: 'MACD',
      bullish,
      detail: bullish
        ? `MACD histogram +${r.macd_hist.toFixed(3)} — bullish momentum building`
        : `MACD histogram ${r.macd_hist.toFixed(3)} — bearish momentum`,
    });
  }

  if (r.volume_z != null) {
    const bullish = r.volume_z > 0.5;
    factors.push({
      label: 'Volume',
      bullish,
      detail: bullish
        ? `Volume spike (z=${r.volume_z.toFixed(1)}) — strong conviction behind move`
        : r.volume_z < -0.5
          ? `Below-average volume (z=${r.volume_z.toFixed(1)}) — weak conviction`
          : `Average volume (z=${r.volume_z.toFixed(1)}) — no strong signal`,
    });
  }

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

  return (
    <div className="rounded-md border border-slate-800 bg-slate-900 p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-300">AI Signal</h3>
        <span className={`rounded px-2.5 py-0.5 text-sm font-bold text-white ${SIGNAL_COLOR[signal.signal]}`}>
          {signal.signal}
        </span>
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
              <span className={`mt-0.5 flex-shrink-0 text-xs font-bold ${f.bullish ? 'text-green-400' : 'text-red-400'}`}>
                {f.bullish ? '▲' : '▼'}
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
