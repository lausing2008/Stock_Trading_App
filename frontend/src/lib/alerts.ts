/**
 * Client-side alert system for StockAI.
 *
 * Architecture
 * ────────────
 * Alerts are stored in localStorage (via `storage` wrapper) as `StockAlert[]`
 * and evaluated in-browser by `checkAlerts()`, which is called every 60 seconds
 * from `_app.tsx` after fetching the latest prices, signals, and rankings.
 *
 * When a condition fires, a `Notification` record is appended to a separate
 * localStorage key and a `stockai:notifications` CustomEvent is dispatched on
 * `window` so the `NotificationBell` component re-renders without polling.
 *
 * Alert lifecycle
 * ───────────────
 *  1. User creates an alert via the Alerts page → `addAlert()`
 *  2. `_app.tsx` calls `checkAlerts(prices, signals, scores)` every 60 s
 *  3. If the condition is met and the cooldown has elapsed, a Notification is
 *     created and the alert is auto-disabled (one-shot behaviour)
 *  4. The bell badge shows `getUnreadCount()`; opening the panel calls
 *     `markAllRead()` to clear the badge
 *
 * Supported condition types
 * ─────────────────────────
 *  price_above / price_below     — latest trade price vs a fixed threshold
 *  change_pct_above / below      — intraday % move vs a threshold
 *  signal_buy / signal_sell      — AI signal becomes BUY or SELL
 *  score_above / score_below     — K-Score composite ranking vs a threshold
 */
import { storage } from './storage';
import { api } from './api';

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

/** A single alert rule owned by the user. */
export type StockAlert = {
  id: string;
  symbol: string;
  name: string;
  condition: AlertCondition;
  enabled: boolean;
  /** Minutes that must pass before the same alert can fire again. */
  cooldownMinutes: number;
  createdAt: string;
  /** ISO timestamp of the last time this alert fired, used for cooldown. */
  lastTriggered?: string;
};

/** An in-app notification produced when an alert fires. Kept up to 100 entries. */
export type Notification = {
  id: string;
  /** The `StockAlert.id` that produced this notification. */
  alertId: string;
  symbol: string;
  message: string;
  triggeredAt: string;
  read: boolean;
  /** The numeric value (price, change %, score) that crossed the threshold. */
  currentValue?: number;
};

// ─── Storage keys ────────────────────────────────────────────────────────────

const ALERTS_KEY = 'alert_rules';

// ─── Storage helpers ─────────────────────────────────────────────────────────
// All reads/writes go through the `storage` wrapper which namespaces keys per
// user so that multi-user logins on the same browser don't share alert state.

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

export async function markAllRead(): Promise<void> {
  await api.markAllNotificationsRead().catch(() => {});
  window.dispatchEvent(new CustomEvent('stockai:notifications'));
}

export async function clearNotifications(): Promise<void> {
  await api.clearNotifications().catch(() => {});
  window.dispatchEvent(new CustomEvent('stockai:notifications'));
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

/**
 * Evaluate all enabled alert rules against the current market snapshot.
 *
 * Called by `_app.tsx` every 60 seconds with fresh data from three endpoints:
 *   - `/stocks/latest_prices`  → prices
 *   - `/signals`               → signals
 *   - `/rankings`              → scores
 *
 * For each alert that fires:
 *   - A `Notification` is created and prepended to the notification list
 *   - The alert is auto-disabled (one-shot) and `lastTriggered` is set
 *   - A `stockai:notifications` event is dispatched for the bell to update
 *
 * Alerts that have not exceeded their cooldown window are skipped silently.
 *
 * @returns Array of notifications that fired in this evaluation pass.
 */
export function checkAlerts(
  prices: PriceMap,
  signals: SignalMap,
  scores: ScoreMap,
): Notification[] {
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
        if (r && r.score != null && r.score > c.threshold) {
          message = `${alert.symbol} K-Score ${r.score.toFixed(0)} above ${c.threshold}`;
          value = r.score;
        }
        break;
      case 'score_below':
        if (r && r.score != null && r.score < c.threshold) {
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
      // Auto-disable after firing — price/signal alerts are one-shot
      updated.push({ ...alert, enabled: false, lastTriggered: new Date().toISOString() });
    } else {
      updated.push(alert);
    }
  }

  if (triggered.length > 0) {
    saveAlerts(updated);
    // Post each new notification to backend, then signal the bell to re-fetch
    Promise.all(triggered.map(n =>
      api.createNotification({
        alert_id: n.alertId,
        symbol: n.symbol,
        message: n.message,
        triggered_at: n.triggeredAt,
        current_value: n.currentValue,
      }).catch(() => {})
    )).then(() => {
      window.dispatchEvent(new CustomEvent('stockai:notifications'));
    });
  }

  return triggered;
}

function uid(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2);
}

// ─── Sound ───────────────────────────────────────────────────────────────────

/**
 * Plays a short 880 Hz tone via the Web Audio API.
 * Called when alerts fire if the user has enabled notification sounds in Settings.
 * Silently no-ops in environments where AudioContext is unavailable (SSR, tests).
 */
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
