/** Closed, local UI commands. Never executes model output or writes a trading object. */
export const CHART_INDICATORS = ['SMA20','SMA50','SMA200','EMA20','EMA50','EMA200','BB','VOL','VWAP','RSI','MACD','SIGNALS','FVG','SR','52W','PIVOTS'] as const;
export type ChartIndicator = typeof CHART_INDICATORS[number];
export const CHART_RANGES = ['5D','1M','3M','6M','1Y','5Y','All','5m'] as const;
export type ChartRange = typeof CHART_RANGES[number];
export const CHART_TIMEFRAMES = ['1d','5m','15m','1h','4h'] as const;
export type ChartTimeframe = typeof CHART_TIMEFRAMES[number];
export type ChartCommand = { kind: 'indicator'; indicator: ChartIndicator; enabled: boolean } | { kind: 'range'; range: ChartRange } | {kind:'timeframe'; timeframe:ChartTimeframe} | { kind: 'undo' };
export type ChartSnapshot = { symbol: string; range: ChartRange; timeframe?:ChartTimeframe; viewport?:{from:number;to:number}; indicators: Record<ChartIndicator, boolean> };
export type ChartController = { applyCommand(command: ChartCommand, symbol: string): Promise<ChartSnapshot> };
export function parseChartCommand(text: string): ChartCommand | null {
  const q = text.trim();
  if (/^undo(?: (?:the )?(?:last )?chart(?: change)?)?$/i.test(q)) return { kind: 'undo' };
  const m = /^(show|hide|add|remove)\s+(SMA\s*(?:20|50|200)|EMA\s*(?:20|50|200)|RSI|MACD|BB|VWAP|VOL|SIGNALS|FVG|SR|52W|PIVOTS)(?:\s+(?:on|from)\s+(?:the\s+)?chart)?$/i.exec(q);
  if (m) return { kind: 'indicator', indicator: m[2].replace(/\s/g,'').toUpperCase() as ChartIndicator, enabled: /show|add/i.test(m[1]) };
  const tf = /^(?:set|switch(?: to)?)\s+(1d|5m|15m|1h|4h)(?:\s+(?:chart|timeframe))?$/i.exec(q);
  if(tf)return {kind:'timeframe',timeframe:tf[1].toLowerCase() as ChartTimeframe};
  const r = /^(?:show|set|switch(?: to)?)\s+(5D|1M|3M|6M|1Y|5Y|All|5m)(?:\s+(?:range|chart|timeframe))?$/i.exec(q);
  if (r) return { kind: 'range', range: r[1].toLowerCase() === 'all' ? 'All' : r[1].toLowerCase() === '5m' ? '5m' : r[1].toUpperCase() as ChartRange };
  if (/^(?:set|switch to)\s+(?:1d|daily)(?:\s+(?:chart|timeframe))?$/i.test(q)) return { kind:'range',range:'3M' };
  return null;
}
