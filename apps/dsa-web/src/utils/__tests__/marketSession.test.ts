import { describe, expect, it } from 'vitest';
import { isAshareLiveWindow } from '../marketSession';

describe('isAshareLiveWindow', () => {
  it('uses Shanghai trading windows independent of browser timezone', () => {
    expect(isAshareLiveWindow(new Date('2026-09-01T01:30:00.000Z'))).toBe(true);
    expect(isAshareLiveWindow(new Date('2026-09-01T04:00:00.000Z'))).toBe(false);
    expect(isAshareLiveWindow(new Date('2026-09-01T05:00:00.000Z'))).toBe(true);
    expect(isAshareLiveWindow(new Date('2026-09-01T07:06:00.000Z'))).toBe(false);
  });

  it('does not continuously poll on weekends', () => {
    expect(isAshareLiveWindow(new Date('2026-09-05T02:00:00.000Z'))).toBe(false);
  });
});
