/** Which watchlists a batch of just-added symbols has landed in.
 *
 *  WHY THIS IS A SET. A stock belongs to as many watchlists as the user likes. The dashboard's
 *  picker tracked a single `selectedListId`, so picking a second list moved the checkmark off
 *  the first — which read as "that replaced my earlier choice" even though the server had
 *  already written both. The state here is additive by construction, so the UI cannot drift
 *  from what was actually written. */

export type ListSelection = {
  /** Lists whose writes have RETURNED successfully. Never speculative. */
  added: number[];
  /** The one list currently being written, if any. */
  pending?: number;
};

export const EMPTY_SELECTION: ListSelection = { added: [] };

/** A click is actionable only when there is something to add, the list is not already done,
 *  and no other write is in flight — concurrent writes would interleave the per-symbol
 *  sequence and make a partial failure impossible to attribute. */
export function canPickList(sel: ListSelection, listId: number, landedCount: number): boolean {
  return landedCount > 0 && !sel.added.includes(listId) && sel.pending === undefined;
}

export function beginPick(sel: ListSelection, listId: number): ListSelection {
  return { added: sel.added, pending: listId };
}

/** Resolve a write. Success ADDS to the set and keeps every earlier list; failure leaves the
 *  set untouched, so a list is never ticked for a write that did not land. */
export function resolvePick(sel: ListSelection, listId: number, ok: boolean): ListSelection {
  if (!ok) return { added: sel.added };
  return { added: sel.added.includes(listId) ? sel.added : [...sel.added, listId] };
}
