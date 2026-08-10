import { describe, it, expect } from 'vitest';
import {
  RECONNECT_BASE_DELAY_MS,
  RECONNECT_MAX_DELAY_MS,
  nextReconnectDelay,
  wsBaseUrl,
  buildQuoteWsUrl,
  parseTickMessage,
} from './useLiveQuotes';

describe('nextReconnectDelay', () => {
  it('doubles the current delay', () => {
    expect(nextReconnectDelay(2_000)).toBe(4_000);
    expect(nextReconnectDelay(4_000)).toBe(8_000);
  });

  it('caps at RECONNECT_MAX_DELAY_MS instead of growing unbounded', () => {
    expect(nextReconnectDelay(20_000)).toBe(RECONNECT_MAX_DELAY_MS);
    expect(nextReconnectDelay(RECONNECT_MAX_DELAY_MS)).toBe(RECONNECT_MAX_DELAY_MS);
    // one more doubling past the cap must still clamp, not overshoot
    expect(nextReconnectDelay(RECONNECT_MAX_DELAY_MS * 2)).toBe(RECONNECT_MAX_DELAY_MS);
  });

  it('starting from the base delay eventually reaches the cap in finitely many steps', () => {
    let delay = RECONNECT_BASE_DELAY_MS;
    let steps = 0;
    while (delay < RECONNECT_MAX_DELAY_MS && steps < 100) {
      delay = nextReconnectDelay(delay);
      steps += 1;
    }
    expect(delay).toBe(RECONNECT_MAX_DELAY_MS);
    expect(steps).toBeLessThan(100);
  });
});

describe('wsBaseUrl', () => {
  it('rewrites an absolute http:// NEXT_PUBLIC_API_URL to ws://', () => {
    expect(wsBaseUrl('http://localhost:8000', null)).toBe('ws://localhost:8000');
  });

  it('rewrites an absolute https:// NEXT_PUBLIC_API_URL to wss://', () => {
    expect(wsBaseUrl('https://api.example.com', null)).toBe('wss://api.example.com');
  });

  it('falls back to the current page location when no env URL is set, using wss for https pages', () => {
    const loc = { protocol: 'https:', host: 'lausing.com' };
    expect(wsBaseUrl(undefined, loc)).toBe('wss://lausing.com/api');
  });

  it('falls back to ws (not wss) for a plain http page', () => {
    const loc = { protocol: 'http:', host: 'localhost:3000' };
    expect(wsBaseUrl(undefined, loc)).toBe('ws://localhost:3000/api');
  });

  it('returns an empty string when there is no env URL and no location (SSR/no window)', () => {
    expect(wsBaseUrl(undefined, null)).toBe('');
  });

  it('prefers the env URL over location even when both are provided', () => {
    const loc = { protocol: 'https:', host: 'lausing.com' };
    expect(wsBaseUrl('http://localhost:8000', loc)).toBe('ws://localhost:8000');
  });
});

describe('buildQuoteWsUrl', () => {
  it('builds the expected path with token and symbols query params', () => {
    const url = buildQuoteWsUrl('ws://localhost:8000', 'abc123', 'AAPL,MSFT');
    expect(url).toBe('ws://localhost:8000/ws/quotes?token=abc123&symbols=AAPL%2CMSFT');
  });

  it('URL-encodes special characters in the token (e.g. JWT dots are safe, but be defensive anyway)', () => {
    const url = buildQuoteWsUrl('wss://lausing.com/api', 'a.b.c', 'AAPL');
    expect(url).toContain('token=a.b.c');
    expect(url).toContain('symbols=AAPL');
  });

  it('encodes a comma-separated symbol list as one query value, not multiple params', () => {
    const url = buildQuoteWsUrl('ws://x', 't', 'AAPL,MSFT,NVDA');
    // exactly one "symbols=" occurrence — commas must be percent-encoded, not left raw
    expect(url.match(/symbols=/g)?.length).toBe(1);
    expect(url).not.toContain('symbols=AAPL,MSFT');
  });
});

describe('parseTickMessage', () => {
  it('parses a well-formed tick', () => {
    const tick = parseTickMessage('{"symbol":"AAPL","price":231.45,"ts":"2026-08-01T12:00:00Z"}');
    expect(tick).toEqual({ symbol: 'AAPL', price: 231.45, ts: '2026-08-01T12:00:00Z' });
  });

  it('returns null for malformed JSON rather than throwing', () => {
    expect(parseTickMessage('not json at all {')).toBeNull();
  });

  it('returns null when symbol is missing', () => {
    expect(parseTickMessage('{"price":100}')).toBeNull();
  });

  it('returns null when price is missing', () => {
    expect(parseTickMessage('{"symbol":"AAPL"}')).toBeNull();
  });

  it('returns null when price is a string instead of a number', () => {
    expect(parseTickMessage('{"symbol":"AAPL","price":"231.45"}')).toBeNull();
  });

  it('returns null when symbol is an empty string', () => {
    expect(parseTickMessage('{"symbol":"","price":100}')).toBeNull();
  });

  it('accepts a price of exactly 0 (falsy but a valid number, not a missing-field case)', () => {
    const tick = parseTickMessage('{"symbol":"XYZ","price":0,"ts":"t"}');
    expect(tick).toEqual({ symbol: 'XYZ', price: 0, ts: 't' });
  });
});
