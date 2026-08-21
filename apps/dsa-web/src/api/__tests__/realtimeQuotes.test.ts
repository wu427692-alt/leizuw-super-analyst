import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({
  default: { get: vi.fn() },
  BACKGROUND_ROUTE_HEADERS: { 'X-DSA-Route-Load': 'background' },
}));

import apiClient from '../index';
import { getRealtimeIndices, getRealtimeQuotes } from '../realtimeQuotes';

describe('realtime quote request coalescing', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shares one request between components asking for the same stock set', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{ stock_code: '603306', current_price: 74.2, change_percent: 1.1 }],
    });

    const [first, second] = await Promise.all([
      getRealtimeQuotes(['603306', '300476']),
      getRealtimeQuotes(['300476', '603306']),
    ]);

    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(first).toEqual(second);
    expect(first[0].currentPrice).toBe(74.2);
  });

  it('shares one request between duplicate index consumers', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: [{ code: '000001.SH', close: 3905.2, change_pct: 0.04 }],
    });

    await Promise.all([
      getRealtimeIndices(['000001.SH', '000300.SH']),
      getRealtimeIndices(['000300.SH', '000001.SH']),
    ]);

    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });
});
