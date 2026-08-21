import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { MarketSeries } from '../../../types/marketSeries';
import { MarketTimeframeChart } from '../MarketTimeframeChart';

const { mockGetMarketSeries } = vi.hoisted(() => ({ mockGetMarketSeries: vi.fn() }));

vi.mock('../../../api/marketSeries', () => ({ getMarketSeries: mockGetMarketSeries }));
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  ComposedChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Bar: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Area: () => null, CartesianGrid: () => null, Cell: () => null, Line: () => null,
  ReferenceLine: () => null, Tooltip: () => null, XAxis: () => null, YAxis: () => null,
}));

const recoveredSeries: MarketSeries = {
  stockCode: '300476.SZ', stockName: '胜宏科技', period: 'intraday', range: '1d',
  source: 'sqlite.test', storedCount: 2, latestAt: '2026-08-20T15:00:00', preClose: 100,
  refreshed: false, storage: 'sqlite',
  data: [
    { date: '2026-08-20T09:30:00', open: 100, high: 101, low: 99, close: 100, volume: 10 },
    { date: '2026-08-20T09:31:00', open: 100, high: 102, low: 100, close: 101, volume: 20 },
  ],
};

describe('MarketTimeframeChart recovery', () => {
  beforeEach(() => vi.clearAllMocks());

  it('automatically recovers from one transient request failure and clears the blocking state', async () => {
    mockGetMarketSeries.mockRejectedValueOnce(new Error('临时 SQLite 锁')).mockResolvedValue(recoveredSeries);

    render(<MarketTimeframeChart symbol="300476.SZ" initialPeriod="intraday" initialRange="1d" />);

    expect(await screen.findByText('行情连接正在恢复，页面会自动重试…')).toBeInTheDocument();
    await waitFor(() => expect(mockGetMarketSeries).toHaveBeenCalledTimes(2), { timeout: 3_000 });
    expect(await screen.findByText(/2 个分钟点/)).toBeInTheDocument();
    expect(screen.queryByText('行情连接正在恢复，页面会自动重试…')).not.toBeInTheDocument();
  });
});
