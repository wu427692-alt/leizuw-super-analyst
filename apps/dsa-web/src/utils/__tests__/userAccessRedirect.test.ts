import { describe, expect, it } from 'vitest';
import { safeUserAccessRedirect } from '../userAccessRedirect';

describe('account redirects', () => {
  it('preserves a same-site workspace and its query', () => {
    expect(safeUserAccessRedirect('/ai-news?view=latest#news')).toBe('/ai-news?view=latest#news');
    expect(safeUserAccessRedirect('/super-watchlist?symbol=603306.SH')).toBe('/super-watchlist?symbol=603306.SH');
  });
  it.each([null, '', 'https://example.com', '//example.com', '/\\example.com', '/\n/example.com', '/access', '/access/?bind=phone'])('rejects unsafe or looping callback %s', value => {
    expect(safeUserAccessRedirect(value)).toBe('/app');
  });
});
