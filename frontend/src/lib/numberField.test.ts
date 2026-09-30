import { describe, it, expect } from 'vitest';
import { parseDraft, clampValue, displayValue, stepValue } from './numberField';

describe('parseDraft — the states a user types THROUGH', () => {
  it('returns null for an empty box rather than 0', () => {
    // THE REPORTED BUG. `Number("") === 0`, and the old handler's `|| 0.01` fallback then wrote
    // 0.01 back into a controlled input on every keystroke — so the field could never be cleared
    // and never retyped.
    expect(parseDraft('')).toBeNull();
    expect(parseDraft('   ')).toBeNull();
  });

  it('returns null for a trailing decimal point', () => {
    // "1." is the state you pass through on the way to "1.5". Committing it as 1 makes the next
    // keystroke fight a re-render, which is the second half of why decimals were untypeable.
    expect(parseDraft('1.')).toBeNull();
    expect(parseDraft('100.')).toBeNull();
  });

  it('returns null for a lone minus sign', () => {
    expect(parseDraft('-')).toBeNull();
  });

  it('returns null for a bare decimal point', () => {
    expect(parseDraft('.')).toBeNull();
  });

  it('parses a leading-dot decimal, which is a complete number', () => {
    expect(parseDraft('.5')).toBe(0.5);
  });

  it('parses ordinary values', () => {
    expect(parseDraft('0')).toBe(0);
    expect(parseDraft('100')).toBe(100);
    expect(parseDraft('1.5')).toBe(1.5);
    expect(parseDraft('337.02')).toBe(337.02);
    expect(parseDraft('-4.25')).toBe(-4.25);
  });

  it('tolerates surrounding whitespace from a paste', () => {
    expect(parseDraft('  42.5  ')).toBe(42.5);
  });

  it('rejects text that Number() would happily coerce', () => {
    // Number("0x10") is 16, Number("1e5") is 100000, Number("Infinity") is Infinity. None of
    // those are things a user meant to type into a strike box.
    expect(parseDraft('0x10')).toBeNull();
    expect(parseDraft('Infinity')).toBeNull();
    expect(parseDraft('abc')).toBeNull();
    expect(parseDraft('1,000')).toBeNull();
  });

  it('typing a price one character at a time never commits a wrong value', () => {
    // The whole sequence, as a user would produce it. Every prefix either parses to exactly what
    // is on screen or parses to nothing at all — it must never parse to something else.
    const keys = ['1', '1.', '1.2', '1.23'];
    const got = keys.map(parseDraft);
    expect(got).toEqual([1, null, 1.2, 1.23]);
  });
});

describe('clampValue', () => {
  it('raises a value below the minimum', () => {
    expect(clampValue(0, 1)).toBe(1);
  });

  it('lowers a value above the maximum', () => {
    expect(clampValue(50, 1, 10)).toBe(10);
  });

  it('leaves an in-range value alone', () => {
    expect(clampValue(5, 1, 10)).toBe(5);
  });

  it('does nothing when no bounds are given', () => {
    expect(clampValue(-99)).toBe(-99);
  });
});

describe('displayValue', () => {
  it('round-trips through parseDraft', () => {
    // This text goes straight back into an editable box, so whatever it produces must be
    // something the parser accepts — otherwise the field becomes uneditable the moment it blurs.
    for (const n of [0, 1, 1.5, 100, 337.02, -4.25, 0.01]) {
      expect(parseDraft(displayValue(n))).toBe(n);
    }
  });

  it('does not add thousands separators', () => {
    // "1,000" is not parseable, and grouping is actively unhelpful in a box you edit.
    expect(displayValue(1000)).toBe('1000');
  });

  it('does not force trailing zeros', () => {
    expect(displayValue(100)).toBe('100');
    expect(displayValue(1.5)).toBe('1.5');
  });

  it('renders a non-finite value as empty rather than "NaN"', () => {
    expect(displayValue(NaN)).toBe('');
    expect(displayValue(Infinity)).toBe('');
  });
});

describe('stepValue', () => {
  it('steps up and down by the step size', () => {
    expect(stepValue(100, 0.5, 1)).toBe(100.5);
    expect(stepValue(100, 0.5, -1)).toBe(99.5);
  });

  it('does not accumulate floating-point noise', () => {
    // 0.1 + 0.2 is 0.30000000000000004. In a premium box that renders as a price nobody typed.
    expect(stepValue(0.1, 0.2, 1)).toBe(0.3);
    expect(stepValue(3, 0.01, 1)).toBe(3.01);
  });

  it('respects the minimum', () => {
    expect(stepValue(1, 1, -1, 1)).toBe(1);
  });

  it('respects the maximum', () => {
    expect(stepValue(10, 5, 1, undefined, 10)).toBe(10);
  });

  it('rounds to a whole number for an integer step', () => {
    expect(stepValue(1, 1, 1)).toBe(2);
    expect(stepValue(100, 100, 1)).toBe(200);
  });
});
