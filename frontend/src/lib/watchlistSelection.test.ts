import { describe, it, expect } from 'vitest';
import {
  EMPTY_SELECTION, canPickList, beginPick, resolvePick, type ListSelection,
} from './watchlistSelection';
import { parseSymbols, mergeSymbols, MAX_SYMBOLS, SYMBOL_SEPARATORS } from './symbolInput';

/** Drive the picker the way a user does: click, await, click the next one. */
function pick(sel: ListSelection, listId: number, landedCount = 2, ok = true): ListSelection {
  if (!canPickList(sel, listId, landedCount)) return sel;
  return resolvePick(beginPick(sel, listId), listId, ok);
}

describe('watchlist picker is multi-select', () => {
  it('keeps every earlier list when a second is picked — the reported defect', () => {
    let sel = pick(EMPTY_SELECTION, 52);
    expect(sel.added).toEqual([52]);
    sel = pick(sel, 77);
    // The single-id version produced [77] here, so list 52's checkmark disappeared.
    expect(sel.added).toEqual([52, 77]);
  });

  it('accumulates across many lists in click order', () => {
    const ids = [174, 175, 176, 177, 178, 179];
    const sel = ids.reduce((acc, id) => pick(acc, id), EMPTY_SELECTION);
    expect(sel.added).toEqual(ids);
  });

  it('ignores a repeat click on a list already added', () => {
    const once = pick(EMPTY_SELECTION, 52);
    expect(pick(once, 52).added).toEqual([52]);
  });

  it('does not tick a list whose write failed, and leaves earlier ones alone', () => {
    const sel = pick(pick(EMPTY_SELECTION, 52), 77, 2, false);
    expect(sel.added).toEqual([52]);
    expect(sel.pending).toBeUndefined();
  });

  it('a failed list can be retried and then sticks', () => {
    const failed = pick(EMPTY_SELECTION, 77, 2, false);
    expect(pick(failed, 77).added).toEqual([77]);
  });

  it('refuses a click while another write is in flight', () => {
    const inFlight = beginPick(EMPTY_SELECTION, 52);
    expect(canPickList(inFlight, 77, 2)).toBe(false);
    expect(pick(inFlight, 77).added).toEqual([]);
  });

  it('refuses every click when nothing landed — the picker would do nothing', () => {
    expect(canPickList(EMPTY_SELECTION, 52, 0)).toBe(false);
  });

  it('never mutates the selection it was handed', () => {
    const before: ListSelection = { added: [52] };
    const frozen = Object.freeze(before.added);
    const after = pick(before, 77);
    expect(frozen).toEqual([52]);
    expect(after.added).not.toBe(before.added);
  });

  it('clears pending on both outcomes so the picker never jams', () => {
    expect(resolvePick(beginPick(EMPTY_SELECTION, 52), 52, true).pending).toBeUndefined();
    expect(resolvePick(beginPick(EMPTY_SELECTION, 52), 52, false).pending).toBeUndefined();
  });
});

describe('bulk symbol input, shared by both add screens', () => {
  it.each([
    ['AAPL,NVDA', ['AAPL', 'NVDA']],
    ['AAPL NVDA', ['AAPL', 'NVDA']],
    ['AAPL\nNVDA', ['AAPL', 'NVDA']],
    ['AAPL\tNVDA', ['AAPL', 'NVDA']],
    ['AAPL; NVDA', ['AAPL', 'NVDA']],
    ['  AAPL ,  nvda ,, ', ['AAPL', 'NVDA']],
    ['0700.HK, 9988.HK', ['0700.HK', '9988.HK']],
    ['', []],
    ['   ', []],
  ])('parses %j', (raw, expected) => {
    expect(parseSymbols(raw as string)).toEqual(expected);
  });

  it('treats a semicolon as a separator on both screens, not part of a ticker', () => {
    expect(SYMBOL_SEPARATORS.test('AAPL;NVDA')).toBe(true);
  });

  it('merges without duplicating and preserves the existing order', () => {
    expect(mergeSymbols(['AAPL', 'NVDA'], ['NVDA', 'MSFT'])).toEqual(['AAPL', 'NVDA', 'MSFT']);
  });

  it('caps at the server limit rather than sending a request that will 400', () => {
    const many = Array.from({ length: MAX_SYMBOLS + 10 }, (_, i) => `S${i}`);
    expect(mergeSymbols([], many)).toHaveLength(MAX_SYMBOLS);
  });

  it('keeps already-chosen symbols when the cap is reached', () => {
    const existing = ['AAPL'];
    const merged = mergeSymbols(existing, Array.from({ length: 40 }, (_, i) => `S${i}`));
    expect(merged[0]).toBe('AAPL');
    expect(merged).toHaveLength(MAX_SYMBOLS);
  });
});
