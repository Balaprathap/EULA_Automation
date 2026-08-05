import { describe, expect, it } from 'vitest';

import {
  confidenceLabel,
  formatBytes,
  formatCost,
  humanizeCategory,
  severityClasses,
  slugify,
  splitForHighlight,
  toCsv,
} from './format';

describe('severityClasses', () => {
  it('gives each severity a distinct treatment', () => {
    const classes = new Set(
      (['info', 'low', 'medium', 'high', 'critical'] as const).map(severityClasses),
    );
    expect(classes.size).toBe(5);
  });

  it('falls back safely for an unexpected value', () => {
    expect(severityClasses('nonsense' as never)).toContain('slate');
  });
});

describe('formatBytes', () => {
  it('formats each magnitude', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
  });

  it('handles null', () => {
    expect(formatBytes(null)).toBe('-');
  });
});

describe('formatCost', () => {
  it('shows sub-cent amounts without rounding them to zero', () => {
    expect(formatCost(0.004)).toBe('<$0.01');
  });

  it('formats normal amounts', () => {
    expect(formatCost(1.234)).toBe('$1.23');
    expect(formatCost(0)).toBe('$0.00');
    expect(formatCost(null)).toBe('-');
  });
});

describe('humanizeCategory', () => {
  it('turns snake_case into title case', () => {
    expect(humanizeCategory('class_action_waiver')).toBe('Class Action Waiver');
    expect(humanizeCategory('data_retention')).toBe('Data Retention');
  });
});

describe('splitForHighlight', () => {
  const text = 'The vendor may retain data indefinitely after termination.';

  it('splits on absolute offsets', () => {
    const result = splitForHighlight(text, 15, 26);
    expect(result).not.toBeNull();
    expect(result!.highlight).toBe('retain data');
    expect(result!.before + result!.highlight + result!.after).toBe(text);
  });

  it('applies an offset base for a windowed excerpt', () => {
    const excerpt = text.slice(10);
    const result = splitForHighlight(excerpt, 15, 26, 10);
    expect(result!.highlight).toBe('retain data');
  });

  it('rejects out-of-range offsets rather than highlighting the wrong text', () => {
    expect(splitForHighlight(text, 0, 9999)).toBeNull();
    expect(splitForHighlight(text, 30, 10)).toBeNull();
    expect(splitForHighlight(text, -5, 10)).toBeNull();
  });

  it('rejects an empty range', () => {
    expect(splitForHighlight(text, 10, 10)).toBeNull();
  });
});

describe('confidenceLabel', () => {
  it('bands confidence into plain language', () => {
    expect(confidenceLabel(0.95)).toBe('high');
    expect(confidenceLabel(0.6)).toBe('moderate');
    expect(confidenceLabel(0.2)).toBe('low');
  });

  it('uses inclusive lower bounds', () => {
    expect(confidenceLabel(0.8)).toBe('high');
    expect(confidenceLabel(0.5)).toBe('moderate');
  });
});

describe('toCsv', () => {
  it('writes a header and rows', () => {
    const csv = toCsv([{ a: 1, b: 'x' }], ['a', 'b']);
    expect(csv.split('\r\n')).toEqual(['a,b', '1,x']);
  });

  it('escapes commas, quotes and newlines', () => {
    const csv = toCsv([{ q: 'a,b' }, { q: 'say "hi"' }, { q: 'line1\nline2' }], ['q']);
    expect(csv).toContain('"a,b"');
    expect(csv).toContain('"say ""hi"""');
    expect(csv).toContain('"line1\nline2"');
  });

  it('renders missing values as empty cells', () => {
    expect(toCsv([{ a: 1 }], ['a', 'missing'])).toBe('a,missing\r\n1,');
  });
});

describe('slugify', () => {
  it('makes a safe filename stem', () => {
    expect(slugify('Acme Cloud EULA (2026)')).toBe('acme-cloud-eula-2026');
  });

  it('falls back when nothing usable remains', () => {
    expect(slugify('!!!')).toBe('export');
  });
});
