import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cachedQuery, clearRequestCache, invalidateCachedQueries, invalidateCachedQueryValue, peekCachedQuery } from '../requestCache';

describe('requestCache', () => {
  beforeEach(() => {
    clearRequestCache();
    vi.useRealTimers();
  });
  afterEach(() => { vi.useRealTimers(); clearRequestCache(); });

  it('peeks only the existing value without fetching, extending freshness, or surviving invalidation', async () => {
    vi.useFakeTimers();
    expect(peekCachedQuery('snapshot')).toBeUndefined();
    await cachedQuery('snapshot', async () => 0, { freshMs: 100, staleMs: 0 });
    vi.advanceTimersByTime(200);
    expect(peekCachedQuery('snapshot')).toBe(0);
    const query = vi.fn().mockResolvedValue(1);
    expect(await cachedQuery('snapshot', query, { freshMs: 100, staleMs: 0 })).toBe(1);
    expect(query).toHaveBeenCalledTimes(1);
    clearRequestCache();
    expect(peekCachedQuery('snapshot')).toBeUndefined();
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

  it('registers loader ownership synchronously and retires it on forced replacement', async () => {
    let resolveOld!: (value: number) => void;
    let isOldCurrent!: () => boolean;
    const old = cachedQuery('owner', ({ isCurrent }) => {
      isOldCurrent = isCurrent;
      expect(isCurrent()).toBe(true);
      return new Promise<number>(resolve => { resolveOld = resolve; });
    }, { freshMs: 1000 });
    await cachedQuery('owner', async ({ isCurrent }) => {
      expect(isOldCurrent()).toBe(false);
      expect(isCurrent()).toBe(true);
      return 2;
    }, { freshMs: 1000, force: true });
    resolveOld(1); await old;
    expect(isOldCurrent()).toBe(false);
    expect(peekCachedQuery('owner')).toBe(2);
  });

  it('clears a fresh value but preserves its pending owner, deduplication, and successful cache write', async () => {
    const options = { freshMs: 1000, staleMs: 2000 };
    await cachedQuery('full', async () => 'old', options);
    await cachedQuery('full:unrelated', async () => 'other', options);
    let resolve!: (value: string) => void;
    let isCurrent!: () => boolean;
    const pending = cachedQuery('full', context => {
      isCurrent = context.isCurrent;
      return new Promise<string>(done => { resolve = done; });
    }, { ...options, force: true });
    invalidateCachedQueryValue('full');
    expect(isCurrent()).toBe(true);
    expect(peekCachedQuery('full')).toBeUndefined();
    expect(peekCachedQuery('full:unrelated')).toBe('other');
    const duplicate = vi.fn().mockResolvedValue('unexpected');
    let settled = false;
    const shared = cachedQuery('full', duplicate, options).then(value => { settled = true; return value; });
    await Promise.resolve();
    expect(settled).toBe(false);
    expect(duplicate).not.toHaveBeenCalled();
    resolve('fresh');
    await expect(Promise.all([pending, shared])).resolves.toEqual(['fresh', 'fresh']);
    await expect(cachedQuery('full', duplicate, options)).resolves.toBe('fresh');
    expect(duplicate).not.toHaveBeenCalled();
    invalidateCachedQueryValue('full');
    expect(peekCachedQuery('full')).toBeUndefined();
    expect(peekCachedQuery('full:unrelated')).toBe('other');
  });

  it('does not restore an invalidated value when its pending refresh fails', async () => {
    await cachedQuery('full', async () => 'old', { freshMs: 1000 });
    let reject!: (error: Error) => void;
    const pending = cachedQuery('full', () => new Promise<string>((_, fail) => { reject = fail; }), { freshMs: 1000, force: true });
    invalidateCachedQueryValue('full');
    reject(new Error('timeout'));
    await expect(pending).rejects.toThrow('timeout');
    expect(peekCachedQuery('full')).toBeUndefined();
  });

  it.each(['success', 'failure'] as const)('a value-invalidated old %s cannot replace or interrupt a forced request', async outcome => {
    let resolveOld!: (value: string) => void;
    let rejectOld!: (error: Error) => void;
    let resolveNew!: (value: string) => void;
    const old = cachedQuery('full', () => new Promise<string>((resolve, reject) => { resolveOld = resolve; rejectOld = reject; }), { freshMs: 1000 })
      .catch(() => undefined);
    invalidateCachedQueryValue('full');
    const newer = cachedQuery('full', () => new Promise<string>(resolve => { resolveNew = resolve; }), { freshMs: 1000, force: true });
    if (outcome === 'success') resolveOld('old'); else rejectOld(new Error('old failure'));
    await old;
    const duplicate = vi.fn().mockResolvedValue('unexpected');
    const shared = cachedQuery('full', duplicate, { freshMs: 1000 });
    expect(duplicate).not.toHaveBeenCalled();
    resolveNew('new');
    await expect(Promise.all([newer, shared])).resolves.toEqual(['new', 'new']);
    expect(peekCachedQuery('full')).toBe('new');
  });

  it('removes a preregistered owner when the loader throws synchronously', async () => {
    await expect(cachedQuery('sync', () => { throw new Error('sync failure'); }, { freshMs: 1000 })).rejects.toThrow('sync failure');
    expect(peekCachedQuery('sync')).toBeUndefined();
    await expect(cachedQuery('sync', async () => 'recovered', { freshMs: 1000 })).resolves.toBe('recovered');
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

  const invalidations = [
    { name: 'clear all on logout', invalidate: () => clearRequestCache() },
    { name: 'invalidate the matching prefix', invalidate: () => invalidateCachedQueries('quant:') },
  ];
  describe.each(invalidations)('$name', ({ invalidate }) => {
    it('retires the pending loader context as well as its value', async () => {
      let isCurrent!: () => boolean;
      let resolve!: (value: string) => void;
      const pending = cachedQuery('quant:tasks', context => {
        isCurrent = context.isCurrent;
        return new Promise<string>(done => { resolve = done; });
      }, { freshMs: 1000 });
      expect(isCurrent()).toBe(true);
      invalidate();
      expect(isCurrent()).toBe(false);
      resolve('old'); await pending;
      expect(peekCachedQuery('quant:tasks')).toBeUndefined();
    });

    it('does not repopulate an invalidated entry when an older request succeeds', async () => {
      let resolveOld!: (value: string) => void;
      const old = cachedQuery('quant:tasks', () => new Promise<string>(resolve => { resolveOld = resolve; }), { freshMs: 10_000 });
      invalidate();
      resolveOld('private A'); await old;
      const fresh = vi.fn().mockResolvedValue('private B');
      await expect(cachedQuery('quant:tasks', fresh, { freshMs: 10_000 })).resolves.toBe('private B');
      expect(fresh).toHaveBeenCalledTimes(1);
    });

    it.each(['success', 'failure'] as const)('an old %s cannot change a new successful entry', async outcome => {
      let resolveOld!: (value: string) => void;
      let rejectOld!: (error: Error) => void;
      const old = cachedQuery('quant:tasks', () => new Promise<string>((resolve, reject) => { resolveOld = resolve; rejectOld = reject; }), { freshMs: 10_000 })
        .catch(() => 'old error observed');
      invalidate();
      await cachedQuery('quant:tasks', async () => 'private B', { freshMs: 10_000 });
      if (outcome === 'success') resolveOld('private A'); else rejectOld(new Error('old timeout'));
      await old;
      const unexpected = vi.fn().mockResolvedValue('unexpected');
      await expect(cachedQuery('quant:tasks', unexpected, { freshMs: 10_000 })).resolves.toBe('private B');
      expect(unexpected).not.toHaveBeenCalled();
    });

    it('an old failure cannot drop the new in-flight request or break its deduplication', async () => {
      let rejectOld!: (error: Error) => void;
      let resolveNew!: (value: string) => void;
      const old = cachedQuery('quant:tasks', () => new Promise<string>((_, reject) => { rejectOld = reject; }), { freshMs: 10_000 }).catch(() => undefined);
      invalidate();
      const newer = cachedQuery('quant:tasks', () => new Promise<string>(resolve => { resolveNew = resolve; }), { freshMs: 10_000 });
      rejectOld(new Error('old timeout')); await old;
      const duplicate = vi.fn().mockResolvedValue('wrong request');
      const shared = cachedQuery('quant:tasks', duplicate, { freshMs: 10_000 });
      expect(duplicate).not.toHaveBeenCalled();
      resolveNew('private B');
      await expect(Promise.all([newer, shared])).resolves.toEqual(['private B', 'private B']);
    });

    it.each(['success', 'failure'] as const)('a stale background refresh %s cannot resurrect the cleared account', async outcome => {
      vi.useFakeTimers();
      const options = { freshMs: 100, staleMs: 2000 };
      await cachedQuery('quant:tasks', async () => 'private A', options);
      await vi.advanceTimersByTimeAsync(200);
      let resolveOld!: (value: string) => void;
      let rejectOld!: (error: Error) => void;
      await expect(cachedQuery('quant:tasks', () => new Promise<string>((resolve, reject) => { resolveOld = resolve; rejectOld = reject; }), options)).resolves.toBe('private A');
      invalidate();
      if (outcome === 'success') resolveOld('updated private A'); else rejectOld(new Error('old background error'));
      await Promise.resolve(); await Promise.resolve();
      const fresh = vi.fn().mockResolvedValue('private B');
      await expect(cachedQuery('quant:tasks', fresh, options)).resolves.toBe('private B');
      expect(fresh).toHaveBeenCalledTimes(1);
    });
  });

  it('prefix invalidation leaves unrelated cached channels intact', async () => {
    await cachedQuery('acquisition:tasks', async () => 'downloads', { freshMs: 10_000 });
    invalidateCachedQueries('quant:');
    const duplicate = vi.fn().mockResolvedValue('unexpected');
    await expect(cachedQuery('acquisition:tasks', duplicate, { freshMs: 10_000 })).resolves.toBe('downloads');
    expect(duplicate).not.toHaveBeenCalled();
  });

  it('a normal failed background refresh preserves stale data until the next retry succeeds', async () => {
    vi.useFakeTimers();
    const options = { freshMs: 100, staleMs: 2000 };
    await cachedQuery('quant:tasks', async () => 'first', options);
    await vi.advanceTimersByTimeAsync(200);
    await expect(cachedQuery('quant:tasks', async () => { throw new Error('temporary error'); }, options)).resolves.toBe('first');
    await Promise.resolve(); await Promise.resolve();
    await expect(cachedQuery('quant:tasks', async () => 'recovered', options)).resolves.toBe('first');
    await Promise.resolve();
    await expect(cachedQuery('quant:tasks', async () => 'unexpected', options)).resolves.toBe('recovered');
  });
});
