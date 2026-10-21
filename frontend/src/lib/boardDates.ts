/** The API sends naive UTC ISO strings; without a zone the browser would read local time,
 *  so a card created minutes ago showed "-1d ago" in US time zones. */
export function utcMs(iso: string) {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z').getTime();
}

export function relDate(iso: string, now = Date.now()) {
  const d = Math.floor((now - utcMs(iso)) / 86_400_000);
  if (d === 0) return 'Today';
  if (d === 1) return 'Yesterday';
  return `${d}d ago`;
}
