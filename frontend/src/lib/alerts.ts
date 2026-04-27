import { storage } from './storage';

// ─── Types ───────────────────────────────────────────────────────────────────

export type ConditionType =
  | 'price_above' | 'price_below'
  | 'change_pct_above' | 'change_pct_below'
  | 'signal_buy' | 'signal_sell'
  | 'score_above' | 'score_below';

export type AlertCondition =
  | { type: 'price_above';       threshold: number }
  | { type: 'price_below';       threshold: number }
  | { type: 'change_pct_above';  threshold: number }
  | { type: 'change_pct_below';  threshold: number }
  | { type: 'signal_buy' }
  | { type: 'signal_sell' }
  | { type: 'score_above';       threshold: number }
  | { type: 'score_below';       threshold: number };

export type StockAlert = {
  id: string;
  symbol: string;
  name: string;
  condition: AlertCondition;
  enabled: boolean;
  cooldownMinutes: number;
  createdAt: string;
  lastTriggered?: string;
};

export type Notification = {
  id: string;
  alertId: string;
  symbol: string;
  message: string;
  triggeredAt: string;
  read: boolean;
  currentValue?: number;
};

// ─── Storage keys ────────────────────────────────────────────────────────────

const ALERTS_KEY        = 'alert_rules';
const NOTIFICATIONS_KEY = 'notifications';

// ─── Storage helpers ─────────────────────────────────────────────────────────

export function loadAlerts(): StockAlert[] {
  if (typeof window === 'undefined') return [];
  try { return JSON.parse(storage.getItem(ALERTS_KEY) ?? '[]'); }
  catch { return []; }
}

export function saveAlerts(alerts: StockAlert[]): void {
  storage.setItem(ALERTS_KEY, JSON.stringify(alerts));
}

export function addAlert(alert: Omit<StockAlert, 'id' | 'createdAt'>): StockAlert {
  const a: StockAlert = { ...alert, id: uid(), createdAt: new Date().toISOString() };
  saveAlerts([...loadAlerts(), a]);
  return a;
}

export function deleteAlert(id: string): void {
  saveAlerts(loadAlerts().filter(a => a.id !== id));
}

export function toggleAlert(id: string): void {
  saveAlerts(loadAlerts().map(a => a.id === id ? { ...a, enabled: !a.enabled } : a));
}

export function loadNotifications(): Notification[] {
  if (typeof window === 'undefined') return [];
  try { return JSON.parse(storage.getItem(NOTIFICATIONS_KEY) ?? '[]'); }
  catch { return []; }
}

export function saveNotifications(ns: Notification[]): void {
  storage.setItem(NOTIFICATIONS_KEY, JSON.stringify(ns.slice(0, 100)));
}

export function markAllRead(): void {
  saveNotifications(loadNotifications().map(n => ({ ...n, read: true })));
  window.dispatchEvent(new CustomEvent('stockai:notifications'));
}

export function clearNotifications(): void {
  saveNotifications([]);
  window.dispatchEvent(new CustomEvent('stockai:notifications'));
}

export function getUnreadCount(): number {
  return loadNotifications().filter(n => !n.read).length;
}

// ─── Condition label ──────────────────────────────────────────────────────────

export function conditionLabel(c: AlertCondition): string {
  switch (c.type) {
    case 'price_above':      return `Price above $${c.threshold}`;
    case 'price_below':      return `Price below $${c.threshold}`;
    case 'change_pct_above': return `Day change > +${c.threshold}%`;
    case 'change_pct_below': return `Day change < -${c.threshold}%`;
    case 'signal_buy':       return `Signal becomes BUY`;
    case 'signal_sell':      return `Signal becomes SELL`;
    case 'score_above':      return `K-Score above ${c.threshold}`;
    case 'score_below':      return `K-Score below ${c.threshold}`;
  }
}

// ─── Alert checker ───────────────────────────────────────────────────────────

type PriceMap  = Record<string, { price: number; change_pct: number | null }>;
type SignalMap = Record<string, { signal: string; confidence: number }>;
type ScoreMap  = Record<string, { score: number }>;

export function checkAlerts(
  prices: PriceMap,
  signals: SignalMap,
  scores: ScoreMap,
): Notification[] {
  const alerts = loadAlerts().filter(a => a.enabled);
  const now = Date.now();
  const triggered: Notification[] = [];
  const updated: StockAlert[] = [];

  for (const alert of loadAlerts()) {
    if (!alert.enabled) { updated.push(alert); continue; }

    // Respect cooldown
    if (alert.lastTriggered) {
      const elapsed = now - new Date(alert.lastTriggered).getTime();
      if (elapsed < alert.cooldownMinutes * 60 * 1000) { updated.push(alert); continue; }
    }

    const p = prices[alert.symbol];
    const s = signals[alert.symbol];
    const r = scores[alert.symbol];
    const c = alert.condition;
    let message: string | null = null;
    let value: number | undefined;

    switch (c.type) {
      case 'price_above':
        if (p && p.price > c.threshold) {
          message = `${alert.symbol} price $${p.price.toFixed(2)} crossed above $${c.threshold}`;
          value = p.price;
        }
        break;
      case 'price_below':
        if (p && p.price < c.threshold) {
          message = `${alert.symbol} price $${p.price.toFixed(2)} crossed below $${c.threshold}`;
          value = p.price;
        }
        break;
      case 'change_pct_above':
        if (p?.change_pct != null && p.change_pct > c.threshold) {
          message = `${alert.symbol} up ${p.change_pct.toFixed(1)}% today (target +${c.threshold}%)`;
          value = p.change_pct;
        }
        break;
      case 'change_pct_below':
        if (p?.change_pct != null && p.change_pct < -c.threshold) {
          message = `${alert.symbol} down ${Math.abs(p.change_pct).toFixed(1)}% today (target -${c.threshold}%)`;
          value = p.change_pct;
        }
        break;
      case 'signal_buy':
        if (s?.signal === 'BUY') {
          message = `${alert.symbol} signal is BUY (${s.confidence.toFixed(0)}% confidence)`;
        }
        break;
      case 'signal_sell':
        if (s?.signal === 'SELL') {
          message = `${alert.symbol} signal is SELL (${s.confidence.toFixed(0)}% confidence)`;
        }
        break;
      case 'score_above':
        if (r && r.score > c.threshold) {
          message = `${alert.symbol} K-Score ${r.score.toFixed(0)} above ${c.threshold}`;
          value = r.score;
        }
        break;
      case 'score_below':
        if (r && r.score < c.threshold) {
          message = `${alert.symbol} K-Score ${r.score.toFixed(0)} below ${c.threshold}`;
          value = r.score;
        }
        break;
    }

    if (message) {
      triggered.push({
        id: uid(), alertId: alert.id, symbol: alert.symbol,
        message, triggeredAt: new Date().toISOString(), read: false, currentValue: value,
      });
      updated.push({ ...alert, lastTriggered: new Date().toISOString() });
    } else {
      updated.push(alert);
    }
  }

  if (triggered.length > 0) {
    saveAlerts(updated);
    const existing = loadNotifications();
    saveNotifications([...triggered, ...existing]);
    window.dispatchEvent(new CustomEvent('stockai:notifications'));
  }

  return triggered;
}

function uid(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// ─── Sound ───────────────────────────────────────────────────────────────────

export function playNotificationSound(): void {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.25, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.4);
  } catch {}
}
