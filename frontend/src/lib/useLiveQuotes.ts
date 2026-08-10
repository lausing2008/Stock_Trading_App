/**
 * T230-DATA-STREAMING-QUOTES: real-time US equity quote streaming via api-gateway's
 * WebSocket relay (GET /ws/quotes) — pushes ticks from Alpaca's free IEX feed as they arrive,
 * instead of waiting for the next 60s SWR poll of GET /stocks/latest_prices.
 *
 * US-only (Alpaca has no HK coverage at any tier) — a symbol not covered simply never receives
 * a tick through this hook; callers should keep their existing SWR-polled price as the base
 * value and only overlay a live tick when one arrives for that specific symbol, never treat
 * "no tick yet" as an error. Also degrades gracefully with zero visible symptom when: the
 * backend has no Alpaca credentials configured, the connection drops, or the browser is on a
 * network that blocks WebSockets — this hook's own reconnect-with-backoff handles transient
 * drops, and a caller relying on the SWR-polled fallback price is never left with nothing.
 *
 * Deliberately does NOT replace the existing 60s SWR polling anywhere — this is an ADDITIVE
 * overlay a page can opt into for its own watched/visible symbols, matching the tracker item's
 * own framing ("push updates ON TOP of the existing polling fallback", not a wholesale
 * replacement of a mechanism many other pages still correctly rely on for HK coverage and for
 * when streaming is unavailable).
 */
import { useEffect, useState } from 'react';

export type LiveQuote = { symbol: string; price: number; ts: string };

export const RECONNECT_BASE_DELAY_MS = 2_000;
export const RECONNECT_MAX_DELAY_MS = 30_000;

/**
 * Pulled out as a pure function (no window/DOM access) so the escalation math itself is
 * directly unit-testable without a browser environment: doubles each call, capped at
 * RECONNECT_MAX_DELAY_MS, never resets on its own (the caller resets to the base delay after
 * a clean connect — see useLiveQuotes' own onopen handler).
 */
export function nextReconnectDelay(currentDelayMs: number): number {
  return Math.min(currentDelayMs * 2, RECONNECT_MAX_DELAY_MS);
}

/**
 * Derives the ws(s):// base URL from the same NEXT_PUBLIC_API_URL / relative-/api convention
 * api.ts's own BASE constant uses — pure function of its two inputs (no direct window access)
 * so it's testable without a browser environment.
 */
export function wsBaseUrl(apiUrl: string | undefined, loc: { protocol: string; host: string } | null): string {
  if (apiUrl) {
    // Local dev: NEXT_PUBLIC_API_URL is an absolute http://localhost:8000-style URL.
    return apiUrl.replace(/^http/, 'ws');
  }
  // Production: relative /api base, proxied by nginx — derive ws(s):// from the current page's
  // own protocol/host rather than hardcoding a domain.
  if (!loc) return '';
  const proto = loc.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${loc.host}/api`;
}

/** Builds the full connection URL — a pure function of its 3 inputs, no side effects. */
export function buildQuoteWsUrl(base: string, token: string, symbolsKey: string): string {
  return `${base}/ws/quotes?token=${encodeURIComponent(token)}&symbols=${encodeURIComponent(symbolsKey)}`;
}

/**
 * Parses one raw WebSocket message into a LiveQuote, or null for anything malformed/invalid —
 * pure function so the "drop silently on bad data" contract is directly testable.
 */
export function parseTickMessage(raw: string): LiveQuote | null {
  try {
    const tick = JSON.parse(raw) as LiveQuote;
    if (!tick.symbol || typeof tick.price !== 'number') return null;
    return tick;
  } catch {
    return null;
  }
}

/**
 * Subscribes to real-time ticks for `symbols` (capped server-side at 50). Returns a map of
 * symbol -> most recent LiveQuote seen since this hook mounted (empty until the first tick for
 * a given symbol arrives — callers must merge this over their own SWR-polled base data, not
 * use it as the sole source of truth).
 */
export function useLiveQuotes(symbols: string[]): Record<string, LiveQuote> {
  const [quotes, setQuotes] = useState<Record<string, LiveQuote>>({});
  const symbolsKey = symbols.slice().sort().join(',');

  useEffect(() => {
    if (typeof window === 'undefined' || !symbolsKey) return;

    let stopped = false;
    let ws: WebSocket | null = null;
    let reconnectDelay = RECONNECT_BASE_DELAY_MS;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (stopped) return;
      const token = localStorage.getItem('stockai_jwt')?.trim();
      if (!token) return; // not logged in — nothing to stream, no point retrying yet
      const base = wsBaseUrl(process.env.NEXT_PUBLIC_API_URL, window.location);
      if (!base) return;
      const url = buildQuoteWsUrl(base, token, symbolsKey);

      try {
        ws = new WebSocket(url);
      } catch {
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        reconnectDelay = RECONNECT_BASE_DELAY_MS; // reset backoff after a clean connect
      };
      ws.onmessage = (event) => {
        const tick = parseTickMessage(event.data);
        if (!tick) return; // malformed/incomplete tick — drop it silently
        setQuotes((prev) => ({ ...prev, [tick.symbol]: tick }));
      };
      ws.onclose = () => {
        if (!stopped) scheduleReconnect();
      };
      ws.onerror = () => {
        // onclose always fires after onerror for a WebSocket — the reconnect is scheduled there,
        // not here, to avoid double-scheduling.
      };
    }

    function scheduleReconnect() {
      if (stopped) return;
      reconnectTimer = setTimeout(() => {
        reconnectDelay = nextReconnectDelay(reconnectDelay);
        connect();
      }, reconnectDelay);
    }

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey]);

  return quotes;
}
