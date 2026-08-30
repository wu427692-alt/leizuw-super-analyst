import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cachedQuery, clearRequestCache } from '../requestCache';

describe('requestCache', () => {
  beforeEach(() => {
    clearRequestCache();
    vi.useRealTimers();
  });

  it('deduplicates concurrent page aggregation requests', async () => {
    let resolve!: (value: number) => void;
    const loader = vi.fn(() => new Promise<number>((done) => { resolve = done; }));
    const first = cachedQuery('same-page', loader, { freshMs: 10_000 });
    const second = cachedQuery('same-page', loader, { freshMs: 10_000 });

    expect(loader).toHaveBeenCalledTimes(1);
    resolve(42);
    await expect(Promise.all([first, second])).resolves.toEqual([42, 42]);
  });

  it('serves a recent stale value immediately while refreshing the shared cache', async () => {
    vi.useFakeTimers();
    const loader = vi.fn()
      .mockResolvedValueOnce('first')
      .mockResolvedValueOnce('fresh');
    await expect(cachedQuery('stale-page', loader, { freshMs: 100, staleMs: 2_000 })).resolves.toBe('first');
    await vi.advanceTimersByTimeAsync(200);

    await expect(cachedQuery('stale-page', loader, { freshMs: 100, staleMs: 2_000 })).resolves.toBe('first');
    await vi.runAllTicks();
    await expect(cachedQuery('stale-page', loader, { freshMs: 100, staleMs: 2_000 })).resolves.toBe('fresh');
    expect(loader).toHaveBeenCalledTimes(2);
  });
});
