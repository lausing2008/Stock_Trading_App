export type Status = 'todo' | 'in-progress' | 'done';

export type StatusSeedItem = { id: string; defaultStatus?: Status };

/**
 * Merges a saved (localStorage-cached) status map with the current source-of-truth
 * defaultStatus: 'done' seeds — seeded 'done' values always win, so a stale cached status
 * from a visit BEFORE an item was actually fixed (and its defaultStatus flipped to 'done' in
 * source) can never override the fresh, correct status. A user's manual status change on a
 * todo/in-progress item (anything NOT defaultStatus: 'done') still persists across visits,
 * since `saved` is the base and only 'done'-seeded ids are force-overridden.
 *
 * Bug fixed 2026-07-21: the original inline version spread `saved` LAST
 * (`{ ...seeded, ...saved }`), so a cached 'todo' status silently overrode a freshly-fixed
 * item's 'done' seed — directly contradicting the surrounding comment's own stated intent.
 * A user who loaded the tracker once before a fix shipped would see that one item stuck at
 * its old status forever after, even across a hard refresh (localStorage survives a reload).
 */
export function mergeImprovementStatuses(
  items: StatusSeedItem[],
  saved: Record<string, Status>,
): Record<string, Status> {
  const seeded: Record<string, Status> = {};
  for (const item of items) {
    if (item.defaultStatus === 'done') {
      seeded[item.id] = 'done';
    }
  }
  return { ...saved, ...seeded };
}
