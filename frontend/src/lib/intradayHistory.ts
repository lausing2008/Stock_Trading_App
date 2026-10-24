/** Keep prior bars for indicator warmup while displaying the current UTC session. */
export function intradayWindow<T extends { ts: string }>(history: T[], today: string) {
  const session = history.filter(p => p.ts.slice(0, 10) === today);
  const visible = session.length >= 5 ? session : history.slice(-100);
  return { history, visible };
}
