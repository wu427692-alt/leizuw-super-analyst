import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { usePageActivationRefresh } from '../usePageActivationRefresh';

describe('usePageActivationRefresh', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  });

  afterEach(() => { vi.useRealTimers(); });

  it('refreshes on mount, tab activation and interval without duplicate focus bursts', async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    renderHook(() => usePageActivationRefresh(refresh, {
      intervalMs: 30_000, minIntervalMs: 2_000,
    }));
    await act(async () => { await Promise.resolve(); });
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve();
    });
    expect(refresh).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });
    expect(refresh).toHaveBeenCalledTimes(3);
  });

  it('guards background intervals without blocking activation refreshes', async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const intervalGuard = vi.fn().mockReturnValue(false);
    renderHook(() => usePageActivationRefresh(refresh, {
      intervalMs: 15_000, minIntervalMs: 2_000, intervalGuard,
    }));
    await act(async () => { await Promise.resolve(); });
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(15_000);
      await Promise.resolve();
    });
    expect(intervalGuard).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new Event('focus'));
      await Promise.resolve();
    });
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
