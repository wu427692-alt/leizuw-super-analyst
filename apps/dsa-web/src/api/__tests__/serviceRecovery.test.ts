import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  resetApiServiceRecoveryForTests,
  waitForApiServiceRecovery,
} from '../serviceRecovery';

describe('API service recovery', () => {
  afterEach(() => {
    resetApiServiceRecoveryForTests();
    vi.restoreAllMocks();
  });

  it('keeps probing until the API health endpoint recovers', async () => {
    const fetchImpl = vi.fn()
      .mockRejectedValueOnce(new TypeError('network down'))
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: true });

    await expect(waitForApiServiceRecovery({
      fetchImpl: fetchImpl as unknown as typeof fetch,
      intervalMs: 0,
      probeTimeoutMs: 100,
      timeoutMs: 1_000,
    })).resolves.toBe(true);

    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(fetchImpl).toHaveBeenLastCalledWith('/api/health', expect.objectContaining({
      cache: 'no-store',
      credentials: 'include',
    }));
  });

  it('shares one probe loop across simultaneous failed page requests', async () => {
    let releaseHealthCheck: ((value: { ok: boolean }) => void) | undefined;
    const fetchImpl = vi.fn(() => new Promise<{ ok: boolean }>((resolve) => {
      releaseHealthCheck = resolve;
    }));
    const options = {
      fetchImpl: fetchImpl as unknown as typeof fetch,
      intervalMs: 0,
      probeTimeoutMs: 1_000,
      timeoutMs: 1_000,
    };

    const first = waitForApiServiceRecovery(options);
    const second = waitForApiServiceRecovery(options);
    expect(second).toBe(first);
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    releaseHealthCheck?.({ ok: true });
    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
