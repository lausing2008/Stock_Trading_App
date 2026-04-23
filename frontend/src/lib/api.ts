const BASE = process.env.NEXT_PUBLIC_API_URL ?? '/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { ...init, headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) } });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return (await r.json()) as T;
}

export const api = {
  listStocks: (market?: string) => request<Stock[]>(`/stocks${market ? `?market=${market}` : ''}`),
  latestPrices: () => request<LatestPrice[]>(`/stocks/latest_prices`),
  getStock: (symbol: string) => request<Stock>(`/stocks/${symbol}`),
  getPrices: (symbol: string, tf = '1d', limit = 400) =>
    request<Price[]>(`/stocks/${symbol}/prices?timeframe=${tf}&limit=${limit}`),
  overview: (symbol: string) => request<Overview>(`/aggregate/overview/${symbol}`),
  rankings: (market?: string) =>
    request<{ rankings: RankingRow[] }>(`/rankings${market ? `?market=${market}` : ''}`),
  signal: (symbol: string) => request<Signal>(`/signals/${symbol}`),
  allSignals: () => request<SignalSummary[]>(`/signals`),
  predict: (symbol: string, model = 'xgboost') =>
    request<Prediction>(`/ml/predict`, { method: 'POST', body: JSON.stringify({ symbol, model }) }),
  trainModel: (symbol: string, model = 'xgboost') =>
    request<{ status: string }>(`/ml/train`, { method: 'POST', body: JSON.stringify({ symbol, model }) }),
  listModels: () => request<string[]>(`/ml/models`),
  getNews: (symbol: string) => request<NewsItem[]>(`/stocks/${symbol}/news`),
  createStrategy: (body: unknown) => request<{ id: number }>(`/strategies`, { method: 'POST', body: JSON.stringify(body) }),
  listStrategies: () => request<{ id: number; name: string; description?: string }[]>(`/strategies`),
  backtest: (body: unknown) => request<Backtest>(`/backtest`, { method: 'POST', body: JSON.stringify(body) }),
  optimizePortfolio: (body: unknown) => request<PortfolioWeights>(`/portfolio/optimize`, { method: 'POST', body: JSON.stringify(body) }),
  ingest: (symbols: string[]) => request<{ status: string; symbols?: number }>(`/admin/ingest`, { method: 'POST', body: JSON.stringify({ symbols }) }),
  trainAll: () => request<{ status: string; count: number; symbols: string[] }>(`/ml/train_all`, { method: 'POST' }),
  addStock: (symbol: string) => request<{ status: string; symbol: string; name: string; sector?: string }>(`/admin/add_stock`, { method: 'POST', body: JSON.stringify({ symbol }) }),
  listWatchlist: () => request<WatchlistItem[]>(`/watchlist`),
  addToWatchlist: (symbol: string) => request<WatchlistItem>(`/watchlist/${symbol}`, { method: 'POST' }),
  removeFromWatchlist: (symbol: string) => request(`/watchlist/${symbol}`, { method: 'DELETE' }),
  isWatched: async (symbol: string): Promise<boolean> => {
    const items = await request<WatchlistItem[]>(`/watchlist`);
    return items.some(i => i.symbol === symbol);
  },
};

export type Stock = {
  id: number;
  symbol: string;
  name: string;
  market: string;
  exchange: string;
  sector?: string;
  currency: string;
};

export type Price = {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type Signal = {
  symbol: string;
  signal: 'BUY' | 'SELL' | 'HOLD';
  horizon: string;
  confidence: number;
  bullish_probability: number;
  reasons: Record<string, unknown>;
};

export type SignalSummary = { symbol: string; signal: 'BUY' | 'SELL' | 'HOLD'; horizon: string; confidence: number; bullish_probability: number | null };
export type RankingRow = { symbol: string; name: string; score: number; market: string; fair_price?: number; sector?: string };
export type Prediction = { symbol: string; bullish_probability: number; confidence: number; direction: string };
export type Backtest = {
  backtest_id: number;
  total_return: number;
  cagr: number;
  sharpe: number;
  max_drawdown: number;
  win_rate: number;
  profit_factor: number;
  n_trades: number;
  equity_curve: { ts: string; equity: number }[];
};
export type PortfolioWeights = {
  method: string;
  weights: Record<string, number>;
  cash: number;
  expected_return?: number | null;
  expected_vol?: number | null;
  sharpe_ratio?: number | null;
  max_drawdown?: number | null;
  diversification?: number | null;
};
export type LatestPrice = { symbol: string; price: number; prev_close: number | null; change_pct: number | null; currency: string };
export type WatchlistItem = { symbol: string; name: string; market: string; exchange: string; sector?: string; currency: string; added_at: string };
export type NewsItem = { title: string; url: string; source: string; published_at: number; sentiment: number; sentiment_label: 'bullish' | 'bearish' | 'neutral'; thumbnail?: string };

export type SRLevel = { price: number; strength: number; kind: 'support' | 'resistance' };
export type Levels = {
  support_resistance: SRLevel[];
  trendlines: { slope: number; intercept: number; r2: number; direction: string; start_idx: number; end_idx: number }[];
  fibonacci: Record<string, number>;
} | null;

export type Fundamentals = {
  // Valuation
  market_cap: number | null;
  enterprise_value: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  price_to_book: number | null;
  ev_to_ebitda: number | null;
  // Income statement
  total_revenue: number | null;
  gross_profit: number | null;
  net_income: number | null;
  ebitda: number | null;
  // Margins
  profit_margin: number | null;
  operating_margin: number | null;
  gross_margin: number | null;
  // Cash flow & balance sheet
  free_cashflow: number | null;
  operating_cashflow: number | null;
  total_cash: number | null;
  total_debt: number | null;
  // Per share
  trailing_eps: number | null;
  forward_eps: number | null;
  book_value: number | null;
  dividend_yield: number | null;
  dividend_rate: number | null;
  // Returns & risk
  return_on_equity: number | null;
  return_on_assets: number | null;
  revenue_growth: number | null;
  earnings_growth: number | null;
  beta: number | null;
  // Range & volume
  week_52_high: number | null;
  week_52_low: number | null;
  average_volume: number | null;
  shares_outstanding: number | null;
  // Analyst
  target_price: number | null;
  recommendation: string | null;
  number_of_analysts: number | null;
};

export type Overview = {
  price: Stock | null;
  prices: Price[] | null;
  indicators: { ts: string[]; values: Record<string, (number | null)[]> } | null;
  patterns: { patterns: { name: string; confidence: number; start_idx: number; end_idx: number }[] } | null;
  levels: Levels;
  signal: Signal | null;
  ranking: { score: number; fair_price?: number; technical: number; momentum: number; value: number; growth: number; volatility: number } | null;
  fundamentals: Fundamentals | null;
};
