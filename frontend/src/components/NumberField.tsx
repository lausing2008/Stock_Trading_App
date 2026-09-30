// T412-CALCINPUT: a number input you can actually type in.
//
// REPORTED BY THE USER: "the option calculator is not able to input the numbers. always showing
// decimal 0.00 if I remove the numbers."
//
// See lib/numberField.ts for the full diagnosis. The short version: a controlled
// `<input type="number">` whose handler coerced on every keystroke could never be cleared, because
// clearing it produced `Number("") === 0`, which the `|| fallback` turned straight back into a
// value React wrote into the box.
//
// The two rules this component exists to enforce:
//
//   1. HOLD THE RAW TEXT while the field is focused. The committed number only moves when the
//      text is a COMPLETE number, so an in-progress "1." survives to become "1.5" instead of
//      being read as empty and overwritten.
//   2. CLAMP ONLY ON BLUR. Clamping mid-typing pushes a half-typed "1" up to a minimum of 10 and
//      the next digit lands somewhere the user did not mean.
//
// `type="text"` with `inputMode="decimal"` rather than `type="number"`, deliberately: a number
// input reports `value === ""` for anything not yet a valid number, so the component can never
// see the "1." the user is halfway through typing. Phones still get the numeric keypad from
// `inputMode`. The spinner arrows a number input provides for free are replaced by the explicit
// ▲/▼ buttons below, so nothing is lost.
import { useEffect, useRef, useState } from 'react';
import { parseDraft, clampValue, displayValue, stepValue } from '@/lib/numberField';

interface Props {
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
  style?: React.CSSProperties;
  'aria-label'?: string;
  disabled?: boolean;
}

export default function NumberField({
  value, onChange, min, max, step = 1, style, disabled, ...rest
}: Props) {
  const [draft, setDraft] = useState<string | null>(null);
  const focused = useRef(false);

  // Keep the box in sync when the value changes from OUTSIDE (a preset button, a fetched spot
  // price) — but never while the user is mid-edit, which would yank the text out from under them.
  useEffect(() => {
    if (!focused.current) setDraft(null);
  }, [value]);

  const shown = draft ?? displayValue(value);

  const commitDraft = (text: string) => {
    setDraft(text);
    const parsed = parseDraft(text);
    // Only a complete number moves the value. An empty or half-typed box leaves the last good
    // value in place so the chart and totals keep rendering something real.
    if (parsed !== null) onChange(parsed);
  };

  const finish = () => {
    focused.current = false;
    const parsed = parseDraft(draft ?? '');
    // An empty or unparseable box on blur falls back to the last committed value, clamped — not
    // to zero, which would silently wipe a figure the user was only part-way through changing.
    onChange(clampValue(parsed ?? value, min, max));
    setDraft(null);
  };

  const bump = (direction: 1 | -1) => {
    const base = parseDraft(draft ?? '') ?? value;
    onChange(stepValue(base, step, direction, min, max));
    setDraft(null);
  };

  const BTN: React.CSSProperties = {
    background: '#0f172a', border: '1px solid #334155', borderLeft: 'none', color: '#64748b',
    cursor: disabled ? 'not-allowed' : 'pointer', fontSize: 8, lineHeight: 1, padding: '0 5px',
    display: 'flex', alignItems: 'center', justifyContent: 'center', height: 14,
  };

  return (
    <div style={{ display: 'flex', alignItems: 'stretch' }}>
      <input
        {...rest}
        type="text"
        inputMode="decimal"
        value={shown}
        disabled={disabled}
        onFocus={e => { focused.current = true; setDraft(e.target.value); }}
        onChange={e => commitDraft(e.target.value)}
        onBlur={finish}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.currentTarget.blur(); return; }
          if (e.key === 'ArrowUp') { e.preventDefault(); bump(1); }
          if (e.key === 'ArrowDown') { e.preventDefault(); bump(-1); }
        }}
        style={{ ...style, borderTopRightRadius: 0, borderBottomRightRadius: 0, minWidth: 0 }}
      />
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        <button type="button" tabIndex={-1} disabled={disabled} aria-hidden
          onClick={() => bump(1)}
          style={{ ...BTN, borderTopRightRadius: 6, borderBottom: 'none' }}>▲</button>
        <button type="button" tabIndex={-1} disabled={disabled} aria-hidden
          onClick={() => bump(-1)}
          style={{ ...BTN, borderBottomRightRadius: 6 }}>▼</button>
      </div>
    </div>
  );
}
