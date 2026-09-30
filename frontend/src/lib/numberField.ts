// T412-CALCINPUT: parsing for a number input you can actually type in.
//
// REPORTED BY THE USER: "the option calculator is not able to input the numbers. always showing
// decimal 0.00 if I remove the numbers."
//
// THE BUG, exactly. Every field on the Options Calculator was a controlled `<input type="number">`
// whose handler collapsed an unparseable value to a fallback on EVERY KEYSTROKE:
//
//     onChange={e => setSpot(Math.max(0.01, Number(e.target.value) || 0.01))}
//
// Clear the field and `e.target.value` is `""`; `Number("") === 0`; `0 || 0.01` is `0.01`; and
// because the input is controlled, React writes 0.01 straight back into the box. The field can
// never be empty, so it can never be retyped — which is precisely what the user hit, and why the
// screenshot shows an underlying price of 0.01.
//
// It also made decimals nearly impossible for a second, separate reason. For `type="number"`, a
// browser reports `value === ""` whenever the box holds something that is not yet a valid number
// — and "1." is not a valid number. So typing "1.5" passes through a state that the handler reads
// as empty and rewrites, destroying the keystroke. That is why these fields are `type="text"` with
// `inputMode="decimal"` now: a text input hands back the literal characters, so an in-progress
// "1." survives long enough to become "1.5".
//
// The rule these helpers encode: NEVER coerce while the user is typing. Hold the raw text, commit
// a value only when the text is a complete number, and clamp only on blur — when the user has
// said they are done.

/** Parse in-progress input text.
 *
 * Returns `null` for anything not yet a complete number — an empty box, a lone "-", a bare "."
 * or a trailing "1." — because those are states a user passes THROUGH on the way to a real value,
 * not values to act on. Returning 0 for them is the bug this module exists to prevent.
 */
export function parseDraft(text: string): number | null {
  const t = text.trim();
  if (t === '') return null;
  // Reject the partial forms explicitly rather than relying on Number(), which is happy to turn
  // "" into 0 and would read a trailing-dot "1." as 1 — committing a value the user has not
  // finished typing, so the next keystroke ("1.5") fights a re-render.
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(t)) return null;
  if (/\.$/.test(t)) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** Clamp a committed value into range. Applied on BLUR only, never mid-typing — clamping a
 * half-typed "1" up to a minimum of 10 makes the next digit land somewhere the user did not mean. */
export function clampValue(n: number, min?: number, max?: number): number {
  let v = n;
  if (min != null && v < min) v = min;
  if (max != null && v > max) v = max;
  return v;
}

/** What to show when the field is not being edited.
 *
 * Plain digits, no thousands separators and no forced decimal places: this text goes back into an
 * editable box, and a grouped "1,234.00" is both harder to edit and no longer parseable by
 * `parseDraft`. Trailing zeros are dropped for the same reason — `Number`'s own string form is
 * already the shortest round-trippable one.
 */
export function displayValue(n: number): string {
  if (!Number.isFinite(n)) return '';
  return String(n);
}

/** Apply a stepper press. Kept here rather than in the component so the rounding is testable:
 * floating-point addition would otherwise turn 0.1 + 0.2 into 0.30000000000000004 in a price box.
 */
export function stepValue(current: number, step: number, direction: 1 | -1, min?: number, max?: number): number {
  const raw = current + step * direction;
  // Round to the step's own precision, so a 0.01 step yields cents and a 0.5 step yields halves.
  const decimals = (String(step).split('.')[1] ?? '').length;
  const rounded = decimals > 0 ? Number(raw.toFixed(decimals)) : Math.round(raw);
  return clampValue(rounded, min, max);
}
