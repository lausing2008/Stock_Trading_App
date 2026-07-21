import { describe, it, expect } from 'vitest';
import { mergeImprovementStatuses, type StatusSeedItem } from './improvementStatuses';

describe('mergeImprovementStatuses', () => {
  it('a stale cached todo status cannot override a defaultStatus done item', () => {
    // The exact bug: an item shipped with defaultStatus: 'done' in source, but the user's
    // browser cached 'todo' for it from a visit BEFORE the fix — the seeded 'done' must win.
    const items: StatusSeedItem[] = [{ id: 'FIXED-ITEM', defaultStatus: 'done' }];
    const saved = { 'FIXED-ITEM': 'todo' as const };
    expect(mergeImprovementStatuses(items, saved)).toEqual({ 'FIXED-ITEM': 'done' });
  });

  it('a stale cached in-progress status also cannot override a defaultStatus done item', () => {
    const items: StatusSeedItem[] = [{ id: 'FIXED-ITEM', defaultStatus: 'done' }];
    const saved = { 'FIXED-ITEM': 'in-progress' as const };
    expect(mergeImprovementStatuses(items, saved)).toEqual({ 'FIXED-ITEM': 'done' });
  });

  it("a user's manual status on a non-done-by-default item persists across visits", () => {
    // Items without defaultStatus: 'done' are genuinely user-managed (todo/in-progress) —
    // the merge must NOT force-override those, only ones seeded as 'done' in source.
    const items: StatusSeedItem[] = [{ id: 'TODO-ITEM', defaultStatus: 'todo' }];
    const saved = { 'TODO-ITEM': 'in-progress' as const };
    expect(mergeImprovementStatuses(items, saved)).toEqual({ 'TODO-ITEM': 'in-progress' });
  });

  it('an item with no defaultStatus field at all is left to the saved map, not force-seeded', () => {
    const items: StatusSeedItem[] = [{ id: 'NO-DEFAULT-ITEM' }];
    const saved = { 'NO-DEFAULT-ITEM': 'in-progress' as const };
    expect(mergeImprovementStatuses(items, saved)).toEqual({ 'NO-DEFAULT-ITEM': 'in-progress' });
  });

  it('a brand-new defaultStatus done item with nothing cached yet seeds to done', () => {
    const items: StatusSeedItem[] = [{ id: 'NEW-ITEM', defaultStatus: 'done' }];
    expect(mergeImprovementStatuses(items, {})).toEqual({ 'NEW-ITEM': 'done' });
  });

  it('mixed items: only the done-seeded ids are forced, everything else keeps its saved value', () => {
    const items: StatusSeedItem[] = [
      { id: 'A', defaultStatus: 'done' },
      { id: 'B', defaultStatus: 'todo' },
      { id: 'C', defaultStatus: 'in-progress' },
    ];
    const saved = {
      A: 'todo' as const,          // stale — must be forced back to 'done'
      B: 'in-progress' as const,   // real user progress — must persist
      C: 'in-progress' as const,   // matches its own defaultStatus — unaffected either way
    };
    expect(mergeImprovementStatuses(items, saved)).toEqual({
      A: 'done',
      B: 'in-progress',
      C: 'in-progress',
    });
  });

  it('an id present in saved but no longer in items is harmlessly carried through', () => {
    const items: StatusSeedItem[] = [{ id: 'STILL-HERE', defaultStatus: 'done' }];
    const saved = { 'REMOVED-ITEM': 'done' as const };
    expect(mergeImprovementStatuses(items, saved)).toEqual({
      'REMOVED-ITEM': 'done',
      'STILL-HERE': 'done',
    });
  });
});
