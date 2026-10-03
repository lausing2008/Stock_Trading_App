/** Parsing a typed or pasted list of tickers.
 *
 *  Lives here rather than in a component because TWO screens add symbols in bulk — the
 *  dashboard's Add-to-Universe modal and the watchlist's own add modal — and they had drifted
 *  to different separator sets (`;` was a separator in one and part of the ticker in the
 *  other). Two screens that accept the same paste should accept it the same way. */

// The separators people actually produce: a spreadsheet column, an article, a chat message.
// `\s` already covers newlines and tabs.
export const SYMBOL_SEPARATORS = /[\s,;]+/;

/** Mirrors the server's own per-request cap in `add_stocks`, which exists because the data
 *  provider rate-limits lookups. */
export const MAX_SYMBOLS = 25;

export function parseSymbols(text: string): string[] {
  return text.toUpperCase().split(SYMBOL_SEPARATORS).map(t => t.trim()).filter(Boolean);
}

/** Merge newly parsed symbols into an existing selection, de-duplicated, order preserved,
 *  capped. Returns a NEW array; never mutates. */
export function mergeSymbols(existing: string[], incoming: string[], cap = MAX_SYMBOLS): string[] {
  const merged = [...existing];
  for (const sym of incoming) if (!merged.includes(sym)) merged.push(sym);
  return merged.slice(0, cap);
}
